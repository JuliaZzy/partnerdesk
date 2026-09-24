# CLAUDE.md

> 先读 [README.md](README.md)：架构、目录结构、数据层设计、能力现状表都在那里，本文件不重复。
> 每个模块顶部的 docstring 说明了它自己的职责和设计取舍——改一个模块前先读它的 docstring。
>
> 本文件只放**读代码推断不出来、但会改变你怎么动手**的东西。

## 命令

不要直接调 `streamlit` / `uvicorn` / `pytest`，统一走 `dev.bat`（它用 `.venv` 里的 python，不需要 activate）：

```
dev            桌面 UI（Streamlit）   http://localhost:8501
dev api        HTTP API（FastAPI）    http://127.0.0.1:8000/docs
dev db         建库（读 PARTNERDESK_DB_URL），首次运行需要
dev test       全部测试，不联网
dev lint       ruff
dev eval       评估，需要真实模型
dev py ...     用 venv 的 python 跑任意命令
dev kill       结束占用 8501 的进程
```

环境是 Windows + PowerShell。`dev.bat` 之外需要直接调解释器时用 `.venv\Scripts\python.exe`。

## 数据库

- **PostgreSQL 是唯一的引擎，没有 SQLite 回落**，这是刻意的——双引擎意味着每个 schema 决策都要在两边都成立，
  Postgres 的原生类型就一直用不上（时间戳卡在字符串上很久，就是那段历史留下的）。不要为了"方便测试"把它加回来。
- 本地跑着 **PostgreSQL 17.7，端口 5432，库名 `partnerdesk`**。连接串在 `.env` 的 `PARTNERDESK_DB_URL`，首次建库用 `dev db`。
- 测试用 `PARTNERDESK_TEST_DB_URL`，每个测试建一个一次性库、结束丢弃。测试拿数据库只有一个入口：
  `tests/conftest.py` 的 `make_db` 夹具，不要在测试里直接构造 `Database(...)`。没设这个变量测试会 skip，不会静默换引擎。
- 改表的流程是固定的：改 `db/orm.py` → `.venv\Scripts\alembic revision --autogenerate -m "..."` → **人工检查生成的迁移文件** →
  `tests/test_db.py::test_migrations_match_the_models` 会在模型和迁移不一致时失败。绝不手写 `CREATE TABLE`。
- `db/` 之外的任何代码都**不许 import `orm`**。对外只有 `Database` 门面，它进出的是 pydantic 模型和普通 dict。

## 写代码时的硬约束

这几条是这个项目存在的理由，违反了就不是这个项目了：

1. **模型抽取，代码决定。** 任何数字、价格、SKU 解析结果、规则判定都必须来自确定性代码。
   LLM 只负责把自然语言填进固定 slot。不要为了省事让模型算总价或判断规则是否通过。
2. **副作用幂等。** confirm token 在建单**之前**烧掉。
3. **所有业务数据入库，按真实表建模**（列、外键、索引），不要 JSON blob 表。

## 约定

- **代码里的注释和 docstring 用英文**，README 和面向用户的文档用中文。跟着周围代码的密度走——
  这个库的注释解释的是"为什么"，不是"这行在干什么"。
- **测试不调模型。** 模型调用一律用 `FakeLLM`（`llm.py` 是唯一的模型接缝），页面用 Streamlit 的 `AppTest` 无头驱动。
  数据库不是假的：测试连真的本地 Postgres。
- ruff：`line-length = 110`，规则集见 `pyproject.toml`。提交前 `dev lint`。

## 坑

- **`.env` 里有真实的 API key 和数据库密码**，已被 gitignore。不要提交、不要打印它的内容、不要把它的值写进任何输出。
  改配置示例改 `.env.example`。
- **`legacy/` 是第一版的代码，原样保留**，作为移植的参照物。不要去"修"它、重构它或让它通过 lint——
  它的作用是被逐步搬出来，不是被维护。
- **时间戳是 `timestamptz`，写入的值一律是 UTC-aware 的 `datetime`**（`database.utcnow`），不要再写 ISO 字符串。
  但**纯日期**（合同期限、报表期间）仍然是 `YYYY-MM-DD` 文本，这是有意的——它们是日历标签，背后没有具体时刻。
  改这两类字段前先确认自己在改哪一类。
- **Streamlit UI 计划被替换掉**（方向是 Vite + React + TypeScript + Vitest，对接 `app.py` 已有的 HTTP 端点）。
  `ui.py` 和 `ui_pages/` 属于待拆除的部分——修 bug 可以，不要在上面做大的新投入。
  领域逻辑不受影响：`service.py` 以下完全不认识任何 UI 框架。
- `app.py` 里的 pydantic 请求模型**必须定义在模块层**。因为有 `from __future__ import annotations`，
  定义在 `create_app` 内部的类会被 FastAPI 静默当成 query parameter（代码里有注释说明）。
