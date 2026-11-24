from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.health import router
from app.core.config import Settings
from app.core.errors import http_error, unexpected_errors, validation_error
from app.core.logging import configure_logging, logger
from app.database.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        database = Database(settings) if settings.database_url is not None else None
        app.state.database = database
        logger.info("Payout Risk Desk API started")
        try:
            yield
        finally:
            if database is not None:
                database.close()

    application = FastAPI(title="Payout Risk Desk", lifespan=lifespan)
    application.include_router(router)
    application.add_exception_handler(HTTPException, http_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.middleware("http")(unexpected_errors)
    return application
