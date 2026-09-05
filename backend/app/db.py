"""SQLAlchemy engine + session. SQLite in dev, Postgres in prod — same models."""
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Every datetime this app writes is produced via `utcnow()` or
    `datetime.now(timezone.utc)` — but neither SQLite nor a plain (non-tz) Postgres
    `TIMESTAMP` column preserves that tzinfo across a round trip, so it comes back
    naive. FastAPI then serializes a naive datetime WITHOUT a UTC suffix ("Z" or
    "+00:00"), and every browser not itself on UTC parses that string as ITS OWN
    local time — a candidate in IST would see "Applied 5.5 hours ago" for something
    that just happened. This type restores the tzinfo this app already knows is
    always true, at the one place it can be forgotten.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------- light dev migrations
#: (table, column, full column DDL). Additive only — never a rename or drop, so an
#: older sqlite dev.db upgrades in place instead of erroring on a column the ORM now
#: expects. Real production schema changes still go through Alembic, per main.py.
_NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("candidates", "is_active", "is_active BOOLEAN NOT NULL DEFAULT 1"),
]


def run_light_migrations() -> None:
    """Add any column the models declare that an existing sqlite file predates.

    `Base.metadata.create_all` only creates tables that do not exist yet — it never
    alters an existing table, so a column added to a model after someone's dev.db was
    created would otherwise 500 on the very first query that touches it.
    """
    if not settings.is_sqlite:
        return
    with engine.begin() as conn:
        for table, column, ddl in _NEW_COLUMNS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")
