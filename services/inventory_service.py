"""Stock projection, shared by validations, UI, and spreadsheet snapshots."""

from sqlalchemy import func, select
from constants import ACTIVE_STATUSES
from models import Inventory, Order, OrderItem


def inventory_rows(session, settings: dict) -> list[dict]:
    result = []
    for item in session.scalars(select(Inventory).order_by(Inventory.active.desc(), Inventory.name, Inventory.sku)):
        def total(*conditions):
            query = (select(func.coalesce(func.sum(OrderItem.quantity), 0))
                     .join(Order, Order.order_id == OrderItem.order_id)
                     .where(OrderItem.sku == item.sku, *conditions))
            return int(session.scalar(query) or 0)
        reserved = total(Order.status.in_(ACTIVE_STATUSES))
        delivered = total(Order.status == "تم التوصيل")
        returned_unsellable = total(Order.status == "مرتجع", Order.returned_sellable.is_(False))
        on_hand = item.opening_stock + item.added_stock - delivered - returned_unsellable
        available = on_hand - reserved
        unit_cost = item.unit_cost
        result.append({
            "sku": item.sku, "name": item.name, "active": item.active,
            "opening_stock": item.opening_stock, "added_stock": item.added_stock,
            "reserved": reserved, "delivered": delivered,
            "returned_unsellable": returned_unsellable, "available": available,
            "on_hand": on_hand, "physical_count": item.physical_count,
            "variance": None if item.physical_count is None else item.physical_count - on_hand,
            "reorder_point": item.reorder_point, "unit_cost": float(unit_cost),
            "value": float(available * unit_cost),
            "low_stock": available <= item.reorder_point,
        })
    return result
