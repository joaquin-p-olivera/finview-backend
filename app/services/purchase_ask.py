"""Answers questions about the user's purchases in plain language with Claude.

Claude gets read-only tools over the user's completed cart items (the same
data as purchase_analytics: a few hundred rows loaded once per question) and
answers with a short text plus an optional table. It never writes SQL: every
tool is a fixed computation in Python over the purchases of the logged-in
user, so it can't read other users' data or change anything.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime

import anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.purchase import PurchaseCategory, PurchaseProduct, PurchaseQuestion
from . import purchase_analytics
from .purchase_analytics import URUGUAY_TZ, Purchase

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 500
# Tool calls per question; the usual question needs two or three.
MAX_TURNS = 8
MAX_ROWS = 100

SYSTEM_PROMPT = """You answer questions a person asks about their own supermarket purchases \
in Uruguay, inside a personal finance app. The data is what they typed into the app's \
shopping cart while shopping: each completed cart has a store and a date, and each item a \
product, a category, a unit price in Uruguayan pesos and a quantity (spend = price x \
quantity).

Use the tools to get the numbers; never make numbers up or estimate what the tools can \
compute. Call get_catalog first when you need product ids, category or store names, or the \
date range of the data. Product, category and store names in the question may be written \
differently from the data (singular/plural, accents, abbreviations): map them yourself to \
the closest ones in the catalog, and if several products match ("leche" and "leche \
descremada"), include all of them and say so.

Relative dates ("este año", "el mes pasado", "los últimos 3 meses") are relative to today's \
date, given by get_catalog. Prices of products sold by weight (meat, fish, vegetables, \
fruit) depend on how much was bought, so for those compare spend, not unit prices, and say \
it if the person asks about their price.

