"""Categorizes purchase products with Claude.

Products without a category are sent to Claude in batches, together with the
user's categories and some already-categorized products as examples. Claude
answers, for each product, a category (an existing one or a new one), its
package size when the name says it, and optionally that it's the same
product as another one. Categories are applied right away (marked "ai", the
user can change them); "same product" suggestions are only stored, the user
confirms them by merging.
"""

import json
import logging

import anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.purchase import PurchaseCartItem, PurchaseCategory, PurchaseProduct
from . import purchase_products

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
MAX_EXAMPLES = 300
SIZE_UNITS = {"g", "kg", "ml", "l", "u"}
# Assigned in this fixed order; checked for color-blind separation between
# neighbors on the app's dark background, so stacked charts stay readable.
NEW_CATEGORY_COLORS = [
    "#3987e5", "#d95926", "#199e70", "#c98500",
    "#d55181", "#008300", "#9085e9", "#e66767",
]

SYSTEM_PROMPT = """You categorize the products a person buys at supermarkets in Uruguay, so they \
can see what they spend on and compare prices over time. They type the names themselves on \
their phone while shopping: short, in Rioplatense Spanish, sometimes without accents, with \
abbreviations or brand names (for example "ph" is toilet paper, "colet" is a chocolate / \
alfajor brand, "rapiditas" are wrap tortillas).

Write category names and notes in Rioplatense Spanish, since the person reads them in a \
Spanish app.

For each pending product return:
- category: the category name. Use one of their existing categories when it fits, spelled \
exactly the same. If none fits, create a new one: short, plural, in Spanish, in the style of \
"Carnes", "Pescados", "Verduras", "Frutas", "Lácteos", "Huevos", "Bebidas", "Almacén", \
"Panadería", "Snacks y dulces", "Congelados", "Limpieza", "Higiene personal". Prefer a few \
broad categories over many small ones, and reuse the new ones you already created in this \
same answer.
- size_value and size_unit: the package size, only if the name states it ("agua 6l" -> 6 and \
"l", "yogurt 1kg" -> 1 and "kg"). Units: g, kg, ml, l, u. Otherwise 0 and "".
- same_as_product_id: the id of another product in the data (pending or already categorized), \
only if it is clearly the same product written differently ("Limon" and "Limones", "Yogurth" \
and "Yogurt"). Don't join different products of the same family: "Galletas" and "Galletas \
integrales", "Aceite" and "Aceite de oliva", "Agua" and "Agua 6l" are different products. \
Otherwise "".
- note: a short note, only when something deserves a look, for example when the prices paid \
vary a lot for something not sold by weight ("Agua" at $65 and at $147: maybe two different \
sizes). For meat, fish, vegetables and fruit sold by weight prices naturally vary: don't flag \
them. If there's nothing to say, "".
"""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "category": {"type": "string"},
                    "size_value": {"type": "number"},
                    "size_unit": {"type": "string"},
                    "same_as_product_id": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["product_id", "category", "size_value", "size_unit", "same_as_product_id", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["products"],
    "additionalProperties": False,
}


class PurchaseAIError(Exception):
    """Categorization could not run (no API key, API error, refusal)."""


def is_configured() -> bool:
    return bool(get_settings().ANTHROPIC_API_KEY)


def pending_products(db: Session, user_id: str) -> list[PurchaseProduct]:
    return (
        db.query(PurchaseProduct)
        .filter(PurchaseProduct.user_id == user_id, PurchaseProduct.category_id.is_(None))
        .order_by(PurchaseProduct.name)
        .all()
    )


def _price_summary(db: Session, product_ids: list[str]) -> dict[str, dict]:
    rows = (
        db.query(
            PurchaseCartItem.product_id,
            func.count(PurchaseCartItem.id),
            func.min(PurchaseCartItem.price),
            func.max(PurchaseCartItem.price),
        )
        .filter(PurchaseCartItem.product_id.in_(product_ids), PurchaseCartItem.price > 0)
        .group_by(PurchaseCartItem.product_id)
        .all()
    )
    return {pid: {"times_bought": n, "min_price": float(lo), "max_price": float(hi)} for pid, n, lo, hi in rows}


