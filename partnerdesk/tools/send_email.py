"""Send an email over SMTP. Side-effect tool — only when the user asked to send.

Config (all from env, never shown to the model):
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM (defaults to SMTP_USER), SMTP_USE_TLS (default 1).

If SMTP_HOST is empty, the tool refuses rather than pretending to send.
Set EMAIL_DRY_RUN=1 to accept and log without talking to SMTP (tests / local).
"""

from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from typing import Any

from ..config import env

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "Send an email when the user explicitly asked to email someone. "
            "Do not invent recipients. Prefer a short clear subject and body."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text email body",
                },
                "cc": {
                    "type": "string",
                    "description": "Optional CC address",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
}


def _addr(raw: Any) -> str:
    return str(raw or "").strip()


def _valid(addr: str) -> bool:
    return bool(addr) and bool(_EMAIL.match(addr))


def send(
    *,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    from_addr: str | None = None,
    use_tls: bool | None = None,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    to_a, subj, text = _addr(to), str(subject or "").strip(), str(body or "").strip()
    cc_a = _addr(cc)
    if not _valid(to_a):
        raise ValueError("to must be a valid email address")
    if cc_a and not _valid(cc_a):
        raise ValueError("cc must be a valid email address")
    if not subj:
        raise ValueError("subject required")
    if not text:
        raise ValueError("body required")

    dry = env("EMAIL_DRY_RUN") in ("1", "true", "yes") if dry_run is None else dry_run
    smtp_host = (host if host is not None else env("SMTP_HOST")).strip()
    if dry:
        return {
            "sent": False,
            "dry_run": True,
            "to": to_a,
            "cc": cc_a or None,
            "subject": subj,
            "from": (from_addr or env("SMTP_FROM") or env("SMTP_USER") or "dry-run@localhost"),
        }
    if not smtp_host:
        raise RuntimeError("SMTP_HOST is not set — configure SMTP or EMAIL_DRY_RUN=1")

    smtp_port = int(port if port is not None else (env("SMTP_PORT") or "587"))
    smtp_user = (user if user is not None else env("SMTP_USER")).strip()
    smtp_password = password if password is not None else env("SMTP_PASSWORD")
    mail_from = (from_addr if from_addr is not None else env("SMTP_FROM") or smtp_user).strip()
    tls = use_tls if use_tls is not None else env("SMTP_USE_TLS", "1") not in ("0", "false", "no")
    if not mail_from:
        raise RuntimeError("SMTP_FROM or SMTP_USER required")

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = to_a
    if cc_a:
        msg["Cc"] = cc_a
    msg["Subject"] = subj
    msg.set_content(text)

    recipients = [to_a] + ([cc_a] if cc_a else [])
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as client:
        client.ehlo()
        if tls:
            client.starttls()
            client.ehlo()
        if smtp_user:
            client.login(smtp_user, smtp_password)
        client.send_message(msg, from_addr=mail_from, to_addrs=recipients)

    return {
        "sent": True,
        "dry_run": False,
        "to": to_a,
        "cc": cc_a or None,
        "subject": subj,
        "from": mail_from,
    }


def run(arguments: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    try:
        data = send(
            to=str(arguments.get("to") or ""),
            subject=str(arguments.get("subject") or ""),
            body=str(arguments.get("body") or ""),
            cc=str(arguments.get("cc") or ""),
        )
        return {"ok": True, "data": data, "error": None}
    except Exception as err:
        return {"ok": False, "data": None, "error": str(err)}
