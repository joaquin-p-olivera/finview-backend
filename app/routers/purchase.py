from datetime import datetime
from typing import Annotated, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models.purchase import (
    PurchaseCart,
    PurchaseCartItem,
    PurchaseCategory,
    PurchaseList,
    PurchaseListItem,
    PurchaseProduct,
    PurchaseProductAlias,
    PurchaseStore,
)
from ..models.user import User
from ..schemas.purchase import (
    PurchaseCartCreate,
    PurchaseCartItemCreate,
    PurchaseCartItemRead,
    PurchaseCartItemUpdate,
    PurchaseCartRead,
    PurchaseCartWithItems,
    PurchaseCategoryCreate,
    PurchaseCategoryRead,
    PurchaseCategoryUpdate,
    PurchaseListCreate,
    PurchaseListItemCreate,
    PurchaseListItemRead,
    PurchaseListItemUpdate,
    PurchaseListRead,
    PurchaseListUpdate,
    PurchaseListWithItems,
    PurchaseProductLinkResult,
    PurchaseProductMerge,
    PurchaseProductRead,
    PurchaseProductUpdate,
    PurchaseStoreCreate,
    PurchaseStoreRead,
    PurchaseStoreUpdate,
)
from ..services import purchase_products


router = APIRouter(prefix="/api/v1/purchase", tags=["purchase"])

