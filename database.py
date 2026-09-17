"""Database setup and serialized inventory mutations for SQLite/PostgreSQL."""

from contextlib import contextmanager
import os
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, Inventory

PROJECT_ROOT = Path(__file__).resolve().parent


class Database:
    def __init__(self, database_url: str | None = None):
        default_path = PROJECT_ROOT / "data" / "easyoats.db"
        self.url = database_url or os.getenv("DATABASE_URL") or f"sqlite:///{default_path.as_posix()}"
        if self.url.startswith("postgres://"):
            self.url = "postgresql+psycopg://" + self.url[len("postgres://"):]
        elif self.url.startswith("postgresql://"):
            self.url = "postgresql+psycopg://" + self.url[len("postgresql://"):]
        parsed = make_url(self.url)
        sqlite = parsed.get_backend_name() == "sqlite"
        kwargs = {"pool_pre_ping": True}
        if sqlite:
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if parsed.database in (None, "", ":memory:"):
                kwargs["poolclass"] = StaticPool
            else:
                Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(self.url, **kwargs)
        if sqlite:
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()
        self.is_sqlite = sqlite
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        """Serialize stock validation with writes; never check stock outside a lock."""
        with self.Session() as session:
            try:
                if self.is_sqlite:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    # A single database-wide advisory lock also serializes the first
                    # seed transaction, before the two inventory rows exist.
                    session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(690041210)")
                    session.execute(select(Inventory).order_by(Inventory.sku).with_for_update()).all()
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
