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
)


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

    db.delete(category)
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
                category_name=category_name,
                created_at=item.created_at,
            )
        )

    return PurchaseCartWithItems(
        id=cart.id,
        user_id=cart.user_id,
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

    cart = PurchaseCart(
        user_id=str(current_user.id),
        store_name=cart_in.store_name,
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
                category_name=category_name,
                created_at=item.created_at,
            )
        )

    return PurchaseCartWithItems(
        id=cart.id,
        user_id=cart.user_id,
        store_name=cart.store_name,
        is_active=cart.is_active,
        total=float(cart.total),
        created_at=cart.created_at,
        updated_at=cart.updated_at,
        completed_at=cart.completed_at,
        items=items_read,
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

    item = PurchaseCartItem(
        cart_id=str(cart_id),
        product_name=item_in.product_name,
        price=item_in.price,
        quantity=item_in.quantity,
        category_id=item_in.category_id,
    )
    db.add(item)

    # Update cart total
    cart.total = float(cart.total) + (item_in.price * item_in.quantity)
    cart.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(item)

    # Get category name
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
        category_name=category_name,
        created_at=item.created_at,
    )


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

    if item_update.product_name is not None:
        item.product_name = item_update.product_name
    if item_update.price is not None:
        item.price = item_update.price
    if item_update.quantity is not None:
        item.quantity = item_update.quantity
    if item_update.category_id is not None:
        item.category_id = item_update.category_id

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
