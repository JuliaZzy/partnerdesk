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
  contracts.py     合同抽取结果 → 全量入库（原始 JSON + 规范化条款）→ PO 规则、产品 MOQ、下一单的合同折扣
  reports.py       销售报表：整洁表格 → 事实（代码读格子，模型不碰数字）→ 草稿 → 人确认 → 同期重发即取代
  knowledge.py     品牌知识库：品牌资料 → 两遍蒸馏（扫描类型 → 抽片段）→ 草稿片段 → 人批准后才进 prompt
  memory.py        长期记忆：跨会话记住的偏好 / 指令 / 事实，每轮 PO 对话作为上下文读入（只能提醒，不能放宽规则）
  catalog.py       产品索引 + 给 prompt 的目录清单
  llm.py           唯一的模型接缝：complete_json / stream_text；测试用 FakeLLM
  db/              数据层：orm.py（表结构）、database.py（唯一入口）、migrations/（Alembic）、seed.py（fixtures 入库）
  service.py       业务操作，不依赖传输层：handle_message（事件流）/ confirm / orders
  app.py           service.py 的 HTTP 适配层（FastAPI）：SSE 聊天、确认、订单
  ui.py            Streamlit 入口 + 聊天页：底部 desk() 把聊天和 ui_pages/ 的页面挂到同一个导航上，纯 Python
  ui_pages/        其余页面：orders / contracts / reports / knowledge / memory；common.py 是共用的后端、合作关系、侧栏导航
