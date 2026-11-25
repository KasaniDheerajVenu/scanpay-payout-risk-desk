from dataclasses import dataclass
from math import ceil

import numpy as np
from numpy.typing import NDArray

from app.ml.config import MLPolicy
from app.risk_engine.feature_schema import FEATURE_VERSION, Availability
from app.services.statistical_analysis_service import MODEL_FEATURE_NAMES, ModelVector

Array = NDArray[np.float64]


class InvalidFeatures(ValueError):
    pass


def numeric_vector(vector: ModelVector) -> Array:
    if (
        vector.feature_version != FEATURE_VERSION
        or vector.names != MODEL_FEATURE_NAMES
        or len(vector.values) != len(MODEL_FEATURE_NAMES)
        or len(vector.availability) != len(MODEL_FEATURE_NAMES)
    ):
        raise InvalidFeatures("incompatible_feature_schema")
    numeric = np.full(len(MODEL_FEATURE_NAMES), np.nan)
    for index, (value, availability) in enumerate(
        zip(vector.values, vector.availability, strict=True)
    ):
        if (value is None) != (availability != Availability.AVAILABLE):
            raise InvalidFeatures("inconsistent_feature_availability")
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                raise InvalidFeatures("invalid_numeric_feature") from None
            if not np.isfinite(number):
                raise InvalidFeatures("invalid_numeric_feature")
            numeric[index] = number
    return numeric


@dataclass(frozen=True, slots=True)
class Preprocessor:
    columns: tuple[int, ...]
    medians: tuple[float, ...]
    minimum_observed: int

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(MODEL_FEATURE_NAMES[column] for column in self.columns)

    def transform(self, vector: Array) -> tuple[Array | None, int]:
        selected = vector[list(self.columns)]
        observed = ~np.isnan(selected)
        count = int(observed.sum())
        if count < self.minimum_observed:
            return None, count
        values = np.where(observed, selected, self.medians)
        return values, count


def fit_preprocessing(rows: tuple[Array, ...], policy: MLPolicy) -> Preprocessor:
    columns: list[int] = []
    medians: list[float] = []
    if rows:
        matrix = np.stack(rows)
        required = max(2, ceil(len(rows) * policy.min_column_coverage))
        for index, column in enumerate(matrix.T):
            observed = column[~np.isnan(column)]
            if len(observed) >= required and observed.min() != observed.max():
                columns.append(index)
                medians.append(float(np.median(observed)))
    minimum = max(policy.min_features, ceil(len(columns) * policy.min_row_coverage))
    return Preprocessor(tuple(columns), tuple(medians), minimum)
