import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, List

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..dependencies import get_current_user
from ..models.category import Category
from ..models.statement import Statement
from ..models.transaction import Transaction
from ..models.user import User
from ..schemas.statement import (
    ExternalStatementRequest,
    StatementConfirmRequest,
    StatementDetail,
    StatementListItem,
    StatementStatus,
    TransactionForReview,
)
from ..services import statement_parser


router = APIRouter(prefix="/api/v1/statements", tags=["statements"])

DbDep = Annotated[Session, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
settings = get_settings()
logger = logging.getLogger(__name__)


PROCESSING_TIMEOUT = timedelta(minutes=10)


async def _read_pdf(file: UploadFile) -> bytes:
    """Reads the uploaded PDF into memory, checking type, size and magic bytes.
    The file is never written to disk."""
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo debe ser un PDF (content-type application/pdf).",
        )

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El archivo excede el tamaño máximo de {settings.MAX_FILE_SIZE_MB} MB.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo no parece ser un PDF válido.",
        )
    return data


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _resolve_categories(db: Session, user_id: str, transactions: list[dict]) -> None:
    """Adds category_id to each parsed transaction, creating the category when the
    user doesn't have it yet (a user without categories gets the default ones)."""
    by_name = {
        c.name.lower(): c.id for c in db.query(Category).filter(Category.user_id == user_id).all()
    }
    for tx in transactions:
        name = (tx.get("category") or "").strip()
        if not name:
            continue
        if name.lower() not in by_name:
            by_name[name.lower()] = _get_or_create_category(db, user_id, name)
        tx["category_id"] = by_name[name.lower()]


def _parse_in_background(statement_id: str, user_id: str, pdf_bytes: bytes) -> None:
    """Parses the PDF with Claude and leaves the statement ready for review."""
    db = SessionLocal()
    try:
        stmt: Statement | None = (
            db.query(Statement).filter(Statement.id == statement_id, Statement.user_id == user_id).first()
        )
        if not stmt:
            return

        user_categories = [
            c.name for c in db.query(Category).filter(Category.user_id == user_id).order_by(Category.name).all()
        ]
        try:
            parsed = statement_parser.parse_statement(
                pdf_bytes, statement_parser.prompt_categories(user_categories)
            )
        except statement_parser.StatementParseError as exc:
            stmt.status = "error"
            stmt.error_message = str(exc)
            db.commit()
            return
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error parsing statement %s", statement_id)
            stmt.status = "error"
            stmt.error_message = "Hubo un error al procesar el PDF."
            db.commit()
            return

        period_start = _parse_date(parsed.get("period_start"))
        period_end = _parse_date(parsed.get("period_end"))
        if _confirmed_duplicate(db, user_id, parsed.get("bank_name"), period_start, period_end):
            stmt.status = "error"
            stmt.error_message = "Ya existe un estado de cuenta confirmado para ese banco y período."
            db.commit()
            return

        transactions = parsed.get("transactions") or []
        _resolve_categories(db, user_id, transactions)

        stmt.bank_name = parsed.get("bank_name")
        stmt.card_last4 = (parsed.get("card_last4") or None) and parsed["card_last4"][-4:]
        stmt.period_start = period_start
        stmt.period_end = period_end
        stmt.raw_json = parsed
        stmt.status = "pending_review"
        stmt.error_message = None
        db.commit()
    finally:
        db.close()


def _confirmed_duplicate(
    db: Session, user_id: str, bank_name: str | None, period_start: date | None, period_end: date | None
) -> bool:
    if not bank_name or not period_start or not period_end:
        return False
    return (
        db.query(Statement)
        .filter(
            Statement.user_id == user_id,
            Statement.bank_name.ilike(bank_name),
            Statement.period_start == period_start,
            Statement.period_end == period_end,
            Statement.status == "confirmed",
        )
        .first()
        is not None
    )


