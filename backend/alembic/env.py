from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from alembic import context
from app.core.config import Settings, postgres_url
from app.database.session import create_migration_engine
from app.models import Base

config = context.config


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
        return

    settings = Settings()
    url = settings.migration_database_url or settings.database_url
    if url is None:
        raise RuntimeError("Configure a PostgreSQL migration or application URL first")

    engine = create_migration_engine(postgres_url(url), settings.database_timeout_seconds)
    try:
        try:
            connection = engine.connect()
        except SQLAlchemyError:
            raise RuntimeError("Could not connect to PostgreSQL for migrations") from None
        with connection:
            run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_online()
