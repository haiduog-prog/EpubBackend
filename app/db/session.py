import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_engine_url(raw_url: str) -> str:
    url_str = raw_url.strip()
    if url_str.startswith('postgres://'):
        return url_str.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url_str.startswith('postgresql://') and not url_str.startswith('postgresql+'):
        return url_str.replace('postgresql://', 'postgresql+psycopg://', 1)

    parsed = make_url(url_str)
    if parsed.drivername.startswith('sqlite'):
        db_path = parsed.database
        if (
            db_path
            and db_path != ':memory:'
            and not db_path.startswith('file:')
            and parsed.query.get('mode') != 'memory'
        ):
            if not os.path.isabs(db_path):
                resolved = (PROJECT_ROOT / db_path).resolve()
                try:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
                parsed = parsed.set(database=resolved.as_posix())
                return str(parsed)
            else:
                try:
                    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
    return url_str


@event.listens_for(Engine, 'connect')
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA busy_timeout=5000;')
            cursor.execute('PRAGMA foreign_keys=ON;')
            try:
                cursor.execute('PRAGMA journal_mode=WAL;')
            except sqlite3.OperationalError:
                # WAL is an optimization and cannot be enabled for valid
                # read-only SQLite URI connections. Keep those connections usable.
                pass
        finally:
            cursor.close()


def create_db_engine(db_url_override: str = None):
    raw_url = db_url_override or settings.database_url
    db_url = get_engine_url(raw_url)
    connect_args = {}
    if db_url.startswith('sqlite'):
        connect_args['check_same_thread'] = False
        return create_engine(db_url, connect_args=connect_args)
    
    return create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
    )


engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def reset_db_engine(db_url_override: str = None):
    """Reconfigures the database engine and SessionLocal."""
    global engine, SessionLocal
    engine = create_db_engine(db_url_override)
    SessionLocal.configure(bind=engine)
    return engine


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

