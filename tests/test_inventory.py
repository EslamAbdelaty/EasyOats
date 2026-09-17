from datetime import date
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.order_service import ValidationError


def honey(service):
    return next(x for x in service.inventory() if x["sku"] == "honey")


def test_reserve_cancel_release(service, order_data):
    assert honey(service)["available"] == 238
    order = service.create_order(order_data)
    assert honey(service)["reserved"] == 1
    assert honey(service)["available"] == 237
    service.update_order(order["order_id"], {"status": "ملغي"})
    assert honey(service)["available"] == 238
    assert honey(service)["reserved"] == 0


def test_delivery_and_return_confirmation(service, order_data):
    order = service.create_order(order_data)
    service.update_order(order["order_id"], {
        "status": "تم التوصيل", "actual_delivery_date": date(2026, 9, 2),
    })
    assert honey(service)["reserved"] == 0
    assert honey(service)["delivered"] == 1
    assert honey(service)["available"] == 237
    service.update_order(order["order_id"], {"status": "مرتجع", "returned_sellable": False})
    assert honey(service)["available"] == 237
    service.update_order(order["order_id"], {"returned_sellable": True})
    assert honey(service)["available"] == 238


def test_overselling_rejected_without_side_effects(service, order_data):
    order_data["honey_qty"] = 239
    with pytest.raises(ValidationError):
        service.create_order(order_data)
    assert service.list_orders() == []
    assert honey(service)["available"] == 238


def test_admin_override_requires_role_and_reason(service, order_data):
    order_data["honey_qty"] = 239
    with pytest.raises(ValidationError):
        service.create_order(order_data, override_reason="Confirmed production")
    with pytest.raises(ValidationError):
        service.create_order(order_data, is_admin=True)
    service.create_order(order_data, is_admin=True, override_reason="Confirmed production")
    assert honey(service)["available"] == -1
    assert any("Confirmed production" in str(h) for h in service.audit_history())


def test_stock_additions_and_physical_variance(service, order_data):
    service.create_order(order_data)
    service.update_inventory("honey", {"added_stock": 10, "physical_count": 247})
    inv = honey(service)
    assert inv["available"] == 247
    # Reserved units remain physically in the warehouse until delivery.
    assert inv["variance"] == -1


def test_low_stock(service, order_data):
    order_data["honey_qty"] = 190
    service.create_order(order_data)
    assert honey(service)["low_stock"] is True


def test_concurrent_last_unit_cannot_be_oversold(service, order_data):
    service.update_inventory("honey", {"opening_stock": 1})
    order_data.update(honey_qty=1, date_qty=0)

    def attempt(_):
        try:
            return service.create_order(order_data)["order_id"]
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1
    assert honey(service)["available"] == 0


def test_delivered_order_cannot_bypass_return_confirmation(service, order_data):
    order = service.create_order(order_data)
    service.update_order(order["order_id"], {
        "status": "تم التوصيل", "actual_delivery_date": date(2026, 9, 2),
    })
    with pytest.raises(ValidationError):
        service.update_order(order["order_id"], {"status": "ملغي"})
    assert honey(service)["available"] == 237
