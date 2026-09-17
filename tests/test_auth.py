from pathlib import Path

import pytest

from services.order_service import AppService, ValidationError


def hosted_service(tmp_path: Path, monkeypatch) -> AppService:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAILS", "Owner@EasyOats.example")
    monkeypatch.setenv("BOOTSTRAP_STAFF_EMAILS", "staff@easyoats.example")
    return AppService(database_url=f"sqlite:///{(tmp_path / 'auth.db').as_posix()}",
                      workbook_path=tmp_path / "report.xlsx",
                      backup_dir=tmp_path / "backups", auto_sync=False)


def test_oidc_allowlist_and_roles(tmp_path, monkeypatch):
    service = hosted_service(tmp_path, monkeypatch)
    try:
        admin = service.authenticate_user("OWNER@easyoats.example", "Order owner")
        assert admin["email"] == "owner@easyoats.example"
        assert admin["role"] == "admin"
        assert admin["last_login"] is not None
        assert service.authenticate_user("unknown@example.com", "Unknown") is None

        account = service.save_user({
            "email": "new.member@example.com", "display_name": "New member",
            "role": "staff", "active": True,
        }, admin["email"])
        assert account["role"] == "staff"
        assert service.authenticate_user(account["email"], "New name")["display_name"] == "New name"
        assert any(user["email"] == account["email"] for user in service.list_users(admin["email"]))
    finally:
        service.engine.dispose()


def test_staff_cannot_manage_access_and_admin_cannot_disable_self(tmp_path, monkeypatch):
    service = hosted_service(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValidationError):
            service.list_users("staff@easyoats.example")
        with pytest.raises(ValidationError):
            service.save_user({
                "email": "owner@easyoats.example", "display_name": "Owner",
                "role": "staff", "active": False,
            }, "owner@easyoats.example")
        assert service.authenticate_user("owner@easyoats.example")["role"] == "admin"
    finally:
        service.engine.dispose()
