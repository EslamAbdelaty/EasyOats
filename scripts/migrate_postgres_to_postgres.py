"""Safely copy an EasyOats PostgreSQL database into an empty PostgreSQL target."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_sqlite_to_postgres import (
    TABLE_ORDER,
    copy_rows,
    normalize_url,
    source_counts,
    upgrade_target,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=os.getenv("SOURCE_DATABASE_URL"))
    parser.add_argument("--target-url", default=os.getenv("TARGET_DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _validate_url(value: str | None, label: str) -> str:
    if not value:
        raise SystemExit(f"Provide --{label}-url or set {label.upper()}_DATABASE_URL.")
    normalized = normalize_url(value)
    backend = make_url(normalized).get_backend_name()
    allow_test_sqlite = os.getenv("EASYOATS_TEST_ALLOW_SQLITE_TARGET") == "1"
    if backend != "postgresql" and not (backend == "sqlite" and allow_test_sqlite):
        raise SystemExit(f"The {label} database must be PostgreSQL.")
    return normalized


def _check_connection(url: str, label: str) -> set[str]:
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return set(inspect(engine).get_table_names())
    except Exception as exc:
        raise SystemExit(f"Could not connect to the {label} database: {exc}") from None
    finally:
        engine.dispose()


def main(argv=None) -> int:
    args = parse_args(argv)
    source_url = _validate_url(args.source_url, "source")
    target_url = _validate_url(args.target_url, "target")
    if make_url(source_url) == make_url(target_url):
        raise SystemExit("Source and target database URLs must be different.")

    source_tables = _check_connection(source_url, "source")
    missing = [name for name in TABLE_ORDER if name not in source_tables]
    if missing:
        raise SystemExit("Source is not a complete EasyOats database; missing tables: " + ", ".join(missing))
    _check_connection(target_url, "target")

    counts = source_counts(source_url)
    print("Source records: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    if args.dry_run:
        print("Dry run complete. Both databases are reachable; no data was changed.")
        return 0

    upgrade_target(target_url)
    copied = copy_rows(source_url, target_url)
    print("Verified transfer: " + ", ".join(f"{key}={value}" for key, value in copied.items()))
    print("Migration complete. Keep the Render database until the Neon app is fully verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
