"""销售报告：一份聊天草稿，以及把表格行变成事实。

`spreadsheet` 把一份整齐的导出读成草稿事实。`turn` 根据这些事实和订单写聊天草稿。
聊天草稿要等用户确认，才写入 narrative_reports。
"""

from .turn import ReportDraft

__all__ = ["ReportDraft"]
