from hashlib import sha256
from importlib.metadata import version
from typing import Literal

import numpy
import sklearn
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.risk_engine.inputs import EvaluationConfig
from app.services.evaluation_manifest import canonical_json

ALGORITHM = "isolation_forest"


class MLPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str = Field(default="isolation-forest-v1", pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    n_estimators: int = Field(default=100, ge=1, le=1000)
    max_samples: Literal["auto"] | int = Field(default="auto")
    contamination: Literal["auto"] = "auto"
    random_state: int = Field(default=42, ge=0, le=2**32 - 1)
    calibration_fraction: float = Field(default=0.15, gt=0, lt=1)
    validation_fraction: float = Field(default=0.25, gt=0, lt=1)
    calibration_quantile: float = Field(default=0.95, gt=0, lt=1)
    min_training: int = Field(default=30, ge=2)
    min_calibration: int = Field(default=10, ge=2)
    min_validation: int = Field(default=15, ge=2)
    min_features: int = Field(default=6, ge=2, le=48)
    min_column_coverage: float = Field(default=0.50, gt=0, le=1)
    min_row_coverage: float = Field(default=0.50, gt=0, le=1)
    min_validation_coverage: float = Field(default=0.80, gt=0, le=1)
    max_validation_anomaly_rate: float = Field(default=0.25, ge=0, le=1)
    max_normal_alert_rate: float = Field(default=0.20, ge=0, le=1)
    max_incremental_review_rate: float = Field(default=0.15, ge=0, le=1)
    min_actionable_alerts: int = Field(default=1, ge=1)
    min_actionable_families: int = Field(default=2, ge=2, le=5)

    @model_validator(mode="after")
    def validate_partition(self) -> "MLPolicy":
        if self.calibration_fraction + self.validation_fraction >= 1:
            raise ValueError("Historical proportions must leave a training partition")
        if isinstance(self.max_samples, int) and self.max_samples < 2:
            raise ValueError("max_samples must be auto or at least two")
        return self


def model_configuration(policy: MLPolicy) -> dict:
    return {
        "policy": policy.model_dump(mode="json"),
        "algorithm": ALGORITHM,
        "runtime": {
            "sklearn": sklearn.__version__,
            "numpy": numpy.__version__,
            "scipy": version("scipy"),
        },
        "workers": 1,
        "preprocessing": {
            "imputation": "training_median",
            "remove_constant": True,
            "remove_sparse": True,
            "scaling": "none",
        },
        "calibration": "ordinary_history_quantile_higher_strict_greater",
    }


def ml_config(base: EvaluationConfig, policy: MLPolicy) -> EvaluationConfig:
    digest = sha256(canonical_json(model_configuration(policy)).encode()).hexdigest()
    configured = base.model_copy(
        update={"ml_policy_version": policy.version, "ml_policy_digest": digest}
    )
    if base.ml_policy_digest is not None and configured != base:
        raise ValueError("ml_policy_fingerprint_mismatch")
    return configured
