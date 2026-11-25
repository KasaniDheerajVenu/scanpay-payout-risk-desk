from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from math import ceil
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from app.ml.config import MLPolicy
from app.ml.preprocessing import Array, Preprocessor, fit_preprocessing, numeric_vector
from app.risk_engine.inputs import Eligibility
from app.services.evaluation_manifest import canonical_json
from app.services.statistical_analysis_service import (
    VALIDATION_REASONS,
    HistoricalFeatureRow,
    HistoryUse,
    ModelVector,
)

ModelError = (ValueError, ArithmeticError, RuntimeError)


def event(row: HistoricalFeatureRow) -> datetime:
    return row.analysis.observation.created_at


@dataclass(frozen=True, slots=True)
class Split:
    training: tuple[HistoricalFeatureRow, ...]
    calibration: tuple[HistoricalFeatureRow, ...]
    validation: tuple[HistoricalFeatureRow, ...]
    fit_cutoff: datetime | None
    calibration_cutoff: datetime | None
    fit_candidates: int
    validation_only: int


def chronological_split(
    rows: tuple[HistoricalFeatureRow, ...],
    pending_ids: frozenset[str],
    cutoff: datetime,
    snapshot_at: datetime,
    policy: MLPolicy,
) -> Split:
    eligible = tuple(
        sorted(
            (
                row
                for row in rows
                if row.eligibility == Eligibility.NORMAL_CANDIDATE
                and not row.linked_exclusion
                and row.analysis.observation.payout_id not in pending_ids
                and event(row) < cutoff
                and row.label_decided_at <= cutoff
                and max(row.label_available_at, row.feature_available_by) <= snapshot_at
                and (
                    row.use == HistoryUse.FIT_CANDIDATE
                    and row.reason_code not in VALIDATION_REASONS
                    or row.use == HistoryUse.VALIDATION_ONLY
                    and row.reason_code in VALIDATION_REASONS
                )
            ),
            key=lambda row: (event(row), row.analysis.observation.payout_id),
        )
    )
    if not eligible:
        return Split((), (), (), None, None, 0, 0)
    fit_count = sum(row.use == HistoryUse.FIT_CANDIDATE for row in eligible)
    size = len(eligible)
    calibration_start = event(
        eligible[
            max(0, size - ceil(size * (policy.calibration_fraction + policy.validation_fraction)))
        ]
    )
    validation_start = event(eligible[max(0, size - ceil(size * policy.validation_fraction))])
    validation = tuple(row for row in eligible if event(row) >= validation_start)
    between = tuple(row for row in eligible if calibration_start <= event(row) < validation_start)
    fit_available = min(row.feature_available_by for row in (between or validation))
    calibration_available = min(row.feature_available_by for row in validation)
    training = tuple(
        row
        for row in eligible
        if event(row) < calibration_start
        and row.use == HistoryUse.FIT_CANDIDATE
        and row.label_decided_at < calibration_start
        and row.label_available_at <= fit_available
    )
    calibration = tuple(
        row
        for row in between
        if row.use == HistoryUse.FIT_CANDIDATE
        and row.label_decided_at < validation_start
        and row.label_available_at <= calibration_available
    )
    return Split(
        training,
        calibration,
        validation,
        calibration_start,
        validation_start,
        fit_count,
        size - fit_count,
    )


def training_fingerprint(split: Split, config: dict[str, Any], cutoff: datetime) -> str:
    digest = sha256()

    def append(value: Any) -> None:
        digest.update(canonical_json(value).encode())
        digest.update(b"\n")

    append(
        [
            config,
            cutoff,
            split.fit_cutoff,
            split.calibration_cutoff,
            split.fit_candidates,
            split.validation_only,
        ]
    )
    for name in ("training", "calibration", "validation"):
        append(name)
        for row in getattr(split, name):
            analysis = row.analysis
            values = [str(value) if value is not None else None for value in analysis.vector.values]
            append(
                [
                    analysis.observation.payout_id,
                    event(row),
                    row.feature_available_by,
                    row.label_decided_at,
                    row.label_available_at,
                    row.use,
                    row.reason_code,
                    analysis.observation.covers_from,
                    analysis.observation.covers_to,
                    analysis.vector.names,
                    analysis.vector.availability,
                    values,
                    [
                        [pair.team.model_dump(mode="json"), pair.profession.model_dump(mode="json")]
                        for pair in analysis.comparisons
                    ]
                    if name == "validation"
                    else [],
                ]
            )
    return digest.hexdigest()


@dataclass(slots=True)
class FittedModel:
    estimator: IsolationForest
    preprocessing: Preprocessor

    def score(self, vector: ModelVector) -> tuple[float | None, int]:
        values, count = self.preprocessing.transform(numeric_vector(vector))
        if values is None:
            return None, count
        score = -float(self.estimator.score_samples(values.reshape(1, -1))[0])
        if not np.isfinite(score):
            raise ValueError("invalid_anomaly_score")
        return score, count


def prepare_fit(
    rows: tuple[HistoricalFeatureRow, ...], policy: MLPolicy
) -> tuple[Preprocessor, tuple[Array, ...]]:
    vectors = (numeric_vector(row.analysis.vector) for row in rows)
    numeric = tuple(
        array for array in vectors if np.count_nonzero(~np.isnan(array)) >= policy.min_features
    )
    preprocessing = fit_preprocessing(numeric, policy)
    transformed = tuple(
        array for row in numeric if (array := preprocessing.transform(row)[0]) is not None
    )
    return preprocessing, transformed


def fit_model(
    preprocessing: Preprocessor, rows: tuple[Array, ...], policy: MLPolicy
) -> FittedModel:
    estimator = IsolationForest(
        n_estimators=policy.n_estimators,
        max_samples=policy.max_samples,
        contamination=policy.contamination,
        random_state=policy.random_state,
        n_jobs=1,
    )
    estimator.fit(np.stack(rows))
    return FittedModel(estimator, preprocessing)