Answer in Rioplatense Spanish, briefly: lead with the number or the direct answer in one or \
two sentences, then at most a short clarification. Write amounts like "$ 1.234" (pesos, \
period for thousands, no decimals unless they matter) and dates like "3 de octubre". No \
markdown in the text. Add a table only when it helps (a comparison, a ranking, a month by \
month): at most 12 rows, short column names, values already formatted as text. Without a \
table, return empty columns and rows. If the data can't answer the question (it's not about \
their purchases, or there's no data for it), say so plainly in one sentence.
"""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "table": {
            "type": "object",
            "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
            "required": ["columns", "rows"],
            "additionalProperties": False,
        },
    },
    "required": ["answer", "table"],
    "additionalProperties": False,
}

_DATE_RANGE = {
    "start_date": {"type": "string", "description": "First day included, YYYY-MM-DD. Omit for no lower bound."},
    "end_date": {"type": "string", "description": "Last day included, YYYY-MM-DD. Omit for no upper bound."},
}
_FILTERS = {
    "categories": {
        "type": "array", "items": {"type": "string"},
        "description": "Only these categories (names as in get_catalog).",
    },
    "product_ids": {
        "type": "array", "items": {"type": "string"},
        "description": "Only these products (ids from get_catalog).",
    },
    "stores": {
        "type": "array", "items": {"type": "string"},
        "description": "Only these stores (names as in get_catalog).",
    },
}
GROUP_KEYS = ["year", "month", "category", "store", "product", "cart"]

TOOLS = [
    {
        "name": "get_catalog",
        "description": (
            "Today's date, the date range of the data, and the person's stores, categories (with "
            "total spend) and products (id, name, category, times bought, last purchase date and "
            "last price). Use it to resolve names in the question to ids and exact names."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_spending",
        "description": (
            "Total spend, number of items, units and carts, optionally filtered by dates, "
            "categories, products and stores, and grouped by up to two of: year, month (YYYY-MM), "
            "category, store, product, cart (one row per cart with its date and store). Without "
            "group_by returns only the overall total. Rows sorted by period, else by spend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_DATE_RANGE,
                **_FILTERS,
                "group_by": {
                    "type": "array", "items": {"type": "string", "enum": GROUP_KEYS},
                    "description": "Up to two keys, e.g. [\"month\", \"category\"].",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product_prices",
        "description": (
            "For each given product: every purchase (date, store, unit price, quantity; the last "
            "60), and the minimum, maximum, average and last price, overall and per store."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Up to 10 product ids from get_catalog.",
                },
                **_DATE_RANGE,
            },
            "required": ["product_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_price_changes",
        "description": (
            "Products bought at least twice within the dates: first and last unit price in that "
            "period and the change between them, plus the previous price before the last one. "
            "Sorted from the biggest rise to the biggest drop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_DATE_RANGE, "categories": _FILTERS["categories"]},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_store_comparison",
        "description": (
            "Products bought in two or more stores within the dates: average unit price per "
            "store, the cheapest store and the saving against the most expensive one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_DATE_RANGE,
                "categories": _FILTERS["categories"],
                "product_ids": _FILTERS["product_ids"],
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_basket_index",
        "description": (
            "Personal inflation month by month: for each month, the change in unit prices against "
            "the previous month counting only products bought in both, and an index starting at "
            "100 in the first month of the range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_DATE_RANGE},
            "additionalProperties": False,
        },
    },
]


class PurchaseAskError(Exception):
    """The question could not be answered (API error, refusal, too many steps)."""


class ToolInputError(Exception):
    pass


def is_configured() -> bool:
    return bool(get_settings().ANTHROPIC_API_KEY)


# ---------- daily limit and history ----------


def _today_start() -> datetime:
    now = datetime.now(URUGUAY_TZ)
    return datetime(now.year, now.month, now.day, tzinfo=URUGUAY_TZ)


def questions_today(db: Session, user_id: str) -> int:
    return (
        db.query(func.count(PurchaseQuestion.id))
        .filter(PurchaseQuestion.user_id == user_id, PurchaseQuestion.created_at >= _today_start())
        .scalar()
    ) or 0


def remaining_today(db: Session, user_id: str) -> int:
    return max(0, get_settings().PURCHASE_ASK_DAILY_LIMIT - questions_today(db, user_id))


def history(db: Session, user_id: str, limit: int = 20) -> list[PurchaseQuestion]:
    return (
        db.query(PurchaseQuestion)
        .filter(PurchaseQuestion.user_id == user_id)
        .order_by(PurchaseQuestion.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------- tools ----------


def _round(value: float) -> float:
    return round(value, 2)


def _parse_date(value, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolInputError(f"{field} must be YYYY-MM-DD, got {value!r}")


def _str_list(value, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ToolInputError(f"{field} must be a list of strings")
    return value


def _filter(purchases: list[Purchase], args: dict) -> list[Purchase]:
    start = _parse_date(args.get("start_date"), "start_date")
    end = _parse_date(args.get("end_date"), "end_date")
    categories = {c.strip().lower() for c in _str_list(args.get("categories"), "categories")}
    product_ids = set(_str_list(args.get("product_ids"), "product_ids"))
    stores = {s.strip().lower() for s in _str_list(args.get("stores"), "stores")}
    result = []
    for p in purchases:
        day = p.bought_at.date()
        if (start and day < start) or (end and day > end):
            continue
        if categories and p.category.lower() not in categories:
            continue
        if product_ids and p.product_id not in product_ids:
            continue
        if stores and p.store.lower() not in stores:
            continue
        result.append(p)
    return result


class _Tools:
    def __init__(self, db: Session, user_id: str):
        self.db = db
        self.user_id = user_id
        self.purchases = purchase_analytics.load_purchases(db, user_id)

    def run(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None or name not in {t["name"] for t in TOOLS}:
            raise ToolInputError(f"Unknown tool {name}")
        return handler(args or {})

    def tool_get_catalog(self, args: dict) -> dict:
        products: dict[str, dict] = {}
        stores: dict[str, set] = defaultdict(set)
        for p in self.purchases:
            stores[p.store].add(p.cart_id)
            if not p.product_id:
                continue
            entry = products.setdefault(p.product_id, {"id": p.product_id, "name": p.product_name, "times": 0})
            entry.update(category=p.category, last_date=p.bought_at.date().isoformat(), last_price=p.price)
            entry["times"] += 1
        # Products and categories with no purchase yet still help to resolve names.
        for product in self.db.query(PurchaseProduct).filter(PurchaseProduct.user_id == self.user_id):
            products.setdefault(product.id, {"id": product.id, "name": product.name, "times": 0})
        category_names = [
            c.name for c in self.db.query(PurchaseCategory).filter(PurchaseCategory.user_id == self.user_id)
        ]
        totals = {c["category"]: c["total"] for c in purchase_analytics.category_totals(self.purchases)}
        for name in category_names:
            totals.setdefault(name, 0.0)
        return {
            "today": datetime.now(URUGUAY_TZ).date().isoformat(),
            "first_purchase": self.purchases[0].bought_at.date().isoformat() if self.purchases else None,
            "last_purchase": self.purchases[-1].bought_at.date().isoformat() if self.purchases else None,
            "carts": len({p.cart_id for p in self.purchases}),
            "stores": [
                {"name": name, "carts": len(carts)}
                for name, carts in sorted(stores.items(), key=lambda kv: -len(kv[1]))
            ],
            "categories": [{"name": n, "total_spent": t} for n, t in sorted(totals.items(), key=lambda kv: -kv[1])],
            "products": sorted(products.values(), key=lambda p: p["name"].lower()),
        }

    def tool_get_spending(self, args: dict) -> dict:
        group_by = _str_list(args.get("group_by"), "group_by")
        if len(group_by) > 2 or any(k not in GROUP_KEYS for k in group_by):
            raise ToolInputError(f"group_by takes up to two of {GROUP_KEYS}")
        purchases = _filter(self.purchases, args)

        def key_of(p: Purchase, key: str) -> str:
            if key == "year":
                return str(p.bought_at.year)
            if key == "month":
                return p.month
            if key == "category":
                return p.category
            if key == "store":
                return p.store
            if key == "product":
                return p.product_name
            return f"{p.bought_at.date().isoformat()} {p.store}"

        groups: dict[tuple, dict] = {}
        for p in purchases:
            group = groups.setdefault(
                tuple(key_of(p, k) for k in group_by),
                {"total": 0.0, "items": 0, "units": 0, "carts": set()},
            )
            group["total"] += p.spent
            group["items"] += 1
            group["units"] += p.quantity
            group["carts"].add(p.cart_id)
        rows = [
            {**dict(zip(group_by, key)), "total": _round(g["total"]), "items": g["items"],
             "units": g["units"], "carts": len(g["carts"])}
            for key, g in groups.items()
        ]
        periods = [k for k in group_by if k in ("year", "month", "cart")]
        if periods:
            rows.sort(key=lambda r: tuple(r[k] for k in periods) + (-r["total"],))
        else:
            rows.sort(key=lambda r: -r["total"])
        return {
            "total": _round(sum(p.spent for p in purchases)),
            "items": len(purchases),
            "carts": len({p.cart_id for p in purchases}),
            "rows": rows[:MAX_ROWS] if group_by else [],
            "truncated": len(rows) > MAX_ROWS,
        }

    def tool_get_product_prices(self, args: dict) -> dict:
        product_ids = _str_list(args.get("product_ids"), "product_ids")[:10]
        if not product_ids:
            raise ToolInputError("product_ids is required")
        purchases = _filter(self.purchases, {**args, "product_ids": product_ids})
        result = []
        for product_id in product_ids:
            items = [p for p in purchases if p.product_id == product_id]
            if not items:
                result.append({"product_id": product_id, "purchases": []})
                continue
            prices = [p.price for p in items]
            by_store: dict[str, list[float]] = defaultdict(list)
            for p in items:
                by_store[p.store].append(p.price)
            result.append({
                "product_id": product_id,
                "name": items[-1].product_name,
                "category": items[-1].category,
                "times": len(items),
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": _round(sum(prices) / len(prices)),
                "last_price": items[-1].price,
                "last_date": items[-1].bought_at.date().isoformat(),
                "by_store": sorted(
                    ({"store": s, "avg_price": _round(sum(v) / len(v)), "min_price": min(v),
                      "max_price": max(v), "times": len(v)} for s, v in by_store.items()),
                    key=lambda s: s["avg_price"],
                ),
                "purchases": [
                    {"date": p.bought_at.date().isoformat(), "store": p.store, "price": p.price, "quantity": p.quantity}
                    for p in items[-60:]
                ],
            })
        return {"products": result}

    def tool_get_price_changes(self, args: dict) -> dict:
        by_product: dict[str, list[Purchase]] = defaultdict(list)
        for p in _filter(self.purchases, args):
            if p.product_id:
                by_product[p.product_id].append(p)
        rows = []
        for product_id, items in by_product.items():
            if len(items) < 2:
                continue
            first, previous, last = items[0], items[-2], items[-1]
            rows.append({
                "product_id": product_id,
                "name": last.product_name,
                "category": last.category,
                "times": len(items),
                "first_price": first.price,
                "first_date": first.bought_at.date().isoformat(),
                "previous_price": previous.price,
                "last_price": last.price,
                "last_date": last.bought_at.date().isoformat(),
                "change_first_to_last": round(last.price / first.price - 1, 4),
                "change_previous_to_last": round(last.price / previous.price - 1, 4),
            })
        rows.sort(key=lambda r: -r["change_first_to_last"])
        if len(rows) > 60:
            # Keep both ends: the biggest rises and the biggest drops.
            rows = rows[:30] + rows[-30:]
        return {"products": rows}

    def tool_get_store_comparison(self, args: dict) -> dict:
        rows = purchase_analytics.cheapest_stores(_filter(self.purchases, args))
        return {"products": rows[:MAX_ROWS]}

    def tool_get_basket_index(self, args: dict) -> dict:
        return {"months": purchase_analytics.basket_index(_filter(self.purchases, args))}


# ---------- the conversation with Claude ----------


def _clean_table(table: dict | None) -> dict | None:
    if not table:
        return None
    columns = [str(c) for c in table.get("columns") or []]
    rows = [[str(v) for v in row][: len(columns)] for row in table.get("rows") or []]
    if not columns or not rows:
        return None
    return {"columns": columns, "rows": rows[:50]}


def answer_question(db: Session, user_id: str, question: str) -> PurchaseQuestion:
    """Ask Claude, let it call the tools, save and return the answer."""
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    tools = _Tools(db, user_id)
    messages: list[dict] = [{"role": "user", "content": question}]
    input_tokens = output_tokens = 0

    for _ in range(MAX_TURNS):
        try:
            response = client.messages.create(
                model=settings.PURCHASE_ASK_MODEL,
                max_tokens=16000,
                # Caches tools, system prompt and the turns so far for the next tool round.
                cache_control={"type": "ephemeral"},
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
            )
        except anthropic.APIError as exc:
            logger.exception("Purchase question request failed")
            raise PurchaseAskError("No pudimos responder ahora. Probá de nuevo en un rato.") from exc

        input_tokens += response.usage.input_tokens or 0
        output_tokens += response.usage.output_tokens or 0

        if response.stop_reason == "refusal":
            raise PurchaseAskError("No pudimos responder esa pregunta. Probá escribirla de otra forma.")
        if response.stop_reason == "max_tokens":
            raise PurchaseAskError("La respuesta quedó incompleta. Probá de nuevo.")
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    content = json.dumps(tools.run(block.name, block.input), ensure_ascii=False)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                except ToolInputError as exc:
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
                    )
            messages.append({"role": "user", "content": results})
            continue

        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise PurchaseAskError("No pudimos armar una respuesta. Probá de nuevo.")
        data = json.loads(text)
        record = PurchaseQuestion(
            user_id=user_id,
            question=question,
            answer=data["answer"].strip(),
            answer_table=_clean_table(data.get("table")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    raise PurchaseAskError("La pregunta necesitó demasiados pasos. Probá con una más concreta.")
