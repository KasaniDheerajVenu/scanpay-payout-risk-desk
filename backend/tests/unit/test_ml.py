from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest
from test_statistics import FIRST_IMPORT, LATER_IMPORT, OBSERVED, POLICY, attempt, feature_row, raw

from app.ml.config import MLPolicy, ml_config
from app.ml.inference import actionable, infer, material_context
from app.ml.preprocessing import InvalidFeatures, fit_preprocessing, numeric_vector
from app.ml.training import chronological_split, fit_model, prepare_fit
from app.ml.validation import ScoredRow, calibrate, validation_gates
from app.models.values import MLStatus, ReasonCode
from app.risk_engine.feature_schema import FEATURE_VERSION, Availability
from app.risk_engine.features import extract_features
from app.risk_engine.inputs import Eligibility, EvaluationConfig
from app.schemas.risk_findings import ComparisonStatus
from app.services import ml_analysis_service
from app.services.ml_analysis_service import analyze_ml
from app.services.statistical_analysis_service import (
    MODEL_FEATURE_NAMES,
    HistoricalFeatureRow,
    HistoryUse,
    ModelVector,
    StatisticalAnalysis,
    analyze_observation,
)

CUTOFF = OBSERVED + timedelta(days=1)
CONFIG = EvaluationConfig()


def vector(index, *, missing=()):
    values = tuple(
        (index + column * 3) if column < 12 and column not in missing else None
        for column in range(len(MODEL_FEATURE_NAMES))
    )
    return ModelVector(
        FEATURE_VERSION,
        MODEL_FEATURE_NAMES,
        values,
        tuple(
            Availability.AVAILABLE if item is not None else Availability.UNAVAILABLE
            for item in values
        ),
    )


def history(size=80):
    rows = []
    for index in range(size):
        observed = feature_row(f"synthetic-{index:03}", weeks=size - index)
        analyzed = replace(analyze_observation(observed, (), POLICY), vector=vector(index))
        rows.append(
            HistoricalFeatureRow(
                analyzed,
                HistoryUse.FIT_CANDIDATE,
                Eligibility.NORMAL_CANDIDATE,
                None,
                False,
                FIRST_IMPORT,
                observed.created_at + timedelta(hours=1),
                FIRST_IMPORT,
            )
        )
    return tuple(rows)


def anomalous_pending(payout_id="pending", weeks=0):
    cohort = tuple(feature_row(f"baseline-{i}", weeks=weeks + i + 1) for i in range(4))
    covered = raw(payout_id, weeks=weeks).payout.covers_from
    prefix = "current" if weeks == 0 else "historical"
    attempts = tuple(attempt(f"{prefix}-{i}", cents=20000, day=covered) for i in range(6))
    observed = extract_features(raw(payout_id, weeks=weeks, attempts=attempts), POLICY)
    return replace(analyze_observation(observed, cohort, POLICY), vector=vector(120))


def analyze(rows, pending=None, policy=None, snapshot=LATER_IMPORT):
    statistics = StatisticalAnalysis(
        "test-inputs", "frozen", (pending or anomalous_pending(),), rows
    )
    return analyze_ml(statistics, CONFIG, "local", CUTOFF, snapshot, policy or MLPolicy())


