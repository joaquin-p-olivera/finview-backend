"""Tells a user by email that the import inbox handled one of their statements.

Sent through Gmail's SMTP from the import inbox itself (same address and app
password as the IMAP side), to the user's own email, never to their `+TOKEN`
address, so the import never reads its own notices. Sending is best effort: a
failure is logged and never affects the import. Gmail allows ~500 sends a day
and some notices may land in spam.
"""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from ..config import get_settings
from ..models.statement import Statement
from ..models.user import User

logger = logging.getLogger(__name__)
settings = get_settings()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 20
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def frontend_url() -> str | None:
    """The app's public address: the first CORS origin that isn't localhost."""
    for origin in settings.cors_origins_list:
        if not any(host in origin for host in _LOCAL_HOSTS):
            return origin.rstrip("/")
    return None


def _send(user: User, subject: str, text: str, html: str) -> None:
    if not settings.EMAIL_IMPORT_ADDRESS or not settings.EMAIL_IMPORT_APP_PASSWORD:
        return
    if not getattr(user, "email_notifications", True) or not user.email:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Finview", settings.EMAIL_IMPORT_ADDRESS))
    msg["To"] = user.email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(settings.EMAIL_IMPORT_ADDRESS, settings.EMAIL_IMPORT_APP_PASSWORD)
            smtp.send_message(msg)
    except Exception:  # noqa: BLE001
        logger.exception("Could not send the import notice to user %s", user.id)


def _money(amount: float, currency: str) -> str:
    # 12.345,67 like the app, whatever the server locale
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if amount < 0 else ''}{currency} {text}"


def statement_totals(stmt: Statement) -> list[tuple[str, float, int]]:
    """(currency, total, transactions) per currency. The total is the bank's
    official one when the statement carries it, else the sum of its transactions."""
    data = stmt.raw_json or {}
    summary = data.get("summary") or {}
    sums: dict[str, list] = {}
    for tx in data.get("transactions") or []:
        currency = tx.get("currency") or "UYU"
        entry = sums.setdefault(currency, [0.0, 0])
        entry[0] += float(tx.get("amount") or 0)
        entry[1] += 1
    result = []
    currencies = set(sums) | {c for c in ("UYU", "USD") if summary.get(f"statement_total_{c.lower()}")}
    for currency in sorted(currencies, key=lambda c: (c != "UYU", c)):
        total, count = sums.get(currency, [0.0, 0])
        official = summary.get(f"statement_total_{currency.lower()}")
        result.append((currency, float(official) if official is not None else round(total, 2), count))
    return result


def _period(stmt: Statement) -> str | None:
    if stmt.period_start and stmt.period_end:
        return f"{stmt.period_start:%d/%m/%Y} al {stmt.period_end:%d/%m/%Y}"
    return None


def _page(heading: str, lines: list[str], link: tuple[str, str] | None) -> tuple[str, str]:
    """Plain text and HTML versions of a notice: a heading, paragraphs and an optional (label, url) link."""
    text = "\n\n".join([heading, *lines] + ([f"{link[0]}: {link[1]}"] if link else []))
    paragraphs = "".join(f'<p style="margin:0 0 12px">{escape(line)}</p>' for line in lines)
    button = (
        f'<p style="margin:20px 0"><a href="{escape(link[1], quote=True)}" '
        'style="background:#4f46e5;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">'
        f"{escape(link[0])}</a></p>"
        if link
        else ""
    )
    html = (
        '<div style="font-family:Arial,sans-serif;color:#1f2937;max-width:520px">'
        f'<h2 style="margin:0 0 16px">{escape(heading)}</h2>{paragraphs}{button}'
        "</div>"
    )
    return text, html


def notify_statement_imported(user: User, stmt: Statement) -> None:
    """The statement was parsed and is waiting for review."""
    bank = stmt.bank_name or "Tu banco"
    lines = [f"{bank} - {_period(stmt)}" if _period(stmt) else bank]
    for currency, total, count in statement_totals(stmt):
        lines.append(f"{_money(total, currency)} ({count} {'movimiento' if count == 1 else 'movimientos'})")
    lines.append("Revisalo y confirmalo para que entre en tus reportes.")
    base = frontend_url()
    link = ("Revisar el estado", f"{base}/review/{stmt.id}") if base else None
    heading = "Llegó tu estado de cuenta"
    text, html = _page(heading, lines, link)
    _send(user, f"{heading}: {bank}", text, html)


def notify_import_failed(user: User, filename: str, reason: str) -> None:
    """A PDF from the inbox could not be imported (protected, unreadable...)."""
    lines = [f"No pudimos importar {filename}.", reason]
    base = frontend_url()
    link = ("Ver importaciones", f"{base}/email-import") if base else None
    heading = "No pudimos importar tu estado de cuenta"
    text, html = _page(heading, lines, link)
    _send(user, heading, text, html)
