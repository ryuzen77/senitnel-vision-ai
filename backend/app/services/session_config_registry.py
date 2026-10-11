"""Lease-bound binding of an operator configuration to one analysis session instance.

``session_id`` names a session in results, but the reusing test factory shows it is not
an authority: every entry carries the reservation's ``lease`` (VideoService token).
``bind`` refuses while an entry for that ``session_id`` exists, and ``unbind`` with any
other lease is a counted no-op, so a late unbind of session A never removes the binding
of a session B that reuses A's id. Serializing bind, the sink starts (whose trigger
``start_session`` calls ``get``) and unbind is VideoService's job (``_lifecycle_lock``,
P1b); this class only keeps its own map consistent under its lock. Configurations are
not logged.
"""

from __future__ import annotations

import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class BindingConflict(RuntimeError):
    """An entry for this session id is still bound (refused, nothing changed)."""


class SessionConfigRegistry(Generic[T]):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, T]] = {}
        self._counts = {"bound": 0, "bind_refused": 0, "unbound": 0, "unbind_ignored": 0}

    def bind(self, session_id: str, lease: str, config: T) -> None:
        if not isinstance(session_id, str) or not session_id or not isinstance(lease, str) or not lease:
            raise ValueError("session id and lease are required")
        with self._lock:
            if session_id in self._entries:
                self._counts["bind_refused"] += 1
                raise BindingConflict("session id is still bound")
            self._entries[session_id] = (lease, config)
            self._counts["bound"] += 1

    def get(self, session_id: str) -> T | None:
        with self._lock:
            entry = self._entries.get(session_id)
            return None if entry is None else entry[1]

    def unbind(self, session_id: str, lease: str) -> bool:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None or entry[0] != lease:
                self._counts["unbind_ignored"] += 1
                return False
            del self._entries[session_id]
            self._counts["unbound"] += 1
            return True

    def count(self, name: str) -> int:
        with self._lock:
            return self._counts[name]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
