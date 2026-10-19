"""Chat sales-report draft: questions, public context, charts, one prose pass.

Spreadsheet ingest stays in `partnerdesk.reports`. This package does not write
`sales_reports`; the draft is for a person to read and correct in the chat.
"""

from .turn import ReportDraft, draft_report

__all__ = ["ReportDraft", "draft_report"]
