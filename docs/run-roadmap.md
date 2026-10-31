# Run 现状与未做事项

**文档背景**
目前这个项目正在做后端工程建设。

**项目目标**
1. 做一个可监控、可重试、能找回、不丢数据的完整的 agent 工程。
2. 这份文档记三件事：代码里已经成立的事实、正在修改中的内容、最后再修改的内容。

**代码修改规则**
1. 不要自己改代码，不要自己改代码，不要自己改代码。
2. 我正在学习写代码，把`哪份文件`的`哪一份部分内容`，以及`需要修改为什么样的代码（给出代码原文）`发给我，我自己一个一个抄，具体写法如下：
- 如果文件修改过多，直接把整个文件的代码给我
- 如果文件修改不太多，或者只是增加/删除一整个函数，尽量告诉我文件的哪一部分修改为什么，不然我要一行一行找修改的地方
3. 可以你直接修改代码的情形：
- 前端代码
- ./test/下的代码
- 在我明确表达让你直接修改代码的时候
4. 建 run 的函数名是 `create_agent_run`，不要写成旧名 `create_run_with_outbox`。
5. 每次修改完代码（无论是AI直接修改，还是我自己抄的），都同步把这个docs一起更新`docs/run-roadmap.md`。

## 现状

发一条聊天消息时，HTTP 不再同步跑模型。它只做三件事：建一条排队中的 run、把任务写进出站队列、叫醒 worker，然后立刻把 `run_id` 推给前端。关掉这条 SSE 不是取消。（`backend/orchestrator/turn.py` 的 `handle_message`；建 run 在 `backend/services/chat_run.py` 的 `enqueue_chat_run`。）

真正跑模型和工具的是 Compose 里那个 worker。Celery 只负责叫醒它去领队列里那一行；领到之后才把 run 改成 running，再进循环。测试里没有 Celery，自己调 `work_chat`。（worker 入口 `backend/celery/worker.py`；任务 `backend/celery/tasks.py`；循环 `backend/services/run_loop.py`。）

前端拿到 `run_id` 之后轮询这条 run 的轨迹，等到成功、失败、取消、或等用户确认，再读会话 history 画回复。点停止走取消接口；关页、切聊天、Abort 只停本地轮询，worker 继续跑。

Run 有六个状态，只经同一处切换：queued → running，再从 running 到 waiting_for_user、succeeded、failed、cancelled。（`backend/db/database/run.py` 的 `_transition`。）

轨迹存在自己的表里：run、step、模型调用、工具调用、用量、出站任务。会话的 `history` 仍是给人看的对话，不是轨迹副本，不要删。（表模型在 `backend/db/orm/run.py`。）

领域能力（采购、合同、报表、查数、品牌）仍是工具，返回 dict。采购单的价格、SKU、MOQ、商业规则由代码算，模型只填 slot。（工具绑定在 `backend/services/session_tools.py`；计价在 `backend/po/`。）

### 模型和记账

厂商客户端只有一套：GLM、Qwen、DeepSeek、交大。（`backend/llm/client.py`。）分叉的不是模型，是谁构造、谁把这次调用写进当前 run。

聊天主回复：循环注入一个 client，走聊天补全（GLM 走 Responses）。token 记在 client 上，再写入这条 run 的模型调用表。

工具内部的模型调用：循环先给这个工具开一条 step，再调工具。工具把 `run_id` / `step_id` 带下去，跑完写到这条 step 上。已经这样记的有：

- 采购抽取、售后判定、报表规划/撰写/审核：走统一的校园 / 官方 JSON 入口，并把当前 run 的 client 传进去。（入口 `backend/llm/calls.py`；抽取 `backend/po/extract.py`；售后 `backend/po/claims.py`；报表 `backend/report/turn.py`。）
- 查数：选表和打分用交大校园的 deepseek-chat，写 SQL 用官方 deepseek-flash，所以自己构造两家 client，不是 chat 的 decide client。`answer_question` 拿到 `run_id` / `step_id` 后，选表、写 SQL、失败改写、打分和低分重问都写入当前 tool step。聊天模型决定调用这个工具的那一次也记在同一条 step 上。（`backend/text2sql/text2sql.py`。）
- 报表图表和代码修：第一次生成走报表的 `_call_json`。失败后的改写走 `with_reflection`，官方 flash 记在同一条 `report.draft` step 上。调用把当前 `llm` 交给 `flash_json`，不另建客户端。（`backend/codefix/fix.py`；`backend/report/turn.py`。）
- 记忆折叠：前缀到了预算就走校园 `deepseek-chat`，把当前 `llm` 传进 `school_json`。发生在 `save_reply` 里，这时 chat run 还是 running，所以记在这条 run 上，另开一条名为 `model` 的 step。没有 `run_id`，或 run 已经不是 running，照常折叠，不写轨迹。（`backend/memory.py`。）
- 新会话标题：第一句还没有标题时，worker 在领到 run 之后、进入循环之前做。走校园 `deepseek-chat`，把当前 `llm` 传进 `school_json`，记在这条 chat run 上，另开一条名为 `model` 的 step。模型失败只记日志，标题留空。首包 SSE 只有 `run_id`，页面在 run 结束后重读会话才看到标题。（`backend/orchestrator/turn.py` 的 `open_session`、`name_new_chat`；调用点在 `execute_chat`。）

