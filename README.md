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
4. **最小充分 agency。** 一个 orchestrator、几个有界的 specialist、确定性校验——不是 swarm。

## 目录结构

```
partnerdesk/
  prompts/         每个 agent 一个 py（po.py、contract.py…），常量 + registry.json；填槽用 prompts.fill
  mcp/             SocialCrawl MCP（多平台社交/电商数据）：servers.json + runtime → 并入 tools=
  orchestrator.py  聊天入口：先 native tools=，再意图分类 → po / contract / report / 弃权
  po/              slots → 抽取（LLM）→ 校验（代码）→ 草稿 + hash → 回复（LLM）→ 确认门 → 提交
  claims.py        提交之后：收货 / 付款 / 破损，落到订单状态上
  contract_read.py 协议文件（PDF / Word / 文本）→ 模型填条款表 → contracts.ingest
  contracts.py     合同抽取结果 → 全量入库（原始 JSON + 规范化条款）→ PO 规则、产品 MOQ、下一单的合同折扣
  documents.py     PDF / Word / 文本 → 逐页文本；PDF 页渲染成 JPEG，给视觉模型看
  reports.py       销售报表：整洁表格 → 事实（代码读格子，模型不碰数字）→ 草稿 → 人确认 → 同期重发即取代
  knowledge.py     品牌知识库：品牌资料 → 两遍蒸馏（扫描类型 → 抽片段）→ 草稿片段 → 人批准后才进 prompt
  memory.py        长期记忆：跨会话记住的偏好 / 指令 / 事实，每轮 PO 对话作为上下文读入（只能提醒，不能放宽规则）
  catalog.py       产品索引 + 给 prompt 的目录清单
  llm.py           唯一的模型接缝：complete_json / stream_text / complete（tools=）；测试用 FakeLLM
  db/              数据层：orm.py（表结构）、database.py（唯一入口）、migrations/（Alembic）、seed.py（fixtures 入库）
  service.py       业务操作，不依赖传输层：handle_message（转发 orchestrator）/ confirm / orders
  app.py           service.py 的 HTTP 适配层（FastAPI）：SSE 聊天、确认、订单
  desk_api.py      桌面其余页面的 HTTP：产品目录、合同、报表、品牌知识、记忆
web/               桌面（Vite + React + TypeScript）：聊天 / 产品 / 订单 / 合同 / 报表 / 品牌知识 / 记忆，只调上面的 HTTP
fixtures/          演示用的品牌、经销商、合作关系、规则、设置、一份合同抽取——每次启动时 upsert 进数据库
scripts/           fetch_off_products.py 拉 Open Food Facts 快照到 data/off/；build_catalog.py（dev catalog）把它导入产品目录
data/              大数据集（Open Food Facts 快照），gitignore，不进仓库
tests/             不联网的测试（FakeLLM；页面行为由 HTTP 测试和 web/ 的 Vitest 覆盖）
evals/             模型层回归，需要真实模型
docs/porting/      从第一版带过来了什么、为什么（01–06）
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
- **产品目录**：真实的 Open Food Facts 食品（ODbL），约 41 万个、10 万个真实品牌。只收同时有英文名（拉丁字母）、
  标签原语言名（`product_name_native`，英文标签就等于英文名）和包装规格的产品。名称、品牌、类目、规格、标签是原数据；
  箱规、箱价、MOQ、托盘、箱重是按真实规格和类目**确定性推导**的（同一条码每次算出一样的数）；
  `product_status`（正品 / 赠品 / 样品 / 停产）里只有 OFF 标了 obsolete 的停产是真的，其余是推导的；
  `restricted_territories` 留空——原数据没有，不编。量大，所以不走 fixtures：`dev catalog` 用 COPY 一次性替换整张表，
  产品页的搜索、计数、分页都在数据库里做（名称和 SKU 有 trigram 索引）。目录不依赖合作关系；下单仍然要。
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
| 多页桌面：聊天 / 产品 / 订单 / 合同 / 报表 / 品牌知识 / 记忆 | **新增** — `web/`（Vite + React），对接 `app.py` / `desk_api.py` |
| 产品目录：Open Food Facts 真实食品 + 推导的批发字段，多品牌；产品页库内搜索分页 | **新增** — `scripts/`、`web/` 产品页；全目录进 prompt 待改成检索（RAG） |
| 意图 / 聊天入口 | 已改 — `session_open`（首次 title+tags）+ `PLAN`（tools/MCP/agents）；无代码侧 route |
| 提交后的收货 / 付款 / 索赔 | 已移植 — `claims.py` |
| 合同全量入库 + 条款 → PO 规则 + 合同折扣预填 | **新增** — `contracts.py`、`db/` |
| 合同页：上传协议（PDF / Word / 文本）或导入抽取 JSON；条款 / 派生规则 / 证据 | 已接入 — `contract_read.py`、`web/` 合同页；模型填条款，代码派生规则 |
| 报表：整洁表格 → 事实 → 确认 → 取代；报表页 + 图表 + 下单 vs 售出 | **新增** — `reports.py`、`web/` 报表页；多块乱表（06）待做 |
| 品牌知识库：上传 → 两遍蒸馏（文本；视觉模型同时看页面图）→ 批准 | 已接入 — `knowledge.py`、`web/` 知识页；扫描页需要选 Qwen |
| 长期记忆：记忆页 + 每轮 PO 对话读入 | **新增** — `memory.py`、`web/` 记忆页 |
| 聊天里的合同 specialist | 已接入 — 附上协议文件即抽取并入库；只有文件名、没有文件内容时会要求重传 |
| 聊天里的报表 specialist | 占位 — 设计和 prompt 在 `docs/porting/06-report-core.md` |
| 聊天里引用知识库 / 合同 / 报表数据 | 待做 — 数据已在库里并按产品关联，prompt 侧还没接 |
| 评估 | 脚手架 — `evals/` |

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

产品目录另外导入（可选，约半小时拉数据 + 几分钟入库）：

```
dev py scriptsetch_off_products.py
dev catalog
```

之后不需要 activate，用 `dev.bat`：

```
dev            桌面（聊天 / 产品 / 订单 / 合同 / 报表 / 品牌知识 / 记忆）   http://localhost:5173
dev api        HTTP API          http://127.0.0.1:8000/docs（`dev` 会一并拉起）
dev db         建库，首次运行需要
dev catalog    导入产品目录（data/off/ 的快照；--dry-run 只统计不写库）
dev test       全部测试，不联网（模型；数据库是真的）
dev lint       ruff
dev eval       评估（需要真实模型）
dev py -m partnerdesk.contracts extraction.json --brand brand-aurora --partnership ps-aurora-nordic --apply
```

（VS Code 打开这个目录会自动选中 `.venv`，新终端里直接 `python` 就是它。）

## 可选依赖

| extra | 内容 |
|---|---|
| `contract` | 读 PDF / Word：PyMuPDF、python-docx。合同和品牌资料都要用 |
| `dev` | pytest、httpx、ruff |
