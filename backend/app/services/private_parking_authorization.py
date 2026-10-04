"""Authorization signal adapter for the private-parking trigger.

T1 has no gesture model: production code only gets this Protocol (the test fake
lives in the tests). A real source (e.g. a pose model) is a later task. The
trigger calls ``signals`` OUTSIDE its lock and re-validates every returned
signal against the session, episode, track and observation time when it applies
it under the lock. A signal is not proof of identity or ownership.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.models.private_parking import AuthorizationSignal


class AuthorizationSignalSource(Protocol):
    def signals(self, session_id: str) -> Sequence[AuthorizationSignal]:
        """Return (and consume) pending signals; may block; never called under a lock."""
        ...
