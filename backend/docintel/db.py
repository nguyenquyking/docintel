"""Database engine and sessions. SQLite (WAL) for local use, Postgres in production."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .settings import Settings, get_settings

_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")      # readers never block the writer
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")    # wait for the write lock instead of failing
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.db_url
    if url not in _engines:
        if url.startswith("sqlite"):
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
            event.listen(engine, "connect", _sqlite_pragmas)
        else:
            engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
        _engines[url] = engine
        _factories[url] = sessionmaker(engine, expire_on_commit=False)
    return _engines[url]


def init_db(settings: Settings | None = None) -> Engine:
    from .models import Base

    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """One unit of work: commit on success, roll back on error."""
    settings = settings or get_settings()
    get_engine(settings)
    session = _factories[settings.db_url]()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def is_sqlite(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).db_url.startswith("sqlite")


def reset_engines() -> None:
    """Tests: drop cached engines so a new database URL takes effect."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _factories.clear()