DbDep = Annotated[Session, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


# ============== CATEGORIES ==============


@router.get("/categories", response_model=List[PurchaseCategoryRead])
def list_categories(db: DbDep, current_user: CurrentUserDep):
    return (
        db.query(PurchaseCategory)
        .filter(PurchaseCategory.user_id == str(current_user.id))
        .order_by(PurchaseCategory.name.asc())
        .all()
    )


@router.post("/categories", response_model=PurchaseCategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(
    category_in: PurchaseCategoryCreate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    exists = (
        db.query(PurchaseCategory)
        .filter(
            PurchaseCategory.user_id == str(current_user.id),
            PurchaseCategory.name == category_in.name,
        )
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya tenés una categoría con ese nombre",
        )
    category = PurchaseCategory(
        user_id=str(current_user.id),
        name=category_in.name,
        color=category_in.color,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/categories/{category_id}", response_model=PurchaseCategoryRead)
def update_category(
    category_id: UUID,
    category_update: PurchaseCategoryUpdate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    category = (
        db.query(PurchaseCategory)
        .filter(
            PurchaseCategory.id == str(category_id),
            PurchaseCategory.user_id == str(current_user.id),
        )
        .first()
    )
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")

    if category_update.name is not None:
        exists = (
            db.query(PurchaseCategory)
            .filter(
                PurchaseCategory.user_id == str(current_user.id),
                PurchaseCategory.name == category_update.name,
                PurchaseCategory.id != str(category_id),
            )
            .first()
        )
        if exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya tenés otra categoría con ese nombre",
            )
        category.name = category_update.name

    if category_update.color is not None:
        category.color = category_update.color

    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    category = (
        db.query(PurchaseCategory)
        .filter(
            PurchaseCategory.id == str(category_id),
            PurchaseCategory.user_id == str(current_user.id),
        )
        .first()
    )
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")

    # Products and items lose the category (ON DELETE SET NULL); clear how it
    # was set too.
    db.query(PurchaseCartItem).filter(PurchaseCartItem.category_id == category.id).update(
        {PurchaseCartItem.categorized_by: None}, synchronize_session=False
    )
    db.query(PurchaseProduct).filter(PurchaseProduct.category_id == category.id).update(
        {PurchaseProduct.category_source: None}, synchronize_session=False
    )
    db.delete(category)
    db.commit()
    return None


def _get_user_category(db: Session, user_id: str, category_id: str) -> PurchaseCategory:
    category = (
        db.query(PurchaseCategory)
        .filter(PurchaseCategory.id == category_id, PurchaseCategory.user_id == user_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")
    return category


# ============== PRODUCTS ==============


def _get_user_product(db: Session, user_id: str, product_id: str) -> PurchaseProduct:
    product = (
        db.query(PurchaseProduct)
        .filter(PurchaseProduct.id == product_id, PurchaseProduct.user_id == user_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return product


def _products_read(db: Session, user_id: str, products: list[PurchaseProduct]) -> list[PurchaseProductRead]:
    """Products with what was paid for them: times bought, prices and the
    last purchase. A user has a few hundred items, so they're summed here."""
    ids = [p.id for p in products]
    stats: dict[str, dict] = {pid: {"times": 0, "total": 0.0, "prices": [], "last": None} for pid in ids}
    aliases: dict[str, list[str]] = {pid: [] for pid in ids}
    if ids:
        rows = (
            db.query(PurchaseCartItem, PurchaseCart.store_name, PurchaseCart.completed_at)
            .join(PurchaseCart, PurchaseCartItem.cart_id == PurchaseCart.id)
            .filter(PurchaseCartItem.product_id.in_(ids))
            .all()
        )
        for item, store_name, completed_at in rows:
            entry = stats[item.product_id]
            price = float(item.price)
            entry["times"] += 1
            entry["total"] += price * item.quantity
            # Items brought from a shopping list start with price 0.
            if price > 0:
                entry["prices"].append(price)
                bought_at = completed_at or item.created_at
                if entry["last"] is None or bought_at > entry["last"][0]:
                    entry["last"] = (bought_at, price, store_name)
        for alias in db.query(PurchaseProductAlias).filter(PurchaseProductAlias.product_id.in_(ids)):
            aliases[alias.product_id].append(alias.alias_key)

    categories = {
        c.id: c.name
        for c in db.query(PurchaseCategory).filter(PurchaseCategory.user_id == user_id).all()
    }
    result = []
    for p in products:
        entry = stats[p.id]
        last = entry["last"]
        result.append(
            PurchaseProductRead(
                id=p.id,
                name=p.name,
                category_id=p.category_id,
                category_name=categories.get(p.category_id) if p.category_id else None,
                category_source=p.category_source,
                size_value=float(p.size_value) if p.size_value is not None else None,
                size_unit=p.size_unit,
                times_bought=entry["times"],
                total_spent=round(entry["total"], 2),
                last_price=last[1] if last else None,
                min_price=min(entry["prices"]) if entry["prices"] else None,
                max_price=max(entry["prices"]) if entry["prices"] else None,
                last_store=last[2] if last else None,
                last_bought_at=last[0] if last else None,
                aliases=sorted(aliases[p.id]),
                created_at=p.created_at,
            )
        )
    return result


@router.get("/products", response_model=List[PurchaseProductRead])
def list_products(db: DbDep, current_user: CurrentUserDep):
    products = (
        db.query(PurchaseProduct)
        .filter(PurchaseProduct.user_id == str(current_user.id))
        .order_by(PurchaseProduct.name)
        .all()
    )
    return _products_read(db, str(current_user.id), products)


@router.get("/products/unlinked-items")
def count_unlinked_items(db: DbDep, current_user: CurrentUserDep):
    """Items without a product yet (bought before products existed)."""
    count = (
        db.query(func.count(PurchaseCartItem.id))
        .join(PurchaseCart, PurchaseCartItem.cart_id == PurchaseCart.id)
        .filter(PurchaseCart.user_id == str(current_user.id), PurchaseCartItem.product_id.is_(None))
        .scalar()
    )
    return {"count": count or 0}


@router.post("/products/link-items", response_model=PurchaseProductLinkResult)
def link_items_to_products(db: DbDep, current_user: CurrentUserDep):
    """Group the items bought so far into products, by name. Safe to repeat."""
    result = purchase_products.link_unlinked_items(db, str(current_user.id))
    db.commit()
    return result


@router.put("/products/{product_id}", response_model=PurchaseProductRead)
def update_product(
    product_id: UUID,
    product_update: PurchaseProductUpdate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    user_id = str(current_user.id)
    product = _get_user_product(db, user_id, str(product_id))
    fields = product_update.model_fields_set

    if product_update.name is not None:
        name = " ".join(product_update.name.split())
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El nombre no puede estar vacío")
        duplicate = (
            db.query(PurchaseProduct)
            .filter(
                PurchaseProduct.user_id == user_id,
                func.lower(func.btrim(PurchaseProduct.name)) == name.lower(),
                PurchaseProduct.id != product.id,
            )
            .first()
        )
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya tenés un producto con ese nombre. Usá \"Unir\" para juntarlos.",
            )
        product.name = name
        purchase_products.add_alias(db, user_id, product, name)

    if "category_id" in fields:
        if product_update.category_id:
            _get_user_category(db, user_id, product_update.category_id)
        purchase_products.set_product_category(db, product, product_update.category_id, source="manual")

    if "size_value" in fields or "size_unit" in fields:
        product.size_value = product_update.size_value
        product.size_unit = (product_update.size_unit or "").strip().lower() or None

    db.commit()
    db.refresh(product)
    return _products_read(db, user_id, [product])[0]


@router.post("/products/{product_id}/merge", response_model=PurchaseProductRead)
def merge_product(
    product_id: UUID,
    merge_in: PurchaseProductMerge,
    db: DbDep,
    current_user: CurrentUserDep,
):
    """Merge this product into another one (e.g. "Agua bidon" into "Agua 6L")."""
    user_id = str(current_user.id)
    source = _get_user_product(db, user_id, str(product_id))
    target = _get_user_product(db, user_id, merge_in.into_product_id)
    if source.id == target.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Elegí otro producto")
    purchase_products.merge_products(db, user_id, source, target)
    db.commit()
    db.refresh(target)
    return _products_read(db, user_id, [target])[0]


# ============== STORES ==============


def _find_store_by_name(db: Session, user_id: str, name: str) -> PurchaseStore | None:
    return (
        db.query(PurchaseStore)
        .filter(
            PurchaseStore.user_id == user_id,
            func.lower(func.btrim(PurchaseStore.name)) == name.strip().lower(),
        )
        .first()
    )


def _get_user_store(db: Session, user_id: str, store_id: str) -> PurchaseStore:
    store = (
        db.query(PurchaseStore)
        .filter(PurchaseStore.id == store_id, PurchaseStore.user_id == user_id)
        .first()
    )
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermercado no encontrado")
    return store


@router.get("/stores", response_model=List[PurchaseStoreRead])
def list_stores(db: DbDep, current_user: CurrentUserDep):
    # Most used first, so the cart form shows the usual stores on top.
    cart_count = func.count(PurchaseCart.id)
    rows = (
        db.query(PurchaseStore, cart_count)
        .outerjoin(PurchaseCart, PurchaseCart.store_id == PurchaseStore.id)
        .filter(PurchaseStore.user_id == str(current_user.id))
        .group_by(PurchaseStore.id)
        .order_by(cart_count.desc(), PurchaseStore.name)
        .all()
    )
    return [store for store, _ in rows]


@router.post("/stores", response_model=PurchaseStoreRead, status_code=status.HTTP_201_CREATED)
def create_store(
    store_in: PurchaseStoreCreate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    name = store_in.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El nombre no puede estar vacío")
    if _find_store_by_name(db, str(current_user.id), name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya tenés un supermercado con ese nombre",
        )
    store = PurchaseStore(user_id=str(current_user.id), name=name)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.put("/stores/{store_id}", response_model=PurchaseStoreRead)
def update_store(
    store_id: UUID,
    store_update: PurchaseStoreUpdate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    store = _get_user_store(db, str(current_user.id), str(store_id))

    if store_update.name is not None:
        name = store_update.name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El nombre no puede estar vacío")
        existing = _find_store_by_name(db, str(current_user.id), name)
        if existing and existing.id != store.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya tenés otro supermercado con ese nombre",
            )
        store.name = name
        # Carts keep a copy of the name: rename them too so every cart of this
        # store shows the same name.
        db.query(PurchaseCart).filter(PurchaseCart.store_id == store.id).update(
            {PurchaseCart.store_name: name}, synchronize_session=False
        )

    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.delete("/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_store(
    store_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    store = _get_user_store(db, str(current_user.id), str(store_id))
    # Carts keep their store_name; the FK sets their store_id to NULL.
    db.delete(store)
    db.commit()
    return None


# ============== CARTS ==============


@router.get("/carts", response_model=List[PurchaseCartRead])
def list_carts(
    db: DbDep,
    current_user: CurrentUserDep,
    limit: int = Query(default=20, ge=1, le=100),
):
    return (
        db.query(PurchaseCart)
        .filter(PurchaseCart.user_id == str(current_user.id))
        .order_by(PurchaseCart.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/carts/active")
def get_active_cart(db: DbDep, current_user: CurrentUserDep):
    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == True,
        )
        .first()
    )
    if not cart:
        return None

    items = (
        db.query(PurchaseCartItem, PurchaseCategory.name.label("category_name"))
        .outerjoin(PurchaseCategory, PurchaseCartItem.category_id == PurchaseCategory.id)
        .filter(PurchaseCartItem.cart_id == cart.id)
        .all()
    )

    items_read = []
    for item, category_name in items:
        items_read.append(
            PurchaseCartItemRead(
                id=item.id,
                cart_id=item.cart_id,
                product_name=item.product_name,
                price=float(item.price),
                quantity=item.quantity,
                category_id=item.category_id,
                product_id=item.product_id,
                category_name=category_name,
                created_at=item.created_at,
            )
        )

    return PurchaseCartWithItems(
        id=cart.id,
        user_id=cart.user_id,
        store_id=cart.store_id,
        store_name=cart.store_name,
        is_active=cart.is_active,
        total=float(cart.total),
        created_at=cart.created_at,
        updated_at=cart.updated_at,
        completed_at=cart.completed_at,
        items=items_read,
    )


@router.post("/carts", response_model=PurchaseCartRead, status_code=status.HTTP_201_CREATED)
def create_cart(
    cart_in: PurchaseCartCreate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    # Check if there's already an active cart
    existing_active = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == True,
        )
        .first()
    )
    if existing_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya tenés un carrito activo. Finalizá o cancelá el actual antes de crear uno nuevo.",
        )

    if cart_in.store_id:
        store = _get_user_store(db, str(current_user.id), cart_in.store_id)
    else:
        store = _find_store_by_name(db, str(current_user.id), cart_in.store_name)
        if not store:
            store = PurchaseStore(user_id=str(current_user.id), name=cart_in.store_name.strip())
            db.add(store)
            db.flush()

    cart = PurchaseCart(
        user_id=str(current_user.id),
        store_id=store.id,
        store_name=store.name,
        is_active=True,
        total=0,
    )
    db.add(cart)
    db.commit()
    db.refresh(cart)
    return cart


@router.get("/carts/{cart_id}", response_model=PurchaseCartWithItems)
def get_cart(
    cart_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado")

    items = (
        db.query(PurchaseCartItem, PurchaseCategory.name.label("category_name"))
        .outerjoin(PurchaseCategory, PurchaseCartItem.category_id == PurchaseCategory.id)
        .filter(PurchaseCartItem.cart_id == cart.id)
        .all()
    )

    items_read = []
    for item, category_name in items:
        items_read.append(
            PurchaseCartItemRead(
                id=item.id,
                cart_id=item.cart_id,
                product_name=item.product_name,
                price=float(item.price),
                quantity=item.quantity,
                category_id=item.category_id,
                product_id=item.product_id,
                category_name=category_name,
                created_at=item.created_at,
            )
        )

    return PurchaseCartWithItems(
        id=cart.id,
        user_id=cart.user_id,
        store_id=cart.store_id,
        store_name=cart.store_name,
        is_active=cart.is_active,
        total=float(cart.total),
        created_at=cart.created_at,
        updated_at=cart.updated_at,
        completed_at=cart.completed_at,
        items=items_read,
    )


def _cart_item_read(item: PurchaseCartItem, db: Session) -> PurchaseCartItemRead:
    category_name = None
    if item.category_id:
        category = db.query(PurchaseCategory).filter(PurchaseCategory.id == item.category_id).first()
        if category:
            category_name = category.name

    return PurchaseCartItemRead(
        id=item.id,
        cart_id=item.cart_id,
        product_name=item.product_name,
        price=float(item.price),
        quantity=item.quantity,
        category_id=item.category_id,
        product_id=item.product_id,
        category_name=category_name,
        created_at=item.created_at,
    )


@router.post("/carts/{cart_id}/items", response_model=PurchaseCartItemRead, status_code=status.HTTP_201_CREATED)
def add_cart_item(
    cart_id: UUID,
    item_in: PurchaseCartItemCreate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == True,
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado o inactivo")

    if item_in.id is not None:
        # A retry of an add that already went through (the client reuses the
        # id it generated): return the stored item instead of adding it again.
        # Unlike the Idempotency-Key cache, this survives API restarts.
        existing = db.query(PurchaseCartItem).filter(PurchaseCartItem.id == str(item_in.id)).first()
        if existing:
            if existing.cart_id != str(cart_id):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El id del item ya existe")
            return _cart_item_read(existing, db)

    user_id = str(current_user.id)
    product = None
    if item_in.product_id:
        # A product deleted or merged while the item waited offline: find it
        # by name instead.
        product = (
            db.query(PurchaseProduct)
            .filter(PurchaseProduct.id == item_in.product_id, PurchaseProduct.user_id == user_id)
            .first()
        )
    category_id = item_in.category_id
    if category_id and not (
        db.query(PurchaseCategory.id)
        .filter(PurchaseCategory.id == category_id, PurchaseCategory.user_id == user_id)
        .first()
    ):
        # Deleted while the item waited offline: keep the item, without it.
        category_id = None

    item = PurchaseCartItem(
        **({"id": str(item_in.id)} if item_in.id is not None else {}),
        cart_id=str(cart_id),
        product_name=item_in.product_name,
        price=item_in.price,
        quantity=item_in.quantity,
        category_id=category_id,
        categorized_by="manual" if category_id else None,
    )
    db.add(item)
    purchase_products.link_item(db, user_id, item, product)

    # Update cart total
    cart.total = float(cart.total) + (item_in.price * item_in.quantity)
    cart.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(item)

    return _cart_item_read(item, db)


@router.put("/carts/{cart_id}/items/{item_id}", response_model=PurchaseCartItemRead)
def update_cart_item(
    cart_id: UUID,
    item_id: UUID,
    item_update: PurchaseCartItemUpdate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado")

    item = (
        db.query(PurchaseCartItem)
        .filter(
            PurchaseCartItem.id == str(item_id),
            PurchaseCartItem.cart_id == str(cart_id),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado")

    old_subtotal = float(item.price) * item.quantity

    user_id = str(current_user.id)
    if item_update.product_name is not None:
        renamed = item_update.product_name != item.product_name
        item.product_name = item_update.product_name
        if renamed:
            purchase_products.link_item(db, user_id, item)
    if item_update.price is not None:
        item.price = item_update.price
    if item_update.quantity is not None:
        item.quantity = item_update.quantity
    if "category_id" in item_update.model_fields_set:
        if item_update.category_id:
            _get_user_category(db, user_id, item_update.category_id)
            item.category_id = item_update.category_id
            item.categorized_by = "manual"
            if item.product_id:
                product = db.get(PurchaseProduct, item.product_id)
                if product and not product.category_id:
                    purchase_products.set_product_category(db, product, item.category_id)
        else:
            # Back to its product's category.
            item.categorized_by = None
            purchase_products.apply_product_category(
                item, db.get(PurchaseProduct, item.product_id) if item.product_id else None
            )

    new_subtotal = float(item.price) * item.quantity
    cart.total = float(cart.total) - old_subtotal + new_subtotal
    cart.updated_at = datetime.utcnow()

    db.add(item)
    db.commit()
    db.refresh(item)

    category_name = None
    if item.category_id:
        category = db.query(PurchaseCategory).filter(PurchaseCategory.id == item.category_id).first()
        if category:
            category_name = category.name

    return PurchaseCartItemRead(
        id=item.id,
        cart_id=item.cart_id,
        product_name=item.product_name,
        price=float(item.price),
        quantity=item.quantity,
        category_id=item.category_id,
        product_id=item.product_id,
        category_name=category_name,
        created_at=item.created_at,
    )


@router.delete("/carts/{cart_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cart_item(
    cart_id: UUID,
    item_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado")

    item = (
        db.query(PurchaseCartItem)
        .filter(
            PurchaseCartItem.id == str(item_id),
            PurchaseCartItem.cart_id == str(cart_id),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado")

    # Update cart total
    cart.total = float(cart.total) - (float(item.price) * item.quantity)
    cart.updated_at = datetime.utcnow()

    db.delete(item)
    db.commit()
    return None


@router.post("/carts/{cart_id}/complete", response_model=PurchaseCartRead)
def complete_cart(
    cart_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == True,
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado o ya completado")

    cart.is_active = False
    cart.completed_at = datetime.utcnow()
    db.add(cart)
    db.commit()
    db.refresh(cart)
    return cart


# ============== LISTS ==============


@router.get("/lists", response_model=List[PurchaseListRead])
def list_lists(db: DbDep, current_user: CurrentUserDep):
    return (
        db.query(PurchaseList)
        .filter(PurchaseList.user_id == str(current_user.id))
        .order_by(PurchaseList.created_at.desc())
        .all()
    )


@router.post("/lists", response_model=PurchaseListRead, status_code=status.HTTP_201_CREATED)
def create_list(
    list_in: PurchaseListCreate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = PurchaseList(
        user_id=str(current_user.id),
        name=list_in.name,
    )
    db.add(list)
    db.commit()
    db.refresh(list)
    return list


@router.get("/lists/{list_id}", response_model=PurchaseListWithItems)
def get_list(
    list_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    items = db.query(PurchaseListItem).filter(PurchaseListItem.list_id == list.id).all()

    items_read = []
    for item in items:
        items_read.append(
            PurchaseListItemRead(
                id=item.id,
                list_id=item.list_id,
                product_name=item.product_name,
                quantity=item.quantity,
                is_checked=item.is_checked,
                created_at=item.created_at,
            )
        )

    return PurchaseListWithItems(
        id=list.id,
        user_id=list.user_id,
        name=list.name,
        created_at=list.created_at,
        items=items_read,
    )


@router.put("/lists/{list_id}", response_model=PurchaseListRead)
def update_list(
    list_id: UUID,
    list_update: PurchaseListUpdate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    if list_update.name is not None:
        list.name = list_update.name

    db.add(list)
    db.commit()
    db.refresh(list)
    return list


@router.delete("/lists/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_list(
    list_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    db.delete(list)
    db.commit()
    return None


@router.post("/lists/{list_id}/items", response_model=PurchaseListItemRead, status_code=status.HTTP_201_CREATED)
def add_list_item(
    list_id: UUID,
    item_in: PurchaseListItemCreate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    item = PurchaseListItem(
        list_id=str(list_id),
        product_name=item_in.product_name,
        quantity=item_in.quantity,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/lists/{list_id}/items/{item_id}", response_model=PurchaseListItemRead)
def update_list_item(
    list_id: UUID,
    item_id: UUID,
    item_update: PurchaseListItemUpdate,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    item = (
        db.query(PurchaseListItem)
        .filter(
            PurchaseListItem.id == str(item_id),
            PurchaseListItem.list_id == str(list_id),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado")

    if item_update.product_name is not None:
        item.product_name = item_update.product_name
    if item_update.quantity is not None:
        item.quantity = item_update.quantity
    if item_update.is_checked is not None:
        item.is_checked = item_update.is_checked

    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/lists/{list_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_list_item(
    list_id: UUID,
    item_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    item = (
        db.query(PurchaseListItem)
        .filter(
            PurchaseListItem.id == str(item_id),
            PurchaseListItem.list_id == str(list_id),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado")

    db.delete(item)
    db.commit()
    return None


@router.post("/lists/{list_id}/add-to-cart/{cart_id}", response_model=PurchaseCartWithItems)
def add_list_to_cart(
    list_id: UUID,
    cart_id: UUID,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == True,
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado o inactivo")

    items = db.query(PurchaseListItem).filter(PurchaseListItem.list_id == list.id).all()

    for item in items:
        cart_item = PurchaseCartItem(
            cart_id=cart.id,
            product_name=item.product_name,
            price=0,
            quantity=item.quantity or 1,
        )
        db.add(cart_item)
        purchase_products.link_item(db, str(current_user.id), cart_item)
        cart.total = float(cart.total)

    cart.updated_at = datetime.utcnow()
    db.commit()

    return get_cart(cart_id, db, current_user)


class AddItemToCartRequest(BaseModel):
    price: float
    quantity: int = 1


@router.post("/lists/{list_id}/items/{item_id}/add-to-cart/{cart_id}", response_model=PurchaseCartWithItems)
def add_list_item_to_cart(
    list_id: UUID,
    item_id: UUID,
    cart_id: UUID,
    request: AddItemToCartRequest,
    db: DbDep,
    current_user: CurrentUserDep,
):
    list = (
        db.query(PurchaseList)
        .filter(
            PurchaseList.id == str(list_id),
            PurchaseList.user_id == str(current_user.id),
        )
        .first()
    )
    if not list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lista no encontrada")

    cart = (
        db.query(PurchaseCart)
        .filter(
            PurchaseCart.id == str(cart_id),
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == True,
        )
        .first()
    )
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Carrito no encontrado o inactivo")

    item = (
        db.query(PurchaseListItem)
        .filter(
            PurchaseListItem.id == str(item_id),
            PurchaseListItem.list_id == str(list_id),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado")

    cart_item = PurchaseCartItem(
        cart_id=cart.id,
        product_name=item.product_name,
        price=request.price,
        quantity=request.quantity,
    )
    db.add(cart_item)
    purchase_products.link_item(db, str(current_user.id), cart_item)

    cart.total = float(cart.total) + (request.price * request.quantity)
    cart.updated_at = datetime.utcnow()

    db.commit()

    return get_cart(cart_id, db, current_user)


# ============== STATS ==============


@router.get("/stats")
def get_purchase_stats(
    db: DbDep,
    current_user: CurrentUserDep,
    days: int = Query(default=30, ge=7, le=365),
):
    from datetime import timedelta

    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Total spent in period
    total_spent = (
        db.query(func.sum(PurchaseCart.total))
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == False,
            PurchaseCart.completed_at >= cutoff_date,
        )
        .scalar()
    ) or 0

    # Carts completed in period
    carts_count = (
        db.query(func.count(PurchaseCart.id))
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == False,
            PurchaseCart.completed_at >= cutoff_date,
        )
        .scalar()
    ) or 0

    # Average per cart
    avg_per_cart = float(total_spent) / carts_count if carts_count > 0 else 0

    # By store
    by_store = (
        db.query(
            PurchaseCart.store_name,
            func.sum(PurchaseCart.total).label("total"),
            func.count(PurchaseCart.id).label("count"),
        )
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == False,
            PurchaseCart.completed_at >= cutoff_date,
        )
        .group_by(PurchaseCart.store_name)
        .all()
    )

    # By month
    year_col = func.extract('year', PurchaseCart.completed_at)
    month_col = func.extract('month', PurchaseCart.completed_at)
    by_month = (
        db.query(
            year_col.label("year"),
            month_col.label("month"),
            func.sum(PurchaseCart.total).label("total"),
            func.count(PurchaseCart.id).label("count"),
        )
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == False,
            PurchaseCart.completed_at >= cutoff_date,
        )
        .group_by(year_col, month_col)
        .order_by(year_col, month_col)
        .all()
    )

    # Individual carts
    individual_carts = (
        db.query(
            PurchaseCart.id,
            PurchaseCart.store_name,
            PurchaseCart.total,
            PurchaseCart.completed_at,
        )
        .filter(
            PurchaseCart.user_id == str(current_user.id),
            PurchaseCart.is_active == False,
            PurchaseCart.completed_at >= cutoff_date,
        )
        .order_by(PurchaseCart.completed_at)
        .all()
    )

    return {
        "total_spent": float(total_spent),
        "carts_count": carts_count,
        "avg_per_cart": avg_per_cart,
        "by_store": [
            {"store": s.store_name or "Sin nombre", "total": float(s.total), "count": s.count}
            for s in by_store
        ],
        "by_month": [
            {"month": f"{int(m.year)}-{int(m.month):02d}", "total": float(m.total), "count": m.count}
            for m in by_month
        ],
        "individual_carts": [
            {
                "id": str(c.id),
                "store": c.store_name or "Sin nombre",
                "total": float(c.total),
                "date": c.completed_at.strftime("%Y-%m-%d") if c.completed_at else None,
            }
            for c in individual_carts
        ],
    }
