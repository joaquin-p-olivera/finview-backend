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

SYSTEM_PROMPT = """Categorizás los productos que una persona compra en supermercados de Uruguay, \
para que pueda ver en qué gasta y comparar precios en el tiempo. Los nombres los escribe ella \
misma en el celular mientras compra: son cortos, en español rioplatense, a veces sin tildes, \
con abreviaturas o marcas (por ejemplo "ph" es papel higiénico, "colet" es una marca de \
alfajor/chocolate, "rapiditas" son tortillas para wraps).

Para cada producto pendiente devolvé:
- category: el nombre de la categoría. Usá una de sus categorías existentes cuando corresponda, \
escrita exactamente igual. Si ninguna sirve, creá una nueva: corta, en plural, en español, del \
estilo "Carnes", "Pescados", "Verduras", "Frutas", "Lácteos", "Huevos", "Bebidas", "Almacén", \
"Panadería", "Snacks y dulces", "Congelados", "Limpieza", "Higiene personal". Preferí pocas \
categorías amplias antes que muchas chicas, y reusá las nuevas que ya hayas creado en esta \
misma respuesta.
- size_value y size_unit: el tamaño del envase solo si el nombre lo dice ("agua 6l" -> 6 y "l", \
"yogurt 1kg" -> 1 y "kg"). Unidades: g, kg, ml, l, u. Si no lo dice, 0 y "".
- same_as_product_id: el id de otro producto de la lista (pendiente o ya categorizado) solo si \
es claramente el mismo producto escrito distinto ("Limon" y "Limones", "Yogurth" y "Yogurt"). \
No juntes productos distintos de la misma familia: "Galletas" y "Galletas integrales", "Aceite" \
y "Aceite de oliva", "Agua" y "Agua 6l" son productos distintos. Si no, "".
- note: una nota corta en español solo cuando algo merece revisión, por ejemplo cuando los \
precios pagados varían mucho para algo que no se vende al peso ("Agua" a $65 y a $147: \
quizás son dos tamaños distintos). Para carne, pescado, verdura y fruta al peso es normal que \
el precio varíe: no lo marques. Si no hay nada que decir, "".
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
    return {pid: {"veces": n, "precio_min": float(lo), "precio_max": float(hi)} for pid, n, lo, hi in rows}


def _build_request(
    categories: list[PurchaseCategory],
    examples: list[PurchaseProduct],
    batch: list[PurchaseProduct],
    prices: dict[str, dict],
) -> str:
    category_names = {c.id: c.name for c in categories}
    payload = {
        "categorias_existentes": [c.name for c in categories],
        "productos_ya_categorizados": [
            {"id": p.id, "nombre": p.name, "categoria": category_names.get(p.category_id)}
            for p in examples
        ],
        "productos_pendientes": [
            {"id": p.id, "nombre": p.name, **prices.get(p.id, {})} for p in batch
        ],
    }
    return (
        "Categorizá los productos pendientes. Datos (JSON):\n\n"
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