@router.post("/", response_model=StatementListItem, status_code=status.HTTP_201_CREATED)
async def upload_statement(
    background_tasks: BackgroundTasks,
    db: DbDep,
    current_user: CurrentUserDep,
    file: UploadFile = File(...),
    password: str | None = Form(None),
):
    """Uploads a statement PDF; Claude parses it in the background and the
    statement goes to pending_review. Only the parse result is kept, not the PDF."""
    if not statement_parser.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Falta configurar ANTHROPIC_API_KEY en el servidor.",
        )

    data = await _read_pdf(file)
    file_hash = hashlib.sha256(data).hexdigest()
    user_id = str(current_user.id)

    existing = (
        db.query(Statement)
        .filter(Statement.user_id == user_id, Statement.file_hash == file_hash)
        .first()
    )
    if existing and existing.status == "error":
        # a failed attempt doesn't block uploading the same file again
        db.delete(existing)
        db.commit()
    elif existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya subiste este archivo anteriormente.",
        )

    try:
        pdf_bytes = statement_parser.decrypt_pdf(data, password)
    except statement_parser.StatementParseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    stmt = Statement(
        user_id=user_id,
        filename=file.filename,
        file_hash=file_hash,
        status="processing",
    )
    db.add(stmt)
    db.commit()
    db.refresh(stmt)

    background_tasks.add_task(_parse_in_background, stmt.id, user_id, pdf_bytes)
    return stmt


@router.get("/", response_model=List[StatementListItem])
def list_statements(db: DbDep, current_user: CurrentUserDep):
    return (
        db.query(Statement)
        .filter(Statement.user_id == str(current_user.id))
        .order_by(Statement.uploaded_at.desc())
        .all()
    )


@router.get("/{statement_id}/status", response_model=StatementStatus)
def get_statement_status(statement_id: str, db: DbDep, current_user: CurrentUserDep):
    stmt = (
        db.query(Statement)
        .filter(Statement.id == statement_id, Statement.user_id == str(current_user.id))
        .first()
    )
    if not stmt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estado de cuenta no encontrado")
    if stmt.status == "processing" and datetime.now(timezone.utc) - stmt.uploaded_at > PROCESSING_TIMEOUT:
        # the server restarted mid-parse (the PDF only lived in memory)
        stmt.status = "error"
        stmt.error_message = "El procesamiento se interrumpió. Volvé a subir el PDF."
        db.commit()
    return StatementStatus(id=stmt.id, status=stmt.status, error_message=stmt.error_message)


@router.get("/{statement_id}", response_model=StatementDetail)
def get_statement_detail(statement_id: str, db: DbDep, current_user: CurrentUserDep):
    stmt = (
        db.query(Statement)
        .filter(Statement.id == statement_id, Statement.user_id == str(current_user.id))
        .first()
    )
    if not stmt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estado de cuenta no encontrado")
    if stmt.status != "pending_review" or not stmt.raw_json:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El estado de cuenta aún no está listo para revisión.",
        )

    raw = stmt.raw_json or {}
    txs = raw.get("transactions") or []
    user_category_ids = {
        c.id for c in db.query(Category.id).filter(Category.user_id == str(current_user.id)).all()
    }

    items: list[TransactionForReview] = []
    for tx in txs:
        category_id = tx.get("category_id")
        try:
            tx_id = str(uuid.uuid4())
            items.append(
                TransactionForReview(
                    id=tx_id,
                    date=tx.get("date"),
                    description=tx.get("description") or "",
                    merchant=tx.get("merchant"),
                    amount=tx.get("amount"),
                    currency=tx.get("currency") or stmt.currency,
                    installment_num=tx.get("installment_num"),
                    installment_tot=tx.get("installment_tot"),
                    suggested_category=tx.get("category") or tx.get("suggested_category"),
                    # preselected; None if the category was deleted meanwhile
                    category_id=category_id if category_id in user_category_ids else None,
                    category_source="ai",
                )
            )
        except Exception:  # noqa: BLE001
            continue

    return StatementDetail(
        id=stmt.id,
        filename=stmt.filename,
        bank_name=stmt.bank_name,
        period_start=stmt.period_start,
        period_end=stmt.period_end,
        card_last4=stmt.card_last4,
        currency=stmt.currency,
        status=stmt.status,
        uploaded_at=stmt.uploaded_at,
        confirmed_at=stmt.confirmed_at,
        transactions=items,
    )


