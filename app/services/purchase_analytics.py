"""Spending and price analysis over completed carts.

Everything is computed from the user's completed cart items (a few hundred
rows), dated by the cart's completion in Uruguay time. Prices are unit
prices: an item's spend is price * quantity. Items with price 0 (brought
from a shopping list and never priced) count for nothing.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models.purchase import PurchaseCart, PurchaseCartItem, PurchaseCategory, PurchaseProduct

# Uruguay has no daylight saving time since 2015.
URUGUAY_TZ = timezone(timedelta(hours=-3))
UNCATEGORIZED = "Sin categoría"


@dataclass
class Purchase:
    cart_id: str
    store: str
    bought_at: datetime  # Uruguay time
    product_id: str | None
    product_name: str
    category: str
    price: float
    quantity: int

    @property
    def month(self) -> str:
        return self.bought_at.strftime("%Y-%m")

    @property
    def spent(self) -> float:
        return self.price * self.quantity


def load_purchases(db: Session, user_id: str, since: datetime | None = None) -> list[Purchase]:
    query = (
        db.query(PurchaseCartItem, PurchaseCart, PurchaseProduct.name, PurchaseCategory.name)
        .join(PurchaseCart, PurchaseCartItem.cart_id == PurchaseCart.id)
        .outerjoin(PurchaseProduct, PurchaseCartItem.product_id == PurchaseProduct.id)
        .outerjoin(PurchaseCategory, PurchaseCartItem.category_id == PurchaseCategory.id)
        .filter(
            PurchaseCart.user_id == user_id,
            PurchaseCart.is_active == False,  # noqa: E712
            PurchaseCart.completed_at.isnot(None),
            PurchaseCartItem.price > 0,
        )
    )
    if since is not None:
        query = query.filter(PurchaseCart.completed_at >= since)
    purchases = []
    for item, cart, product_name, category_name in query.all():
        completed_at = cart.completed_at
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        purchases.append(
            Purchase(
                cart_id=cart.id,
                store=cart.store_name or "Sin nombre",
                bought_at=completed_at.astimezone(URUGUAY_TZ),
                product_id=item.product_id,
                product_name=product_name or item.product_name,
                category=category_name or UNCATEGORIZED,
                price=float(item.price),
                quantity=item.quantity,
            )
        )
    purchases.sort(key=lambda p: p.bought_at)
    return purchases


def months_ago_start(months: int) -> datetime:
    """Start (UTC) of the month `months - 1` months before the current one, in
    Uruguay time: months=1 is this month, 12 the last twelve months."""
    now = datetime.now(URUGUAY_TZ)
    year, month = now.year, now.month - (months - 1)
    while month < 1:
        month += 12
        year -= 1
    return datetime(year, month, 1, tzinfo=URUGUAY_TZ).astimezone(timezone.utc)


def _round(value: float) -> float:
    return round(value, 2)


def category_colors(db: Session, user_id: str) -> dict[str, str | None]:
    return {
        c.name: c.color
        for c in db.query(PurchaseCategory).filter(PurchaseCategory.user_id == user_id).all()
    }


def by_category_month(purchases: list[Purchase]) -> list[dict]:
    """Spend per month and category: [{month, categories: {name: total}, total}]."""
    months: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for p in purchases:
        months[p.month][p.category] += p.spent
    return [
        {
            "month": month,
            "categories": {name: _round(total) for name, total in cats.items()},
            "total": _round(sum(cats.values())),
        }
        for month, cats in sorted(months.items())
    ]


def by_cart(purchases: list[Purchase], limit: int) -> list[dict]:
    """Spend per category for the last `limit` carts, oldest first."""
    carts: dict[str, dict] = {}
    for p in purchases:
        cart = carts.setdefault(
            p.cart_id,
            {"cart_id": p.cart_id, "date": p.bought_at.date().isoformat(), "store": p.store,
             "categories": defaultdict(float)},
        )
        cart["categories"][p.category] += p.spent
    result = []
    for cart in list(carts.values())[-limit:]:
        cats = cart.pop("categories")
        cart["categories"] = {name: _round(total) for name, total in cats.items()}
        cart["total"] = _round(sum(cats.values()))
        result.append(cart)
    return result


def category_totals(purchases: list[Purchase]) -> list[dict]:
    totals: dict[str, float] = defaultdict(float)
    for p in purchases:
        totals[p.category] += p.spent
    grand = sum(totals.values()) or 1
    return [
        {"category": name, "total": _round(total), "share": round(total / grand, 4)}
        for name, total in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def top_products(purchases: list[Purchase], limit: int = 10) -> list[dict]:
    products: dict[str, dict] = {}
    for p in purchases:
        key = p.product_id or p.product_name.lower()
        entry = products.setdefault(
            key, {"product_id": p.product_id, "name": p.product_name, "category": p.category,
                  "total": 0.0, "times": 0, "dates": []},
        )
        entry["total"] += p.spent
        entry["times"] += 1
        entry["dates"].append(p.bought_at)
    grand = sum(e["total"] for e in products.values()) or 1
    result = []
    for entry in sorted(products.values(), key=lambda e: -e["total"])[:limit]:
        dates = sorted({d.date() for d in entry.pop("dates")})
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        entry["total"] = _round(entry["total"])
        entry["share"] = round(entry["total"] / grand, 4)
        entry["avg_days_between"] = round(sum(gaps) / len(gaps)) if gaps else None
        result.append(entry)
    return result


def price_changes(purchases: list[Purchase]) -> list[dict]:
    """For each product bought at least twice: last price against the previous
    one and against its average. Sorted by the change vs the previous price."""
    by_product: dict[str, list[Purchase]] = defaultdict(list)
    for p in purchases:
        if p.product_id:
            by_product[p.product_id].append(p)
    result = []
    for product_id, items in by_product.items():
        if len(items) < 2:
            continue
        last, previous = items[-1], items[-2]
        average = sum(i.price for i in items) / len(items)
        result.append(
            {
                "product_id": product_id,
                "name": last.product_name,
                "category": last.category,
                "times": len(items),
                "last_price": last.price,
                "last_date": last.bought_at.date().isoformat(),
                "last_store": last.store,
                "previous_price": previous.price,
                "previous_date": previous.bought_at.date().isoformat(),
                "change": round(last.price / previous.price - 1, 4),
                "vs_average": round(last.price / average - 1, 4),
            }
        )
    result.sort(key=lambda r: -r["change"])
    return result


def basket_index(purchases: list[Purchase]) -> list[dict]:
    """Personal inflation: each month against the previous one, comparing only
    products bought in both (average unit price per month). The index starts
    at 100 and accumulates the monthly changes."""
    prices: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for p in purchases:
        if p.product_id:
            prices[p.month][p.product_id].append(p.price)
    months = sorted(prices)
    result = []
    index = 100.0
    for i, month in enumerate(months):
        if i == 0:
            result.append({"month": month, "index": 100.0, "change": None, "products_compared": 0})
            continue
        prev, cur = prices[months[i - 1]], prices[month]
        common = set(prev) & set(cur)
        if not common:
            result.append({"month": month, "index": round(index, 2), "change": None, "products_compared": 0})
            continue
        # Geometric mean of the per-product price ratios, so a product that
        # doubles and one that halves cancel out.
        log_sum = 0.0
        for product_id in common:
            ratio = (sum(cur[product_id]) / len(cur[product_id])) / (sum(prev[product_id]) / len(prev[product_id]))
            log_sum += math.log(ratio)
        change = math.exp(log_sum / len(common)) - 1
        index *= 1 + change
        result.append(
            {"month": month, "index": round(index, 2), "change": round(change, 4), "products_compared": len(common)}
        )
    return result


def cheapest_stores(purchases: list[Purchase]) -> list[dict]:
    """Products bought in two or more stores: average price per store."""
    prices: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    names: dict[str, tuple[str, str]] = {}
    for p in purchases:
        if p.product_id:
            prices[p.product_id][p.store].append(p.price)
            names[p.product_id] = (p.product_name, p.category)
    result = []
    for product_id, stores in prices.items():
        if len(stores) < 2:
            continue
        averages = sorted(
            ({"store": store, "avg_price": _round(sum(v) / len(v)), "times": len(v)} for store, v in stores.items()),
            key=lambda s: s["avg_price"],
        )
        cheapest, priciest = averages[0], averages[-1]
        result.append(
            {
                "product_id": product_id,
                "name": names[product_id][0],
                "category": names[product_id][1],
                "stores": averages,
                "cheapest_store": cheapest["store"],
                "saving": round(1 - cheapest["avg_price"] / priciest["avg_price"], 4),
            }
        )
    result.sort(key=lambda r: -r["saving"])
    return result


def product_price_history(purchases: list[Purchase], product_id: str) -> list[dict]:
    return [
        {"date": p.bought_at.date().isoformat(), "store": p.store, "price": p.price, "quantity": p.quantity}
        for p in purchases
        if p.product_id == product_id
    ]
