from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_all_seven_pages_render_in_local_mode(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'ui.db').as_posix()}")
    monkeypatch.setenv("EXCEL_PATH", str(tmp_path / "report.xlsx"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("TEMPLATE_PATH", str(root / "templates" / "EasyOats_Order_Tracker.xlsx"))
    app = AppTest.from_file(root / "app.py", default_timeout=20).run()
    assert not app.exception
    pages = [
        "لوحة المتابعة", "طلب جديد", "البحث عن عميل", "تحديث الطلب",
        "المخزون", "الفيدباك", "الإعدادات والتصدير",
    ]
    for page in pages:
        app.radio(key="navigation").set_value(page).run(timeout=20)
        assert not app.exception, (page, [str(error.value) for error in app.exception])
