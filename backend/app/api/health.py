from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_database
from app.database.readiness import check_database
from app.database.session import Database
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["Health"])
def health(database: Annotated[Database | None, Depends(get_database)]) -> HealthResponse:
    """Report process liveness and database readiness independently."""
    return HealthResponse(database=check_database(database))
