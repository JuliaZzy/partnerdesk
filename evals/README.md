# evals

模型层的回归测试，对着**真实模型**跑（`tests/` 里的单元测试用 `FakeLLM`，不联网；这里相反）。

| 脚本 | 测什么 | 用例 |
|---|---|---|
| `run_po_extract.py` | slot 抽取：行项目、条款、折扣三态、对客套话不误判 `user_confirmed`、消息里的 prompt 注入、中文 | `po_extract/cases.jsonl` |

每个用例调一次 `extract_turn`，把归一化后的 slots 和 `expect` 比对，按字段打印命中率和失败用例；有失败则退出码 1。

计划中（同样的形状：一个 `cases.jsonl` + 一个打印逐字段准确率的脚本）：

- `router/` — 每个 topic 的精确率 / 召回率，含 `purchase_order` 的置信度下限
- `resolver/` — 口语化产品引用 → SKU，跨语言
- `claims/` — 收货 / 付款 / 破损的判定，0.7 阈值
- 跨模型：每个脚本用 `--model` 分别跑 gpt-4.1 / gpt-4.1-mini / llama3.2:3b / glm-4.6，对比结果

运行：

    python evals/run_po_extract.py --model gpt-4.1
