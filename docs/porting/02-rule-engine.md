# 移植规格：商业规则引擎

来源：`ruleEvaluator.ts`（462 行）+ `commercialRuleScope.ts` + `shared/ruleLifecycle.ts`。
纯函数、无 DB、确定性。code-check 只在"行都干净"（全部解析、定价、过 MOQ、subtotal>0）之后才跑规则——
所以一个不 ready 的订单永远不碰规则引擎。

## 规则的形状

```
CommercialRule
  id, name, rule_type, rule_config{}, severity, is_active
  partner_scope: all | specific(partner_ids[]) | region(partner_regions[])   ← 按 ship-to 国家匹配 region
  product_scope: all | specific(product_ids[]) | role(product_roles[]) | tag(product_tags[])
  effective_from / effective_until   ← 'YYYY-MM-DD'，闭区间，字符串比较
```

**severity**：`warn` | `approval_required` | `block`。只有 `block` 且 triggered 才拦单；其余 triggered 是 advisory，
订单照样提交，但要告诉用户"需要品牌审批，因为…"。

**生命周期**：`is_active=False` → paused；今天 < from → scheduled；今天 > until → expired；否则 live。
只有 live 的规则参与评估。`today` 用本地日历日，不用 UTC。

**scope 解析顺序**：先 partner scope（不命中 → 整条规则跳过，连 pass 结果都不产生），再 product scope 得到 items。

## 9 种规则

| rule_type | config | 计算 | triggered 条件 |
|---|---|---|---|
| `gift_pct_cap` | `max_pct`(默认 10) | scoped 里 role=gift 的行金额 / scoped 总金额 | gift_value > 0 且 pct > max |
| `sample_unit_cap` | `max_units`(默认 24) | scoped 里 role=sample 的 quantity 之和（**箱数**） | > max |
| `territory_restriction` | `territories[]` | ship-to 国家（大写） | 在列表里；列表空或无国家 → pass |
| `discount_approval` | `threshold_pct`(默认 15) | PO 级折扣百分比（金额折扣先换算成 %） | ≥ threshold |
| `discontinued_block` | — | scoped 里 discontinued=True 的行 | 存在 |
| `min_order_value` | `min` | scoped 行金额之和 | < min |
| `unit_cap` | `max_units`(默认 24), `unit`: unit\|case | unit 模式 = 箱数 × case_pack；case 模式（默认，兼容旧规则）= 箱数 | > max |
| `value_pct_cap` | `max_pct`(默认 10) | scoped 金额 / **整单**金额 | scoped > 0 且 pct > max |
| `block_all` | — | scoped 行 | 非空 |

行金额 `line_value` = line_total（>0 时）否则 quantity × (case_price 或 product.case_price)。

config 的 key 同时接受 snake_case 和 camelCase（`max_pct` / `maxPct`）——第一版 UI 两种都写过。

## 结果的形状

```
RuleEvaluationResult
  rule_id, rule_name, rule_type, severity, status: pass|triggered
  message                ← 人读的一句话（含数字）
  message_key, message_params   ← i18n 用；重写里保留 message 即可
  affected_items[]: {product_name, sku, quantity, value}
```

每条 live 且 partner-scope 命中的规则**都**产生一条结果（pass 也产生）——提交时整份结果作为快照存进
`po_rule_evaluations`，审批人看的是"当时评估了什么"。

## catalog-scoped 规则（`commercialRuleScope.ts`）

规则可以绑定一个 `catalog_id`，评估前展开为显式 scope：product_scope → specific（目录成员），partner_scope → all
（但目录不对这个 partner 可见时整条规则丢弃）；目录被删了 → 回退到规则自己存的 scope。
**重写里先不做**——fixture 没有目录概念；引擎保持纯函数，展开层将来加在外面。

## 与合同的连接点（新）

合同抽取里可映射成规则的字段：

| 合同字段 | 规则 | 语义 |
|---|---|---|
| `minimum_order_quantities[scope=per_order]`（金额） | `min_order_value{min}` block | 低于合同起订额拦单 |
| `minimum_order_quantities[per product]`（单位） | 写回 `products.moq_units` | code-check 的 MOQ 检查直接生效 |
| `excluded_countries[]` | `territory_restriction{territories}` block | 合同禁售地 |
| `commercial_discount_rules` 最高档折扣 | `discount_approval{threshold_pct = 最高档 + ε}` approval_required | 超过合同给的折扣要人批 |

映射在 `partnerdesk/contracts/to_rules.py`，每条规则 `name` 带 "from contract" 标记，可追溯。
