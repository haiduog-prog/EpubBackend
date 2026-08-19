import os
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings


def get_engine_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url.startswith('postgresql://') and not url.startswith('postgresql+'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return url


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

