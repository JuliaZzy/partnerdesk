# legacy/

| 包 | 做什么 |
|---|---|
| `email_agent/` | 任务 CLI：话题分类、PO 邮件回复、收货 / 付款 / 破损索赔抽取、名片和报表拍照识别、外联邮件撰写、线索调研 |
| `contract_ai/` | 合同文件 → OCR（glm / openai / textract / 原生 docx）→ 分块 → 关键词召回 → 一次大 pydantic 抽取，带证据位置 |
| `brand_knowledge/` | 品牌 PDF / 演示稿 → JPEG 分页、12 页一批 → Gemini → 结构化品牌知识（语气、产品、卖点） |

每个任务从 stdin 读一个 JSON，在 stdout 最后一行打印且只打印一个 JSON 对象：`{"ok": true, "result": ...}` 或 `{"ok": false, "error": "..."}`；进度和警告只走 stderr。在仓库根目录：

    PYTHONPATH=legacy python -m email_agent.tasks.classify_topics < payload.json

`contract_ai/_env.py` 会加载仓库根目录的 `.env`。
