from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models.category import Category
from ..models.statement import Statement
from ..models.transaction import Transaction
from ..models.user import User


router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

DbDep = Annotated[Session, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
# Optional currency filter: without it, amounts in UYU and USD are summed together
CurrencyQuery = Annotated[str | None, Query(pattern="^(UYU|USD)$")]


def _currency_filters(currency: str | None):
    return [Transaction.currency == currency] if currency else []


def get_month_range():
    today = datetime.now()
    current_month_start = today.replace(day=1)
    if today.month == 1:
        previous_month_start = today.replace(year=today.year - 1, month=12, day=1)
    else:
        previous_month_start = today.replace(month=today.month - 1, day=1)
    previous_month_end = current_month_start
    return current_month_start.date(), previous_month_start.date(), previous_month_end.date()


@router.get("/summary")
def get_summary(db: DbDep, current_user: CurrentUserDep, currency: CurrencyQuery = None):
    current_month_start, previous_month_start, previous_month_end = get_month_range()
    
    current_month_spent = (
        db.query(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
        .filter(
            Transaction.user_id == str(current_user.id),
            *_currency_filters(currency),
            Transaction.date >= current_month_start,
        )
        .scalar()
    )
    
    previous_month_spent = (
        db.query(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
        .filter(
            Transaction.user_id == str(current_user.id),
            *_currency_filters(currency),
            Transaction.date >= previous_month_start,
            Transaction.date < previous_month_end,
        )
        .scalar()
    )
    
    total_transactions = (
        db.query(func.count(Transaction.id))
        .filter(Transaction.user_id == str(current_user.id))
        .scalar()
    )

    categories_count = (
        db.query(func.count(Category.id))
        .filter(Category.user_id == str(current_user.id))
        .scalar()
    )

    statements_count = (
        db.query(func.count(Statement.id))
        .filter(Statement.user_id == str(current_user.id), Statement.status == "confirmed")
        .scalar()
    )

    return {
        "total_transactions": total_transactions or 0,
        "total_spent_current_month": float(current_month_spent or 0),
        "total_spent_previous_month": float(previous_month_spent or 0),
        "categories_count": categories_count or 0,
        "statements_count": statements_count or 0,
    }


@router.get("/by-month")
def get_by_month(
    db: DbDep,
    current_user: CurrentUserDep,
    currency: CurrencyQuery = None,
    months: int = Query(default=6, ge=1, le=24),
):
    transactions = (
        db.query(
            func.extract('year', Transaction.date).label("year"),
            func.extract('month', Transaction.date).label("month"),
            func.sum(func.abs(Transaction.amount)).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(Transaction.user_id == str(current_user.id), *_currency_filters(currency))
        .group_by("year", "month")
        .order_by(text("year DESC, month DESC"))
        .limit(months)
        .all()
    )

    return [
        {"month": f"{int(t.year)}-{int(t.month):02d}", "total": float(t.total), "count": t.count}
        for t in transactions
    ]


def _get_latest_statement_date_range(db: Session, user_id: str):
    """
    Returns (period_start, period_end) of the most recently confirmed statement
    (by period_end), or None if there isn't one yet.
    """
    latest_statement = (
        db.query(Statement)
        .filter(
            Statement.user_id == user_id,
            Statement.status == "confirmed",
            Statement.period_end != None,
        )
        .order_by(Statement.period_end.desc())
        .first()
    )
    if not latest_statement:
        return None
    return latest_statement.period_start, latest_statement.period_end


@router.get("/by-category")
def get_by_category(
    db: DbDep,
    current_user: CurrentUserDep,
    currency: CurrencyQuery = None,
    period: str = Query("all", pattern="^(all|latest)$"),
):
    date_filters = []

    if period == "latest":
        date_range = _get_latest_statement_date_range(db, str(current_user.id))
        if not date_range:
            # No confirmed statement with a period yet — nothing to show for "latest"
            return []
        period_start, period_end = date_range
        date_filters = [Transaction.date >= period_start, Transaction.date <= period_end]

    transactions = (
        db.query(
            Category.name,
            func.sum(func.abs(Transaction.amount)).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .join(Category, Transaction.category_id == Category.id)
        .filter(Transaction.user_id == str(current_user.id), *_currency_filters(currency), *date_filters)
        .group_by(Category.id, Category.name)
        .order_by(func.sum(func.abs(Transaction.amount)).desc())
        .all()
    )

    others = (
        db.query(
            func.sum(func.abs(Transaction.amount)).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == str(current_user.id),
            Transaction.category_id == None,
            *_currency_filters(currency),
            *date_filters,
        )
        .first()
    )

    result = [
        {"category": t.name, "total": float(t.total), "count": t.count}
        for t in transactions
    ]

    if others and others.total:
        result.append({"category": "Sin categoría", "total": float(others.total), "count": others.count})

    return result


@router.get("/by-bank")
def get_by_bank(
    db: DbDep,
    current_user: CurrentUserDep,
    currency: CurrencyQuery = None,
    period: str = Query("all", pattern="^(all|latest)$"),
):
    date_filters = []
    if period == "latest":
        date_range = _get_latest_statement_date_range(db, str(current_user.id))
        if not date_range:
            return []
        period_start, period_end = date_range
        date_filters = [Transaction.date >= period_start, Transaction.date <= period_end]

    transactions = (
        db.query(
            Statement.bank_name,
            func.sum(func.abs(Transaction.amount)).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .join(Statement, Transaction.statement_id == Statement.id)
        .filter(Transaction.user_id == str(current_user.id), *_currency_filters(currency), *date_filters)
        .group_by(Statement.bank_name)
        .order_by(func.sum(func.abs(Transaction.amount)).desc())
        .all()
    )

    return [
        {"bank": t.bank_name or "Desconocido", "total": float(t.total), "count": t.count}
        for t in transactions
        if t.bank_name
    ]


@router.get("/top-merchants")
def get_top_merchants(
    db: DbDep,
    current_user: CurrentUserDep,
    currency: CurrencyQuery = None,
    limit: int = Query(default=10, ge=1, le=50),
    period: str = Query("all", pattern="^(all|latest)$"),
):
    date_filters = []
    if period == "latest":
        date_range = _get_latest_statement_date_range(db, str(current_user.id))
        if not date_range:
            return []
        period_start, period_end = date_range
        date_filters = [Transaction.date >= period_start, Transaction.date <= period_end]

    merchants = (
        db.query(
            Transaction.merchant,
            func.sum(func.abs(Transaction.amount)).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == str(current_user.id),
            Transaction.merchant != None,
            Transaction.merchant != "",
            *_currency_filters(currency),
            *date_filters,
        )
        .group_by(Transaction.merchant)
        .order_by(func.sum(func.abs(Transaction.amount)).desc())
        .limit(limit)
        .all()
    )

    return [
        {"merchant": m.merchant, "total": float(m.total), "count": m.count}
        for m in merchants
    ]


@router.get("/trends")
def get_trends(
    db: DbDep,
    current_user: CurrentUserDep,
    currency: CurrencyQuery = None,
    days: int = Query(default=30, ge=7, le=90),
):
    from datetime import datetime, timedelta
    cutoff_date = datetime.now().date() - timedelta(days=days)
    
    transactions = (
        db.query(
            Transaction.date,
            func.sum(func.abs(Transaction.amount)).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == str(current_user.id),
            *_currency_filters(currency),
            Transaction.date >= cutoff_date,
        )
        .group_by(Transaction.date)
        .order_by(Transaction.date)
        .all()
    )

    return [
        {"date": str(t.date), "total": float(t.total), "count": t.count}
        for t in transactions
    ]


REPORT_CURRENCIES = ("UYU", "USD")


@router.get("/statement-report")
def get_statement_report(
    db: DbDep,
    current_user: CurrentUserDep,
    statement_id: str | None = None,
):
    """
    Category breakdown of one confirmed statement, split by currency — the same
    numbers as the monthly report email. Without statement_id, uses the latest
    confirmed statement (by period_end).

    Amounts keep their sign (refunds subtract), like the email. When the statement
    carries the bank's official totals (raw_json.summary, sent by the Apps Script
    import), other_charges is the official total minus the categorized total:
    insurance, interest and fees that aren't stored as transactions.
    """
    query = db.query(Statement).filter(
        Statement.user_id == str(current_user.id),
        Statement.status == "confirmed",
    )
    if statement_id:
        stmt = query.filter(Statement.id == statement_id).first()
    else:
        stmt = query.order_by(Statement.period_end.desc().nulls_last()).first()

    if not stmt:
        if statement_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estado de cuenta no encontrado")
        return None

    rows = (
        db.query(
            Transaction.currency,
            Category.name,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .filter(Transaction.statement_id == stmt.id)
        .group_by(Transaction.currency, Category.id, Category.name)
        .all()
    )

    by_currency: dict[str, list[dict]] = {}
    for r in rows:
        by_currency.setdefault(r.currency, []).append(
            {"category": r.name or "Sin categoría", "total": float(r.total), "count": r.count}
        )

    summary = (stmt.raw_json or {}).get("summary") or {}
    currencies = sorted(
        set(by_currency) | {c for c in REPORT_CURRENCIES if summary.get(f"statement_total_{c.lower()}")},
        key=lambda c: (REPORT_CURRENCIES.index(c) if c in REPORT_CURRENCIES else len(REPORT_CURRENCIES), c),
    )

    result = []
    for currency in currencies:
        categories = sorted(by_currency.get(currency, []), key=lambda c: c["total"], reverse=True)
        categorized_total = round(sum(c["total"] for c in categories), 2)
        official_total = summary.get(f"statement_total_{currency.lower()}")
        other_charges = None
        if official_total is not None:
            diff = round(float(official_total) - categorized_total, 2)
            other_charges = diff if abs(diff) > 0.01 else 0.0
        result.append(
            {
                "currency": currency,
                "categories": categories,
                "categorized_total": categorized_total,
                "statement_total": float(official_total) if official_total is not None else None,
                "other_charges": other_charges,
            }
        )

    return {
        "statement": {
            "id": stmt.id,
            "bank_name": stmt.bank_name,
            "card_last4": stmt.card_last4,
            "period_start": str(stmt.period_start) if stmt.period_start else None,
            "period_end": str(stmt.period_end) if stmt.period_end else None,
        },
        "currencies": result,
    }
