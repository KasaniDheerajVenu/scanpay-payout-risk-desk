from typing import Literal

from pydantic import BaseModel


class DatabaseHealth(BaseModel):
    connectivity: Literal["not_configured", "unavailable", "connected"]
    ready: bool


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    database: DatabaseHealth
    evaluation_data: Literal["not_ready"] = "not_ready"
