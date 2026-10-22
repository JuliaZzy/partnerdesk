"""聊天里的合同分析。把协议入库，然后引用已经存下的表。

`read` 填表，`ingest` 入库。回复不重算条款。
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

_FILED = {"filed": "已入库", "need_file": "还没有协议文件", "failed": "读取失败"}


def _prompt(text: str, filed: dict[str, Any]) -> str:
    """The chat model quotes this table. It does not recompute the filing."""
    return (
        f"{text}\n\n"
        "=== 合同入库（来自代码——引用这些数字，不要重算，也不要编造条款） ===\n"
        f"{json.dumps(filed, ensure_ascii=False)}\n"
        "若 status 是 need_file，请对方提供协议文件。若 failed，说明出了什么问题。"
        "若 filed，告诉他们入库了什么，包括被跳过的行。"
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
    yield status("contract-reply", "说明", "正在根据入库结果回复", state="running")
    done = yield from answer_directly(
        db, llm, session, text,
        prompt_text=_prompt(text, filed),
        extra={"contract_id": filed["contract_id"], "contract": filed["table"]},
    )
    yield status("contract-reply", "说明", "回复已写完", state="done")
    yield done
