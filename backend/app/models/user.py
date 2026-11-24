from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.values import UserRole, enum_check


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(enum_check("role", UserRole), name="role_values"),
        CheckConstraint("username ~ '[^[:space:]]'", name="username_nonblank"),
        CheckConstraint("password_hash ~ '[^[:space:]]'", name="password_hash_nonblank"),
        CheckConstraint("auth_version >= 1", name="auth_version_positive"),
    )

    user_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    auth_version: Mapped[int] = mapped_column(server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
