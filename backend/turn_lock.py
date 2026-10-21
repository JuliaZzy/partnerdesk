"""One in-flight turn per chat.

Different sessions run together. The same session waits, so each turn reads the
history the previous turn has already stored. The wait is an in-process lock,
not a database row lock: holding a transaction open while the model thinks
would pin a pooled connection for the whole reply.

One API process is what Compose runs. The lock does not cross processes.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.Lock:
    with _guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _locks[session_id] = lock
        return lock


@contextmanager
def hold_session(session_id: str) -> Iterator[None]:
    lock = _lock_for(session_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
