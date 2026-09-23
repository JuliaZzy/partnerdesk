# 移植规格：数据层最小字段集

> 2026-09-20 起数据层改为 SQLAlchemy + Alembic 的真实表（`partnerdesk/db/orm.py`），不再是 JSON 文档表；
> 合同抽取全量入库并预填下一单折扣，见 README「数据层」。下面的字段清单仍然是每张表的来源说明。

第一版 PO agent 触到 7 张表。这里只列 agent **实际读写的列**——重写里每张是一个 pydantic model，
数据从 `fixtures/*.json` 种进数据库。产品管理、目录编辑、导入向导等**功能**不搬。

## 读

### `products` → `Product`
| 列 | 用途 |
|---|---|
| `id`, `sku` | 解析用；模型只回显 SKU（UUID 会被抄错），所以索引按 lowercase SKU |
| `product_name_en`, `native_name` | 搜索 haystack + manifest 显示 + 精确名匹配 |
| `category`, `subcategory`, `variant`, `form`, `unit_size`, `net_content`, `description_en` | 搜索 haystack；size/variant 进 manifest 让模型能区分 "100ml" / "单支" |
| `case_pack` | MOQ 换算（单位 → 箱）、unit_cap 规则 |
| `case_price` | 定价；null → `missing_price` |
| `moq_units` | MOQ（单位），`ceil(moq_units / case_pack)` 得箱数 |
| `cases_per_layer` | 满层 nudge（不拦单） |
| `commercial_role` (gift / sample / …), `discontinued`, `policy_tags[]`, `restricted_territories[]` | 规则引擎的 product scope |
| `layers_per_pallet`, `cases_per_pallet`, `case_weight` | 只进 manifest（回答装箱问题），不参与任何判断 |

manifest 每行：`SKU ▸ name (native) ▸ size variant ▸ pack/case, $price/case, MOQ Nu, category ▸ logistics`。
名字要 sanitize（去掉 `[FILE]` / `[PO]` / `|||`）——品牌可控文本不能注入信号行。上限 400 行进 prompt，但**全部**产品都建索引。

### `product_catalogs` + `distributor_pricing`
第一版按合作分配的目录过滤可见产品、隐藏个别产品的价格。**重写先不做**：一个合作看全目录。
接口留着——`Catalog.for_partnership(partnership_id)` 现在返回全部。

### `commercial_rules` → `CommercialRule`
见 `02-rule-engine.md`。列：`id, name, rule_type, rule_config(json), severity, is_active, partner_scope, partner_ids[], partner_regions[], product_scope, product_ids[], product_roles[], product_tags[], effective_from, effective_until`。

### `brand_agent_settings` → `AgentSettings`
| 列 | 默认 | 用途 |
|---|---|---|
| `payment_terms` | `50_50` | draft 表里的默认条款（用户没说时） |
| `incoterms` | `CIF` | 同上 |
| `shipping_method` | `sea` | 同上 |
| `eta_days` | 60 | 默认 ETA = 今天 + N |
| `operating_context` | null | 品牌自由文本，进两次 prompt（≤2000 字符） |
| `min_po_confidence` | 0 | router 里 purchase_order 的置信度下限 |
| `tone` | 默认语气 | 回复的 persona（第一版从 outreach settings 拿） |

"没有行"和"行里全 NULL"必须解析成同一个结果；条款值出库时校验在 enum 里，不信任存储。

### `brand_distributor_partnerships` + `brand_profiles` + `distributor_profiles` → `Partnership`
| 字段 | 用途 |
|---|---|
| `id`, `brand_id`, `distributor_id` | 身份 |
| `ship_to_country`（partnership 上的，否则 distributor.country） | territory 规则、region scope |
| `currency`（brand.preferred_currency，默认 USD） | 订单币种，进 hash |
| `distributor_name`, `brand_name` | 显示 |

## 写

### `purchase_orders` → `PurchaseOrder`
`id, po_number (PO-YYYYMMDD-XXXXX), brand_id, distributor_id, partnership_id, status, currency, payment_terms, incoterms, shipping_method, eta_date, ship_to_country, notes, discount_amount, discount_percentage, subtotal_amount, total_amount, submitted_at, submit_cycle_version`

status 流转（agent 触到的）：`draft → submitted`（提交）→ … → `shipped → received → completed / awaiting_payment`（claims）。

### `purchase_order_items`
`po_id, product_id, sku, product_name, quantity(箱), case_price, line_total, case_pack` — 只写已解析、有价、qty>0 的行（code-check 保证）。

### `po_rule_evaluations`
`po_id, evaluation_context='submission', submit_cycle_version, snapshot(json = 全部 RuleEvaluationResult)` — 审批人看"当时评估了什么"。

### 会话（第一版 chat 存浏览器，email 存 `agent_po_threads`）→ `Session`
| 字段 | 用途 |
|---|---|
| `id`, `partnership_id` | |
| `slots` (json) | 逐轮 merge |
| `history` (json) | 最近 8 轮文本（图片不存） |
| `stage` | gathering / confirm / submitted |
| `confirm_token`, `confirm_hash`, `confirm_expires_at`, `confirmed_at` | 确认门；token 烧掉 = 置 NULL |
| `po_id` | 提交后 |
| `specialist` | router 分发结果：po / contract / report / null |

**决定**：重写把会话放服务端（SQLite），而不是像第一版 chat 那样每轮 POST slots 回来——确认门需要服务端持有 hash 和 token。