def test_time_order_label_availability_ties_and_exclusions():
    rows = list(history(20))
    cases = {
        0: {"linked_exclusion": True},
        1: {"eligibility": Eligibility.INCOMPLETE_COVERAGE},
        2: {"label_decided_at": CUTOFF + timedelta(days=1)},
        3: {"label_available_at": LATER_IMPORT + timedelta(days=1)},
        4: {"label_available_at": LATER_IMPORT},
        5: {"label_decided_at": rows[16].analysis.observation.created_at},
        18: {"use": HistoryUse.VALIDATION_ONLY, "reason_code": ReasonCode.OUTLIER_VERIFIED},
        19: {"use": HistoryUse.VALIDATION_ONLY, "reason_code": ReasonCode.CARD_TESTING_VICTIM},
    }
    for index, changes in cases.items():
        rows[index] = replace(rows[index], **changes)
    tied = rows[12]
    obs = replace(tied.analysis.observation, created_at=rows[13].analysis.observation.created_at)
    rows[12] = replace(tied, analysis=replace(tied.analysis, observation=obs))
    split = chronological_split(
        tuple(reversed(rows)), frozenset({"synthetic-006"}), CUTOFF, LATER_IMPORT, MLPolicy()
    )
    partitions = [
        {row.analysis.observation.payout_id for row in part}
        for part in (split.training, split.calibration, split.validation)
    ]
    assert not set.union(*partitions).intersection(
        {f"synthetic-{i:03}" for i in (0, 1, 2, 3, 4, 5, 6)}
    )
    assert all(("synthetic-012" in part) == ("synthetic-013" in part) for part in partitions)
    assert {"synthetic-018", "synthetic-019"} <= partitions[-1]
    for earlier, later in zip(
        (split.training, split.calibration), (split.calibration, split.validation), strict=True
    ):
        if earlier and later:
            assert max(row.analysis.observation.created_at for row in earlier) < min(
                row.analysis.observation.created_at for row in later
            )


def test_preprocessing_fit_medians_order_coverage_and_validation():
    policy = MLPolicy(min_features=2, min_column_coverage=0.75)
    matrix = [numeric_vector(vector(value)) for value in (0, 10, 20, 30)]
    for row in matrix:
        row[1] = 9
    matrix[1][2] = matrix[2][2] = np.nan
    pre = fit_preprocessing(tuple(matrix), policy)
    assert pre.names[:3] == tuple(MODEL_FEATURE_NAMES[i] for i in (0, 3, 4))
    assert pre.medians[:3] == (15, 24, 27)
    future = numeric_vector(vector(1000, missing=(0,)))
    transformed, count = pre.transform(future)
    assert transformed[0] == 15 and transformed[1] == 1009
    assert count == 9 and np.isfinite(transformed).all()
    assert pre.transform(numeric_vector(vector(1, missing=tuple(range(12))))) == (None, 0)
    with pytest.raises(InvalidFeatures, match="incompatible_feature_schema"):
        numeric_vector(replace(vector(2), names=tuple(reversed(MODEL_FEATURE_NAMES))))
    with pytest.raises(InvalidFeatures, match="invalid_numeric_feature"):
        v = vector(1)
        numeric_vector(replace(v, values=(Decimal("NaN"), *v.values[1:])))


def test_deterministic_refit_fingerprint_failure_and_pending_independence():
    rows = history()
    result = analyze(rows)
    assert result == analyze(tuple(reversed(rows)), snapshot=LATER_IMPORT + timedelta(days=1))
    assert result.model.metrics.training_fitted == 48
    assert result.model.metrics.calibration_scored == 12
    assert result.model.metrics.validation_scored == 20
    assert result.model.status == MLStatus.DIAGNOSTIC_ONLY
    assert result.pending[0].anomaly_score is not None
    assert result.model.anomaly_threshold is not None
    changed_pending = replace(anomalous_pending(), vector=vector(8))
    assert analyze(rows, changed_pending).model == result.model
    changed = replace(rows[5], analysis=replace(rows[5].analysis, vector=vector(17)))
    assert analyze((*rows[:5], changed, *rows[6:])).model.training_fingerprint != (
        result.model.training_fingerprint
    )
    different = analyze(rows, policy=MLPolicy(n_estimators=5))
    assert different.model.training_fingerprint != result.model.training_fingerprint
    with pytest.raises(ValueError, match="ml_policy_fingerprint_mismatch"):
        ml_config(ml_config(CONFIG, MLPolicy()), MLPolicy(n_estimators=5))


