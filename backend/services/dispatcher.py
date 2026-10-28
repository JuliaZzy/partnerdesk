"""Move due outbox rows onto `job_queue` table.

Publish hasspens in its own transaction. Marking the outbox row and appending `run.enqueued`
happen in the next one, so a crash between them leaves the outbox pending. 
"""

from __future__ import annotations

import time 

from ..db import Database
from ..repositories.run_repository import enqueue_job, find_pending_outbox_jobs, mark_outbox_dispatched, mark_outbox_retry


def dispatch_pending(db: Database, limit: int = 100) -> int:
    """Publish one batch. Returens how many outbox rows were marked dispatched."""
    delivered = 0
    for job in find_pending_outbox_jobs(db, limit):
        try:
            enqueue_job(db, job["job_type"], job["payload_json"])
        except Exception as e:
            mark_outbox_retry(db, job["id"], str(e))
            continue
        if mark_outbox_dispatched(db, job["id"]) is not None:
            delivered += 1
    return delivered

def serve_dispatcher(db: Database, limit: int = 100) -> None:
    """Keep scanning. Do not call this from `create_app` or from tests."""
    while True:
        dispatch_pending(db, limit)
        time.sleep(0.5)