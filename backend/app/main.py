from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.auth import router as auth_router
from app.api.dependencies import enforce_api_security, private_api_response
from app.api.health import router as health_router
from app.core.config import Settings
from app.core.errors import http_error, unexpected_errors, validation_error
from app.core.logging import configure_logging, logger
from app.database.session import Database
from app.services.auth_service import AuthService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.app_mode == "demo" and settings.database_url is None:
        raise ValueError("Demo mode requires a database connection URL")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.auth = AuthService(settings) if settings.auth_signing_secret is not None else None
        database = Database(settings) if settings.database_url is not None else None
        app.state.database = database
        logger.info("Payout Risk Desk API started")
        try:
            yield
        finally:
            if database is not None:
                database.close()

    application = FastAPI(
        title="Payout Risk Desk", lifespan=lifespan, dependencies=[Depends(enforce_api_security)]
    )
    application.include_router(health_router)
    application.include_router(auth_router)
    application.add_exception_handler(HTTPException, http_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.middleware("http")(unexpected_errors)
    application.middleware("http")(private_api_response)
    return application
