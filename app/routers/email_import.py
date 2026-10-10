import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import get_current_user
from ..models.email_import import EmailImport
from ..models.statement import Statement
from ..models.user import User
from ..schemas.email_import import EmailImportItem, EmailImportOverview, GmailConfirmation
from ..services import email_import
from .statements import processing_timed_out


router = APIRouter(prefix="/api/v1/email-import", tags=["email-import"])

DbDep = Annotated[Session, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
settings = get_settings()

CONFIRMATION_MAX_AGE = timedelta(days=7)
RECENT_IMPORTS = 20


def _new_token(db: Session, user: User) -> str:
    user.import_token = secrets.token_hex(12)
    db.commit()
    return user.import_token


def _item(row: EmailImport, stmt: Statement | None) -> EmailImportItem:
    item = EmailImportItem(
        id=row.id,
        status=row.status,
        filename=row.filename,
        sender=row.sender,
        subject=row.subject,
        error_message=row.error_message,
        created_at=row.created_at,
    )
    if stmt:
        item.statement_id = stmt.id
        item.statement_status = stmt.status
        item.bank_name = stmt.bank_name
        item.period_start = stmt.period_start
        item.period_end = stmt.period_end
        if row.status == "processing":
            if processing_timed_out(stmt):
                item.status = "error"
                item.error_message = "El procesamiento se interrumpió. Reenviá el mail o subí el PDF desde la web."
            elif stmt.status != "processing":
                item.status = "error" if stmt.status == "error" else "done"
                item.error_message = stmt.error_message if stmt.status == "error" else None
    elif row.status == "processing":
        item.status = "error"
        item.error_message = "El procesamiento se interrumpió. Reenviá el mail o subí el PDF desde la web."
    return item


def _overview(db: Session, user: User) -> EmailImportOverview:
    if not email_import.is_configured():
        return EmailImportOverview(enabled=False, imports=[])

    user_id = str(user.id)
    token = user.import_token or _new_token(db, user)

    confirmation = (
        db.query(EmailImport)
        .filter(
            EmailImport.user_id == user_id,
            EmailImport.kind == "gmail_confirmation",
            EmailImport.created_at >= datetime.now(timezone.utc) - CONFIRMATION_MAX_AGE,
        )
        .order_by(EmailImport.created_at.desc())
        .first()
    )
    rows = (
        db.query(EmailImport, Statement)
        .outerjoin(Statement, Statement.id == EmailImport.statement_id)
        .filter(EmailImport.user_id == user_id, EmailImport.kind == "statement")
        .order_by(EmailImport.created_at.desc())
        .limit(RECENT_IMPORTS)
        .all()
    )
    return EmailImportOverview(
        enabled=True,
        address=email_import.import_address(token),
        gmail_confirmation=GmailConfirmation(
            code=(confirmation.details or {}).get("code"),
            link=(confirmation.details or {}).get("link"),
            received_at=confirmation.created_at,
        )
        if confirmation
        else None,
        imports=[_item(row, stmt) for row, stmt in rows],
    )


@router.get("", response_model=EmailImportOverview)
def get_email_import(db: DbDep, current_user: CurrentUserDep):
    """The user's forwarding address (created on first use), Gmail's latest
    forwarding confirmation and the latest imported emails."""
    return _overview(db, current_user)


@router.post("/token", response_model=EmailImportOverview)
def regenerate_address(db: DbDep, current_user: CurrentUserDep):
    """Gives the user a new forwarding address; the old one stops working."""
    if not email_import.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La importación por mail no está configurada en el servidor.",
        )
    _new_token(db, current_user)
    return _overview(db, current_user)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def run_email_import(
    background_tasks: BackgroundTasks,
    x_cron_secret: Annotated[str | None, Header()] = None,
):
    """Reads the import inbox. Called by an external cron (cron-job.org) with the
    X-Cron-Secret header; the work happens after responding."""
    if not settings.EMAIL_IMPORT_CRON_SECRET or not email_import.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La importación por mail no está configurada en el servidor.",
        )
    if not x_cron_secret or not hmac.compare_digest(x_cron_secret, settings.EMAIL_IMPORT_CRON_SECRET):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    background_tasks.add_task(email_import.run_import)
    return {"status": "started"}
