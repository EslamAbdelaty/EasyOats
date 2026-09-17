from datetime import datetime

import pytest

@pytest.fixture
def service(tmp_path):
    from services.order_service import AppService

    app = AppService(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        workbook_path=tmp_path / "report.xlsx",
        backup_dir=tmp_path / "backups",
        auto_sync=False,
    )
    yield app
    if hasattr(app, "engine"):
        app.engine.dispose()


@pytest.fixture
def order_data():
    return {
        "customer_name": "عميل الاختبار",
        "phone_original": "01012345678",
        "order_datetime": datetime(2026, 9, 1, 10, 0),
        "source": "واتساب",
        "area": "القاهرة",
        "address": "عنوان الاختبار",
        "order_type": "سعر عادي",
        "honey_qty": 1,
        "date_qty": 1,
        "discount": 0,
        "delivery_fee": 35,
        "delivery_cost": 30,
        "payment_method": "كاش عند الاستلام",
        "amount_collected": 0,
        "status": "جديد",
        "feedback_consent": "موافق",
    }
