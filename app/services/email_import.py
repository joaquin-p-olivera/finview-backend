"""Imports statements from the import inbox: a Gmail account (read over IMAP with
an app password) that users forward their bank emails to.

Each user forwards to their own address, `local+TOKEN@domain` (Gmail delivers
plus-addressed mail to the same inbox), so the token in the recipient tells
whose email it is. PDF attachments are parsed like a web upload and the
statement waits for review; Gmail's forwarding confirmation is kept so the user
can see the code in the app. Every email sent to a `+TOKEN` address is deleted
once handled (the PDF is never stored); any other email in the inbox is left
untouched and unread, so the inbox can also be a personal account.
"""

import email
import hashlib
import imaplib
import logging
import re
import threading
from datetime import date, timedelta
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import parseaddr

from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models.bank_pdf_password import BankPdfPassword
from ..models.email_import import EmailImport
from ..models.statement import Statement
from ..models.user import User
from . import secret_box, statement_import, statement_parser

logger = logging.getLogger(__name__)
settings = get_settings()

GMAIL_FORWARDING_SENDER = "forwarding-noreply@google.com"
_RECIPIENT_HEADERS = ("Delivered-To", "X-Original-To", "To", "Cc")
# Only recent mail is looked at; the cron runs every hour, so nothing older is pending
SEARCH_DAYS = 14
_CONFIRMATION_CODE = re.compile(r"\(#(\d+)\)|(?:code|código)\D{0,5}(\d{6,})", re.IGNORECASE)
_CONFIRMATION_LINK = re.compile(r"https://mail(?:-settings)?\.google\.com/mail/[^\s\"'<>]+")

PASSWORD_PROTECTED_MESSAGE = (
    "El PDF está protegido con contraseña: guardala en Importar por mail (por banco) o subilo a mano desde la web."
)
PASSWORD_NOT_ACCEPTED_MESSAGE = (
    "Ninguna de las contraseñas guardadas abre este PDF: revisalas en Importar por mail o subilo a mano desde la web."
)

_run_lock = threading.Lock()


def is_configured() -> bool:
    return bool(settings.EMAIL_IMPORT_ADDRESS and settings.EMAIL_IMPORT_APP_PASSWORD)


def import_address(token: str) -> str:
    local, _, domain = (settings.EMAIL_IMPORT_ADDRESS or "").partition("@")
    return f"{local}+{token}@{domain}"


def _token_pattern() -> re.Pattern:
    local, _, domain = (settings.EMAIL_IMPORT_ADDRESS or "").lower().partition("@")
    # Gmail ignores dots in the local part, so a forward to a dotted variant still arrives
    local_re = r"\.?".join(re.escape(ch) for ch in local.replace(".", ""))
    return re.compile(rf"{local_re}\+([a-z0-9]+)@{re.escape(domain)}", re.IGNORECASE)


def _recipient_token(msg: EmailMessage) -> str | None:
    pattern = _token_pattern()
    for header in _RECIPIENT_HEADERS:
        for value in msg.get_all(header) or []:
            match = pattern.search(str(value))
            if match:
                return match.group(1).lower()
    return None


