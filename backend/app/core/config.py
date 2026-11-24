from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def postgres_url(value: SecretStr) -> URL:
    try:
        url = make_url(value.get_secret_value())
    except (ArgumentError, ValueError):
        raise ValueError("Expected a PostgreSQL connection URL") from None

    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise ValueError("The database must use PostgreSQL with Psycopg")
    return url.set(drivername="postgresql+psycopg")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCANPAY_", hide_input_in_errors=True)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
    database_timeout_seconds: int = Field(default=5, ge=1)

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            postgres_url(value)
        return value
