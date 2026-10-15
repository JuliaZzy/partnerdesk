# 移植规格：会话开场标签（session_open）

会话**第一次**用户发言时，用 `prompts/session_open.py` 打标签并起标题；之后多轮不再 classify。
侧栏历史对话显示 `title` + `tags`。编排由 `PLAN` 自行选择 tools / MCP / agents，**没有**代码侧 `route`。

## 5 个 tag（= 5 个 agent）

| key | 含义 | agent |
|---|---|---|
| `po` | 询价、下单、改单、订单生命周期（付款/物流/退换） | `po` |
| `contract` | 经销协议、续约、独家、区域等关系条款 | `contract` |
| `report` | 经销商提交卖穿/销售报表或问如何报 | `report` |
| `brand` | 品牌知识、规格说明、营销素材（已批准片段） | `brand`（原 rag） |
| `data` | 读库：已下订单、已确认报表事实、合同折扣、目录查询 | `data`（原 data_query） |

可多选；空 tags + 中性 title 对寒暄合法。

## 流程

1. `sessions.title` 为空 → `open_session`（无历史）→ 写入 `title` / `tags`
2. 每轮 → `make_plan`（只排 agent）→ 有 po/contract/report 则进 specialist
3. 否则 → 原生 `tools=`（web_search / translate + SocialCrawl MCP），`tool_choice=auto`，模型自己填参数
4. 模型不调工具 → 散文直接答
5. 活跃 PO（gathering / confirm）运行时强制保留 `po`

## tools / MCP

- 注入方式与 OpenAI function calling 相同：`complete(..., tools=schemas, tool_choice=auto)`
- MCP：SocialCrawl（LinkedIn / TikTok / Instagram / X 等多平台），`SOCIALCRAWL_API_KEY`；schema 合并进同一份 `tools=` 列表
