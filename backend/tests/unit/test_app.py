import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings, postgres_url
from app.main import create_app


def local_settings() -> Settings:
    return Settings(database_url=None, migration_database_url=None)


def test_health_reports_liveness() -> None:
    with TestClient(create_app(local_settings())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": {"connectivity": "not_configured", "ready": False},
        "evaluation_data": "not_ready",
    }


def test_unknown_api_route_has_a_consistent_error() -> None:
    with TestClient(create_app(local_settings())) as client:
        response = client.get("/api/missing")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_unsupported_http_method_preserves_allow_header() -> None:
    with TestClient(create_app(local_settings())) as client:
        response = client.post("/health")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"


def test_validation_error_omits_request_values() -> None:
    app = create_app(local_settings())

    @app.get("/test")
    def read_number(number: int) -> int:
        return number

    with TestClient(app) as client:
        response = client.get("/test", params={"number": "private-value"})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "validation_error", "message": "Invalid request"}}


def test_http_error_omits_internal_detail() -> None:
    app = create_app(local_settings())

    @app.get("/test")
    def unavailable() -> None:
        raise HTTPException(status_code=503, detail="private-value")

    with TestClient(app) as client:
        response = client.get("/test")
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "service_unavailable", "message": "Service Unavailable"}
    }


def test_unexpected_error_omits_exception_detail() -> None:
    app = create_app(local_settings())

    @app.get("/test")
    def fail() -> None:
        raise RuntimeError("private-value")

    with TestClient(app) as client:
        response = client.get("/test")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_server_error", "message": "Internal server error"}
    }


def test_settings_load_environment_and_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCANPAY_LOG_LEVEL", "WARNING")
    assert local_settings().log_level == "WARNING"

    monkeypatch.setenv("SCANPAY_LOG_LEVEL", "invalid")
    with pytest.raises(ValidationError):
        local_settings()


def test_unavailable_database_keeps_liveness_and_sanitizes_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        Settings(
            database_url=SecretStr("postgresql://fictional@127.0.0.1:55432/scanpay_test"),
            migration_database_url=None,
        )
    )

    def unavailable() -> None:
        raise OperationalError(None, None, ValueError("fictional driver detail"))

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.database.engine, "connect", unavailable)
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": {"connectivity": "unavailable", "ready": False},
        "evaluation_data": "not_ready",
    }


def test_database_settings_normalize_urls_and_reject_other_dialects() -> None:
    settings = Settings(
        database_url=SecretStr("postgres://fictional:fake-password@127.0.0.1/scanpay_test"),
        migration_database_url=SecretStr("postgresql://fictional@127.0.0.1/scanpay_test"),
    )
    assert postgres_url(settings.database_url).drivername == "postgresql+psycopg"
    assert "fake-password" not in repr(settings)
    with pytest.raises(ValidationError, match="PostgreSQL with Psycopg"):
        Settings(database_url=SecretStr("sqlite:///:memory:"), migration_database_url=None)
