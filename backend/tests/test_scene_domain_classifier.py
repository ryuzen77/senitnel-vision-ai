"""Fake-only tests for the nonblocking early-frame scene sampler."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import numpy as np

from app.models.detection import Detection, DetectionResult
from app.models.domain import DecisionSource, DomainName, DomainState
from app.models.video import FramePacket
from app.services.domain_service import DomainService
from app.services.scene_domain_classifier import SceneDomainClassifier, SceneFrameScores


def _packet(session: str, seq: int, ts: float) -> FramePacket:
    return FramePacket(session, seq, ts, np.full((2, 2, 3), seq, dtype=np.uint8), b"jpeg")


def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _scores(parking: float, retail: float) -> list[SceneFrameScores]:
    return [SceneFrameScores(parking, retail) for _ in range(5)]


def _start(domain: DomainService, classifier: SceneDomainClassifier, session: str = "session") -> None:
    domain.start_session(session)
    classifier.start_session(session)


def _offer_five(classifier: SceneDomainClassifier, session: str = "session", start_seq: int = 1) -> None:
    for offset, target in enumerate((1, 2, 3, 4, 5)):
        classifier.offer(_packet(session, start_seq + offset, target + 0.01))


def _yolo_lock(domain: DomainService, session: str = "session") -> None:
    detection = Detection(3, "motorcycle", 0.9, (1.0, 2.0, 3.0, 4.0))
    for sequence in range(3):
        domain.observe(DetectionResult(sequence, session, sequence, float(sequence), [detection]))


def test_start_and_early_packets_do_not_make_runner_or_samples() -> None:
    domain = DomainService()
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return lambda _frames: _scores(0.4, 0.1)

    classifier = SceneDomainClassifier(domain, factory, clock=lambda: 0.0)
    try:
        _start(domain, classifier)
        classifier.offer(_packet("session", 1, 0.99))
        assert factory_calls == 0
        assert classifier._samples == []  # noqa: SLF001
    finally:
        classifier.close()


def test_selects_first_packet_after_each_target_and_ignores_duplicate_or_old_sequence() -> None:
    domain = DomainService()
    classifier = SceneDomainClassifier(domain, lambda: lambda _frames: _scores(0.4, 0.1), clock=lambda: 0.0)
    try:
        _start(domain, classifier)
        classifier.offer(_packet("session", 1, 1.01))
        classifier.offer(_packet("session", 0, 2.01))  # Out of order.
        classifier.offer(_packet("session", 2, 2.01))
        classifier.offer(_packet("session", 3, 3.01))
        assert [packet.seq for packet in classifier._samples] == [1, 2, 3]  # noqa: SLF001
        # Packet at 5.01 can fill only target 4, never both 4 and 5.
        classifier.offer(_packet("session", 4, 5.01))
        assert [packet.seq for packet in classifier._samples] == [1, 2, 3, 4]  # noqa: SLF001
    finally:
        classifier.close()


def test_worker_copies_frames_once_and_applies_parking_decision() -> None:
    domain = DomainService()
    received: list[np.ndarray] = []

    def runner(frames):
        received.extend(frames)
        for frame in frames:
            frame[:, :, :] = 255
        return _scores(0.4, 0.1)

    classifier = SceneDomainClassifier(domain, lambda: runner, clock=lambda: 0.0, scene_model="fake-clip")
    packets = [_packet("session", index + 1, index + 1.01) for index in range(5)]
    originals = [packet.frame.copy() for packet in packets]
    try:
        _start(domain, classifier)
        for packet in packets:
            classifier.offer(packet)
        assert _wait_for(lambda: domain.get_snapshot().state is DomainState.LOCKED)
        snapshot = domain.get_snapshot()
        assert snapshot.domain is DomainName.PARKING_MOTORCYCLE
        assert snapshot.decision_source is DecisionSource.CLIP
        assert len(received) == 5
        assert all(np.array_equal(packet.frame, original) for packet, original in zip(packets, originals))
    finally:
        classifier.close()


def test_factory_once_and_one_batch_per_session_across_two_sessions() -> None:
    domain = DomainService()
    calls = batches = 0

    def factory():
        nonlocal calls
        calls += 1

        def runner(_frames):
            nonlocal batches
            batches += 1
            return _scores(0.4, 0.1)

        return runner

    classifier = SceneDomainClassifier(domain, factory, clock=lambda: 0.0)
    try:
        _start(domain, classifier, "one")
        _offer_five(classifier, "one")
        assert _wait_for(lambda: domain.get_snapshot().locked)
        _start(domain, classifier, "two")
        _offer_five(classifier, "two", 10)
        assert _wait_for(lambda: domain.get_snapshot().locked and domain.get_snapshot().source_session_id == "two")
        assert calls == 1
        assert batches == 2
    finally:
        classifier.close()


def test_retail_low_score_low_consistency_low_margin_and_vote_tie_lock_expected_domain() -> None:
    cases = [
        (_scores(0.1, 0.2), DomainName.UNKNOWN),
        ([SceneFrameScores(0.4, 0.1)] * 3 + [SceneFrameScores(0.1, 0.4)] * 2, DomainName.UNKNOWN),
        (_scores(0.30, 0.29), DomainName.UNKNOWN),
        ([SceneFrameScores(0.4, 0.1)] * 2 + [SceneFrameScores(0.1, 0.4)] * 2 + [SceneFrameScores(0.3, 0.3)], DomainName.UNKNOWN),
        (_scores(0.1, 0.4), DomainName.RETAIL_STORE),
    ]
    for result, expected in cases:
        domain = DomainService()
        classifier = SceneDomainClassifier(domain, lambda result=result: lambda _frames: result, clock=lambda: 0.0)
        try:
            _start(domain, classifier)
            _offer_five(classifier)
            assert _wait_for(lambda: domain.get_snapshot().locked)
            assert domain.get_snapshot().domain is expected
        finally:
            classifier.close()


def test_yolo_lock_before_batch_avoids_runner_factory() -> None:
    domain = DomainService()
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return lambda _frames: _scores(0.4, 0.1)

    classifier = SceneDomainClassifier(domain, factory, clock=lambda: 0.0)
    try:
        _start(domain, classifier)
        _yolo_lock(domain)
        _offer_five(classifier)
        time.sleep(0.03)
        assert calls == 0
        assert domain.get_snapshot().decision_source is DecisionSource.YOLO
    finally:
        classifier.close()


def test_nonblocking_offer_and_yolo_lock_during_held_runner_rejects_scene_result() -> None:
    domain = DomainService()
    entered = threading.Event()
    release = threading.Event()

    def runner(_frames):
        entered.set()
        release.wait(timeout=1.0)
        return _scores(0.1, 0.4)

    classifier = SceneDomainClassifier(domain, lambda: runner, clock=lambda: 0.0)
    try:
        _start(domain, classifier)
        _offer_five(classifier)
        assert entered.wait(timeout=1.0)
        started = time.monotonic()
        classifier.offer(_packet("session", 99, 99.0))
        assert time.monotonic() - started < 0.05
        _yolo_lock(domain)
        release.set()
        assert _wait_for(lambda: domain.get_snapshot().decision_source is DecisionSource.YOLO)
    finally:
        release.set()
        classifier.close()


def test_end_session_during_sampling_or_inference_discards_stale_results_and_new_session_can_run() -> None:
    domain = DomainService()
    entered = threading.Event()
    release = threading.Event()

    def runner(_frames):
        entered.set()
        release.wait(timeout=1.0)
        return _scores(0.1, 0.4)

    classifier = SceneDomainClassifier(domain, lambda: runner, clock=lambda: 0.0)
    try:
        _start(domain, classifier, "old")
        classifier.offer(_packet("old", 1, 1.01))
        classifier.end_session("old")
        assert classifier._samples == []  # noqa: SLF001
        _start(domain, classifier, "old")
        _offer_five(classifier, "old")
        assert entered.wait(timeout=1.0)
        classifier.end_session("old")
        domain.end_session("old")
        domain.start_session("new")
        classifier.start_session("new")
        release.set()
        _offer_five(classifier, "new", 20)
        assert _wait_for(lambda: domain.get_snapshot().locked and domain.get_snapshot().source_session_id == "new")
        assert domain.get_snapshot().domain is DomainName.RETAIL_STORE
    finally:
        release.set()
        classifier.close()


def test_eof_before_five_and_runner_exception_do_not_lock_and_next_session_recovers() -> None:
    domain = DomainService()
    calls = 0

    def runner(_frames):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("fake scene failure")
        return _scores(0.4, 0.1)

    classifier = SceneDomainClassifier(domain, lambda: runner, clock=lambda: 0.0)
    try:
        _start(domain, classifier, "eof")
        classifier.offer(_packet("eof", 1, 1.01))
        classifier.end_session("eof")
        time.sleep(0.02)
        assert calls == 0
        _start(domain, classifier, "bad")
        _offer_five(classifier, "bad")
        assert _wait_for(lambda: classifier._last_error is not None)  # noqa: SLF001
        assert domain.get_snapshot().locked is False
        _start(domain, classifier, "good")
        _offer_five(classifier, "good", 10)
        assert _wait_for(lambda: domain.get_snapshot().locked)
    finally:
        classifier.close()


def test_close_is_idempotent_and_stops_worker() -> None:
    classifier = SceneDomainClassifier(DomainService(), lambda: lambda _frames: _scores(0.4, 0.1), clock=lambda: 0.0)
    classifier.close()
    classifier.close()
    assert not classifier._worker.is_alive()  # noqa: SLF001
