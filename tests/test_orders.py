from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest

from services.order_service import ValidationError


@pytest.mark.parametrize("kind,subtotal,margin", [
    ("سعر عادي", 150, 67), ("عرض 2 بـ120", 120, 37), ("عينة", 0, -83),
])
def test_pricing(service, order_data, kind, subtotal, margin):
    order_data["order_type"] = kind
    order = service.create_order(order_data)
    assert order["total_units"] == 2
    assert order["product_subtotal"] == subtotal
    assert order["product_cost"] == 88
    assert order["total_due"] == subtotal + 35
    assert order["outstanding_balance"] == subtotal + 35
    assert order["contribution_margin"] == margin


@pytest.mark.parametrize("collected,status,balance", [
    (0, "غير مدفوع", 185), (50, "مدفوع جزئيًا", 135),
    (185, "مدفوع", 0), (200, "مدفوع", -15),
])
def test_payment_status(service, order_data, collected, status, balance):
    order_data["amount_collected"] = collected
    order = service.create_order(order_data)
    assert order["payment_status"] == status
    assert order["outstanding_balance"] == balance


def test_discount_and_fractional_money(service, order_data):
    order_data.update(discount=10.25, delivery_fee=34.75, amount_collected=25.50)
    order = service.create_order(order_data)
    assert order["total_due"] == 174.50
    assert order["outstanding_balance"] == 149


def test_historical_prices_unchanged(service, order_data):
    first = service.create_order(order_data)
    service.update_settings({"retail_price": 90, "honey_unit_cost": 50})
    service.update_order(first["order_id"], {"notes": "متابعة"})
    old = service.get_order(first["order_id"])
    new = service.create_order(order_data)
    assert old["total_due"] == 185
    assert old["product_cost"] == 88
    assert new["total_due"] == 215
    assert new["product_cost"] == 94


@pytest.mark.parametrize("field,value", [
    ("customer_name", "  "), ("phone_original", "123"),
    ("honey_qty", -1), ("date_qty", -1), ("honey_qty", 1.5),
    ("discount", -1), ("amount_collected", -1),
    ("delivery_fee", -1), ("delivery_cost", -1),
    ("expected_delivery_date", "bad-date"), ("status", "invalid"),
])
def test_invalid_order_rolls_back(service, order_data, field, value):
    order_data[field] = value
    with pytest.raises(ValidationError):
        service.create_order(order_data)
    assert service.list_orders() == []


def test_zero_units_rejected_for_standard(service, order_data):
    order_data.update(honey_qty=0, date_qty=0)
    with pytest.raises(ValidationError):
        service.create_order(order_data)


def test_ids_stable_and_unique(service, order_data):
    first = service.create_order(order_data)
    second = service.create_order(order_data)
    assert first["order_id"] == "EO-000001"
    assert second["order_id"] == "EO-000002"
    service.update_order(first["order_id"], {"status": "مؤكد"})
    assert service.get_order(first["order_id"])["order_id"] == first["order_id"]


def test_concurrent_creates_have_unique_ids(service, order_data):
    with ThreadPoolExecutor(max_workers=4) as executor:
        orders = list(executor.map(lambda _: service.create_order(order_data), range(12)))
    assert len({o["order_id"] for o in orders}) == 12
    assert len(service.list_orders()) == 12


@pytest.mark.parametrize("query", ["01012345678", "+201012345678", "00201012345678", "+20 (10) 1234-5678"])
def test_customer_search_equivalence(service, order_data, query):
    service.create_order(order_data)
    order_data["order_datetime"] = datetime(2026, 9, 2, 10)
    newest = service.create_order(order_data)
    results = service.search_orders(query)
    assert len(results) == 2
    assert results[0]["order_id"] == newest["order_id"]


def test_partial_search_requires_four_digits(service, order_data):
    service.create_order(order_data)
    assert len(service.search_orders("5678", partial=True)) == 1
    with pytest.raises(ValidationError):
        service.search_orders("678", partial=True)


def test_delivery_requires_actual_date(service, order_data):
    order = service.create_order(order_data)
    with pytest.raises(ValidationError):
        service.update_order(order["order_id"], {"status": "تم التوصيل"})
    assert service.get_order(order["order_id"])["status"] == "جديد"


def test_delivery_actions_and_audit(service, order_data):
    order = service.create_order(order_data, user="Tester")
    updated = service.update_order(order["order_id"], {
        "status": "تم التوصيل", "actual_delivery_date": date(2026, 9, 2),
    }, user="Courier")
    assert "المتبقي" in updated["next_action"]
    paid = service.update_order(order["order_id"], {"amount_collected": 185})
    assert "فيدباك" in paid["next_action"]
    history = service.audit_history(order["order_id"])
    assert history
    assert any(h.get("new_value") == "تم التوصيل" for h in history)


def test_failed_export_does_not_lose_order(service, order_data, monkeypatch):
    from services.excel_service import SyncResult

    class FailingExcel:
        def sync(self, _snapshot):
            return SyncResult(False, "تعذرت المزامنة")

    service.auto_sync = True
    service.excel_service = FailingExcel()
    # Any workbook failure must leave a committed order and a pending retry.
    order = service.create_order(order_data)
    assert service.get_order(order["order_id"])["customer_name"] == order_data["customer_name"]
    assert service.sync_status()["pending"] is True
