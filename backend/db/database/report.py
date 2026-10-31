"""Sales reports, the facts extracted from them, and confirmed narrative write-ups."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from ...schemas import NarrativeReport, ReportFact, SalesReport
from .. import orm
from .support import _row_dict, utcnow


class ReportMixin:
    def save_report(
        self, report: SalesReport, *, method: str, model: str | None, raw: dict[str, Any], skipped: list[str],
        facts: list[ReportFact],
    ) -> str:
        """One transaction: the report, the extraction run that read it, and its draft facts."""
        ts, xid = utcnow(), uuid.uuid4().hex
        with self.tx() as s:
            self._upsert(s, orm.SalesReport, report.id, report.model_dump(exclude={"created_at", "updated_at"}))
            s.flush()
            s.add(orm.ReportExtraction(id=xid, report_id=report.id, method=method, model=model, raw=raw, skipped=list(skipped), extracted_at=ts))
            s.add_all(
                orm.ReportFact(**{**f.model_dump(exclude={"id", "created_at", "report_id", "extraction_id"}), "report_id": report.id, "extraction_id": xid, "created_at": ts})
                for f in facts
            )
        return report.id

    def reports(self, partnership_id: str) -> list[SalesReport]:
        with self.tx() as s:
            rows = s.scalars(
                select(orm.SalesReport).where(orm.SalesReport.partnership_id == partnership_id)
                .order_by(orm.SalesReport.period_start.desc(), orm.SalesReport.created_at.desc())
            ).all()
            return [SalesReport(**_row_dict(r)) for r in rows]

    def report(self, id: str) -> SalesReport | None:
        with self.tx() as s:
            row = s.get(orm.SalesReport, id)
            return SalesReport(**_row_dict(row)) if row else None

    def report_facts(self, report_id: str, status: str | None = None) -> list[ReportFact]:
        with self.tx() as s:
            q = select(orm.ReportFact).where(orm.ReportFact.report_id == report_id)
            if status:
                q = q.where(orm.ReportFact.status == status)
            return [ReportFact(**_row_dict(r)) for r in s.scalars(q.order_by(orm.ReportFact.id)).all()]

    def report_extractions(self, report_id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.ReportExtraction).where(orm.ReportExtraction.report_id == report_id).order_by(orm.ReportExtraction.extracted_at)).all()
            return [_row_dict(r) for r in rows]

    def confirm_report(self, report_id: str) -> dict[str, int]:
        """The gate for a report's numbers. Drafts become confirmed; a confirmed fact from
        another report of the same partnership measuring the same thing for the same period
        is superseded — a re-sent month replaces, it never doubles. Repeat calls are no-ops."""
        ts = utcnow()
        with self.tx() as s:
            rep = s.get(orm.SalesReport, report_id)
            if rep is None:
                raise KeyError(f"report {report_id} not found")
            drafts = s.scalars(select(orm.ReportFact).where(orm.ReportFact.report_id == report_id, orm.ReportFact.status == "draft")).all()
            keys = {ReportFact(**_row_dict(f)).identity() for f in drafts}
            superseded = 0
            if keys:
                others = s.scalars(
                    select(orm.ReportFact).join(orm.SalesReport, orm.ReportFact.report_id == orm.SalesReport.id).where(
                        orm.SalesReport.partnership_id == rep.partnership_id, orm.ReportFact.report_id != report_id,
                        orm.ReportFact.status == "confirmed",
                    )
                ).all()
                for f in others:
                    if ReportFact(**_row_dict(f)).identity() in keys:
                        f.status, superseded = "superseded", superseded + 1
            for f in drafts:
                f.status = "confirmed"
            rep.status, rep.confirmed_at, rep.updated_at = "confirmed", rep.confirmed_at or ts, ts
            return {"confirmed": len(drafts), "superseded": superseded}

    def confirmed_facts(self, partnership_id: str, fact_type: str | None = None) -> list[ReportFact]:
        """The live numbers for a partnership — confirmed and not superseded, across reports."""
        with self.tx() as s:
            q = (
                select(orm.ReportFact).join(orm.SalesReport, orm.ReportFact.report_id == orm.SalesReport.id)
                .where(orm.SalesReport.partnership_id == partnership_id, orm.ReportFact.status == "confirmed")
            )
            if fact_type:
                q = q.where(orm.ReportFact.fact_type == fact_type)
            return [ReportFact(**_row_dict(r)) for r in s.scalars(q.order_by(orm.ReportFact.period_start, orm.ReportFact.id)).all()]

    def delete_report(self, id: str) -> None:
        with self.tx() as s:
            row = s.get(orm.SalesReport, id)
            if row is not None:
                s.delete(row)

    def save_narrative_report(self, report: NarrativeReport) -> str:
        """Insert a confirmed write-up, or replace the text of one already confirmed."""
        ts = utcnow()
        with self.tx() as s:
            row = s.get(orm.NarrativeReport, report.id)
            if row is None:
                s.add(orm.NarrativeReport(
                    id=report.id, brand_id=report.brand_id, partnership_id=report.partnership_id,
                    session_id=report.session_id, title=report.title, body=report.body, charts=list(report.charts),
                    status="confirmed", confirmed_at=ts, created_at=ts, updated_at=ts,
                ))
            else:
                row.title, row.body, row.charts = report.title, report.body, list(report.charts)
                row.status, row.updated_at = "confirmed", ts
                if row.confirmed_at is None:
                    row.confirmed_at = ts
        return report.id

    def narrative_reports(self, partnership_id: str) -> list[NarrativeReport]:
        """Confirmed chat write-ups, the one touched last first."""
        with self.tx() as s:
            rows = s.scalars(
                select(orm.NarrativeReport).where(orm.NarrativeReport.partnership_id == partnership_id)
                .order_by(orm.NarrativeReport.updated_at.desc(), orm.NarrativeReport.created_at.desc())
            ).all()
            return [NarrativeReport(**_row_dict(r)) for r in rows]

    def narrative_report(self, id: str) -> NarrativeReport | None:
        with self.tx() as s:
            row = s.get(orm.NarrativeReport, id)
            return NarrativeReport(**_row_dict(row)) if row else None
