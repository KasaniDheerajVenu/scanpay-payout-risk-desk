from uuid import uuid4

from app.core.permissions import Permission, effective_permissions
from app.core.review_scope import resolve_review_scope
from app.models.values import UserRole


def test_permissions_cover_roles_and_mode_restrictions() -> None:
    read = {Permission.READ_RISK, Permission.VIEW_AUDIT}
    analyst = read | {Permission.OVERRIDE_DECISIONS, Permission.EXPORT_DECISIONS}
    for mode in ("local", "demo"):
        assert effective_permissions(UserRole.VIEWER, mode) == read
        assert effective_permissions(UserRole.ANALYST, mode) == analyst
    admin = analyst | {Permission.RUN_EVALUATIONS}
    assert effective_permissions(UserRole.ADMIN, "local") == admin | {Permission.UPLOAD_DATASETS}
    assert effective_permissions(UserRole.ADMIN, "demo") == admin | {
        Permission.IMPORT_SYNTHETIC_DEMO
    }


def test_scope_is_shared_locally_and_account_specific_on_demo() -> None:
    first, second = uuid4(), uuid4()
    assert resolve_review_scope("local", first) == resolve_review_scope("local", second) == "local"
    assert resolve_review_scope("demo", first) == f"demo:{first}"
    assert resolve_review_scope("demo", first) != resolve_review_scope("demo", second)
