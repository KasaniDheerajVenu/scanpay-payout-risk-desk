from dataclasses import dataclass
from uuid import UUID

from app.core.config import AppMode
from app.models.values import UserRole


def resolve_review_scope(mode: AppMode, user_id: UUID) -> str:
    return f"demo:{user_id}" if mode == "demo" else "local"


@dataclass(frozen=True, slots=True)
class ActorContext:
    user_id: UUID
    username: str
    display_name: str
    role: UserRole
    auth_version: int
    scope_key: str
