"""Compatibility alias.

The ported interview engine imports `app.database`; HireIQ's own modules use `app.db`.
Keeping the alias means the engine's 3,200 lines port unmodified.
"""
from .db import Base, SessionLocal, engine, get_db  # noqa: F401
