from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings, postgres_url


def create_migration_engine(url: URL, timeout: int) -> Engine:
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={"connect_timeout": timeout},
        hide_parameters=True,
    )


class Database:
    def __init__(self, settings: Settings) -> None:
        if settings.database_url is None:
            raise ValueError("Application database URL is not configured")
        self.app_mode = settings.app_mode
        self.engine = create_engine(
            postgres_url(settings.database_url),
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_timeout_seconds,
            connect_args={
                "connect_timeout": settings.database_timeout_seconds,
                "prepare_threshold": None,
            },
            hide_parameters=True,
        )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            try:
                yield session
            except BaseException:
                session.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()
