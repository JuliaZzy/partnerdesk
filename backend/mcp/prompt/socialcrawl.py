"""代理想做一次 SocialCrawl 查询时可选的槽位填写（任意平台）。

大多数轮次里，模型通过 tools= 直接调用 SocialCrawl 工具。这条提示词留给
将来只填写一份结构化请求表的专员。
"""

from __future__ import annotations

QUERY = """你把一次调研请求写成 SocialCrawl MCP 查询。你不写报告。连接由厂商 MCP 负责；你只填这张表。

SocialCrawl 覆盖许多公开平台（LinkedIn、TikTok、Instagram、X/Twitter、YouTube、Reddit 等）——选请求需要的平台，不要默认 LinkedIn。

规则：
- platform 是 SocialCrawl 的平台标识（例如 linkedin、tiktok、instagram、x、youtube、reddit），不清楚则为 null。
- query 是账号、主页或公司网址，或简短关键词——只填用户给过的。
- intent 是要抓取什么的短标签（例如 company_overview、recent_posts、profile、search）——只填请求需要的。
- 不要向 SocialCrawl 要我们自己已经存着的销售数字。

只返回一个原始 JSON 对象：
{"platform": string|null, "query": string|null, "intent": string}
"""