@router.post("/{statement_id}/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_statement(
    statement_id: str,
    payload: StatementConfirmRequest,
    db: DbDep,
    current_user: CurrentUserDep,
):
    stmt = (
        db.query(Statement)
        .filter(Statement.id == statement_id, Statement.user_id == str(current_user.id))
        .first()
    )
    if not stmt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estado de cuenta no encontrado")

    # borrar transacciones previas asociadas (por si se re-confirma)
    db.query(Transaction).filter(
        Transaction.statement_id == stmt.id,
        Transaction.user_id == str(current_user.id),
    ).delete()

    for tx in payload.transactions:
        tr = Transaction(
            statement_id=stmt.id,
            user_id=str(current_user.id),
            date=tx.date,
            description=tx.description,
            merchant=tx.merchant,
            amount=tx.amount,
            currency=tx.currency,
            category_id=tx.category_id,
            category_source=tx.category_source,
            installment_num=tx.installment_num,
            installment_tot=tx.installment_tot,
        )
        db.add(tr)

    stmt.status = "confirmed"
    stmt.confirmed_at = datetime.utcnow()
    db.add(stmt)
    db.commit()
    return None


@router.delete("/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_statement(statement_id: str, db: DbDep, current_user: CurrentUserDep):
    stmt = (
        db.query(Statement)
        .filter(Statement.id == statement_id, Statement.user_id == str(current_user.id))
        .first()
    )
    if not stmt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estado de cuenta no encontrado")

    db.delete(stmt)
    db.commit()
    return None


def _get_or_create_category(db: Session, user_id: str, name: str | None) -> str | None:
    """
    Looks up a user's category by name (case-insensitive); creates it if missing.
    Returns the category_id, or None if no name was provided.
    """
    if not name:
        return None

    category = (
        db.query(Category)
        .filter(Category.user_id == user_id, Category.name.ilike(name))
        .first()
    )
    if category:
        return category.id

    category = Category(user_id=user_id, name=name)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category.id


@router.post("/external", response_model=StatementListItem, status_code=status.HTTP_201_CREATED)
def create_external_statement(
    payload: ExternalStatementRequest,
    db: DbDep,
    current_user: CurrentUserDep,
    x_external_import_key: Annotated[str | None, Header()] = None,
):
    """
    Saves an already-parsed statement from a trusted external process (e.g. the
    Apps Script automation) directly, without a PDF upload or the manual
    pending_review step. Intended for trusted integrations, not the web app flow.

    Restricted on purpose: requires a shared secret (X-External-Import-Key header,
    matching EXTERNAL_IMPORT_SECRET) that only lives in the Apps Script project's
    Script Properties — never in the web app — and only works for the account
    configured in EXTERNAL_IMPORT_ALLOWED_EMAIL. This keeps the bypass-review path
    scoped to the personal automation, not exposed as a general app feature.
    """
    is_authorized = (
        settings.EXTERNAL_IMPORT_SECRET
        and x_external_import_key == settings.EXTERNAL_IMPORT_SECRET
        and settings.EXTERNAL_IMPORT_ALLOWED_EMAIL
        and current_user.email == settings.EXTERNAL_IMPORT_ALLOWED_EMAIL
    )
    if not is_authorized:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    # Avoid duplicating the same bank+period if it was already imported before
    existing = (
        db.query(Statement)
        .filter(
            Statement.user_id == str(current_user.id),
            Statement.bank_name == payload.bank_name,
            Statement.period_start == payload.period_start,
            Statement.period_end == payload.period_end,
            Statement.status == "confirmed",
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe un estado de cuenta confirmado para ese banco y período.",
        )

    stmt = Statement(
        user_id=str(current_user.id),
        filename=None,
        file_path=None,
        file_hash=None,
        bank_name=payload.bank_name,
        card_last4=payload.card_last4,
        period_start=payload.period_start,
        period_end=payload.period_end,
        status="confirmed",
        confirmed_at=datetime.utcnow(),
        raw_json={"summary": payload.summary.model_dump()} if payload.summary else None,
    )
    db.add(stmt)
    db.commit()
    db.refresh(stmt)

    for tx in payload.transactions:
        category_id = _get_or_create_category(db, str(current_user.id), tx.category_name)
        transaction = Transaction(
            statement_id=stmt.id,
            user_id=str(current_user.id),
            date=tx.date,
            description=tx.description,
            merchant=tx.merchant,
            amount=tx.amount,
            currency=tx.currency,
            category_id=category_id,
            category_source="ai",
            installment_num=tx.installment_num,
            installment_tot=tx.installment_tot,
        )
        db.add(transaction)

    db.commit()
    return stmt
