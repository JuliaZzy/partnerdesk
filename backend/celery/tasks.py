"""Tasks the compose `worker` service runs.

Import `celery` from `backend.worker` and decorate with `@celery.task`.
The worker loads this module from `celery.conf.include` when it starts.
Enqueue from the API with `task.delay(...)` — that call returns before the task runs.
"""

from __future__ import annotations

from .worker import celery


@celery.task(name="partnerdesk.ping")
def ping() -> str:
    """Broker round-trip. The worker is up when this returns ``pong``."""
    return "pong"
