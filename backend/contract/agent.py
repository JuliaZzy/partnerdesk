"""聊天里的合同分析。读出条款，等用户确认后才入库。

`read` 填表。回复不重算条款，也不要说已经写入。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.speak import answer_directly
from ..orchestrator.status import status
from .turn import file_contract

_FILED = {"ready": "条款已抽出，等确认", "need_file": "还没有协议文件", "failed": "读取失败"}


def _prompt(text: str, filed: dict[str, Any]) -> str:
    """The chat model quotes this table. It does not recompute the filing."""
    shown = {key: value for key, value in filed.items() if key != "pending"}
    return (
        f"{text}\n\n"
        "=== 合同解析（来自代码——引用这些数字，不要重算，也不要编造条款） ===\n"
        f"{json.dumps(shown, ensure_ascii=False)}\n"
        "若 status 是 need_file，请对方提供协议文件。若 failed，说明出了什么问题。"
        "若 ready，条款还在右侧浮窗里，还没写入数据库。请对方核对、修改，并点确认后才会入库。"
        "不要说已经入库。被跳过的行也要提到。"
    )


def run(
    db: Database, llm: LLM, session: dict[str, Any], text: str, documents: list[tuple[str, bytes]],
) -> Iterator[dict[str, Any]]:
    db.update_session(session["id"], specialist="contract")
    yield status("contract-read", "读合同", "正在抽取条款", state="running")
    filed = file_contract(db, llm, session, documents)
    yield status("contract-read", "读合同", _FILED.get(filed["status"], filed["status"]), state="done")
    yield {
        "type": "contract",
        "status": filed["status"],
        "contract_id": filed["contract_id"],
        "table": filed["table"],
    }
    yield status("contract-reply", "说明", "正在根据解析结果回复", state="running")
    extra: dict[str, Any] = {}
    if filed["table"]:
        extra["contract"] = filed["table"]
    if filed.get("pending"):
        extra["contract_pending"] = filed["pending"]
    done = yield from answer_directly(
        db, llm, session, text,
        prompt_text=_prompt(text, filed),
        extra=extra,
        done_step=("contract-reply", "说明", "回复已写完"),
    )
    yield done
