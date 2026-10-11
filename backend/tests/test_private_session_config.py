"""P1b: per-session operator configuration in the private trigger and the coordinator's
start refusal (plan §16.2 items 9-10, §16.3 acceptance 4). Fakes only."""

from __future__ import annotations

import logging

import pytest

from app.models.private_parking import build_operator_configuration, make_thresholds
from app.services.private_parking_coordinator import PrivateParkingCoordinator, SessionAdmissionError
from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler
from app.services.private_parking_trigger import PrivateParkingAdmissionError, PrivateParkingTrigger
from app.services.session_config_registry import SessionConfigRegistry


def operator_config(inside: str, **thresholds):
    return build_operator_configuration(
        frame_width=64, frame_height=48, line={"p1": [0.2, 0.5], "p2": [0.8, 0.5]}, inside=inside,
        zones=[{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}],
        thresholds=make_thresholds(**thresholds) if thresholds else None)


def test_exactly_one_configuration_source():
    with pytest.raises(ValueError):
        PrivateParkingTrigger()
    with pytest.raises(ValueError):
        PrivateParkingTrigger(operator_config("left").config, session_config=lambda _sid: None)
    with pytest.raises(ValueError):
        PrivateParkingTrigger(session_config="not callable")


def test_each_session_uses_its_own_bound_configuration():
    registry = SessionConfigRegistry()
    left, right = operator_config("left"), operator_config("right", approach_observations=4)
    registry.bind("a", "lease-a", left)
    registry.bind("b", "lease-b", right)
    trigger = PrivateParkingTrigger(session_config=registry.get)
    trigger.start_session("a")
    assert trigger._config is left.config and trigger._t.approach_observations == 3   # noqa: SLF001
    trigger.end_session("a")
    trigger.start_session("b")
    assert trigger._config is right.config and trigger._t.approach_observations == 4  # noqa: SLF001
    assert trigger._config.line.signed_distance(0.5, 0.8) > 0                         # noqa: SLF001


@pytest.mark.parametrize("lookup", [lambda _sid: None, lambda _sid: "not a config",
                                    lambda _sid: (_ for _ in ()).throw(RuntimeError("boom"))])
def test_a_session_without_configuration_is_refused_never_served(lookup, caplog):
    fallback = operator_config("left")
    calls = []

    def spy(session_id):
        calls.append(session_id)
        return lookup(session_id)

    trigger = PrivateParkingTrigger(session_config=spy)
    with pytest.raises(PrivateParkingAdmissionError) as info:
        trigger.start_session("secret-session")
    assert "secret-session" not in str(info.value) and calls == ["secret-session"]
    assert trigger.get_snapshot().source_session_id is None
    assert trigger._config is not fallback.config                                     # noqa: SLF001


def test_closed_trigger_does_not_consult_the_registry():
    calls = []
    trigger = PrivateParkingTrigger(session_config=lambda sid: calls.append(sid))
    trigger.close()
    trigger.start_session("a")
    assert calls == []


def _coordinator(trigger):
    return PrivateParkingCoordinator(trigger, EpisodeEvidenceSampler())


def test_coordinator_re_raises_a_trigger_start_refusal(caplog):
    trigger = PrivateParkingTrigger(session_config=lambda _sid: None)
    coordinator = _coordinator(trigger)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SessionAdmissionError) as info:
            coordinator.start_session("secret-session")
    assert "secret-session" not in str(info.value) and "secret-session" not in caplog.text
    assert trigger.get_snapshot().source_session_id is None


def test_coordinator_starts_a_bound_session():
    registry = SessionConfigRegistry()
    registry.bind("a", "lease-a", operator_config("right"))
    trigger = PrivateParkingTrigger(session_config=registry.get)
    coordinator = _coordinator(trigger)
    coordinator.start_session("a")
    assert trigger.get_snapshot().source_session_id == "a"
    coordinator.end_session("a")
    assert trigger.get_snapshot().source_session_id is None


def test_startup_file_composition_is_unchanged():
    trigger = PrivateParkingTrigger(operator_config("left").config)
    coordinator = _coordinator(trigger)
    coordinator.start_session("a")
    assert trigger.get_snapshot().source_session_id == "a"
