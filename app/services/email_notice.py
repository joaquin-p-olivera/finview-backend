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


_CURRENCIES = {"UYU": ("PESOS (UYU)", "$"), "USD": ("DÓLARES (USD)", "US$")}


def _money(amount: float, currency: str) -> str:
    # 12.345,67 like the app, whatever the server locale
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = _CURRENCIES.get(currency, (currency, currency))[1]
    return f"{'-' if amount < 0 else ''}{symbol} {text}"


def _count(count: int) -> str:
    return f"{count} {'movimiento' if count == 1 else 'movimientos'}"


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


_LOGO_MARK = "apple-touch-icon.png"
_FOOTER = "Finview · Recibís este aviso porque tu casilla de importación procesó un estado de cuenta."
_FONT = "font-family:Arial,Helvetica,sans-serif"


def _html_page(
    heading: str,
    subtitle: str,
    body: str,
    link: tuple[str, str] | None,
    base: str | None,
) -> str:
    """Finview-styled HTML: dark header with the logo (the app's mark when the frontend URL is
    known), light body with `body` (already escaped HTML) and an optional (label, url) button.
    Table layout and inline styles, which is what email clients support."""
    logo = (
        f'<td style="padding-right:10px"><img src="{escape(base, quote=True)}/{_LOGO_MARK}" width="36" '
        'height="36" alt="" style="display:block;border-radius:8px"></td>'
        if base
        else ""
    )
    button = (
        f'<a href="{escape(link[1], quote=True)}" style="display:inline-block;background:#4f46e5;color:#ffffff;'
        f'padding:11px 20px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:bold">'
        f"{escape(link[0])}</a>"
        if link
        else ""
    )
    return (
        f'<div style="background:#f1f5f9;padding:24px 12px;{_FONT}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;margin:0 auto;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0">'
        f'<tr><td style="background:#020617;padding:18px 24px"><table role="presentation" cellpadding="0" '
        f'cellspacing="0"><tr>{logo}<td style="font-size:20px;font-weight:bold;color:#f8fafc">'
        'Fin<span style="color:#818cf8">view</span></td></tr></table></td></tr>'
        '<tr><td style="background:#ffffff;color:#1f2937;padding:24px">'
        f'<h2 style="margin:0 0 4px;font-size:20px;color:#0f172a">{escape(heading)}</h2>'
        f'<p style="margin:0 0 18px;color:#64748b;font-size:14px">{escape(subtitle)}</p>'
        f"{body}{button}</td></tr>"
        f'<tr><td style="background:#f8fafc;color:#94a3b8;font-size:12px;padding:14px 24px;'
        f'border-top:1px solid #e2e8f0">{_FOOTER}</td></tr></table></div>'
    )


def _totals_card(totals: list[tuple[str, float, int]]) -> str:
    rows = ""
    for index, (currency, total, count) in enumerate(totals):
        label = _CURRENCIES.get(currency, (currency, ""))[0]
        border = "" if index == len(totals) - 1 else "border-bottom:1px solid #e2e8f0;"
        rows += (
            f'<tr><td style="padding:12px 16px;{border}"><div style="font-size:12px;font-weight:bold;'
            f'color:#64748b;letter-spacing:0.5px">{escape(label)}</div>'
            f'<div style="font-size:12px;color:#64748b">{_count(count)}</div></td>'
            f'<td align="right" style="padding:12px 16px;{border}font-size:20px;font-weight:bold;color:#0f172a">'
            f"{escape(_money(total, currency))}</td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid #e2e8f0;border-radius:10px;margin-bottom:18px">{rows}</table>'
    )


def notify_statement_imported(user: User, stmt: Statement) -> None:
    """The statement was parsed and is waiting for review."""
    bank = stmt.bank_name or "Tu banco"
    subtitle = f"{bank} · {_period(stmt)}" if _period(stmt) else bank
    totals = statement_totals(stmt)
    note = "Revisalo y confirmalo para que entre en tus reportes."
    base = frontend_url()
    link = ("Revisar el estado", f"{base}/review/{stmt.id}") if base else None
    heading = "Llegó tu estado de cuenta"

    text_lines = [heading, subtitle] + [f"{_money(t, c)} ({_count(n)})" for c, t, n in totals] + [note]
    if link:
        text_lines.append(f"{link[0]}: {link[1]}")
    html = _html_page(
        heading,
        subtitle,
        f'{_totals_card(totals)}<p style="margin:0 0 18px;font-size:14px">{escape(note)}</p>',
        link,
        base,
    )
    _send(user, f"Finview - {heading}", "\n\n".join(text_lines), html)


def notify_import_failed(user: User, filename: str, reason: str) -> None:
    """A PDF from the inbox could not be imported (protected, unreadable...)."""
    base = frontend_url()
    link = ("Ver importaciones", f"{base}/email-import") if base else None
    heading = "No pudimos importar tu estado de cuenta"

    text_lines = [heading, filename, reason]
    if link:
        text_lines.append(f"{link[0]}: {link[1]}")
    html = _html_page(
        heading,
        filename,
        '<div style="background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:10px;'
        f'padding:12px 16px;font-size:14px;margin-bottom:18px">{escape(reason)}</div>',
        link,
        base,
    )
    _send(user, f"Finview - {heading}", "\n\n".join(text_lines), html)
