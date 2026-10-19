"""Sales reports: a chat draft, and spreadsheet rows turned into facts.

`spreadsheet` reads a tidy export into draft facts. `turn` writes a chat draft
from those facts and from orders. The chat draft is not stored as a sales report.
"""

from .turn import ReportDraft, draft_report

__all__ = ["ReportDraft", "draft_report"]
