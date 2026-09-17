"""Stock projection, shared by validations, UI, and spreadsheet snapshots."""

from decimal import Decimal
from sqlalchemy import func, select
from constants import ACTIVE_STATUSES
from models import Inventory, Order


def inventory_rows(session, settings: dict) -> list[dict]:
    result = []
    for item in session.scalars(select(Inventory).order_by(Inventory.sku.desc())):
        qty = Order.honey_qty if item.sku == "honey" else Order.date_qty
        def total(*conditions):
            return int(session.scalar(select(func.coalesce(func.sum(qty), 0)).where(*conditions)) or 0)
        reserved = total(Order.status.in_(ACTIVE_STATUSES))
        delivered = total(Order.status == "تم التوصيل")
        returned_unsellable = total(Order.status == "مرتجع", Order.returned_sellable.is_(False))
        on_hand = item.opening_stock + item.added_stock - delivered - returned_unsellable
        available = on_hand - reserved
        unit_cost = Decimal(str(settings[f"{item.sku}_unit_cost"]))
        result.append({
            "sku": item.sku, "name": item.name,
            "opening_stock": item.opening_stock, "added_stock": item.added_stock,
            "reserved": reserved, "delivered": delivered,
            "returned_unsellable": returned_unsellable, "available": available,
            "on_hand": on_hand, "physical_count": item.physical_count,
            "variance": None if item.physical_count is None else item.physical_count - on_hand,
            "reorder_point": item.reorder_point, "unit_cost": float(unit_cost),
            "value": float((Decimal(available) * unit_cost).quantize(Decimal("0.01"))),
            "low_stock": available <= item.reorder_point,
        })
    return result
