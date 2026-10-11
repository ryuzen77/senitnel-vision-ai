"""Lease-bound session configuration registry (plan §7.2, acceptance 6 registry part)."""

from __future__ import annotations

import threading

import pytest

from app.services.session_config_registry import BindingConflict, SessionConfigRegistry

WAIT = 5.0


def test_bind_get_unbind():
    registry = SessionConfigRegistry()
    registry.bind("sid", "lease-a", "config-a")
    assert registry.get("sid") == "config-a" and len(registry) == 1
    assert registry.unbind("sid", "lease-a") is True
    assert registry.get("sid") is None and len(registry) == 0


def test_bind_refuses_while_the_id_is_still_bound():
    registry = SessionConfigRegistry()
    registry.bind("sid", "lease-a", "config-a")
    with pytest.raises(BindingConflict):
        registry.bind("sid", "lease-b", "config-b")
    assert registry.get("sid") == "config-a" and registry.count("bind_refused") == 1


def test_late_unbind_of_a_does_not_remove_b_with_a_reused_session_id():
    registry = SessionConfigRegistry()
    registry.bind("same", "lease-a", "config-a")
    assert registry.unbind("same", "lease-a")
    registry.bind("same", "lease-b", "config-b")
    assert registry.unbind("same", "lease-a") is False                 # late unbind of A
    assert registry.get("same") == "config-b" and registry.count("unbind_ignored") == 1
    assert registry.unbind("unknown", "lease-a") is False and registry.count("unbind_ignored") == 2


@pytest.mark.parametrize("session_id, lease", [("", "lease"), ("sid", ""), (None, "lease"), ("sid", None)])
def test_bind_requires_identity(session_id, lease):
    with pytest.raises(ValueError):
        SessionConfigRegistry().bind(session_id, lease, "config")


def test_concurrent_binds_of_one_id_have_one_winner():
    registry = SessionConfigRegistry()
    barrier = threading.Barrier(8, timeout=WAIT)
    winners: list[str] = []
    lock = threading.Lock()

    def bind(lease: str) -> None:
        barrier.wait()
        try:
            registry.bind("sid", lease, lease)
        except BindingConflict:
            return
        with lock:
            winners.append(lease)

    threads = [threading.Thread(target=bind, args=(f"lease-{n}",), daemon=True) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
        assert not thread.is_alive()
    assert len(winners) == 1 and registry.get("sid") == winners[0]
    assert registry.count("bind_refused") == 7