def _text_body(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    try:
        return body.get_content() if body else ""
    except (LookupError, ValueError):
        return ""


def _pdf_attachments(msg: EmailMessage) -> list[tuple[str, bytes]]:
    pdfs = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename() or ""
        if part.get_content_type() != "application/pdf" and not filename.lower().endswith(".pdf"):
            continue
        data = part.get_payload(decode=True) or b""
        if data.startswith(b"%PDF"):
            pdfs.append((filename or "estado-de-cuenta.pdf", data))
    return pdfs


def _confirmation_details(msg: EmailMessage) -> dict:
    text = f"{msg.get('Subject', '')}\n{_text_body(msg)}"
    code_match = _CONFIRMATION_CODE.search(text)
    link_match = _CONFIRMATION_LINK.search(text)
    return {
        "code": next((g for g in code_match.groups() if g), None) if code_match else None,
        "link": link_match.group(0) if link_match else None,
    }


def _already_handled(db: Session, user_id: str, message_id: str, filename: str) -> bool:
    return (
        db.query(EmailImport)
        .filter(
            EmailImport.user_id == user_id,
            EmailImport.message_id == message_id,
            EmailImport.filename == filename,
        )
        .first()
        is not None
    )


def _decrypt_with_saved_passwords(
    db: Session, user_id: str, data: bytes, hints: list[str]
) -> bytes:
    """Opens a protected PDF with the user's saved bank passwords. Banks whose
    name appears in the sender, subject or filename are tried first; the rest
    follow, since the bank is not always recognizable from the email."""
    rows = db.query(BankPdfPassword).filter(BankPdfPassword.user_id == user_id).all()
    if not rows:
        raise statement_parser.PdfPasswordError(PASSWORD_PROTECTED_MESSAGE)
    haystack = " ".join(hints).lower()
    rows.sort(key=lambda row: row.bank_key not in haystack)
    for row in rows:
        password = secret_box.decrypt(row.password_encrypted)
        if not password:
            continue
        try:
            return statement_parser.decrypt_pdf(data, password)
        except statement_parser.PdfPasswordError:
            continue
    raise statement_parser.PdfPasswordError(PASSWORD_NOT_ACCEPTED_MESSAGE)


def _import_pdf(db: Session, user: User, base: dict, filename: str, data: bytes) -> None:
    user_id = str(user.id)
    if _already_handled(db, user_id, base["message_id"], filename):
        return
    row = EmailImport(user_id=user_id, kind="statement", filename=filename[:255], status="processing", **base)

    if len(data) > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        row.status = "error"
        row.error_message = f"El PDF excede el tamaño máximo de {settings.MAX_FILE_SIZE_MB} MB."
        db.add(row)
        db.commit()
        return

    file_hash = hashlib.sha256(data).hexdigest()
    existing = (
        db.query(Statement).filter(Statement.user_id == user_id, Statement.file_hash == file_hash).first()
    )
    if existing and existing.status == "error":
        db.delete(existing)
    elif existing:
        row.status = "duplicate"
        row.statement_id = existing.id
        db.add(row)
        db.commit()
        return

    try:
        try:
            pdf_bytes = statement_parser.decrypt_pdf(data, None)
        except statement_parser.PdfPasswordError:
            pdf_bytes = _decrypt_with_saved_passwords(
                db, user_id, data, [filename, base.get("sender") or "", base.get("subject") or ""]
            )
    except statement_parser.PdfPasswordError as exc:
        row.status = "error"
        row.error_message = str(exc)
        db.add(row)
        db.commit()
        return
    except statement_parser.StatementParseError as exc:
        row.status = "error"
        row.error_message = str(exc)
        db.add(row)
        db.commit()
        return

    stmt = Statement(
        user_id=user_id, filename=filename[:255], file_hash=file_hash, status="processing", source="email"
    )
    db.add(stmt)
    db.flush()
    row.statement_id = stmt.id
    db.add(row)
    db.commit()

    statement_import.parse_into_statement(stmt.id, user_id, pdf_bytes)

    db.refresh(stmt)
    row.status = "error" if stmt.status == "error" else "done"
    row.error_message = stmt.error_message if stmt.status == "error" else None
    db.commit()


def process_message(db: Session, raw: bytes) -> None:
    """Handles one email sent to a `+TOKEN` address. Safe to call again for the
    same email."""
    msg = email.message_from_bytes(raw, policy=default_policy)
    token = _recipient_token(msg)
    user = db.query(User).filter(User.import_token == token).first() if token else None
    if not user:
        logger.info("Import email for an unknown token; discarding it")
        return

    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    base = {
        "message_id": str(msg.get("Message-ID") or hashlib.sha256(raw).hexdigest())[:255],
        "sender": sender[:255] or None,
        "subject": str(msg.get("Subject") or "")[:500] or None,
    }

    if sender == GMAIL_FORWARDING_SENDER:
        if not _already_handled(db, str(user.id), base["message_id"], ""):
            db.add(
                EmailImport(
                    user_id=str(user.id),
                    kind="gmail_confirmation",
                    status="done",
                    details=_confirmation_details(msg),
                    **base,
                )
            )
            db.commit()
        return

    pdfs = _pdf_attachments(msg)
    if not pdfs:
        if not _already_handled(db, str(user.id), base["message_id"], ""):
            db.add(
                EmailImport(
                    user_id=str(user.id),
                    kind="statement",
                    status="error",
                    error_message="El mail no traía un PDF adjunto.",
                    **base,
                )
            )
            db.commit()
        return

    for filename, data in pdfs:
        _import_pdf(db, user, base, filename, data)


def _trash_folder(imap: imaplib.IMAP4) -> str | None:
    """Gmail's trash name depends on the account language; it is flagged \\Trash."""
    status, folders = imap.list()
    if status != "OK":
        return None
    for line in folders or []:
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        if "\\Trash" in text:
            match = re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)\s*$", text)
            return match.group(1) if match else None
    return None


def _delete(imap: imaplib.IMAP4, uid: bytes, trash: str | None) -> None:
    # In Gmail, flagging \Deleted in INBOX only archives; the copy to the trash deletes it
    if trash:
        imap.uid("COPY", uid, f'"{trash}"')
    imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")


def _connect() -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(settings.EMAIL_IMPORT_IMAP_HOST)


def _fetch(imap: imaplib.IMAP4, uid: bytes, item: str) -> bytes | None:
    # PEEK keeps the email unread, which matters for the ones that aren't ours
    status, fetched = imap.uid("FETCH", uid, f"({item})")
    if status != "OK":
        return None
    return next((part[1] for part in fetched or [] if isinstance(part, tuple)), None)


def _is_import_email(headers: bytes) -> bool:
    return _recipient_token(email.message_from_bytes(headers, policy=default_policy)) is not None


def run_import() -> int:
    """Processes the recent emails sent to a `+TOKEN` address. Returns how many
    it handled; 0 when another run is still going."""
    if not is_configured():
        return 0
    if not _run_lock.acquire(blocking=False):
        logger.info("Email import already running; skipping")
        return 0
    try:
        imap = _connect()
        try:
            imap.login(settings.EMAIL_IMPORT_ADDRESS, settings.EMAIL_IMPORT_APP_PASSWORD)
            imap.select("INBOX")
            since = (date.today() - timedelta(days=SEARCH_DAYS)).strftime("%d-%b-%Y")
            status, data = imap.uid("SEARCH", None, "SINCE", since)
            uids = data[0].split() if status == "OK" and data and data[0] else []
            if not uids:
                return 0
            trash = _trash_folder(imap)
            handled = 0
            for uid in uids:
                headers = _fetch(imap, uid, "BODY.PEEK[HEADER]")
                if headers is None or not _is_import_email(headers):
                    continue
                raw = _fetch(imap, uid, "BODY.PEEK[]")
                if raw is None:
                    continue
                db = SessionLocal()
                try:
                    process_message(db, raw)
                except Exception:  # noqa: BLE001
                    # left in the inbox: the next run tries again
                    logger.exception("Error importing email %s", uid)
                    db.rollback()
                    continue
                finally:
                    db.close()
                _delete(imap, uid, trash)
                handled += 1
            imap.expunge()
            return handled
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
    finally:
        _run_lock.release()
