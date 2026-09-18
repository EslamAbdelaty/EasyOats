"""Apply EasyOats schema migrations, adopting the legacy local schema safely."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INITIAL_REVISION = "f34f4c65517f"
LEGACY_TABLES = {
    "app_users", "audit_history", "feedback", "inventory",
    "orders", "settings", "sync_state",
}


def normalized_url() -> str:
    value = os.getenv("DATABASE_URL") or f"sqlite:///{(ROOT / 'data' / 'easyoats.db').as_posix()}"
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


def main() -> int:
    url = normalized_url()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection_url"] = url
    engine = create_engine(url)
    try:
        existing = set(inspect(engine).get_table_names())
        if existing and "alembic_version" not in existing:
            if not LEGACY_TABLES.issubset(existing):
                raise RuntimeError("Existing database is not a recognized EasyOats schema.")
            inventory_columns = {column["name"] for column in inspect(engine).get_columns("inventory")}
            if "order_items" in existing and {"unit_cost", "active"}.issubset(inventory_columns):
                command.stamp(config, "head")
            else:
                command.stamp(config, INITIAL_REVISION)
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
