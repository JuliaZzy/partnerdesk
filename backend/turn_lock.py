"""One in-flight turn per chat.

Different sessions run together. The same session waits, so each turn reads the
history the previous turn has already stored. The wait is a Redis lock, not a
database row lock: holding a transaction open while the model thinks would pin
a pooled connection for the whole reply. A process-local lock would not stop a
second API process from confirming the same chat.
"""

from __future__ import annotations

from .redis_store import hold_session

__all__ = ["hold_session"]
