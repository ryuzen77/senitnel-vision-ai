"""Local CLIP runner for the early-frame scene-domain sampler.

This module only provides the production scoring primitive.  Construction is
deliberately deferred to a ``SceneDomainClassifier`` runner factory, so merely
importing it never loads a model or allocates GPU memory.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from typing import Any, ContextManager

import numpy as np

from app.services.scene_domain_classifier import SceneFrameScores

DEFAULT_MODEL_ID = os.environ.get("SENTINEL_CLIP_MODEL_ID", "openai/clip-vit-base-patch32")
# This is the revision currently referenced by the local Hugging Face cache.
DEFAULT_MODEL_REVISION = os.environ.get(
    "SENTINEL_CLIP_MODEL_REVISION", "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
)
PARKING_PROMPTS = (
    "a CCTV camera view of a motorcycle parking area",
    "parked motorcycles in a parking lot seen by a security camera",
    "an outdoor or indoor motorcycle parking facility",
)
RETAIL_PROMPTS = (
    "a CCTV camera view inside a retail store",
    "a clothing store with garments, shelves, or merchandise",
    "a small shop or minimarket with products for sale",
)

CudaGate = ContextManager[None]


class ClipSceneRunner:
    """Convert exactly five BGR frames into raw CLIP cosine score pairs."""

    def __init__(
        self,
        cuda_gate: CudaGate,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        revision: str = DEFAULT_MODEL_REVISION,
        device: str = "cuda:0",
        torch_module: Any | None = None,
        model_loader: Any | None = None,
        processor_loader: Any | None = None,
        image_fromarray: Callable[[np.ndarray], Any] | None = None,
    ) -> None:
        self._torch = torch_module or _load_torch()
        if not self._torch.cuda.is_available():
            raise RuntimeError("CLIP scene runner requires CUDA, but CUDA is unavailable")
        if not model_id or not revision:
            raise ValueError("model_id and revision must be non-empty")

        if model_loader is None or processor_loader is None:
            model_loader, processor_loader = _load_transformers()
        self._image_fromarray = image_fromarray or _load_image_fromarray()
        self._cuda_gate = cuda_gate
        self.model_id = model_id
        self.revision = revision
        self.device = device
        load_kwargs = {
            "revision": revision,
            "local_files_only": True,
            "trust_remote_code": False,
        }
        self._processor = processor_loader.from_pretrained(model_id, **load_kwargs)
        self._model = model_loader.from_pretrained(model_id, **load_kwargs)
        self._projection_dim = self._model.config.projection_dim
        with self._cuda_gate:
            self._model.to(device)
            self._model.eval()
            text_inputs = self._move_to_device(
                self._processor(text=[*PARKING_PROMPTS, *RETAIL_PROMPTS], padding=True, return_tensors="pt")
            )
            text_embeddings = self._normalize(
                self._extract_projected_features(self._model.get_text_features(**text_inputs), "text")
            )
            self._domain_embeddings = self._build_domain_embeddings(text_embeddings)

    def __call__(self, frames: Sequence[np.ndarray]) -> Sequence[SceneFrameScores]:
        return self.classify(frames)

    def classify(self, frames: Sequence[np.ndarray]) -> list[SceneFrameScores]:
        """Score five BGR frames in one image-encoder forward pass."""
        if len(frames) != 5:
            raise ValueError("CLIP scene runner requires exactly five frames")
        images = [self._bgr_to_rgb_image(frame) for frame in frames]
        image_inputs = self._processor(images=images, return_tensors="pt")
        with self._cuda_gate:
            with self._torch.inference_mode():
                image_inputs = self._move_to_device(image_inputs)
                image_embeddings = self._normalize(
                    self._extract_projected_features(self._model.get_image_features(**image_inputs), "image")
                )
                similarities = image_embeddings @ self._domain_embeddings.T
        values = similarities.detach().cpu().tolist()
        if len(values) != 5 or any(len(row) != 2 for row in values):
            raise RuntimeError("CLIP image similarity output must have shape (5, 2)")
        result = [SceneFrameScores(float(row[0]), float(row[1])) for row in values]
        if not all(math.isfinite(score) for pair in result for score in (pair.parking_score, pair.retail_score)):
            raise RuntimeError("CLIP returned non-finite scene scores")
        return result

    def _bgr_to_rgb_image(self, frame: np.ndarray) -> Any:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
            raise ValueError("each frame must be a non-empty BGR ndarray with shape (H, W, 3)")
        # Slicing and copying makes a separate RGB buffer and leaves capture data untouched.
        return self._image_fromarray(frame[:, :, ::-1].copy())

    def _move_to_device(self, inputs: Any) -> Any:
        return inputs.to(self.device) if hasattr(inputs, "to") else {
            name: value.to(self.device) if hasattr(value, "to") else value for name, value in inputs.items()
        }

    def _normalize(self, embeddings: Any) -> Any:
        norms = self._torch.linalg.vector_norm(embeddings, dim=-1, keepdim=True)
        if bool(self._torch.any(norms <= 0)) or not bool(self._torch.all(self._torch.isfinite(norms))):
            raise RuntimeError("CLIP returned invalid embedding norms")
        normalized = embeddings / norms
        if not bool(self._torch.all(self._torch.isfinite(normalized))):
            raise RuntimeError("CLIP returned non-finite embeddings")
        return normalized

    def _extract_projected_features(self, output: Any, output_name: str) -> Any:
        """Accept legacy tensors and Transformers 5 output objects strictly.

        ``get_*_features`` in Transformers 5 returns a
        ``BaseModelOutputWithPooling`` whose ``pooler_output`` has already been
        passed through CLIP's projection layer.  Do not substitute a hidden
        state or project this value a second time.
        """
        tensor_type = self._torch.Tensor
        if isinstance(output, tensor_type):
            features = output
        else:
            features = getattr(output, "pooler_output", None)
            if not isinstance(features, tensor_type):
                raise RuntimeError(
                    f"CLIP {output_name} features must be a Tensor or expose Tensor pooler_output"
                )
        if features.ndim != 2:
            raise RuntimeError(f"CLIP {output_name} projected features must be rank-2 [batch, projection_dim]")
        if features.shape[-1] != self._projection_dim:
            raise RuntimeError(
                f"CLIP {output_name} projected features have dimension {features.shape[-1]}, "
                f"expected projection_dim {self._projection_dim}"
            )
        if not bool(self._torch.all(self._torch.isfinite(features))):
            raise RuntimeError(f"CLIP {output_name} projected features contain non-finite values")
        return features

    def _build_domain_embeddings(self, text_embeddings: Any) -> Any:
        if tuple(text_embeddings.shape) != (6, text_embeddings.shape[-1]):
            raise RuntimeError("CLIP text embedding output must contain six prompt embeddings")
        domain_means = self._torch.stack((text_embeddings[:3].mean(dim=0), text_embeddings[3:].mean(dim=0)))
        return self._normalize(domain_means)


def make_clip_scene_runner_factory(
    cuda_gate: CudaGate,
    **runner_kwargs: Any,
) -> Callable[[], ClipSceneRunner]:
    """Return a lazy factory suitable for ``SceneDomainClassifier`` injection."""

    return lambda: ClipSceneRunner(cuda_gate, **runner_kwargs)


def _load_torch() -> Any:
    import torch

    return torch


def _load_transformers() -> tuple[Any, Any]:
    from transformers import CLIPModel, CLIPProcessor

    return CLIPModel, CLIPProcessor


def _load_image_fromarray() -> Callable[[np.ndarray], Any]:
    from PIL import Image

    return Image.fromarray
