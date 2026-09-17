# 移植规格：Report 核心（乱表 → 事实 → 有据叙述）

第一版这块比 PO 大：`server/lib/reports/` 27 个文件 4099 行 + `aiSectionExtract.ts` + `columnAutoMatch.ts` +
`routes/reports/mvp/ai-helpers.ts` + `utils/reportDataSpine.ts`（853 行）+ 20 份测试。**原样副本在
`D:\Workspace\partnerdesk-reference\`（仓库外，不进 git）**，逐行细节去那里看；这份只抄重写真正要的东西：
架构、4 段 prompt 原文、关键确定性规则、测试规格。

产品壳（月报状态机、审批、PDF 导出、Data Spine 表结构）**不重建**。

---

## 1. 架构：模型只给结构，数值永远由代码从格子里读

```
xlsx ──► gridFromSheet（合并单元格前向填充）
      ├─► 确定性 parser（优先）  channelSalesParser（周 × 渠道）/ inventoryExpiryParser（SKU × 效期）
      │                          marketingParser / narrativeParser / genericBlockParser（兜底：任何没写过的表）
      │         blockDetector 先把并排的 band 拆开（销售情况 | 库存情况 共享同一批行）
      └─► aiTableExtractor（LLM，json_schema strict）
                 返回每张表的 headerRow / dataRowStart-End / labelCol / columns{col, header, role, dataType, currency}
                 + notes（散文块）+ derivedTables（写成句子的表）+ reportingPeriod
                 tablesToFacts 再按这些索引从 grid 读单元格 → 数字不可能被编造
      ──► factReconcile：确定性 parser 找到的域，AI 的同域结果**丢弃**；AI 只保留 narrative + 确定性没覆盖的域；
          确定性一张表都没找到时才完全信 AI（否则模型会把一张表拆成几张、把渠道塞进 metric_key、在摄入时翻译 key）
      ──► termCanon（列名英文标准化，见 §3）──► periodColumns（把"9月"这种列名还原成时间维度）
      ──► observationStore：DRAFT observations + extraction run（provenance）
      ──► confirm gate（人确认）──► projectObservations（幂等投影到读模型）
```

**事实的形状**（`ParsedFact`）：`metric_key, fact_type, channel?, sku?, entity?, period_start, period_end, numeric_value | text_value, unit, currency?, source_ref{sheet, row, col, section}, ai_derived: bool`。
原生粒度存储（周就是周），汇总在读的时候做。`ai_derived=true` 的事实（从散文里抽的数）下游要标出来。

**为什么这样**：真实的经销商周报一张 sheet 上有五六种事实（渠道 × 周销售、库存快照、KOL 表、直播月趋势、工作计划），
合并单元格、脚注、散文混排，版式月月漂移（列数 14→18，KOL 表在周/月之间切换）。让模型转录数字 = 幻觉；让模型只定位结构 = 可验证。

---

## 2. Prompt 原文

### 2.1 aiTableExtractor — 表格结构抽取（gpt-4.1，json_schema strict，temp 0）

输入格式：grid 以 `r{N}: c{M}=value …` 逐行列出非空单元格（0-based）。

```
You extract tables from a spreadsheet grid. The grid is given as rows "r{N}:" each listing non-empty cells "c{M}=value" (N and M are 0-based indices).

Identify EVERY distinct data table. A table is a row of column headers followed by one or more data rows. If two tables sit side by side sharing the same rows (e.g. under separate band titles), return them as separate tables.

Separately, capture every PROSE block that is not a cell-table — summaries, commentary, work plans, asks to the brand — in "notes". A prose block that enumerates parallel items (e.g. a per-channel or per-SKU breakdown) belongs in BOTH a note (the prose) AND a "derivedTables" entry (the figures) — see below.

Also return reportingPeriod: the report's OVERALL period if stated anywhere (a header/title like "Reporting period: August 2025", "2025年8月周报", "August 2025"), as "YYYY-MM" (or "YYYY-Qn" for a quarter, "YYYY" for a year). Null if no overall period is stated. EVERY figure ultimately needs a date; this is the document-wide fallback.

