# 移植规格：意图分类体系

来源：`shared/agentTopics.ts`。平台拥有的、人工整理的固定菜单——品牌不能增删。
菜单作为**输入**传给分类器（schema 的 enum 按菜单生成），prompt 里不写死分类。

**原则**：列表要短。每多一个近义类别，小模型就多一种把本该在一起的对话拆开的方式；没有分支依赖的类别买不到任何东西。
`description` 必须同时写"什么时候适用"和"边界在哪"，邻居才不会互相渗透。

## 6 个 topic（原文）

| key | label | description | 建议动作 |
|---|---|---|---|
| `purchase_order` | Purchase order | Anything to do with an order: asking for prices, a quote or a discount; asking whether stock is available or what the lead time is; placing an order or changing quantities on one; asking about order status or confirmation; invoices, payment status and remittance for an order; shipping status, tracking, delivery problems, customs and freight; and returns, damaged or defective goods, and quality complaints about goods received. The whole life of an order, from the first price question to a return, is this one topic. | manual |
| `product_catalog` | Product & catalog | Requests for product specifications, catalog details, new-product information, or marketing assets and images. Informational only — the moment a price, a quantity or an order is involved it is Purchase order, not this. | auto |
| `sales_reporting` | Sales reporting | Submission of sell-through or sales data, monthly reports, or questions about reporting requirements. This is the distributor reporting on what they SOLD; ordering more is Purchase order. | task |
| `marketing_promotion` | Marketing & promotion | Campaigns, livestreams, promotions, co-marketing, or requests for promotional assets. | manual |
| `contract_terms` | Contract & terms | Distribution agreements, renewals, exclusivity, territory, or other contractual terms. Commercial terms attached to a specific order (its price, its payment terms) are Purchase order; this is the agreement that governs the relationship. | manual |
| `general_relationship` | General / relationship | Introductions, check-ins, thanks, and general correspondence that does not fit another topic. | manual |

另有 `unsure`：分类器返回空列表时的落点，不是菜单项。

## 历史：为什么折叠成 6 个

曾经有 pricing_discount / stock_availability / payment_invoicing / shipment_logistics / returns_quality 5 个平行标签，
后来折进 `purchase_order`：它们是一张订单生命周期里的阶段，不是并列类别。拆开的实际代价——新线程问
"上周的单什么时候发货"分到 shipment_logistics，PO agent 不触发。旧数据保留旧 key，通过别名映射显示。

## 重写里的路由表（标签 6 个，specialist 3 个 + 兜底）

| topic | specialist | 现状 |
|---|---|---|
| `purchase_order` | PO | 完整 |
| `contract_terms`（带附件） | Contract | 抽取在 `legacy/contract_ai`，接口先留 stub |
| `sales_reporting`（带附件） | Report | stub |
| `product_catalog` / `marketing_promotion` / `general_relationship` / 空 | **abstain** — 明说这里帮不了、留给人 | 有 |

**规则**
1. 会话里已有活跃 PO（gathering / confirm）→ 任何消息都进 PO，不经 router（"用保湿水那款"会被分到别处，但它是在继续这张单）。
2. `purchase_order` 的置信度低于品牌设定的 floor → 当作没命中。只有这一个 topic 有 floor——一个不确定的订单不该被当订单处理。
3. 多标签同时命中 → PO 优先（它有状态机）；contract / report 只在带附件时接手。
4. 已提交的会话：消息进 router；receipt / payment / damage 分类器只在这时跑。
5. chat 版输入 = 消息文本 + 附件名/类型（一个 .xlsx 是 sales_reporting 的强证据）+ 最近几轮。
