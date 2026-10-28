"""The exercise Run API stores rows in PostgreSQL, not in process memory."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app

HEADERS = {"X-Org-Id": "org-1", "X-User-Id": "user-1"}
BODY = {
    "run_type": "purchase_order",
    "idempotency_key": "po:partnership_01:draft:v1",
    "input": {"partnership_id": "partnership_01", "message": "帮我下两托盘纯净水"},
}


def test_create_run_commits_the_event_and_hides_other_orgs(make_db):
    db = make_db("runs")
    client = TestClient(create_app(db))

    created = client.post("/runs", json=BODY, headers=HEADERS)
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    assert created.json()["input_json"]["partnership_id"] == "partnership_01"
    assert created.json()["started_at"] is None
    assert created.json()["updated_at"] == created.json()["created_at"]

    replay = client.post("/runs", json=BODY, headers=HEADERS)
    assert replay.status_code == 200
    assert replay.json()["id"] == run_id

    events = client.get(f"/runs/{run_id}/events", headers=HEADERS)
    assert events.status_code == 200
    assert [(e["sequence"], e["event_type"]) for e in events.json()] == [(1, "run.created")]

    hidden = client.get(f"/runs/{run_id}", headers={"X-Org-Id": "org-2", "X-User-Id": "user-1"})
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "RUN_NOT_FOUND"

    # A new connection to the same database still sees the row.
    again = TestClient(create_app(make_db("runs")))
    assert again.get(f"/runs/{run_id}", headers=HEADERS).status_code == 200

    trace = client.get(f"/runs/{run_id}/trace", headers=HEADERS)
    assert trace.status_code == 200
    body = trace.json()
    assert body["steps"] == []
    assert body["model_calls"] == []
    assert body["tool_calls"] == []
    assert body["checkpoints"] == []
    assert [e["sequence"] for e in body["events"]] == [1]


def test_cancel_and_resume_append_the_next_sequence(make_db):
    db = make_db("run-status")
    client = TestClient(create_app(db))
    created = client.post("/runs", json=BODY, headers=HEADERS).json()
    run_id = created["id"]

    too_early = client.post(f"/runs/{run_id}/resume", headers=HEADERS)
    assert too_early.status_code == 409
    assert too_early.json()["error"]["code"] == "RUN_NOT_WAITING_FOR_USER"

    db.update_run_status("org-1", run_id, "waiting_for_user")
    db.append_run_event("org-1", run_id, "run.waiting", {})
    resumed = client.post(f"/runs/{run_id}/resume", json={"input": {"message": "继续"}}, headers=HEADERS)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    cancelled = client.post(f"/runs/{run_id}/cancel", json={"reason": "客户改量"}, headers=HEADERS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["ended_at"] is not None
    assert cancelled.json()["updated_at"] >= created["updated_at"]

    again = client.post(f"/runs/{run_id}/cancel", headers=HEADERS)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "RUN_ALREADY_TERMINAL"

    events = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
    assert [e["sequence"] for e in events] == [1, 2, 3, 4]
    assert [e["event_type"] for e in events] == ["run.created", "run.waiting", "run.resumed", "run.cancelled"]

def test_create_run_writes_one_outbox_row_and_dispatch_enqueues_it(make_db):
    from backend.services.dispatcher import dispatch_pending

    db = make_db("outbox")
    client = TestClient(create_app(db))
    created = client.post("/runs", json=BODY, headers=HEADERS)
    assert created.status_code == 201
    run_id = created.json()["id"]

    replay = client.post("/runs", json=BODY, headers=HEADERS)
    assert replay.status_code == 200
    pending = db.find_pending_outbox_jobs()
    assert len(pending) == 1
    assert pending[0]["job_type"] == "execute_run"
    assert pending[0]["payload_json"] == {"run_id": run_id}

    assert dispatch_pending(db) == 1
    assert db.find_pending_outbox_jobs() == []
    events = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
    assert [e["event_type"] for e in events] == ["run.created", "run.enqueued"]

    job = db.claim_queued_job("worker-a")
    assert job is not None
    assert job["payload_json"] == {"run_id": run_id}
    assert job["status"] == "running"

def test_permanent_error_fails_the_run_without_a_retry(make_db):
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_worker import PermanentRunError, work_once

    db = make_db("permanent")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)

    def execute(_run):
        raise PermanentRunError("参数不合法")

    assert work_once(db, "worker-a", execute) is True
    assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["status"] == "failed"
    events = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
    assert events[-1]["event_type"] == "run.failed"
    assert events[-1]["payload_json"]["reason"] == "permanent"
    assert db.claim_queued_job("worker-a") is None


def test_transient_error_retries_then_dead_letters(make_db):
    from datetime import timedelta

    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_worker import work_once

    db = make_db("retry")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)
    immediate = (timedelta(0), timedelta(0), timedelta(0))

    def execute(_run):
        raise TimeoutError("订单系统超时")

    for _ in range(3):
        assert work_once(db, "worker-a", execute, immediate) is True
        body = client.get(f"/runs/{run_id}", headers=HEADERS).json()
        assert body["status"] == "queued"
    assert work_once(db, "worker-a", execute, immediate) is True
    failed = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert failed["status"] == "failed"
    events = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
    assert [e["event_type"] for e in events if e["event_type"] == "run.retry_scheduled"] == [
        "run.retry_scheduled", "run.retry_scheduled", "run.retry_scheduled",
    ]
    assert events[-1]["payload_json"]["reason"] == "dead_letter"