def test_historical_threshold_and_holdout_workload_and_actionability():
    rows = history()
    policy = MLPolicy(min_calibration=3, calibration_quantile=0.75, min_validation=2)
    split = chronological_split(rows, frozenset(), CUTOFF, LATER_IMPORT, policy)
    preprocessing, data = prepare_fit(split.training, policy)
    fitted = fit_model(preprocessing, data, policy)
    calibration = tuple(
        ScoredRow(row, score)
        for row, score in zip(split.calibration, (0.2, 0.3, 0.4, 0.5), strict=False)
    )
    assert calibrate(calibration, policy) == 0.5
    subject = anomalous_pending()
    context = material_context(subject, fitted.preprocessing.names)
    assert actionable(context, policy)
    assert {ref.id for ref in context[0].evidence} >= {"pending", "current-0", "baseline-0"}
    assert "private synthetic" not in context[0].model_dump_json()
    within_fence = replace(
        subject,
        comparisons=tuple(
            replace(pair, team=pair.team.model_copy(update={"status": ComparisonStatus.IN_RANGE}))
            for pair in subject.comparisons
        ),
    )
    outlier = replace(
        split.validation[-1],
        analysis=within_fence,
        use=HistoryUse.VALIDATION_ONLY,
        reason_code=ReasonCode.OUTLIER_VERIFIED,
    )
    scores = (*tuple(ScoredRow(row, 0.5) for row in split.validation[:-1]), ScoredRow(outlier, 0.7))
    # The independent outlier is the only anomaly. Ties at the threshold are ordinary.
    metrics, gates = validation_gates(
        fitted, split, policy, analyze(rows).model.metrics, calibration, scores, 0.5
    )
    assert all(item.passed for item in gates)
    assert (metrics.normal_history_alerts, metrics.verified_outlier_alerts) == (0, 1)
    assert metrics.flagged_exposure_cents == 120000
    assert (metrics.statistical_review_count, metrics.incremental_review_count) == (0, 1)
    draft = analyze(rows).model.model_copy(
        update={"status": MLStatus.ACTIVE, "anomaly_threshold": 0.0}
    )
    finding = infer(subject, fitted, draft, policy)
    assert finding.qualifies_for_review and finding.status == MLStatus.ACTIVE
    assert "decision" not in finding.model_dump()


def test_explicit_insufficiency_validation_and_inference_failures(monkeypatch):
    rows = history()
    too_small = analyze(rows[:10])
    assert too_small.model.status == MLStatus.INSUFFICIENT_HISTORY
    assert too_small.pending[0].anomaly_score is None
    sparse = analyze(rows, replace(anomalous_pending(), vector=vector(2, missing=tuple(range(12)))))
    assert sparse.pending[0].status == MLStatus.INFERENCE_FAILED
    assert sparse.pending[0].diagnostic_code == "insufficient_payout_features"
    assert sparse.pending[0].is_anomaly is None

    def broken_validation(*args):
        raise ValueError("fixture private exception")

    monkeypatch.setattr(ml_analysis_service, "validation_gates", broken_validation)
    failed = analyze(rows)
    assert failed.model.status == MLStatus.VALIDATION_FAILED
    assert failed.pending[0].anomaly_score is not None
    assert failed.pending[0].anomaly_threshold is not None
    assert not failed.pending[0].qualifies_for_review
    assert "fixture private exception" not in failed.pending[0].model_dump_json()


def test_real_fitted_model_can_pass_gates_and_influence_review():
    rows = list(history())
    for index, row in enumerate(rows):
        value = index % 20 if index < 48 else 7 + index % 5
        rows[index] = replace(row, analysis=replace(row.analysis, vector=vector(value)))
    outlier = rows[-1]
    historical_subject = anomalous_pending(outlier.analysis.observation.payout_id, weeks=1)
    rows[-1] = replace(
        outlier,
        analysis=historical_subject,
        use=HistoryUse.VALIDATION_ONLY,
        reason_code=ReasonCode.OUTLIER_VERIFIED,
    )
    result = analyze(tuple(rows))
    assert result.model.status == MLStatus.ACTIVE
    assert all(gate.passed for gate in result.model.quality_gates)
    assert result.model.metrics.verified_outlier_alerts == 1
    assert result.model.metrics.normal_history_alerts == 0
    assert result.pending[0].qualifies_for_review
