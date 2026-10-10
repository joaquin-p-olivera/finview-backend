"""Turns a statement PDF into a statement ready for review: parses it with
Claude and resolves the categories. Used by the web upload and by the email
import; the PDF only lives in memory."""

import logging
from datetime import date

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.category import Category
from ..models.statement import Statement
from . import statement_parser

logger = logging.getLogger(__name__)


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
            by_name[name.lower()] = get_or_create_category(db, user_id, name)
        tx["category_id"] = by_name[name.lower()]


def parse_into_statement(statement_id: str, user_id: str, pdf_bytes: bytes) -> None:
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
        if confirmed_duplicate(db, user_id, parsed.get("bank_name"), period_start, period_end):
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


def confirmed_duplicate(
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


def get_or_create_category(db: Session, user_id: str, name: str | None) -> str | None:
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
