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
compose.yaml / Dockerfile / web/Dockerfile   Docker Compose：Postgres + Redis + Milvus + API + Vite
dev / dev.bat      所有命令的入口（容器里跑，不在宿主机起服务）
partnerdesk/
  prompts/         每个 agent 一个 py（po.py、contract.py…），常量 + registry.json；填槽用 prompts.fill
  mcp/             SocialCrawl MCP（多平台社交/电商数据）：servers.json + runtime → 并入 tools=
  orchestrator.py  聊天入口：先 native tools=，再意图分类 → po / contract / report / 弃权
  po/              slots → 抽取（LLM）→ 校验（代码）→ 草稿 + hash → 回复（LLM）→ 确认门 → 提交
  claims.py        提交之后：收货 / 付款 / 破损，落到订单状态上
  contract/        协议：read 填条款表 → ingest 入库并派生 PO 规则 → turn 聊天归档
  documents.py     PDF / Word / 文本 → 逐页文本；PDF 页渲染成 JPEG，给视觉模型看
  reports.py       销售报表：整洁表格 → 事实（代码读格子，模型不碰数字）→ 草稿 → 人确认 → 同期重发即取代
  memory.py        长期记忆：跨会话记住的偏好 / 指令 / 事实，每轮 PO 对话作为上下文读入（只能提醒，不能放宽规则）
  catalog.py       产品索引 + 给 prompt 的目录清单
  llm.py           唯一的模型接缝：complete_json / stream_text / complete（tools=）；测试用 FakeLLM
  db/              数据层：orm.py（表结构）、database.py（唯一入口）、migrations/（Alembic）
  service.py       业务操作，不依赖传输层：handle_message（转发 orchestrator）/ confirm / orders
  app.py           service.py 的 HTTP 适配层（FastAPI）：SSE 聊天、确认、订单
  desk_api.py      桌面其余页面的 HTTP：产品目录、合同、报表、记忆
web/               桌面（Vite + React + TypeScript）：聊天 / 产品 / 订单 / 合同 / 报表 / 记忆，只调上面的 HTTP
scripts/           fetch_off_products.py 拉 Open Food Facts 快照到 data/off/；build_catalog.py（dev catalog）把它导入产品目录
data/              大数据集（Open Food Facts 快照），gitignore，不进仓库
tests/             不联网的测试（FakeLLM；页面行为由 HTTP 测试和 web/ 的 Vitest 覆盖）
evals/             模型层回归，需要真实模型
docs/porting/      从第一版带过来了什么、为什么（01–06）
```

## 数据层

所有业务数据都进数据库，按真实的表建模（列、外键、索引），不再有 JSON blob 表：

- **引擎**：SQLAlchemy 2.0 + PostgreSQL，没有第二个引擎。连接串由 Docker Compose 注入
  （`PARTNERDESK_DB_URL`），`postgres:17-alpine` 镜像会建好库本身。不保留 SQLite 回落是刻意的：双引擎意味着每个 schema 决策都要在两边都成立，
  原生类型（`timestamptz`、`jsonb`、数组、枚举）就一直用不上；而静默回落会让配置错误的部署
  在一个空库上正常启动，而不是当场失败。
- **schema 由 Alembic 版本化**（`partnerdesk/db/migrations`）。`Database()` 构造时自动升到 head，
  测试每个临时库也走同一套 migration；`tests/test_db.py` 会在模型和 migration 不一致时直接失败。
  改表：改 `db/orm.py` → `dev py -m alembic revision --autogenerate -m "..."` → 检查生成文件。
- **合同**：`contracts` 一行一份协议；`contract_extractions` 原样保存每次抽取的 JSON（审计 / 重跑）；
  折扣阶梯、MOQ、价格表、区域、原文证据各有自己的表；由合同派生的规则通过 `commercial_rules.contract_id` 追溯。
  下单时 `contract.contract_discount_for_next_order` 按合同年内已下单数选档，预填到订单里
  （假设：一张已提交的 PO = 一柜）。经销商在对话里说的折扣覆盖预填；超过合同最高档走审批规则。
- **品牌资料**：`brand_documents` 一行一份上传的文件（含原文件字节），`brand_document_pages` 是逐页文本。
  蒸馏成片段的链路已去掉。问答走 `brand_knowledge/rag.py`：问题原样做向量检索，现有文本模型只根据命中的段落作答。解析仍走 `documents.py`。
- **销售报表**：`sales_reports` 一行一份文件，`report_extractions` 记录每次读取（列映射、跳过了什么），
  `report_facts` 按原生粒度存每个数字（metric_key / 渠道 / SKU / `product_id` / 期间 / 来源单元格）。
  事实是 draft，`Database.confirm_report` 才变 confirmed；同一合作关系同一测量同一期间的旧事实自动 superseded——重发不会翻倍。
  `product_id` 让报表的销量能和 `purchase_order_items` 按产品对上（报表页已有「下单 vs 售出」）。
- **产品目录**：真实的 Open Food Facts 食品（ODbL），约 41 万个、10 万个真实品牌。只收同时有英文名（拉丁字母）、
  标签原语言名（`product_name_native`，英文标签就等于英文名）和包装规格的产品。名称、品牌、类目、规格、标签是原数据；
  箱规、箱价、MOQ、托盘、箱重是按真实规格和类目**确定性推导**的（同一条码每次算出一样的数）；
  `product_status`（正品 / 赠品 / 样品 / 停产）里只有 OFF 标了 obsolete 的停产是真的，其余是推导的；
  `restricted_territories` 留空——原数据没有，不编。量大，所以 `./dev catalog` 用 COPY 一次性替换整张表，
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
| 多页桌面：聊天 / 产品 / 订单 / 合同 / 报表 / 记忆 | **新增** — `web/`（Vite + React），对接 `app.py` / `desk_api.py` |
| 产品目录：Open Food Facts 真实食品 + 推导的批发字段，多品牌；产品页库内搜索分页 | **新增** — `scripts/`、`web/` 产品页；全目录进 prompt 待改成检索（RAG） |
| 意图 / 聊天入口 | 已改 — `session_open`（首次 title+tags）+ `PLAN`（tools/MCP/agents）；无代码侧 route |
| 提交后的收货 / 付款 / 索赔 | 已移植 — `claims.py` |
| 合同全量入库 + 条款 → PO 规则 + 合同折扣预填 | **新增** — `contract/`、`db/` |
| 合同页：上传协议（PDF / Word / 文本）或导入抽取 JSON；条款 / 派生规则 / 证据 | 已接入 — `contract/`、`web/` 合同页；模型填条款，代码派生规则 |
| 报表：整洁表格 → 事实 → 确认 → 取代；报表页 + 图表 + 下单 vs 售出 | **新增** — `reports.py`、`web/` 报表页；多块乱表（06）待做 |
| 品牌资料：文件 + 逐页文本留在库里；问答时按品牌做向量检索 | 最小 RAG — `brand_knowledge/rag.py`；需先 `index_brand`，embedding 与聊天模型分开配 |
| 长期记忆：记忆页 + 每轮 PO 对话读入 | **新增** — `memory.py`、`web/` 记忆页 |
| 聊天里的合同 specialist | 已接入 — 附上协议文件即抽取并入库；只有文件名、没有文件内容时会要求重传 |
| 聊天里的报表 specialist | 占位 — 设计和 prompt 在 `docs/porting/06-report-core.md` |
| 聊天里引用品牌资料 | 已接入最小链路 — orchestrator 的 `brand` agent 把原问题交给 Milvus；合同 / 报表数字的引用仍待做 |
| 评估 | 脚手架 — `evals/` |

## 运行

本机只需要 **Docker Desktop**。不要再起 PostgreSQL、Python venv 或 Vite。

第一次：`cp .env.example .env`，填模型 key（默认交大校园 API，需校园网或 VPN；也有 OpenAI / Ollama / GLM）。
**不要**填 `PARTNERDESK_DB_URL`、`PARTNERDESK_REDIS_URL`、`MILVUS_URI`——Compose 会注入容器内网地址。Ollama 在宿主机上时，写成
`LLM_BASE_URL=http://host.docker.internal:11434/v1`。

