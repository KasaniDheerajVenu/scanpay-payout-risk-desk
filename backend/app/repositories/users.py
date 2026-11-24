from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.values import UserRole


def find_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username))


def find_by_id(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def increment_auth_version(session: Session, user_id: UUID, expected_version: int) -> bool:
    changed = session.scalar(
        update(User)
        .where(
            User.user_id == user_id,
            User.auth_version == expected_version,
            User.is_active.is_(True),
        )
        .values(auth_version=User.auth_version + 1)
        .returning(User.user_id)
    )
    return changed is not None


def insert_user(
    session: Session, username: str, display_name: str, role: UserRole, password_hash: str
) -> UUID | None:
    return session.scalar(
        insert(User)
        .values(
            username=username,
            display_name=display_name,
            role=role,
            password_hash=password_hash,
        )
        .on_conflict_do_nothing(index_elements=[User.username])
        .returning(User.user_id)
    )
