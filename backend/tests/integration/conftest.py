import os
import re
from collections.abc import Callable, Iterator
from functools import partial

import pytest
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import postgres_url
from app.database.readiness import ALEMBIC_CONFIG_PATH
from app.database.session import create_migration_engine


@pytest.fixture(scope="session")
def test_database_url() -> SecretStr:
    value = os.getenv("SCANPAY_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "Set SCANPAY_TEST_DATABASE_URL to an empty, disposable local PostgreSQL database"
        )
    secret = SecretStr(value)
    url = postgres_url(secret)
    if (
        url.host not in {"127.0.0.1", "::1"}
        or url.port is None
        or not re.fullmatch(r"scanpay_test(?:_[a-z0-9_]+)?", url.database or "")
        or url.query
    ):
        pytest.fail("Tests require an explicit loopback port and a scanpay_test database name")
    return secret


def migrate(engine: Engine, direction: str) -> None:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        if direction == "upgrade":
            command.upgrade(config, "head")
        elif direction == "downgrade":
            command.downgrade(config, "base")
        else:
            command.check(config)


@pytest.fixture
def migrate_schema(engine: Engine) -> Callable[[str], None]:
    return partial(migrate, engine)


@pytest.fixture(scope="session")
def engine(test_database_url: SecretStr) -> Iterator[Engine]:
    engine = create_migration_engine(postgres_url(test_database_url), 5)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
            tables = set(inspect(connection).get_table_names(schema="public"))
            if tables - {"alembic_version"}:
                pytest.fail("The disposable database must start with an empty public schema")
            if tables and connection.scalar(text("SELECT count(*) FROM public.alembic_version")):
                pytest.fail("The disposable database must start without an applied migration")

        migrate(engine, "upgrade")
        try:
            yield engine
        finally:
            migrate(engine, "downgrade")
    finally:
        engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
