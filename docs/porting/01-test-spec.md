# 移植规格：第一版的测试用例

第一版 Python 侧没有测试；TS 侧这几份就是 slots / code-check / hash / claims 的验收标准。
每条一行，右列是它在 `tests/` 里的落点。

## code-check（提交门）— `tests/test_check.py`

目录 fixture：FO-100 鱼油（12/箱，$100/箱，MOQ 100 单位 → 9 箱，5 箱/层）、SR-200 Radiance Serum（$50）、
SR-201 Radiance Serum Plus（$60）、NP-300 无价。

**必须拦（ready=False）**
- 没有行 → `no_items`
- 目录里没有的产品 → `unresolved_reference`，行上无 product_id
- "the serum" 同时匹配两个 → `ambiguous_product`，candidates=2，不同时标 unresolved
- 8 箱对 9 箱 MOQ → `below_moq`，moq_cases 从单位向上取整（100/12 → 9）
- 无价产品 → `missing_price`
- 0 箱 → `zero_total`
- 折扣没有被问过（discount=None）→ `discount_unaddressed`（沉默不等于同意跳过）
- 一行坏了整单不 ready
- 999,999,999 箱 → `implausible_quantity`（曾经：ready、~$100bn、零 gap）；恰好等于上限仍通过

**必须放（ready=True）**
- 模糊引用 "fish oil" 解析到唯一 SKU，subtotal = 单价 × 箱数，gaps 为空
- 不满层（12 箱 / 5 箱每层）不拦，但 partial_layer=True、cases_to_full_layer=3

**精确 SKU / 名称**
- SKU 大小写不敏感
- 上一轮解析出的 sku 跳过搜索
- 输入产品全名 "Radiance Serum" 解析到它自己，不是更长的兄弟 "Radiance Serum Plus"（消歧按钮就是这么回的）
- 用户说的全名/SKU 胜过模型塞的 sku（"Radiance Serum" + sku=SR-201 → SR-200；"FO-100" + sku=SR-200 → FO-100）
- 模糊引用 + 模型猜的 sku（"the serum" + SR-200）→ 仍然 ambiguous，给出真实选择

**LLM 兜底解析器（目录才是权威）**
- 返回不存在的 SKU → 丢弃，仍 unresolved，永不 ready
- 返回一个有效 SKU → 解析并定价
- 返回两个 → ambiguous（问，不猜）
- 解析器抛异常 → 保持 unresolved，绝不自动 ready

## slot merge — `tests/test_slots.py`
- 模型省略的字段保持已定值（etaDate / paymentTerms / incoterms / shippingMethod / discount / notes）
- 明确改变的字段覆盖旧值；改成 discount=none 是"改变"不是"缺席"
- 改一个字段不动其他字段
- lineItems 是唯一可以缩短的字段（模型每轮返回完整列表）；清空行不清空条款

## 确认 hash — `tests/test_draft_hash.py`
**同一订单两次 hash 相同。**
**必须变**：单价、数量、加一行、换 SKU、加折扣、付款条款、incoterms、运输方式、ETA、notes、币种。
**必须不变**：满层提示重算、搜索 candidates、用户对产品的措辞（reference）、订单仍满足的 MOQ 变化。
（ETA 在 fixture 里 pin 死，否则过午夜 hash 就变。）

## 提交幂等 — `tests/test_submit.py`（第一版在 `public-po-confirm.ts` 里，没有单测）
- 先烧 token 再建单：并发两次 confirm 只产生一张 PO，输家得到 `used`
- token 过期 → `expired`
- 点击时重跑 check，hash 不一致 → `changed`，不建单
- 已提交的会话再 confirm → `done`（不是 expired）

## 下单后 claim（纯逻辑部分）— `tests/test_claims.py`
receipt（`inspectionOkOutcome`）：
- 100% 预付 → completed；50/50 → remaining 为余款、final_payment；net_30 → 30 天后到期；旧 key `net60` 等于 60 天；credit 超过总额 → refund
payment（`pickPrepaymentClaimTarget`）：
- 会话里的 PO 优先；只有一张预付 PO 时唯一；多张且无会话 → None（不猜是哪张）；`null` 不是结果
claim line match：
- PO 上只有一个产品就够；SKU 匹配大小写不敏感；产品名包含引用；数量必填；多行不猜；数量取整、描述 trim；垃圾 type/qty 丢弃

## 意图分类 — `tests/test_router.py`（第一版 `topicClassifier.test.ts` 是 wrapper 测试，这里改为路由测试）
- 活跃 PO 会话里的任何消息 → PO（不经 router）
- `purchase_order` 置信度低于 floor → 不路由到 PO
- 空 topics → abstain
- 多标签同时命中 → 按优先级分发（PO 先）
