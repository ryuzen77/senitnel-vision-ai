"""Fake-only unit tests for the production CLIP score runner."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from app.services.clip_scene_runner import (
    DEFAULT_MODEL_REVISION,
    ClipSceneRunner,
    make_clip_scene_runner_factory,
)


class Gate:
    def __init__(self) -> None:
        self.entries = 0

    def __enter__(self):
        self.entries += 1

    def __exit__(self, *_args):
        return False


class Inputs(dict):
    def __init__(self, **values):
        super().__init__(values)
        self.device = None

    def to(self, device):
        self.device = device
        return self


class FakeProcessor:
    calls: list[dict] = []
    kwargs: dict | None = None

    @classmethod
    def from_pretrained(cls, _model_id, **kwargs):
        cls.kwargs = kwargs
        return cls()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if "text" in kwargs:
            return Inputs(kind="text")
        return Inputs(kind="image")


class FakeModel:
    kwargs: dict | None = None
    instances: list["FakeModel"] = []
    wrap_outputs = False

    @classmethod
    def from_pretrained(cls, _model_id, **kwargs):
        cls.kwargs = kwargs
        instance = cls()
        cls.instances.append(instance)
        return instance

    def __init__(self) -> None:
        self.device = None
        self.evaluated = False
        self.text_calls = 0
        self.image_calls = 0
        self.config = type("Config", (), {"projection_dim": 2})()
        self.text_projection = NeverCallProjection()
        self.visual_projection = NeverCallProjection()
        self.image_features = torch.tensor([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, -1.0], [2.0, 0.0]])

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        self.evaluated = True
        return self

    def get_text_features(self, **_kwargs):
        self.text_calls += 1
        features = torch.tensor([[1.0, 0.0]] * 3 + [[0.0, 1.0]] * 3)
        return OutputWithPooler(features) if self.wrap_outputs else features

    def get_image_features(self, **_kwargs):
        self.image_calls += 1
        return OutputWithPooler(self.image_features) if self.wrap_outputs else self.image_features


class OutputWithPooler:
    def __init__(self, pooler_output):
        self.pooler_output = pooler_output


class NeverCallProjection:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _value):
        self.calls += 1
        raise AssertionError("projection must not be called a second time")


class FakeCudaTorch:
    cuda = type("Cuda", (), {"is_available": staticmethod(lambda: True)})()
    inference_mode = staticmethod(torch.inference_mode)
    linalg = torch.linalg
    any = staticmethod(torch.any)
    all = staticmethod(torch.all)
    isfinite = staticmethod(torch.isfinite)
    stack = staticmethod(torch.stack)
    Tensor = torch.Tensor


def _frames():
    return [np.full((2, 3, 3), index, dtype=np.uint8) for index in range(5)]


def _runner(gate: Gate | None = None):
    FakeProcessor.calls = []
    FakeProcessor.kwargs = None
    FakeModel.instances = []
    FakeModel.wrap_outputs = False
    return ClipSceneRunner(
        gate or Gate(),
        torch_module=FakeCudaTorch(),
        model_loader=FakeModel,
        processor_loader=FakeProcessor,
        image_fromarray=lambda array: array,
    )


def test_import_does_not_load_model_and_factory_is_lazy():
    FakeModel.instances = []
    factory = make_clip_scene_runner_factory(
        Gate(), torch_module=FakeCudaTorch(), model_loader=FakeModel, processor_loader=FakeProcessor, image_fromarray=lambda array: array
    )
    assert FakeModel.instances == []
    factory()
    assert len(FakeModel.instances) == 1


def test_constructor_uses_local_revision_cuda_gate_and_caches_text_embeddings():
    gate = Gate()
    runner = _runner(gate)
    model = FakeModel.instances[0]
    assert FakeModel.kwargs == {"revision": DEFAULT_MODEL_REVISION, "local_files_only": True, "trust_remote_code": False}
    assert FakeProcessor.kwargs == FakeModel.kwargs
    assert model.device == "cuda:0" and model.evaluated
    assert model.text_calls == 1 and tuple(runner._domain_embeddings.shape) == (2, 2)  # noqa: SLF001
    assert gate.entries == 1


def test_cuda_unavailable_fails_clearly_without_load():
    unavailable = type("Torch", (), {"cuda": type("Cuda", (), {"is_available": staticmethod(lambda: False)})()})()
    with pytest.raises(RuntimeError, match="CUDA"):
        ClipSceneRunner(Gate(), torch_module=unavailable, model_loader=FakeModel, processor_loader=FakeProcessor)


def test_classify_converts_bgr_does_not_mutate_and_runs_one_batch_under_gate():
    gate = Gate()
    runner = _runner(gate)
    frames = _frames()
    originals = [frame.copy() for frame in frames]
    scores = runner.classify(frames)
    image_call = FakeProcessor.calls[-1]
    assert np.array_equal(image_call["images"][0], originals[0][:, :, ::-1])
    assert all(np.array_equal(frame, original) for frame, original in zip(frames, originals))
    assert len(scores) == 5 and FakeModel.instances[0].image_calls == 1
    assert scores[0].parking_score > scores[0].retail_score
    assert scores[1].retail_score > scores[1].parking_score
    assert gate.entries == 2
    runner.classify(frames)
    assert FakeModel.instances[0].text_calls == 1
    assert FakeModel.instances[0].text_projection.calls == 0
    assert FakeModel.instances[0].visual_projection.calls == 0


def test_pooler_output_objects_match_transformers_v5_contract_for_text_and_image():
    FakeProcessor.calls = []
    FakeProcessor.kwargs = None
    FakeModel.instances = []
    FakeModel.wrap_outputs = True
    runner = ClipSceneRunner(
        Gate(), torch_module=FakeCudaTorch(), model_loader=FakeModel, processor_loader=FakeProcessor, image_fromarray=lambda array: array
    )
    assert runner.classify(_frames())[0].parking_score > 0
    model = FakeModel.instances[0]
    assert model.text_calls == 1 and model.image_calls == 1
    assert model.text_projection.calls == 0 and model.visual_projection.calls == 0


def test_invalid_count_frame_output_shape_and_nonfinite_scores_are_rejected():
    runner = _runner()
    with pytest.raises(ValueError, match="exactly five"):
        runner.classify(_frames()[:4])
    with pytest.raises(ValueError, match="BGR"):
        runner.classify([np.zeros((2, 2), dtype=np.uint8)] * 5)
    FakeModel.instances[0].image_features = torch.ones((4, 2))
    with pytest.raises(RuntimeError, match="shape"):
        runner.classify(_frames())
    FakeModel.instances[0].image_features = torch.tensor([[float("nan"), 0.0]] * 5)
    with pytest.raises(RuntimeError, match="invalid embedding norms|non-finite"):
        runner.classify(_frames())


def test_feature_extraction_rejects_hidden_state_only_rank_dimension_and_nonfinite_values():
    runner = _runner()
    with pytest.raises(RuntimeError, match="pooler_output"):
        runner._extract_projected_features(type("Output", (), {"last_hidden_state": torch.ones((1, 2, 2))})(), "text")  # noqa: SLF001
    with pytest.raises(RuntimeError, match="rank-2"):
        runner._extract_projected_features(OutputWithPooler(torch.ones((1, 1, 2))), "text")  # noqa: SLF001
    with pytest.raises(RuntimeError, match="projection_dim"):
        runner._extract_projected_features(torch.ones((1, 3)), "image")  # noqa: SLF001
    with pytest.raises(RuntimeError, match="non-finite"):
        runner._extract_projected_features(OutputWithPooler(torch.tensor([[float("inf"), 1.0]])), "image")  # noqa: SLF001
