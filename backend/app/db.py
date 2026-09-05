"""SQLAlchemy engine + session. SQLite in dev, Postgres in prod — same models."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


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
