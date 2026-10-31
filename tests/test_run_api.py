"""The exercise Run API stores rows in PostgreSQL, not in process memory."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.db import InvalidRunTransition, utcnow
from backend.main import create_app

HEADERS = {"X-Org-Id": "org-1", "X-User-Id": "user-1"}
BODY = {
    "run_type": "po",
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

    claimed = db.claim_run(run_id, "worker-a", utcnow() + timedelta(seconds=10))
    assert claimed is not None
    paused = db.transition_run("org-1", run_id, "waiting_for_user", {})
    assert paused is not None
    assert paused["status"] == "waiting_for_user"
    assert paused["lease_owner"] is None

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
    assert [e["sequence"] for e in events] == [1, 2, 3, 4, 5, 6, 7]
    assert [e["event_type"] for e in events] == [
        "run.created",
        "run.started",
        "run.waiting_for_user",
        "run.resumed",
        "run.enqueued",
        "run.enqueued",
        "run.cancelled",
    ]

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

def test_forbidden_run_transitions_do_not_commit(make_db):
    db = make_db("transition")
    client = TestClient(create_app(db))

    def open_run(index: int) -> str:
        created = client.post(
            "/runs",
            json={**BODY, "idempotency_key": f"transition:{index}"},
            headers=HEADERS,
        )
        assert created.status_code == 201
        return created.json()["id"]

    def reject(run_id: str, to_status: str, source: str, events: list[str]) -> None:
        with pytest.raises(InvalidRunTransition) as caught:
            db.transition_run("org-1", run_id, to_status, {})
        assert (caught.value.from_status, caught.value.to_status) == (source, to_status)
        assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["status"] == source
        listed = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
        assert [event["event_type"] for event in listed] == events

    queued_id = open_run(1)
    reject(queued_id, "succeeded", "queued", ["run.created"])

    waiting_id = open_run(2)
    assert db.claim_run(waiting_id, "worker-a", utcnow() + timedelta(minutes=5)) is not None
    paused = db.transition_run("org-1", waiting_id, "waiting_for_user", {})
    assert paused is not None and paused["lease_owner"] is None
    reject(
        waiting_id,
        "succeeded",
        "waiting_for_user",
        ["run.created", "run.started", "run.waiting_for_user"],
    )

    succeeded_id = open_run(3)
    assert db.claim_run(succeeded_id, "worker-a", utcnow() + timedelta(minutes=5)) is not None
    assert db.complete_run(succeeded_id, {"answer": "ok"})["status"] == "succeeded"
    reject(
        succeeded_id,
        "running",
        "succeeded",
        ["run.created", "run.started", "run.completed"],
    )

    failed_id = open_run(4)
    assert db.claim_run(failed_id, "worker-a", utcnow() + timedelta(minutes=5)) is not None
    assert db.transition_run("org-1", failed_id, "failed", {"reason": "permanent"})["status"] == "failed"
    reject(
        failed_id,
        "succeeded",
        "failed",
        ["run.created", "run.started", "run.failed"],
    )

    cancelled_id = open_run(5)
    cancelled = client.post(f"/runs/{cancelled_id}/cancel", headers=HEADERS)
    assert cancelled.status_code == 200
    reject(cancelled_id, "running", "cancelled", ["run.created", "run.cancelled"])

def test_loop_runs_a_tool_then_stores_the_answer(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once

    def echo(args):
        return {"echo": args["text"]}

    tools = ToolRegistry([Tool(
        name="echo", description="Echo the text.", fn=echo,
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )])
    script = [
        AssistantTurn(
            content="先看一下", 
            tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
            prompt_tokens=100,
            completion_tokens=200,
        ),
        AssistantTurn(content="看到了 hi"),
    ]

    def decide(context, schemas):
        return script.pop(0)

    def answer(context):
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)
    assert work_once(db, "worker-a", execute_run(db, tools, decide=decide, answer=answer)) is True
    body = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert body["status"] == "succeeded"
    assert body["output_json"]["text"] == "看到了 hi"
    trace = client.get(f"/runs/{run_id}/trace", headers=HEADERS).json()
    assert [(step["step_type"], step["name"]) for step in trace["steps"]] == [("tool", "echo"), ("model", "model")]
    assert trace["checkpoints"][-1]["state_json"]["last"] == "assistant:reply"
    assert [call["tool_name"] for call in trace["tool_calls"]] == ["echo"]
    usage = db.list_usage_records(run_id)
    assert [
        (
            row["provider"], row["model"], row["prompt_tokens"], row["completion_tokens"],
            row["total_tokens"], row["estimated_cost_cents"],
        )
        for row in usage
    ] == [
        ("fake", "deepseek-flash", 100, 200, 300, 0),
        ("fake", "deepseek-flash", 0, 0, 0, 0),
    ]
    assert usage[0]["org_id"] == "org-1"
    assert usage[0]["user_id"] == "user-1"


def test_loop_waits_for_approval_and_resumes_the_same_tool(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once

    called: list[dict] = []

    def echo(args):
        called.append(dict(args))
        return {"echo": args["text"]}

    tools = ToolRegistry([Tool(
        name="echo", description="Echo the text.", fn=echo, requires_approval=True,
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )])
    script = [
        AssistantTurn(content="先等确认", tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]),
        AssistantTurn(content="确认后继续"),
    ]
    seen: list[list] = []

    def decide(context, schemas):
        seen.append(list(context))
        return script.pop(0)

    def answer(context):
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop-wait")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)
    execute = execute_run(db, tools, decide=decide, answer=answer)
    assert work_once(db, "worker-a", execute) is True
    paused = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert paused["status"] == "waiting_for_user"
    assert paused["lease_owner"] is None
    assert called == []
    assert db.claim_queued_job("worker-b") is None
    trace = client.get(f"/runs/{run_id}/trace", headers=HEADERS).json()
    assert trace["steps"][0]["status"] == "running"
    assert trace["checkpoints"][-1]["state_json"]["last"] == "assistant:tool_use:echo"
    assert trace["checkpoints"][-1]["state_json"]["open_step_id"] == trace["steps"][0]["id"]

    resumed = client.post(f"/runs/{run_id}/resume", json={"input": {"message": "同意"}}, headers=HEADERS)
    assert resumed.status_code == 200
    dispatch_pending(db)
    assert work_once(db, "worker-a", execute) is True
    assert called == [{"text": "hi"}]
    finished = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert finished["status"] == "succeeded"
    assert finished["output_json"]["text"] == "确认后继续"
    assert any(message.get("content") == "同意" for message in seen[-1])

def test_skip_resumes_without_running_the_tool(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once

    called: list[dict] = []

    def echo(args):
        called.append(dict(args))
        return {"echo": args["text"]}

    tools = ToolRegistry([Tool(
        name="echo", description="Echo the text.", fn=echo, requires_approval=True,
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )])
    script = [
        AssistantTurn(content="先等确认", tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]),
        AssistantTurn(content="已跳过"),
    ]

    def decide(context, schemas):
        del context, schemas
        return script.pop(0)

    def answer(context):
        del context
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop-skip")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)
    execute = execute_run(db, tools, decide=decide, answer=answer)
    assert work_once(db, "worker-a", execute) is True
    assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["status"] == "waiting_for_user"

    resumed = client.post(
        f"/runs/{run_id}/resume",
        json={"input": {"message": "跳过", "decision": "skip"}},
        headers=HEADERS,
    )
    assert resumed.status_code == 200
    assert work_once(db, "worker-a", execute) is True
    assert called == []
    finished = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert finished["status"] == "succeeded"
    assert finished["output_json"]["text"] == "已跳过"
    trace = client.get(f"/runs/{run_id}/trace", headers=HEADERS).json()
    assert trace["tool_calls"][0]["result_json"]["skipped"] is True

def test_an_approved_tool_replays_a_stored_result(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once

    called: list[dict] = []

    def echo(args):
        called.append(dict(args))
        return {"echo": args["text"]}

    tools = ToolRegistry([Tool(
        name="echo", description="Echo the text.", fn=echo, requires_approval=True,
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )])
    script = [
        AssistantTurn(content="先等确认", tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]),
        AssistantTurn(content="确认后继续"),
    ]

    def decide(context, schemas):
        return script.pop(0)

    def answer(context):
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop-replay")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)
    execute = execute_run(db, tools, decide=decide, answer=answer)
    assert work_once(db, "worker-a", execute) is True
    step_id = client.get(f"/runs/{run_id}/trace", headers=HEADERS).json()["steps"][0]["id"]
    claimed = db.claim_tool_call(
        run_id, step_id, tool_name="echo", args={"text": "hi"}, idempotency_key="echo:call_1",
    )
    assert claimed is not None and claimed[1] is True
    assert db.complete_tool_call(
        claimed[0]["id"], result={"echo": "stored"}, status="succeeded", latency_ms=5,
    ) is not None

    resumed = client.post(f"/runs/{run_id}/resume", json={"input": {"message": "同意"}}, headers=HEADERS)
    assert resumed.status_code == 200
    dispatch_pending(db)
    assert work_once(db, "worker-a", execute) is True
    assert called == []
    finished = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert finished["status"] == "succeeded"
    assert finished["output_json"]["text"] == "确认后继续"


def test_an_approved_tool_does_not_run_again_after_a_claim(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once

    called: list[dict] = []

    def echo(args):
        called.append(dict(args))
        return {"echo": args["text"]}

    tools = ToolRegistry([Tool(
        name="echo", description="Echo the text.", fn=echo, requires_approval=True,
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )])
    script = [
        AssistantTurn(content="先等确认", tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]),
        AssistantTurn(content="不会走到这里"),
    ]

    def decide(context, schemas):
        return script.pop(0)

    def answer(context):
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop-claimed")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    dispatch_pending(db)
    execute = execute_run(db, tools, decide=decide, answer=answer)
    assert work_once(db, "worker-a", execute) is True
    step_id = client.get(f"/runs/{run_id}/trace", headers=HEADERS).json()["steps"][0]["id"]
    claimed = db.claim_tool_call(
        run_id, step_id, tool_name="echo", args={"text": "hi"}, idempotency_key="echo:call_1",
    )
    assert claimed is not None and claimed[1] is True

    resumed = client.post(f"/runs/{run_id}/resume", json={"input": {"message": "同意"}}, headers=HEADERS)
    assert resumed.status_code == 200
    dispatch_pending(db)
    assert work_once(db, "worker-a", execute) is True
    assert called == []
    failed = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert failed["status"] == "failed"

def test_a_tool_sees_the_assistant_text_from_this_run(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once
    from backend.tools.lang_translate import resolve_text

    seen: list[dict] = []

    def grab(args, context=None):
        seen.append(context or {})
        return {"ok": True}

    tools = ToolRegistry([Tool(name="grab", description="Read the context.", fn=grab)])
    script = [
        AssistantTurn(
            content="库存还有 12 箱",
            tool_calls=[ToolCall(id="call_1", name="grab", arguments={"source": "last_assistant"})],
        ),
        AssistantTurn(content="记下来了"),
    ]

    def decide(context, schemas):
        return script.pop(0)

    def answer(context):
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop-context")
    client = TestClient(create_app(db))
    client.post("/runs", json=BODY, headers=HEADERS)
    dispatch_pending(db)
    assert work_once(db, "worker-a", execute_run(db, tools, decide=decide, answer=answer)) is True
    assert resolve_text({"source": "last_assistant"}, seen[0]) == "库存还有 12 箱"

def test_translate_reads_the_report_stored_on_the_checkpoint(make_db):
    from backend.llm.types import AssistantTurn, ToolCall
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_loop import Tool, ToolRegistry, execute_run
    from backend.services.run_worker import work_once
    from backend.tools.lang_translate import resolve_text

    seen: list[dict] = []

    def grab(args, context=None):
        seen.append(context or {})
        return {"ok": True}

    tools = ToolRegistry([Tool(name="grab", description="Read the context.", fn=grab)])
    script = [
        AssistantTurn(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="grab", arguments={"source": "report"})],
        ),
        AssistantTurn(content="译好了"),
    ]

    def decide(context, schemas):
        return script.pop(0)

    def answer(context):
        raise AssertionError("answer must not run")

    decide.provider, decide.model = "fake", "deepseek-flash"
    answer.provider, answer.model = "fake", "deepseek-chat"
    db = make_db("loop-report")
    client = TestClient(create_app(db))
    created = client.post(
        "/runs",
        json={
            **BODY,
            "idempotency_key": "report:translate",
            "input": {**BODY["input"], "last_report": "本月销量持平。"},
        },
        headers=HEADERS,
    )
    run_id = created.json()["id"]
    dispatch_pending(db)
    assert work_once(db, "worker-a", execute_run(db, tools, decide=decide, answer=answer)) is True
    assert resolve_text({"source": "report"}, seen[0]) == "本月销量持平。"
    trace = client.get(f"/runs/{run_id}/trace", headers=HEADERS).json()
    assert trace["checkpoints"][-1]["state_json"]["last_report"] == "本月销量持平。"

def test_bind_uses_the_injected_client(monkeypatch):
    import json
    from types import SimpleNamespace

    from backend.llm.types import ToolCall
    from backend.orchestrator.prompt import ANSWER, REACT
    from backend.services.run_models import bind

    seen: dict[str, dict] = {}

    def _completion(content: str | None, tool_calls: list[ToolCall]):
        message = SimpleNamespace(
            content=content,
            tool_calls=[
                SimpleNamespace(
                    id=call.id,
                    function=SimpleNamespace(name=call.name, arguments=json.dumps(call.arguments)),
                )
                for call in tool_calls
            ],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class Client:
        provider = "openai"
        model = "gpt-4.1"

        def chat_completion(self, *, model, messages, tools=None, tool_choice=None):
            system = messages[0]["content"]
            body = messages[1:]
            if tools:
                seen["decide"] = {"system": system, "messages": body, "tools": tools, "model": model}
                self.last_usage = {"prompt_tokens": 8, "completion_tokens": 2}
                return _completion(None, [ToolCall(id="c1", name="echo", arguments={"text": "hi"})])
            seen["answer"] = {"system": system, "messages": body, "model": model}
            self.last_usage = {"prompt_tokens": 1, "completion_tokens": 3}
            return _completion("好的", [])

    context = [
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "先查"},
            {"type": "tool_use", "id": "call_1", "name": "echo", "input": {"text": "hi"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": {"echo": "hi"}},
        ]},
    ]
    schemas = [{"type": "function", "function": {"name": "echo"}}]
    decide, answer = bind(Client())
    turn = decide(context, schemas)
    assert turn.tool_calls[0].name == "echo"
    assert turn.prompt_tokens == 8
    assert turn.completion_tokens == 2
    assert seen["decide"]["system"] == REACT
    assert seen["decide"]["tools"] is schemas
    assert seen["decide"]["messages"][1]["tool_calls"][0]["function"] == {
        "name": "echo",
        "arguments": json.dumps({"text": "hi"}, ensure_ascii=False),
    }
    assert decide.provider == "openai"
    assert decide.model == "gpt-4.1"

    assert answer(context) == "好的"
    assert answer.prompt_tokens == 1
    assert answer.completion_tokens == 3
    assert seen["answer"]["system"] == ANSWER
    assert seen["answer"]["messages"] == [
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": "先查"},
        {"role": "user", "content": json.dumps({"echo": "hi"}, ensure_ascii=False)},
    ]
    assert answer.provider == "openai"
    assert answer.model == "gpt-4.1"

    qwen = Client()
    qwen.provider, qwen.model = "sjtu", "qwen"
    _, qwen_answer = bind(qwen, answer_system="用中文回答")
    assert qwen_answer.model == "qwen"
    assert qwen_answer([]) == "好的"
    assert seen["answer"]["system"] == "用中文回答"

def test_create_run_accepts_only_the_five_types(make_db):
    db = make_db("run-types")
    client = TestClient(create_app(db))
    for run_type in ("po", "contract", "report", "data", "code", "brand", "fix", "reflect", "review", "revise"):
        created = client.post(
            "/runs",
            json={**BODY, "run_type": run_type, "idempotency_key": f"{run_type}:once"},
            headers=HEADERS,
        )
        assert created.status_code == 201
        assert created.json()["run_type"] == run_type

    rejected = client.post(
        "/runs",
        json={**BODY, "run_type": "purchase_order", "idempotency_key": "old"},
        headers=HEADERS,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INVALID_RUN_TYPE"

def test_an_expired_lease_is_swept_without_spending_a_retry(make_db):
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_worker import sweep_expired_leases

    db = make_db("lease-sweep")
    client = TestClient(create_app(db))

    def claim(key: str, lease_for: timedelta) -> tuple[str, str]:
        created = client.post(
            "/runs",
            json={**BODY, "idempotency_key": key},
            headers=HEADERS,
        )
        run_id = created.json()["id"]
        dispatch_pending(db)
        job = db.claim_queued_job("worker-a")
        assert job is not None
        assert db.claim_run(run_id, "worker-a", utcnow() + lease_for) is not None
        return run_id, job["id"]

    expired_id, expired_job = claim("lease:expired", timedelta(seconds=-5))
    live_id, _live_job = claim("lease:live", timedelta(minutes=5))
    assert sweep_expired_leases(db) == 1

    released = client.get(f"/runs/{expired_id}", headers=HEADERS).json()
    assert released["status"] == "queued"
    assert released["lease_owner"] is None
    assert released["attempts"] == 1
    events = client.get(f"/runs/{expired_id}/events", headers=HEADERS).json()
    assert events[-1]["event_type"] == "run.retry_scheduled"
    assert events[-1]["payload_json"]["reason"] == "lease_expired"
    again = db.claim_queued_job("worker-a")
    assert again is not None and again["id"] == expired_job
    assert again["attempts"] == 0
    assert again["last_error_json"]["kind"] == "lease_expired"

    assert client.get(f"/runs/{live_id}", headers=HEADERS).json()["status"] == "running"
    assert sweep_expired_leases(db) == 0

    acked_id, acked_job = claim("lease:acked", timedelta(seconds=-5))
    assert db.ack_job(acked_job) is not None
    assert sweep_expired_leases(db) == 1
    assert client.get(f"/runs/{acked_id}", headers=HEADERS).json()["status"] == "queued"
    assert dispatch_pending(db) == 1
    fresh = db.claim_queued_job("worker-b")
    assert fresh is not None and fresh["id"] != acked_job
    assert fresh["payload_json"] == {"run_id": acked_id}


def test_an_expired_lease_cancels_a_run_that_already_asked(make_db):
    from backend.services.dispatcher import dispatch_pending
    from backend.services.run_worker import sweep_expired_leases

    db = make_db("lease-cancel")
    client = TestClient(create_app(db))
    run_id = client.post(
        "/runs", json={**BODY, "idempotency_key": "lease:cancel"}, headers=HEADERS,
    ).json()["id"]
    dispatch_pending(db)
    job = db.claim_queued_job("worker-a")
    assert job is not None
    assert db.claim_run(run_id, "worker-a", utcnow() - timedelta(seconds=5)) is not None
    cancelled = client.post(f"/runs/{run_id}/cancel", headers=HEADERS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "running"

    assert sweep_expired_leases(db) == 1
    body = client.get(f"/runs/{run_id}", headers=HEADERS).json()
    assert body["status"] == "cancelled"
    assert body["lease_owner"] is None
    assert db.claim_queued_job("worker-a") is None
    events = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
    assert events[-1]["event_type"] == "run.cancelled"
    assert events[-1]["payload_json"]["reason"] == "lease_expired"


def test_rerun_starts_a_new_run_from_the_parent(make_db):
    db = make_db("rerun")
    client = TestClient(create_app(db))
    parent = client.post("/runs", json=BODY, headers=HEADERS)
    assert parent.status_code == 201
    parent_id = parent.json()["id"]

    created = client.post(
        f"/runs/{parent_id}/rerun",
        json={"idempotency_key": "po:rerun:1"},
        headers=HEADERS,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] != parent_id
    assert body["parent_run_id"] == parent_id
    assert body["status"] == "queued"
    assert body["run_type"] == "po"
    assert body["input_json"] == BODY["input"]
    assert client.get(f"/runs/{parent_id}", headers=HEADERS).json()["status"] == "queued"
    events = client.get(f"/runs/{body['id']}/events", headers=HEADERS).json()
    assert events[0]["payload_json"]["parent_run_id"] == parent_id

    replay = client.post(
        f"/runs/{parent_id}/rerun",
        json={"idempotency_key": "po:rerun:1"},
        headers=HEADERS,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]

    overridden = client.post(
        f"/runs/{parent_id}/rerun",
        json={"idempotency_key": "po:rerun:2", "input": {"message": "改一箱"}},
        headers=HEADERS,
    )
    assert overridden.status_code == 201
    assert overridden.json()["input_json"] == {"message": "改一箱"}
    assert overridden.json()["run_type"] == "po"
    assert overridden.json()["parent_run_id"] == parent_id

    missing = client.post(
        "/runs/run_missing/rerun",
        json={"idempotency_key": "po:rerun:missing"},
        headers=HEADERS,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RUN_NOT_FOUND"

    hidden = client.post(
        f"/runs/{parent_id}/rerun",
        json={"idempotency_key": "po:rerun:other"},
        headers={"X-Org-Id": "org-2", "X-User-Id": "user-2"},
    )
    assert hidden.status_code == 404

def test_an_outbox_past_max_attempts_is_dead_lettered(make_db):
    db = make_db("outbox-dead")
    client = TestClient(create_app(db))
    run_id = client.post("/runs", json=BODY, headers=HEADERS).json()["id"]
    pending = db.find_pending_outbox_jobs()
    assert len(pending) == 1
    outbox_id = pending[0]["id"]
    assert pending[0]["max_attempts"] == 10

    for _ in range(10):
        updated = db.mark_outbox_retry(outbox_id, "publish failed")
        assert updated is not None
        assert updated["status"] == "pending"

    dead = db.mark_outbox_retry(outbox_id, "publish failed")
    assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["status"] == "failed"
    assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["attempts"] == 1
    events = client.get(f"/runs/{run_id}/events", headers=HEADERS).json()
    assert [event["event_type"] for event in events] == ["run.created", "run.started", "run.failed"]
    assert events[-1]["payload_json"]["reason"] == "dead_letter"

    letters = db.list_dead_letter_jobs(outbox_id)
    assert len(letters) == 1
    assert letters[0]["job_type"] == "execute_run"
    assert letters[0]["payload_json"] == {"run_id": run_id}
    assert letters[0]["attempts"] == 11
    assert letters[0]["error_json"] == {"kind": "dead_letter", "message": "publish failed"}
    assert db.mark_outbox_retry(outbox_id, "publish failed") is None

def test_tools_register_native_and_domain(db):
    from backend.llm import FakeLLM
    from backend.service import new_session
    from backend.services.session_tools import tools
    from backend.tools import web_search

    db.upsert_user("u-a", "dist-1", "Ada")
    session = new_session(db, "ps-1", "u-a")
    registry = tools(db, FakeLLM(), session, "hello")
    names = [schema["function"]["name"] for schema in registry.schemas()]
    assert names == [
        "web_search", "translate", "exchange_rate", "send_email",
        "po.draft", "contract.read", "report.draft", "data.query", "brand.search",
    ]
    assert registry.get("send_email").requires_approval is True
    assert registry.get("web_search").requires_approval is False
    assert registry.get("po.draft").requires_approval is False
    assert registry.get("web_search").fn is web_search.run


def test_mcp_tools_join_when_enabled_and_wait_for_approval(db, monkeypatch):
    from backend.llm import FakeLLM
    from backend.service import new_session
    from backend.services import session_tools
    from backend.tools import web_search

    class Hub:
        schemas = [
            {"type": "function", "function": {
                "name": "socialcrawl_request",
                "description": "Call SocialCrawl",
                "parameters": {"type": "object", "properties": {}},
            }},
            {"type": "function", "function": {
                "name": "web_search",
                "description": "should not replace the native tool",
                "parameters": {"type": "object", "properties": {}},
            }},
            {"type": "function", "function": {
                "name": "get-user",
                "description": "hyphenated names are skipped",
                "parameters": {"type": "object", "properties": {}},
            }},
        ]

    monkeypatch.setattr(session_tools, "mcp_enabled", lambda: True)
    monkeypatch.setattr(session_tools, "get_hub", lambda: Hub())
    monkeypatch.setattr(session_tools, "make_mcp_tool_fn", lambda hub, name: (lambda arguments, **kwargs: {"ok": True, "name": name}))
    db.upsert_user("u-a", "dist-1", "Ada")
    session = new_session(db, "ps-1", "u-a")
    registry = session_tools.tools(db, FakeLLM(), session, "hello")
    names = [schema["function"]["name"] for schema in registry.schemas()]
    assert names == [
        "web_search", "translate", "exchange_rate", "send_email",
        "po.draft", "contract.read", "report.draft", "data.query", "brand.search",
        "socialcrawl_request",
    ]
    assert registry.get("web_search").fn is web_search.run
    assert registry.get("socialcrawl_request").requires_approval is True