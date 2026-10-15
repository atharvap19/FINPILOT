"""Engine + session factory. SQLite with sane defaults for a single-box app."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _configure_sqlite(dbapi_conn, _record) -> None:
    """WAL + foreign keys. WAL matters because the FastAPI request thread reads
    while the background job thread writes."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        if url.startswith("sqlite"):
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
        if url.startswith("sqlite"):
            event.listen(_engine, "connect", _configure_sqlite)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def init_db() -> None:
    """Create every table. Idempotent."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


#: Columns added to tables that already existed. ``create_all`` creates new
#: tables but never alters old ones, so a database from before accounts would
#: otherwise lack these. Each entry is (table, column, SQL type).
_ADDED_COLUMNS = (
    ("jobs", "user_id", "VARCHAR(40)"),
    ("obligations", "job_id", "VARCHAR(40)"),
    ("users", "consent_version", "VARCHAR(16)"),
    ("users", "consented_at", "DATETIME"),
    ("users", "age_confirmed", "BOOLEAN"),
    ("jobs", "expires_at", "DATETIME"),
    ("jobs", "retention_choice", "VARCHAR(12)"),
    ("jobs", "extensions", "INTEGER"),
    ("jobs", "reminder_7d_at", "DATETIME"),
    ("jobs", "reminder_1d_at", "DATETIME"),
)


def _add_missing_columns(engine: Engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            if not inspector.has_table(table):
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            if column not in present:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})"))


def reset_engine() -> None:
    """Drop the cached engine so a test can point at a different database."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
