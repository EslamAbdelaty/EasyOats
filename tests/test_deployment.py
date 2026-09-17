from pathlib import Path

import pytest
import deployment.start as start


def auth_environment(monkeypatch):
    values = {
        "AUTH_REQUIRED": "true",
        "OIDC_REDIRECT_URI": "https://orders.example.com/oauth2callback",
        "AUTH_COOKIE_SECRET": "a-long-random-cookie-secret",
        "OIDC_CLIENT_ID": "client-id",
        "OIDC_CLIENT_SECRET": "client-secret",
        "OIDC_SERVER_METADATA_URL": "https://accounts.google.com/.well-known/openid-configuration",
        "BOOTSTRAP_ADMIN_EMAILS": "owner@example.com",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_hosted_auth_secrets_are_generated_outside_source_control(tmp_path, monkeypatch):
    auth_environment(monkeypatch)
    monkeypatch.setattr(start, "ROOT", tmp_path)
    start.write_auth_secrets()
    content = (tmp_path / ".streamlit" / "secrets.toml").read_text(encoding="utf-8")
    assert "[auth]" in content
    assert 'redirect_uri = "https://orders.example.com/oauth2callback"' in content
    assert 'client_secret = "client-secret"' in content


def test_hosted_auth_requires_callback_and_bootstrap_admin(tmp_path, monkeypatch):
    auth_environment(monkeypatch)
    monkeypatch.setattr(start, "ROOT", tmp_path)
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://orders.example.com/wrong")
    with pytest.raises(SystemExit, match="oauth2callback"):
        start.write_auth_secrets()
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://orders.example.com/oauth2callback")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAILS")
    with pytest.raises(SystemExit, match="BOOTSTRAP_ADMIN_EMAILS"):
        start.write_auth_secrets()


def test_render_blueprint_wires_postgres_auth_and_persistent_excel():
    root = Path(__file__).resolve().parents[1]
    blueprint = (root / "render.yaml").read_text(encoding="utf-8")
    assert "fromDatabase:\n          name: easyoats-postgres" in blueprint
    assert 'key: AUTH_REQUIRED\n        value: "true"' in blueprint
    assert "key: BOOTSTRAP_ADMIN_EMAILS\n        sync: false" in blueprint
    assert "mountPath: /var/data" in blueprint
    assert "healthCheckPath: /_stcore/health" in blueprint
