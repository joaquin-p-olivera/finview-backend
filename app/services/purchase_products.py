"""Links cart items to products.

Every cart item points to a product. The name the user typed is normalized
("Limones " -> "limon") and looked up in the product aliases; an unknown name
creates a new product with that name. A product's category is copied to its
items, so categorizing a product once covers every time it was bought.
"""

import re
import unicodedata

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.purchase import (
    PurchaseCartItem,
    PurchaseCart,
    PurchaseProduct,
    PurchaseProductAlias,
)

_UNIT_RE = re.compile(r"(\d)\s+(kg|g|gr|grs|ml|cc|l|lt|lts|u)\b")


def _singular(word: str) -> str:
    # Spanish plurals, enough to join "limon"/"limones" and "tomate"/"tomates".
    # It only builds a lookup key, so an odd result is harmless as long as
    # both forms give the same key.
    if len(word) > 4 and word.endswith("es") and word[-3] in "lnrdj":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and word[-2] in "aeiou":
        return word[:-1]
    return word


def normalize_name(name: str) -> str:
    """Lookup key for a product name: lowercase, no accents or punctuation,
    singular words, and "6 L" written as "6l"."""
    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = _UNIT_RE.sub(r"\1\2", text)
    return " ".join(_singular(word) for word in text.split())


def _display_name(name: str) -> str:
    name = " ".join(name.split())
    return name[:1].upper() + name[1:]


def find_product_by_name(db: Session, user_id: str, name: str) -> PurchaseProduct | None:
    key = normalize_name(name)
    if not key:
        return None
    alias = (
        db.query(PurchaseProductAlias)
        .filter(PurchaseProductAlias.user_id == user_id, PurchaseProductAlias.alias_key == key)
        .first()
    )
    if alias:
        return db.get(PurchaseProduct, alias.product_id)
    return None


def add_alias(db: Session, user_id: str, product: PurchaseProduct, name: str) -> None:
    """Remember that `name` means `product`. A name already used for another
    product keeps pointing there."""
    key = normalize_name(name)
    if not key:
        return
    exists = (
        db.query(PurchaseProductAlias.id)
        .filter(PurchaseProductAlias.user_id == user_id, PurchaseProductAlias.alias_key == key)
        .first()
    )
    if not exists:
        db.add(PurchaseProductAlias(user_id=user_id, product_id=product.id, alias_key=key))
        db.flush()


def get_or_create_product(db: Session, user_id: str, name: str) -> PurchaseProduct:
    product = find_product_by_name(db, user_id, name)
    if product:
        return product
    display = _display_name(name)
    # Same name ignoring case, but not reachable through an alias yet.
    product = (
        db.query(PurchaseProduct)
        .filter(PurchaseProduct.user_id == user_id)
        .filter(func.lower(func.btrim(PurchaseProduct.name)) == display.lower())
        .first()
    )
    if not product:
        product = PurchaseProduct(user_id=user_id, name=display)
        db.add(product)
        db.flush()
    add_alias(db, user_id, product, name)
    return product


def apply_product_category(item: PurchaseCartItem, product: PurchaseProduct | None) -> None:
    """Give the item its product's category, unless the item has one chosen by hand."""
    if item.categorized_by == "manual" and item.category_id:
        return
    if product and product.category_id:
        item.category_id = product.category_id
        item.categorized_by = "product"
    else:
        item.category_id = None
        item.categorized_by = None


def link_item(
    db: Session,
    user_id: str,
    item: PurchaseCartItem,
    product: PurchaseProduct | None = None,
) -> PurchaseProduct:
    """Point `item` to its product (the given one, or found / created from its
    name) and sync categories between them.

    A category chosen by hand for the item (categorized_by == "manual") wins
    for that item; if the product had none yet, the product takes it too, so
    the next time it's bought it comes categorized.
    """
    if product is None:
        product = get_or_create_product(db, user_id, item.product_name)
    else:
        add_alias(db, user_id, product, item.product_name)
    item.product_id = product.id

    if item.categorized_by == "manual" and item.category_id:
        if not product.category_id:
            set_product_category(db, product, item.category_id, source="manual")
    else:
        apply_product_category(item, product)
    return product


def product_items_query(db: Session, product_id: str):
    return db.query(PurchaseCartItem).filter(PurchaseCartItem.product_id == product_id)


def set_product_category(
    db: Session, product: PurchaseProduct, category_id: str | None, source: str = "manual"
) -> None:
    """Change a product's category and copy it to all of its items (except
    those with a category chosen by hand)."""
    product.category_id = category_id
    product.category_source = source if category_id else None
    db.flush()
    for item in product_items_query(db, product.id).all():
        apply_product_category(item, product)


def link_unlinked_items(db: Session, user_id: str) -> dict:
    """Link every item of the user without a product (the history before
    products existed). Safe to run more than once."""
    items = (
        db.query(PurchaseCartItem)
        .join(PurchaseCart, PurchaseCartItem.cart_id == PurchaseCart.id)
        .filter(PurchaseCart.user_id == user_id, PurchaseCartItem.product_id.is_(None))
        .order_by(PurchaseCartItem.created_at)
        .all()
    )
    products_before = db.query(PurchaseProduct).filter(PurchaseProduct.user_id == user_id).count()
    for item in items:
        # Categories set before products existed were chosen by hand.
        if item.category_id and not item.categorized_by:
            item.categorized_by = "manual"
        link_item(db, user_id, item)
    products_after = db.query(PurchaseProduct).filter(PurchaseProduct.user_id == user_id).count()
    return {"linked_items": len(items), "created_products": products_after - products_before}


def merge_products(db: Session, user_id: str, source: PurchaseProduct, target: PurchaseProduct) -> None:
    """Move every item and alias of `source` into `target` and delete `source`."""
    if not target.category_id and source.category_id:
        target.category_id = source.category_id
        target.category_source = source.category_source
    if target.size_value is None and source.size_value is not None:
        target.size_value = source.size_value
        target.size_unit = source.size_unit

    db.query(PurchaseProductAlias).filter(PurchaseProductAlias.product_id == source.id).update(
        {PurchaseProductAlias.product_id: target.id}, synchronize_session=False
    )
    product_items_query(db, source.id).update(
        {PurchaseCartItem.product_id: target.id}, synchronize_session=False
    )
    db.flush()
    db.delete(source)
    db.flush()
    for item in product_items_query(db, target.id).all():
        apply_product_category(item, target)
    # Typing the old name now finds the merged product.
    add_alias(db, user_id, target, source.name)