For each table return:
- title: a short human title (from a title cell or section heading near it, else synthesize)
- factType: lowercase classification (one of sales, inventory, marketing, kol, livestream, forecast, or a short slug if none fit)
- periodHint: the period this table covers when it is IMPLIED by the title/heading but the rows have NO date column — e.g. title "April Sales Overview" -> "2025-04", "8月销售概况" -> "2025-08", "Q2 Summary" -> "2025-Q2". Format "YYYY-MM" / "YYYY-Qn" / "YYYY". Use null when the table has a real date column or per-row date labels (those take precedence) or when no period is implied. Different tables in ONE sheet often cover different months — date each by its own title.
- headerRow: the r index of the column-header row
- dataRowStart, dataRowEnd: inclusive r indices of the data rows, EXCLUDING any total/subtotal/summary row
- labelCol: the c index of the column holding each row's name/entity (e.g. the week, month, SKU, or channel), or null
- columns: array of { col (c index), header (the column header text COPIED VERBATIM in the source's ORIGINAL LANGUAGE — never translate, rename, romanize, or normalize it; e.g. keep "天猫", "坑位费/稿费", "退款率" exactly), role ("entity"|"metric"|"note"|"period"), dataType ("money"|"ratio"|"count"|"number"|"text"|"date"), currency (3-letter code if money else null) }. INCLUDE text/note columns. Do NOT list labelCol in columns.

DO NOT TRANSLATE. Headers, entity/row labels, titles, and note text must stay in the source's original language exactly as written. Translation happens later in the app, on display — never here. Translating on ingest produces inconsistent keys and breaks aggregation.

For each prose block return a note: { heading (a short label from the nearby section/title, or null), markdown (the prose as GitHub-flavored markdown) }. Reproduce the text FAITHFULLY in its original language: you may add markdown structure (paragraphs, bullet lists, bold for key figures) but MUST NOT summarize, translate, add, omit, or otherwise change the meaning. One note per distinct prose block.

CRITICAL: whenever a prose block contains a NUMBERED or bulleted list where each item names an entity (a channel, SKU, platform, person…) followed by the SAME kinds of figures, you MUST ALSO reshape it into a "derivedTables" entry — in ADDITION to keeping the original note. This is a table written as sentences.
Worked example — the prose "分渠道情况如下：1、天猫累计销售28.4万，推广费比35.6%，七夕活动；2、京东累计销售23.8万，推广费比32.7%；3、拼多多累计销售8.1万，推广费比33.1%" MUST yield a derivedTable whose headers and entities stay in the ORIGINAL language:
  { "title":"分渠道情况", "factType":"sales", "periodHint":null, "fromHeading":"<the note's heading>",
    "columns":[{"header":"渠道","role":"entity","dataType":"text"},{"header":"累计销售","role":"metric","dataType":"money","currency":"CNY"},{"header":"推广费比","role":"metric","dataType":"ratio"},{"header":"备注","role":"note","dataType":"text"}],
    "rows":[["天猫","28.4万","35.6%","七夕活动"],["京东","23.8万","32.7%",""],["拼多多","8.1万","33.1%",""]] }
For a derivedTable, set periodHint when the note's heading implies a month/quarter/year (e.g. "8月达人合作" -> "2025-08"), else null.
COPY each value EXACTLY as written, including units (28.4万, 35.6%, 897) — do NOT convert or do math; the system normalizes units. Put each item's free commentary in a "note"/text column. Only reshape genuinely parallel lists; never invent values not stated; do NOT re-derive a table already returned in "tables".
MANDATORY FINAL STEP: re-read every note you wrote. For each one that contains a numbered/bulleted list of items sharing fields, you MUST add a matching derivedTables entry. Returning such a note with no derivedTable is an error.

Return ONLY JSON: {"reportingPeriod": "YYYY-MM" | null, "tables":[ ... ], "notes":[ ... ], "derivedTables":[ ... ]}
```

第二遍补漏（只喂 notes）：

```
You are given report NOTES (prose). Some are really a TABLE written as sentences — a numbered or bulleted list where each item names an entity (channel, SKU, platform, person) followed by the SAME kinds of figures. For EACH such note, output one derived table.
For each: { title (short, in the source's original language), factType (lowercase: sales/inventory/marketing/kol/livestream or a slug), periodHint (the period implied by the note heading as "YYYY-MM"/"YYYY-Qn"/"YYYY", e.g. "8月" -> "2025-08"; else null), fromHeading (the note's heading), columns: [{ header, role ("entity"|"metric"|"note"), dataType ("money"|"ratio"|"count"|"text"), currency (3-letter or null) }], rows: [[value per column]...] }.
DO NOT TRANSLATE: column headers and entity/row labels must be COPIED VERBATIM in the source's original language (keep "天猫", "推广费比", etc.). COPY each value EXACTLY as written in the text, including units — e.g. "28.4万", "35.6%", "897". Do NOT convert or do any math; the system normalizes units. For a percentage metric use dataType "ratio". First column = the entity. Put each item's commentary in a trailing "note"/text column. Use only values stated in the text; never invent. Skip notes that are not parallel lists.
Return ONLY JSON: {"derivedTables":[ ... ]}
```

**为什么**：json_schema strict + additionalProperties:false → 小模型也无法自由发挥；所有可选字段用 nullable 表达。
"DO NOT TRANSLATE" 出现三次，因为摄入时翻译会让同一列在两个月里落到两个 key 上。派生表的数值由模型抄原文（"28.4万"），
单位换算在代码里（`coerceNumeric`）。

### 2.2 termCanon — 列名英文化（只对词汇表没见过的词调用）

```
You translate column headers and table titles from a distributor's sales/inventory report into English.

Rules:
- Return a SHORT business English label for each term, the words a reader would expect on a report column: "Platform outbound", "Refund rate", "Slot fee". Not a sentence, not a definition, no trailing punctuation.
- Keep brand, platform, product and person names as they are commonly written in English; transliterate when there is no English form.
- isPeriod is true when the term names a point or span of TIME rather than a measure (a month, a week, a quarter, a year, a date range). Those are periods, not columns.
- Translate every term you are given, in the same order. Never invent terms that were not given.
```

### 2.3 aiSectionExtract — 在多块工作簿里**定位**数据表（gpt-4.1-mini，只定位不转录）

system：
```
You analyze messy distributor sales workbooks (often Chinese-language brand reports) and locate the data table. Workbooks frequently mix: a narrative summary, a weekly or monthly channel-rollup table (Tmall/JD/Douyin/PDD/Xiaohongshu/Distribution = 天猫/京东/抖音/拼多多/小红书/分销), a per-SKU unit table, and KOL/influencer planning tables. You LOCATE structures by row/column index; you never transcribe numbers. Return STRICT JSON only.
```
user（sheet 压缩成 rows 数组，单元格截到 80 字符）：
```
STEP 1. Prefer a per-SKU table: each row is one product (SKU or product name) with numeric units and/or revenue. If the header row has months ("9月","10月"…) as columns, shape="matrix"; a single units/revenue column is shape="rows". Set sheetName + headerRowIndex (0-based) for these.

STEP 2. If there is NO per-SKU table but there IS a channel sales table (rows are weeks or months, columns are sales channels like 天猫/京东/抖音/拼多多/小红书/分销), set shape="channel_rollup" and fill "channelRollup":
  - sheetName: the sheet holding it
  - totalRowIndex: the row whose cells hold each channel's MONTH total. Prefer an explicit total row labelled "合计"/"总计"/"Total"; if none, use the single month row.
  - monthNumber: 1-12 if stated (e.g. a "8月" label means 8), else null
  - channels: one entry { name, colIndex } per real channel column. EXCLUDE the grand-total column (labelled "合计"/"Total" that sums the other channels) and any inventory columns.
  - stockCell: { rowIndex, colIndex } pointing at a remaining-stock value (剩余库存) if present, else null

If nothing usable exists, shape="none". skippedSections: short labels for sections you ignored (e.g. "KOL planning", "narrative summary"). reason: one short sentence the user will see.

Response shape: { "sheetName": string|null, "headerRowIndex": number|null, "shape": "rows"|"matrix"|"channel_rollup"|"none", "skippedSections": string[], "reason": string, "channelRollup": {...} | null }
```
后处理：返回的 sheetName 必须在输入里存在（防幻觉）；shape 说有表但 sheet/header 缺 → 降级为 none。

### 2.4 columnAutoMatch — 列 → 标准字段（只在规则匹配弱时兜底，gpt-4.1-mini，20s 超时，失败静默回退规则结果）

```
system: You map spreadsheet columns to canonical fields for a distributor monthly sales import. Headers come from many countries and languages and may not match known names. Use BOTH the header text and the sample values to decide. Return STRICT JSON only.

user: Canonical fields:
- "sku_col": product code / SKU identifier
- "units_col": units sold / quantity
- "revenue_col": sales revenue / amount / GMV / billing
- "price_col": unit price / average selling price
- "date_col": date or month of the sales record
- "stock_col": closing inventory / stock on hand

Columns (index, header, sample values): [{index, header, samples[≤4, 各≤40字符]}]

For each canonical field pick the single best matching column index, or omit it if none fits. Respond as JSON: { "<field>": { "colIndex": <number>, "confidence": <0..1> } }.
```
核心字段 = sku / units / revenue；任一弱才问模型。

### 2.5 report assistant — 章节写作（有据生成）

system：
```
You are a senior business analyst helping a distributor write a polished monthly performance report for their brand partner.

You always:
- Use numerical facts only from the user message under "VERIFIED DATA FOR THIS SECTION" and from the "DATA-DRIVEN QUESTIONS" block. Do not invent metrics, SKUs, dates, or percentages not supported there.
- Produce text that will be stored directly in the report: concise professional markdown (paragraphs and/or bullets). No preamble ("Here is…"), no meta commentary, no closing pleasantries.
- Follow the language requirement in the user message when present.
- ALWAYS use English for product names, even if writing in another language.
- ALWAYS format product names as "product_name size (variance)". Use the exact names as they appear in the VERIFIED DATA.

## DISTRIBUTOR CONTEXT (use for tone, seasonal awareness, and market understanding)
{companyName, country, city, region, salesChannels[], geographicMarkets[], companySize, tier, seasonalContext}
```
`seasonalContext` 由代码按国家 + 月份给（中国 1–2 月春节、美国 11 月黑五、日本 12 月 Oseibo、中东 3–4 月斋月……）。

user message 固定分块：`## Section context`（标题、类别、分析焦点）→ `## VERIFIED DATA FOR THIS SECTION (JSON — source of truth for numbers)` → `## DATA-DRIVEN QUESTIONS` → `## CURRENT SECTION DRAFT` → `## TASK`（按 action）→ 语言要求。

分析焦点按类别：sales（收入/销量/环比/驱动因素）、inventory（库存水位/变化/低库存或超库风险/补货）、orders（PO 管线/在途/履约时效）、targets（达成率/差距原因/补救计划）、market（渠道评论，只引用已验证数字）、custom（品牌指令："Check the VERIFIED DATA below for any relevant numbers. If the data supports the brand's request, cite those figures. If no relevant data exists, write qualitative content based on the distributor's notes. Do not invent numbers."）。

6 个 action 的 TASK：
- `draft_initial_content`：只用 VERIFIED DATA + QUESTIONS 写初稿，主动回答问题，只输出章节 markdown
- `help_answer_question`：把经销商的原始笔记（`"""…"""`，保留原意）写成报告语言，用验证数据落地，追加不重复
- `rewrite_section_content`：按用户指令重写
- `professionalize_section_content`：更专业、更分析、更简洁，改语法不改意思
- `shorten_section_content`：大幅缩短，可用 bullets，保留关键事实
- `suggest_more_for_section`：2–3 条可补充的点，只输出 bullet list

**重写时要加的**：一个 faithfulness 检查——输出里出现的每个数字都必须能在 VERIFIED DATA 里找到（或是它们的简单加减），否则拒绝。第一版没有。

---

## 3. 关键确定性规则（这些比 prompt 更值钱）

**`coerceNumeric(raw, header)`** — 单位换算在代码里，不信模型的算术：
- 会计负数 `(21,701.50)` 是负数（曾经丢符号，抖音投流亏损被存成同额盈利）
- 千分位/小数点：最后出现的分隔符是小数点（`1.234,56` 欧式、`1,234.56` 美式）；只有一个分隔符时，后面正好 3 位且无另一种分隔符 → 千分位（`1,234` 是一千，`1,5` 是一点五）
- 中文单位：万 ×1e4、亿 ×1e8；百分比 → 0–1 ratio；幂等（已归一的数直接过）
- Excel 日期序列号 → 真日期；月份标签（"9月"）不是数字

**合并单元格**：`gridFromSheet` 把合并区的值前向填充到整个区域（xlsx 只在左上角存值，其他为 null，模型和读值器都看不到）；`merges` 一起返回，向下合并的月度值（分销、出库总数量）折成一个 span 级事实，不是每周一份重复。

**合计行/列必须排除**（`sheetVocab.isTotalLabel`：合计/总计/Total/Subtotal…多语言 + 结构兜底）——存进去就双计。

**termResolution 优先级**（纯函数，无 DB 无模型）：1. 连续性——这个市场以前发过的表头保留它已有的 key（一个市场的历史绝不能因为改了个名字裂成两列）；2. 共享词汇表（免费、确定性）；3. 才是模型翻译。文件原文永远原样保留在旁边。

**periodColumns**：跨列摆的月份（"9月"|"10月"…）被还原成事实的 PERIOD，表的主题成为 METRIC，行标签保持 entity；期末 = 该月最后一天；原标题作为证据保留。刻意保守：重新定日期一个本来正确的数字比漏掉更糟。

**reportPeriod**：周报自己的日期在**文件名**里（`周报-8.31.xlsx`、`库存效期-26.6.8.xlsx`），sheet 里只有无年份的 `8月`/`第一周`。`YY.M.D`/`YYYY.M.D` 自含；`M.D` 借 picker 的年份，跨年规则：比 picker 月份晚的月属于上一年。

**去重/幂等**：重复导入 → 新事实 confirmed、旧的 superseded，活跃事实数不翻倍，投影的收入不翻倍。投影行用 `source_label = observation_rollup` 命名空间，重投影先删后写。

**信任门**：draft 事实不投影；confirm 后才投影；重复 confirm 是 no-op。

**scenario**：as-of 之后的月份是 plan，之前是 actual（MTD）；只有 actual 投影。

**异常检测 → 章节问题**（`reportDataSpine.ts`，9 条规则，severity warning/info）：环比大跌/大涨、库存低于 N 周覆盖、超库、目标差距、SKU 零销售、缺数据月……每条映射到一个 section category，作为 "DATA-DRIVEN QUESTIONS" 喂给写作助手。具体阈值看参考副本。

---

## 4. 测试规格（20 份 TS 测试的 case 名，作为 pytest 目标）

| 文件 | 保证的事 |
|---|---|
| channelSales | 5 个事实（天猫 ×2 周、京东 ×2 周、分销 ×1 月）；合计列排除；分销是月度 |
| inventoryExpiry | 每 SKU 两个事实（stock_on_hand + stock_value）；总计行排除；全部 draft |
| mergeExtraction | 分销/出库总数量/剩余库存/8月表头 前向填充；未合并的平台库存不动、只一个事实 |
| numberFormats | 月份标签不是数字；会计负数是数字；带币种前缀是数字；Excel 序列号是日期；Date 直通；ISO 字符串是日期 |
| aiNotes | 空 note 不成事实；note 是 narrative 事实、无 numericValue；markdown 原样；heading → source_ref.section |
| factReconcile | AI "渠道当 metric" 靠语义提示归 sales；确定性有的域 AI 同域丢弃；AI 'other' 表在确定性存在时丢弃 |
| termCanon | 平台出库 → platform_outbound、剩余库存 → remaining_inventory；原文保留；已有列保留 key |
| periodColumns | 月份列被识别并全部移动；9月→September、12月→December；期末=月末；每列现在测同一个东西；原标题保留 |
| periodInference | '2025-08' / 'April 2025' / 'April'(借年) / '8月' / '2025年8月' / 'Q2 2025' |
| reportPeriod | YY.M.D → 2026-06-08；8.31 → Aug 2025（跨年）；1.15 → Jan 2026（同年） |
| dedupe | 首次 5 confirmed；重导 5 新 + 5 superseded；活跃仍 5；投影收入 720 不是 1440 |
| projection | draft 不投影；confirm 翻 5+4；重复 confirm no-op；销售投成 1 行月度全渠道；库存按 SKU |
| scenario | as-of 后 → plan；前 → actual；只有 actual 投影；rollup 只算 actual |
| observationSeries | 渠道按工作簿顺序；12 个完整月；部分月排除在完整月求和之外 |
| datasetResolution | normalize 单数化/小写/去标点/保留 CJK；slugify snake_case 避开已用 key；同义词复用已有数据集 |
| embeddedImages | 找到贴入的图片、所在 sheet、锚点单元格、content type、原字节 |
| ingestionApi | extract 200 + runId；真实文件 ≥26 个事实；factTypes 含 sales 和 narrative |

---

## 5. 重写时的最小范围（Report specialist）

1. `xlsx → grid`（openpyxl，合并单元格前向填充）
2. `blockDetector` + `genericBlockParser`（通用，不写 SHAKEUP 专用 parser——那是一个客户的版式）
3. `aiTableExtractor`（§2.1 两段 prompt）+ `tablesToFacts`（代码读格子）+ `coerceNumeric`
4. `factReconcile` 的原则：确定性优先，AI 补位
5. `termCanon` 三级优先级 + `periodColumns`
6. 事实存 SQLite，draft → confirm 门 → 投影
7. 写作助手（§2.5）+ 数字 faithfulness 检查
8. eval：合成 30–50 份带版式漂移的多块表 + 人工标注 golden facts，测 cell 级 precision/recall