测试拦模型的位置跟入口一致：走统一 JSON 入口的，拦 `calls.py`；查数拦它自己的两个构造器。（`tests/conftest.py`；`tests/test_text2sql.py`。）

旧编排已经删掉：`make_plan`、`PlanTask`、`plan_schema`、`_tool_catalog`，以及整个 `backend/orchestrator/gather.py`。每一轮直接进工具循环，系统提示是 `REACT`，工具名单走 `tools=`。`PLAN` 和 `SYNTHESIZE` 已从 `backend/orchestrator/prompt.py` 删掉。`FOLLOWUP` 还在用，品牌检索会把它交给模型。

助手回复写进 history 时带上这次 chat run 的 `run_id`（`save_reply`）。打开会话时页面按它拉轨迹，刷新后轨迹条还在。旧消息没有这个字段，不拉。

同一个会话已经有一条 `running` 的 chat run 时，下一条不领，job 留在 `queued`。检查和领走锁在同一条会话行上（`claim_chat_turn`）。Celery 碰到 `SessionBusy` 过两秒再试，并发名额可以去跑别的会话。`hold_session` 仍留给确认和生成同时写会话的情况。

散文回复按段写进这条 run 的 `output_json.text`，状态保持 `running`（`note_running_reply`）。`decide` 和 `answer` 走流式补全，每段都写一次；页面轮询轨迹时把这段文字画进正在生成的气泡。GLM 和异步的 Qwen 整段写一次。校园网关走 SSE。（`backend/services/run_models.py` 的 `_invoke`；`backend/llm/client.py` 的 `_campus_stream`。）

折叠由大约 2000 token 的预算决定，代码调用，不进模型的工具名单。发生时记成这条 chat run 上的工具 `memory.fold`，校园模型调用挂在这个工具 step 上。没有 `run_id`，或 run 已经不是 running，照常折叠，不写轨迹。（`backend/memory.py` 的 `fold_older`。）

PDF 的 GLM-OCR 由合同读取调用，不进模型的工具名单。`contract.read` 把当前 `run_id` 传进 `load_pages`。run 还是 running 时，记成工具 `document.ocr`。Word 和纯文本不记。（`backend/documents.py`；`backend/contract/read.py` 的 `read_agreement`。）

需要确认的工具在执行前把 run 停在 `waiting_for_user`。`resume` 把出站任务发出去并叫醒 worker。最近一次 resume 的 `decision` 为 `skip` 时不调用工具，记 `{skipped: true}` 后继续；没有 `decision`，或为 `confirm`，照常执行。聊天页在输入框上方给出确认和跳过。（`backend/services/run_loop.py` 的 `_finish_tool`、`_resume_skipped`；`frontend/src/pages/Chat.tsx`。）

### 取消和断连

| | 取消 | 断连 |
| --- | --- | --- |
| 谁发起 | 用户点停止 | 关页、切聊天、SSE 读完、generator 被 close |
| 后端 | `request_cancel`，worker 在循环里收手 | 什么都不做，run 继续 |
| 前端 | `cancelRun`；不要靠 abort 当取消 | abort 只停本地轮询 |

旧代码在流被关掉时把断连当成取消，反了。发消息入口和那套同步驱动都已经不在了：`open_chat_run`、`drive_chat_run`、`record_chat_step` 以及只给它们收尾的函数已从 `backend/services/chat_run.py` 删掉。关流不再取消。

## 正在修改

这一章已经做完。监控台先不做。

## 后续计划

先不做。

### 做监控台

按 org / 用户 / 状态翻 run，打开一条能看 step、model_call、tool_call、lease、outbox、dead letter。单条 run 的 API 已经有了，还要做列表页、过滤和重放。

## 状态机（人看的）

```
HTTP POST /message
  → create_agent_run（queued + outbox execute_run）
  → dispatch_pending
  → wake_chat_worker / execute_run.delay
  → SSE: run_id  （关流到此为止）

Celery execute_run / 测试 work_chat
  → claim job + claim_run
  → execute_chat / run_loop
       需要确认 → waiting_for_user （resume 叫醒 worker；skip 不执行，confirm 执行）
       取消位   → cancelled
       异常     → failed / retry / dead letter
       正常     → save_reply + succeeded

前端
  → 轮询 trace
  → succeeded：拉 session.history
  → 点停止：cancel API
```