def _build_request(
    categories: list[PurchaseCategory],
    examples: list[PurchaseProduct],
    batch: list[PurchaseProduct],
    prices: dict[str, dict],
) -> str:
    category_names = {c.id: c.name for c in categories}
    payload = {
        "existing_categories": [c.name for c in categories],
        "categorized_products": [
            {"id": p.id, "name": p.name, "category": category_names.get(p.category_id)}
            for p in examples
        ],
        "pending_products": [
            {"id": p.id, "name": p.name, **prices.get(p.id, {})} for p in batch
        ],
    }
    return (
        "Categorize the pending products. Data (JSON):\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )


def _call_claude(user_content: str) -> list[dict]:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=settings.PURCHASE_AI_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": RESULT_SCHEMA},
            },
        )
    except anthropic.APIError as exc:
        logger.exception("Purchase categorization request failed")
        raise PurchaseAIError("No se pudo contactar a Claude. Probá de nuevo en un rato.") from exc

    if response.stop_reason == "refusal":
        raise PurchaseAIError("Claude no pudo categorizar estos productos.")
    if response.stop_reason == "max_tokens":
        raise PurchaseAIError("La respuesta de Claude quedó incompleta.")
    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise PurchaseAIError("Claude no devolvió una respuesta.")
    return json.loads(text)["products"]


def _get_or_create_category(
    db: Session, user_id: str, name: str, categories: list[PurchaseCategory]
) -> PurchaseCategory:
    name = " ".join(name.split())[:100]
    for category in categories:
        if category.name.strip().lower() == name.lower():
            return category
    category = PurchaseCategory(
        user_id=user_id,
        name=name,
        color=NEW_CATEGORY_COLORS[len(categories) % len(NEW_CATEGORY_COLORS)],
        created_by_ai=True,
    )
    db.add(category)
    db.flush()
    categories.append(category)
    return category


def categorize_pending_products(db: Session, user_id: str) -> dict:
    """Categorize every product of the user without a category. Commits after
    each batch, so a failure keeps what was already done."""
    if not is_configured():
        raise PurchaseAIError("Falta configurar ANTHROPIC_API_KEY en el servidor.")

    pending = pending_products(db, user_id)
    result = {"categorized": 0, "new_categories": [], "suggestions": 0}
    if not pending:
        return result

    categories = db.query(PurchaseCategory).filter(PurchaseCategory.user_id == user_id).all()
    known_before = {c.id for c in categories}
    examples = (
        db.query(PurchaseProduct)
        .filter(PurchaseProduct.user_id == user_id, PurchaseProduct.category_id.isnot(None))
        .order_by(PurchaseProduct.name)
        .limit(MAX_EXAMPLES)
        .all()
    )
    user_products = {
        p.id: p for p in db.query(PurchaseProduct).filter(PurchaseProduct.user_id == user_id).all()
    }
    prices = _price_summary(db, [p.id for p in pending])

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        batch_ids = {p.id for p in batch}
        answers = _call_claude(_build_request(categories, examples, batch, prices))

        for answer in answers:
            product = user_products.get(answer["product_id"])
            # Only products that were asked for and are still uncategorized
            # (the user may have set one meanwhile).
            if not product or product.id not in batch_ids or product.category_id:
                continue
            category_name = answer["category"].strip()
            if category_name:
                category = _get_or_create_category(db, user_id, category_name, categories)
                purchase_products.set_product_category(db, product, category.id, source="ai")
                result["categorized"] += 1

            unit = answer["size_unit"].strip().lower()
            if product.size_value is None and answer["size_value"] > 0 and unit in SIZE_UNITS:
                product.size_value = answer["size_value"]
                product.size_unit = unit

            same_as = answer["same_as_product_id"].strip()
            if same_as and same_as != product.id and same_as in user_products:
                product.suggested_merge_into_id = same_as
                result["suggestions"] += 1
            note = answer["note"].strip()
            if note:
                product.ai_note = note[:255]
                result["suggestions"] += 1

        db.commit()
        examples = examples + [p for p in batch if p.category_id]

    result["new_categories"] = [c.name for c in categories if c.id not in known_before]
    return result


def categorize_in_background(user_id: str) -> None:
    """Run after a cart is completed: categorize what's new, never fail the request."""
    from ..database import SessionLocal

    if not is_configured():
        return
    db = SessionLocal()
    try:
        categorize_pending_products(db, user_id)
    except Exception:
        logger.exception("Background purchase categorization failed")
        db.rollback()
    finally:
        db.close()
