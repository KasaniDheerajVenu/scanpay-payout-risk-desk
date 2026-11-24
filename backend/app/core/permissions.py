from enum import StrEnum

from app.core.config import AppMode
from app.models.values import UserRole


class Permission(StrEnum):
    READ_RISK = "read_risk"
    VIEW_AUDIT = "view_audit"
    UPLOAD_DATASETS = "upload_datasets"
    RUN_EVALUATIONS = "run_evaluations"
    IMPORT_SYNTHETIC_DEMO = "import_synthetic_demo"
    OVERRIDE_DECISIONS = "override_decisions"
    EXPORT_DECISIONS = "export_decisions"


_READ = frozenset({Permission.READ_RISK, Permission.VIEW_AUDIT})
_ANALYZE = _READ | {Permission.OVERRIDE_DECISIONS, Permission.EXPORT_DECISIONS}
ROLE_PERMISSIONS = {
    UserRole.VIEWER: _READ,
    UserRole.ANALYST: _ANALYZE,
    UserRole.ADMIN: frozenset(Permission),
}


def effective_permissions(role: UserRole, mode: AppMode) -> frozenset[Permission]:
    excluded = Permission.UPLOAD_DATASETS if mode == "demo" else Permission.IMPORT_SYNTHETIC_DEMO
    return ROLE_PERMISSIONS[role] - {excluded}