然后：

```
./dev
```

| | |
|---|---|
| 桌面 | http://localhost:5173 |
| API | http://127.0.0.1:8000/docs |
| 停掉 | `Ctrl+C`，或另开终端 `./dev kill`（数据卷保留） |

Postgres、Redis、Milvus 只在 Compose 网络里，不往本机暴露端口。镜像已经 build 过的话，`./dev` 只是起容器。Milvus 第一次要拉一个比较大的镜像，起来也比 Postgres 慢。

产品目录可选（约半小时拉数据 + 几分钟入库）：

```
./dev py scripts/fetch_off_products.py
./dev catalog
```

其它命令（Windows 用 `dev.bat`）：

```
./dev api        只起 API + Postgres + Redis + Milvus     http://127.0.0.1:8000/docs
./dev db         只起 Postgres
./dev catalog    导入产品目录（data/off/ 快照；--dry-run 只统计不写库）
./dev test       pytest（假模型，真 Postgres）
./dev test-web   前端 Vitest
./dev lint       ruff
./dev eval       评估（需要真模型）
./dev py ...     容器里的 python
./dev build      依赖变了才需要（pyproject.toml / web/package.json）
./dev kill       停掉栈，保留 Postgres 数据卷
```

基础镜像默认 DaoCloud，pip / npm 默认清华和 npmmirror。能直连 docker.io 时，把
`POSTGRES_IMAGE` / `REDIS_IMAGE` / `MILVUS_IMAGE` / `PYTHON_IMAGE` / `NODE_IMAGE` 改回官方名即可。

## 可选依赖

| extra | 内容 |
|---|---|
| `contract` | Word 用 python-docx；PDF 页图用 PyMuPDF。PDF 正文走 GLM-OCR（`GLM_OCR_API_KEY`） |
| `dev` | pytest、httpx、ruff |
| `catalog` | pyarrow：拉 Open Food Facts 快照、导入产品目录 |
