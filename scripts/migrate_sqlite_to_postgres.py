"""Safely copy the local EasyOats SQLite data into an empty hosted database."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TABLE_ORDER = (
    "settings", "inventory", "orders", "order_items", "feedback", "sync_state",
    "audit_history", "app_users",
)
SEQUENCE_TABLES = ("orders", "feedback", "audit_history")


def normalize_url(value: str) -> str:
    value = value.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def consistent_backup(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"easyoats_before_postgres_{stamp}.db"
    with sqlite3.connect(source) as current, sqlite3.connect(target) as backup:
        current.backup(backup)
        backup.execute("PRAGMA integrity_check")
    return target


def source_counts(source_url: str) -> dict[str, int]:
    engine = create_engine(source_url)
    try:
        names = set(inspect(engine).get_table_names())
        metadata = MetaData()
        metadata.reflect(engine, only=[name for name in TABLE_ORDER if name in names])
        with engine.connect() as connection:
            return {name: int(connection.scalar(select(func.count()).select_from(metadata.tables[name])))
                    for name in TABLE_ORDER if name in metadata.tables}
    finally:
        engine.dispose()


def upgrade_target(target_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection_url"] = target_url
    # Older builds initialized tables with SQLAlchemy before Alembic was added.
    # If the complete current schema exists but has no version marker, adopt it
    # rather than attempting to recreate its tables.
    engine = create_engine(target_url)
    try:
        existing = set(inspect(engine).get_table_names())
        if existing and "alembic_version" not in existing:
            legacy = {"settings", "inventory", "orders", "feedback", "sync_state", "audit_history", "app_users"}
            if legacy.issubset(existing):
                inventory_columns = {column["name"] for column in inspect(engine).get_columns("inventory")}
                if "order_items" in existing and {"unit_cost", "active"}.issubset(inventory_columns):
                    command.stamp(config, "head")
                else:
                    command.stamp(config, "f34f4c65517f")
    finally:
        engine.dispose()
    command.upgrade(config, "head")


def copy_rows(source_url: str, target_url: str) -> dict[str, int]:
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        source_names = set(inspect(source_engine).get_table_names())
        target_names = set(inspect(target_engine).get_table_names())
        source_meta, target_meta = MetaData(), MetaData()
        available = [name for name in TABLE_ORDER if name in source_names]
        source_meta.reflect(source_engine, only=available)
        target_meta.reflect(target_engine, only=[name for name in TABLE_ORDER if name in target_names])
        with target_engine.connect() as connection:
            occupied = {
                name: int(connection.scalar(select(func.count()).select_from(target_meta.tables[name])))
                for name in TABLE_ORDER if name in target_meta.tables
            }
        nonempty = {name: count for name, count in occupied.items() if count}
        seeded_tables = {"settings", "inventory", "sync_state", "app_users"}
        seed_only = bool(nonempty) and set(nonempty).issubset(seeded_tables)
        if seed_only:
            with target_engine.connect() as connection:
                sync = target_meta.tables.get("sync_state")
                revisions = list(connection.execute(select(sync.c.revision))) if sync is not None else []
            seed_only = (occupied.get("settings", 0) in (0, 8)
                         and occupied.get("inventory", 0) in (0, 2)
                         and occupied.get("sync_state", 0) in (0, 1)
                         and all(row.revision == 0 for row in revisions))
        if nonempty and not seed_only:
            details = ", ".join(f"{name}={count}" for name, count in nonempty.items())
            raise RuntimeError("Target database contains application activity; migration stopped without copying data: " + details)

        copied: dict[str, int] = {}
        expected_counts: dict[str, int] = {}
        with source_engine.connect() as source, target_engine.begin() as target:
            if seed_only:
                # A first Render boot creates only these defaults. Replacing them
                # is safe because revision zero and an empty audit log prove that
                # no operational change has occurred. OIDC accounts are merged.
                for name in ("sync_state", "inventory", "settings"):
                    if name in target_meta.tables:
                        target.execute(target_meta.tables[name].delete())
            for name in TABLE_ORDER:
                if name not in source_meta.tables or name not in target_meta.tables:
                    continue
                source_table, target_table = source_meta.tables[name], target_meta.tables[name]
                shared = [column.name for column in target_table.columns
                          if column.name in source_table.columns]
                rows = [dict(row._mapping) for row in source.execute(
                    select(*(source_table.c[column] for column in shared)))]
                if name == "app_users":
                    existing = set(target.execute(select(target_table.c.email)).scalars())
                    rows = [row for row in rows if row["email"] not in existing]
                    expected_counts[name] = len(existing) + len(rows)
                else:
                    expected_counts[name] = len(rows)
                if rows:
                    target.execute(target_table.insert(), rows)
                copied[name] = len(rows)
            if target_engine.dialect.name == "postgresql":
                for name in SEQUENCE_TABLES:
                    if name not in target_meta.tables:
                        continue
                    target.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {name}), 1), "
                        f"EXISTS(SELECT 1 FROM {name}))"
                    ))
            if "sync_state" in target_meta.tables:
                target.execute(target_meta.tables["sync_state"].update().values(
                    pending=True, synced_revision=-1,
                    message="تم نقل البيانات إلى PostgreSQL وتنتظر مزامنة Excel.",
                ))

        with target_engine.connect() as target:
            for name, expected in expected_counts.items():
                actual = int(target.scalar(select(func.count()).select_from(target_meta.tables[name])))
                if actual != expected:
                    raise RuntimeError(f"Verification failed for {name}: expected {expected}, found {actual}")
                copied[name] = actual
        return copied
    finally:
        source_engine.dispose()
        target_engine.dispose()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "easyoats.db")
    parser.add_argument("--target-url", default=os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "backups" / "migrations")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"SQLite source does not exist: {source}")
    if not args.target_url:
        raise SystemExit("Provide --target-url or set TARGET_DATABASE_URL.")
    target_url = normalize_url(args.target_url)
    backend = make_url(target_url).get_backend_name()
    allow_test_sqlite = os.getenv("EASYOATS_TEST_ALLOW_SQLITE_TARGET") == "1"
    if backend != "postgresql" and not (backend == "sqlite" and allow_test_sqlite):
        raise SystemExit("The target must be PostgreSQL.")
    original_counts = source_counts(sqlite_url(source))
    print("Source records: " + ", ".join(f"{key}={value}" for key, value in original_counts.items()))
    if args.dry_run:
        print("Dry run complete. No database or file was changed.")
        return 0
    backup = consistent_backup(source, args.backup_dir.resolve())
    print(f"Consistent SQLite backup created: {backup}")
    upgrade_target(target_url)
    copied = copy_rows(sqlite_url(backup), target_url)
    print("Verified transfer: " + ", ".join(f"{key}={value}" for key, value in copied.items()))
    print("Migration complete. Keep the SQLite backup until the hosted app is verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
