# legacy/

第一版平台里的 Python LLM 模块，**原样搬过来**，作为重写的起点。这里的东西都不是目标设计——
每个模块要么被移植进 `partnerdesk/`，要么被删掉。

| 包 | 做什么 | 状态 |
|---|---|---|
| `email_agent/` | stdin 进 JSON、stdout 出 JSON 的任务 CLI：话题分类、PO 邮件回复、收货 / 付款 / 破损索赔抽取、名片和报表拍照识别、外联邮件撰写、线索调研 | PO 相关的已移植进 `partnerdesk/`（prompt 重写、不再走子进程）；外联 / 线索部分待定 |
| `contract_ai/` | 合同文件 → OCR（glm / openai / textract / 原生 docx）→ 分块 → 关键词召回 → 一次大 pydantic 抽取，带证据位置 | 待移植 |
| `brand_knowledge/` | 品牌 PDF / 演示稿 → JPEG 分页、12 页一批 → Gemini → 结构化品牌知识（语气、产品、卖点） | 待移植 |

按第一版的方式跑一个任务——把 `legacy/` 放进路径，JSON 从 stdin 进（在仓库根目录）：

    PYTHONPATH=legacy python -m email_agent.tasks.classify_topics < payload.json

每个任务在 stdout 最后一行打印且只打印一个 JSON 对象：`{"ok": true, "result": ...}` 或 `{"ok": false, "error": "..."}`；
进度和警告只走 stderr。`contract_ai/_env.py` 会加载 `<仓库根目录>/.env`，从这里解析路径没问题。

没有带过来的：所有 TypeScript。第一版的编排层是 TS 写的，在这里用 Python 重写，不是复制；
原文件在仓库外的 `D:\Workspace\partnerdesk-reference\` 里作只读参考。
