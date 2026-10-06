from datetime import datetime

from services.order_service import AppService
from scripts.migrate_postgres_to_postgres import main as migrate_postgres
from scripts.migrate_sqlite_to_postgres import main as migrate


def test_sqlite_transfer_preserves_records_and_ids(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAILS", "owner@example.com")
    original = AppService(database_url=f"sqlite:///{source.as_posix()}",
                          workbook_path=tmp_path / "source.xlsx",
                          backup_dir=tmp_path / "source-backups", auto_sync=False)
    first = original.create_order({
        "customer_name": "عميل النقل", "phone_original": "01012345678",
        "order_datetime": datetime(2026, 9, 1, 10), "source": "واتساب",
        "order_type": "سعر عادي", "honey_qty": 1, "date_qty": 0,
        "payment_method": "كاش", "status": "جديد",
        "feedback_consent": "موافق",
    })
    original.engine.dispose()

    monkeypatch.setenv("EASYOATS_TEST_ALLOW_SQLITE_TARGET", "1")
    target_url = f"sqlite:///{target.as_posix()}"
    # Simulate the automatic first hosted boot. The transfer is allowed to
    # replace untouched defaults while preserving the bootstrap account.
    seeded = AppService(database_url=target_url, workbook_path=tmp_path / "seeded.xlsx",
                        backup_dir=tmp_path / "seeded-backups", auto_sync=False)
    seeded.engine.dispose()
    assert migrate(["--source", str(source), "--target-url", target_url,
                    "--backup-dir", str(tmp_path / "migration-backups")]) == 0
    assert list((tmp_path / "migration-backups").glob("easyoats_before_postgres_*.db"))

    migrated = AppService(database_url=target_url, workbook_path=tmp_path / "target.xlsx",
                          backup_dir=tmp_path / "target-backups", auto_sync=False)
    try:
        assert migrated.get_order(first["order_id"])["customer_name"] == "عميل النقل"
        assert migrated.authenticate_user("owner@example.com")["role"] == "admin"
        second = migrated.create_order({
            "customer_name": "عميل جديد", "phone_original": "01112345678",
            "order_datetime": datetime(2026, 9, 2, 10), "source": "واتساب",
            "order_type": "سعر عادي", "honey_qty": 1, "date_qty": 0,
            "payment_method": "كاش", "status": "جديد",
            "feedback_consent": "موافق",
        })
        assert second["order_id"] == "EO-000002"
        assert migrated.sync_status()["pending"] is True
    finally:
        migrated.engine.dispose()


def test_database_to_database_transfer_uses_guarded_verified_copy(tmp_path, monkeypatch):
    source = tmp_path / "hosted-source.db"
    target = tmp_path / "neon-target.db"
    original = AppService(database_url=f"sqlite:///{source.as_posix()}", auto_sync=False)
    order = original.create_order({
        "customer_name": "عميل Neon", "phone_original": "01098765432",
        "order_datetime": datetime(2026, 10, 6, 10), "source": "واتساب",
        "order_type": "سعر عادي", "honey_qty": 1, "date_qty": 1,
        "payment_method": "كاش", "status": "جديد",
        "feedback_consent": "موافق",
    })
    original.engine.dispose()

    monkeypatch.setenv("EASYOATS_TEST_ALLOW_SQLITE_TARGET", "1")
    source_url = f"sqlite:///{source.as_posix()}"
    target_url = f"sqlite:///{target.as_posix()}"
    assert migrate_postgres([
        "--source-url", source_url, "--target-url", target_url, "--dry-run",
    ]) == 0
    assert migrate_postgres([
        "--source-url", source_url, "--target-url", target_url,
    ]) == 0

    migrated = AppService(database_url=target_url, auto_sync=False)
    try:
        assert migrated.get_order(order["order_id"])["customer_name"] == "عميل Neon"
        assert migrated.sync_status()["pending"] is True
    finally:
        migrated.engine.dispose()
