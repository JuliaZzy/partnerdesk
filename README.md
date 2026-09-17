# partnerdesk

面向品牌经销商的**对话式运营 agent**。一个聊天入口，agent 判断经销商在做什么，然后替一个初级客户经理把活干了——
每一步动作都有确定性校验和人工确认把关：

- **路由**：每条消息（和附件）先判断意图，有对应 specialist 的交给 specialist，没有的明确弃权、留给人。
- **采购订单（PO）**：通过对话把订单建起来。模型只负责把消息抽取进一个固定的 slot 模板；产品解析、定价、
  MOQ、品牌商业规则全部由确定性代码完成；用户不点确认，什么都不会提交。提交之后，同一个聊天继续处理
  收货、付款、破损索赔。
- **合同**：抽取管辖条款（区域、独家、MOQ、价格表、折扣阶梯），带原文证据位置，并映射成 PO 的规则。
- **报表**：把多块混排的销售表变成结构化事实，再只根据已验证的数字生成叙述。

从第一版沿用下来的设计原则（也是它为什么这么建的原因）：

1. **模型抽取，代码决定。** 每个数字、价格、SKU、规则结果都来自确定性代码。模型不做校验，也不选择提交路径。
2. **确认是一个动作，不是一个推断。** 提交需要按钮或 hash 绑定的链接。模型的"用户好像同意了"在小模型上实测是错的
   （"thanks" → 已确认），所以永远不采信。
3. **副作用幂等。** 提交以 confirm token 为键，token 在建单**之前**烧掉——双击、重试只产生一张订单。
4. **最小充分 agency。** 一个 router、几个有界的 specialist、确定性校验——不是 swarm。

## 目录结构

```
partnerdesk/
  router.py        意图分类 → po / contract / report / 弃权
  po/              slots → 抽取（LLM）→ 校验（代码）→ 草稿 + hash → 回复（LLM）→ 确认门 → 提交
  claims.py        提交之后：收货 / 付款 / 破损，落到订单状态上
  contracts.py     合同抽取结果 → PO 规则 + 产品 MOQ    （python -m partnerdesk.contracts）
  catalog.py       产品索引 + 给 prompt 的目录清单
  llm.py           唯一的模型接缝：complete_json / stream_text；测试用 FakeLLM
  db.py            SQLite：参考数据、会话、订单、规则快照、事件
  service.py       业务操作，不依赖传输层：handle_message（事件流）/ confirm / orders
  app.py           service.py 的 HTTP 适配层（FastAPI）：SSE 聊天、确认、订单
  ui.py            service.py 的页面适配层（Streamlit）：聊天页——纯 Python，无 JavaScript
fixtures/          目录、规则、合作关系、设置——首次运行时种进 SQLite
tests/             79 个测试，不联网（FakeLLM；页面用 AppTest 无头驱动）
evals/             模型层回归，需要真实模型
docs/porting/      从第一版带过来了什么、为什么（01–06）
docs/              邮件渠道的面试备忘
legacy/            第一版的 Python 模块，原样保留，逐步移植出来
```

## 现状

| 能力 | 状态 |
|---|---|
| slot 模板 + merge 语义 | 已移植 — `po/slots.py` |
| 确定性校验（搜索 → LLM 兜底解析 → 定价 → MOQ → 规则 → 缺项） | 已移植 — `po/check.py`、`po/rules.py` |
| 抽取（LLM，结构化输出，支持图片） | 已移植 — `po/extract.py` |
| 草稿 + 摘要 + hash | 已移植 — `po/draft.py` |
| 流式回复 | 已移植 — `po/reply.py` |
| 一轮对话 + 确认门 + 提交 | 已移植 — `po/turn.py`、`po/submit.py`、`service.py` |
| 聊天页 | `ui.py`（Streamlit）；其他程序走 `app.py` 的 HTTP API |
| 意图 router | 已移植 — `router.py` |
| 提交后的收货 / 付款 / 索赔 | 已移植 — `claims.py` |
| 合同条款 → PO 规则 | **新增** — `contracts.py` |
| 聊天里的合同 specialist | 占位 — 抽取在 `legacy/contract_ai`，还没接到聊天 |
| 聊天里的报表 specialist | 占位 — 设计和 prompt 在 `docs/porting/06-report-core.md` |
| 评估 | 脚手架 — `evals/` |

第一版还有一个邮件渠道（IMAP 轮询 → 分类 → 通过幂等 outbox 回信 → hash 绑定的确认链接）。这里不重建；
设计笔记在 [docs/email-channel-interview-notes.md](docs/email-channel-interview-notes.md)。

## 运行

第一次（只做一次）：

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[contract,dev]"
```

然后在 `.env` 里填一个模型的 key（文件里有 OpenAI / Ollama / GLM 三个选项）。之后不需要 activate，用 `dev.bat`：

```
dev            聊天页            http://localhost:8501
dev api        HTTP API          http://127.0.0.1:8000/docs（可选）
dev test       79 个测试，不联网
dev lint       ruff
dev eval       评估（需要真实模型）
dev py -m partnerdesk.contracts extraction.json --brand brand-aurora --partnership ps-aurora-nordic --apply
```

（VS Code 打开这个目录会自动选中 `.venv`，新终端里直接 `python` 就是它。）

## 可选依赖

| extra | 内容 |
|---|---|
| `contract` | `legacy/contract_ai`：PDF/DOCX 读取、GLM OCR |
| `textract` | `legacy/contract_ai`：AWS Textract OCR（`CONTRACT_OCR_PROVIDER=textract` 时才需要） |
| `brand` | `legacy/brand_knowledge`：Gemini 蒸馏品牌资料 |
| `dev` | pytest、httpx、ruff |