fixtures/          品牌、经销商、合作关系、目录、规则、设置、一份合同抽取——首次运行时种进数据库
tests/             不联网的测试（FakeLLM；页面用 AppTest 无头驱动）
evals/             模型层回归，需要真实模型
docs/porting/      从第一版带过来了什么、为什么（01–06）
docs/              邮件渠道的面试备忘
legacy/            第一版的 Python 模块，原样保留，逐步移植出来
```

## 数据层

所有业务数据都进数据库，按真实的表建模（列、外键、索引），不再有 JSON blob 表：

- **引擎**：SQLAlchemy 2.0 + PostgreSQL，没有第二个引擎。`PARTNERDESK_DB_URL` 是必填的，
  首次用 `dev db` 建库。不保留 SQLite 回落是刻意的：双引擎意味着每个 schema 决策都要在两边都成立，
  原生类型（`timestamptz`、`jsonb`、数组、枚举）就一直用不上；而静默回落会让配置错误的部署
  在一个空库上正常启动，而不是当场失败。
- **schema 由 Alembic 版本化**（`partnerdesk/db/migrations`）。`Database()` 构造时自动升到 head，
  测试每个临时库也走同一套 migration；`tests/test_db.py` 会在模型和 migration 不一致时直接失败。
  改表：改 `db/orm.py` → `.venv\Scripts\alembic revision --autogenerate -m "..."` → 检查生成文件。
- **合同**：`contracts` 一行一份协议；`contract_extractions` 原样保存每次抽取的 JSON（审计 / 重跑）；
  折扣阶梯、MOQ、价格表、区域、原文证据各有自己的表；由合同派生的规则通过 `commercial_rules.contract_id` 追溯。
  下单时 `contracts.contract_discount_for_next_order` 按合同年内已下单数选档，预填到订单里
  （假设：一张已提交的 PO = 一柜）。经销商在对话里说的折扣覆盖预填；超过合同最高档走审批规则。
- **品牌知识库**：`brand_documents` 一行一份上传的资料，`brand_document_pages` 逐页文本（片段引用的依据），
  `brand_document_sections` 是第一遍扫描找到的内容类型，`knowledge_fragments` 是可复用的知识片段
  （开放的 type slug、可选 `product_id` 关联目录产品、`status` draft → approved → archived）。只有 approved 的片段会给模型看。
- **销售报表**：`sales_reports` 一行一份文件，`report_extractions` 记录每次读取（列映射、跳过了什么），
  `report_facts` 按原生粒度存每个数字（metric_key / 渠道 / SKU / `product_id` / 期间 / 来源单元格）。
  事实是 draft，`Database.confirm_report` 才变 confirmed；同一合作关系同一测量同一期间的旧事实自动 superseded——重发不会翻倍。
  `product_id` 让报表的销量能和 `purchase_order_items` 按产品对上（报表页已有「下单 vs 售出」）。
- **长期记忆**：`memories` 按合作关系存偏好 / 指令 / 事实 / 事件，`is_active` 控制是否读入；
  `po/turn.py::load_bundle` 每轮把 active 记忆追加到品牌 operating context 之后——只是上下文，进不了 code-check。

## 现状

| 能力 | 状态 |
|---|---|
| slot 模板 + merge 语义 | 已移植 — `po/slots.py` |
| 确定性校验（搜索 → LLM 兜底解析 → 定价 → MOQ → 规则 → 缺项） | 已移植 — `po/check.py`、`po/rules.py` |
| 抽取（LLM，结构化输出，支持图片） | 已移植 — `po/extract.py` |
| 草稿 + 摘要 + hash | 已移植 — `po/draft.py` |
| 流式回复 | 已移植 — `po/reply.py` |
| 一轮对话 + 确认门 + 提交 | 已移植 — `po/turn.py`、`po/submit.py`、`service.py` |
| 多页桌面：聊天 / 订单 / 合同 / 报表 / 品牌知识 / 记忆 | **新增** — `ui.py`（入口 + 聊天）、`ui_pages/`；其他程序走 `app.py` 的 HTTP API |
| 意图 router | 已移植 — `router.py` |
| 提交后的收货 / 付款 / 索赔 | 已移植 — `claims.py` |
| 合同全量入库 + 条款 → PO 规则 + 合同折扣预填 | **新增** — `contracts.py`、`db/` |
| 合同页：条款 / 派生规则 / 证据 / 抽取记录，导入抽取 JSON | **新增** — `ui_pages/contracts.py`；PDF 读取仍在 `legacy/contract_ai` |
| 报表：整洁表格 → 事实 → 确认 → 取代；报表页 + 图表 + 下单 vs 售出 | **新增** — `reports.py`、`ui_pages/reports.py`；多块乱表（06）待做 |
| 品牌知识库：上传 → 两遍蒸馏（文本层）→ 批准；知识页 | **新增** — `knowledge.py`、`ui_pages/knowledge.py`；页面图像送视觉模型待做 |
| 长期记忆：记忆页 + 每轮 PO 对话读入 | **新增** — `memory.py`、`ui_pages/memory.py` |
| 聊天里的合同 specialist | 占位 — 抽取在 `legacy/contract_ai`，还没接到聊天 |
| 聊天里的报表 specialist | 占位 — 设计和 prompt 在 `docs/porting/06-report-core.md` |
| 聊天里引用知识库 / 合同 / 报表数据 | 待做 — 数据已在库里并按产品关联，prompt 侧还没接 |
| 评估 | 脚手架 — `evals/` |

第一版还有一个邮件渠道（IMAP 轮询 → 分类 → 通过幂等 outbox 回信 → hash 绑定的确认链接）。这里不重建；
设计笔记在 [docs/email-channel-interview-notes.md](docs/email-channel-interview-notes.md)。

## 运行

第一次（只做一次）：

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[contract,dev]"
```

然后 `copy .env.example .env`，填两样东西：一个模型的 key（默认是交大校园 API，需校园网或 VPN；
也有 OpenAI / Ollama / GLM），以及本地 PostgreSQL 的连接串（`PARTNERDESK_DB_URL` 和
`PARTNERDESK_TEST_DB_URL`，后者跑测试要用）。最后建库：

```
dev db
```

之后不需要 activate，用 `dev.bat`：

```
dev            桌面（聊天 / 订单 / 合同 / 报表 / 品牌知识 / 记忆）   http://localhost:8501
dev api        HTTP API          http://127.0.0.1:8000/docs（可选）
dev db         建库，首次运行需要
dev test       全部测试，不联网（模型；数据库是真的）
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
