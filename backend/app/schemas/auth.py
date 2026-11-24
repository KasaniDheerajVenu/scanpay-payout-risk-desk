from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from app.core.config import AppMode
from app.core.permissions import Permission
from app.core.security import PASSWORD_MAX_LENGTH
from app.models.values import UserRole

type Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$"
    ),
]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: Username
    password: Annotated[SecretStr, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class UserResponse(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    role: UserRole
    mode: AppMode
    permissions: list[Permission]


class CsrfResponse(BaseModel):
    csrf_token: str


class UserProvision(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    username: Username
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    role: UserRole

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("Display name must be printable")
        return value
