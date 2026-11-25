from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

type AppMode = Literal["local", "demo"]

LOCAL_TRUSTED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
)


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
    app_mode: AppMode = "local"
    auth_signing_secret: SecretStr | None = None
    auth_session_seconds: int = Field(default=1800, ge=60, le=3600)
    auth_cookie_name: str = Field(
        default="scanpay_session", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"
    )
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_enabled: bool = False
    trusted_origins: tuple[str, ...] = LOCAL_TRUSTED_ORIGINS
    login_rate_limit: int = Field(default=5, ge=1)
    login_rate_window_seconds: int = Field(default=300, ge=1)
    login_rate_max_entries: int = Field(default=4096, ge=2)
    upload_max_file_bytes: int = Field(default=4 * 1024 * 1024, ge=1)
    upload_max_total_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    upload_max_rows_per_file: int = Field(default=100_000, ge=1)
    upload_max_field_chars: int = Field(default=4096, ge=1, le=131_072)

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            postgres_url(value)
        return value

    @field_validator("auth_signing_secret")
    @classmethod
    def validate_signing_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().strip().encode()) < 32:
            raise ValueError("Use at least 32 bytes of randomly generated signing secret")
        return value

    @field_validator("trusted_origins")
    @classmethod
    def validate_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        if not origins:
            raise ValueError("Configure one or more explicit trusted origins")
        for origin in origins:
            url = urlsplit(origin)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or any(c.isspace() or c in "*\\%" for c in origin)
                or origin != f"{url.scheme}://{url.netloc}"
            ):
                raise ValueError("Trusted origins must be exact HTTP or HTTPS origins")
            if url.port == 0:
                raise ValueError("Trusted origins must use a valid port")
        return origins

    @model_validator(mode="after")
    def validate_demo_security(self) -> "Settings":
        if self.auth_cookie_samesite == "none" and not self.secure_cookies:
            raise ValueError("SameSite=None requires Secure cookies")
        if self.app_mode == "demo":
            if self.auth_signing_secret is None or not self.secure_cookies:
                raise ValueError("Demo mode requires a signing secret and Secure cookies")
            if any(not origin.startswith("https://") for origin in self.trusted_origins):
                raise ValueError("Demo mode requires HTTPS trusted origins")
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.auth_cookie_secure is None:
            return self.app_mode == "demo"
        return self.auth_cookie_secure
