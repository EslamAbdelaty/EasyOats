"""Run database migrations, configure OIDC, and start Streamlit in hosted mode."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def require(names: tuple[str, ...]) -> dict[str, str]:
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SystemExit("Missing required deployment variables: " + ", ".join(missing))
    return values


def write_auth_secrets() -> None:
    if not enabled("AUTH_REQUIRED"):
        return
    values = require((
        "OIDC_REDIRECT_URI", "AUTH_COOKIE_SECRET", "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET", "OIDC_SERVER_METADATA_URL", "BOOTSTRAP_ADMIN_EMAILS",
    ))
    if not values["OIDC_REDIRECT_URI"].endswith("/oauth2callback"):
        raise SystemExit("OIDC_REDIRECT_URI must end with /oauth2callback")
    auth = {
        "redirect_uri": values["OIDC_REDIRECT_URI"],
        "cookie_secret": values["AUTH_COOKIE_SECRET"],
        "client_id": values["OIDC_CLIENT_ID"],
        "client_secret": values["OIDC_CLIENT_SECRET"],
        "server_metadata_url": values["OIDC_SERVER_METADATA_URL"],
    }
    target = ROOT / ".streamlit" / "secrets.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".tmp")
    body = "[auth]\n" + "".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in auth.items()
    )
    staging.write_text(body, encoding="utf-8")
    os.replace(staging, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def main() -> int:
    require(("DATABASE_URL",))
    write_auth_secrets()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=ROOT, check=True)
    # Seed bootstrap users and materialize the downloadable workbook on the
    # persistent disk. A report failure must never prevent database access.
    from services.order_service import AppService
    service = AppService()
    try:
        if not service.sync_excel():
            print("Excel initialization is pending; retry it from the application.", file=sys.stderr)
    finally:
        service.engine.dispose()
    port = os.getenv("PORT", "8501")
    command = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.address", "0.0.0.0", "--server.port", port,
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
