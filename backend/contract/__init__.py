"""协议归档，放在一个包里。

`read` 填抽取表。`chunks` 按标题把协议切开。`ingest` 存下每一条条款，并在代码里推导采购单规则。
`turn` 从聊天里归档，并把存下的结果作为一张表返回；编排器负责说话。
模型不决定一条条款是否适用。
"""

from __future__ import annotations

from .chunks import split_chunks
from .ingest import contract_discount_for_next_order, ingest_extraction, record_from_extraction
from .read import read_agreement, read_and_ingest, sanitize_extraction, summarize
from .turn import file_contract

__all__ = [
    "contract_discount_for_next_order",
    "ingest_extraction",
    "read_agreement",
    "read_and_ingest",
    "record_from_extraction",
    "file_contract",
    "sanitize_extraction",
    "split_chunks",
    "summarize",
]
