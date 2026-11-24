from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app import models
from app.database.session import Database
from app.schemas.health import DatabaseHealth

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def check_database(database: Database | None) -> DatabaseHealth:
    if database is None:
        return DatabaseHealth(connectivity="not_configured", ready=False)

    connected = False
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connected = True
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
            expected_heads = set(
                ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG_PATH))).get_heads()
            )
            ready = (
                bool(expected_heads)
                and current_heads == expected_heads
                and models.Base.metadata.tables.keys() <= set(inspect(connection).get_table_names())
            )
    except SQLAlchemyError:
        return DatabaseHealth(
            connectivity="connected" if connected else "unavailable",
            ready=False,
        )

    return DatabaseHealth(connectivity="connected", ready=ready)
