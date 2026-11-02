"""Splink comparators the matching agent may choose, built in one place.

Level counts and level descriptions come from the Splink objects themselves, so
the scores shown in the UI and the documentation given to the agent cannot drift
from what Splink actually computes.
"""
import functools
from typing import Any

ALL_SUPPORTED_COMPARATORS = [
    "ExactMatch",
    "JaroWinklerAtThresholds",
    "JaroAtThresholds",
    "LevenshteinAtThresholds",
    "DamerauLevenshteinAtThresholds",
    "JaccardAtThresholds",
    "AbsoluteDateDifferenceAtThresholds",
    "NameComparison",
    "DateOfBirthComparison",
    "EmailComparison",
    "PostcodeComparison",
]

THRESHOLD_DEFAULTS: dict[str, list[int | float]] = {
    "JaroWinklerAtThresholds": [0.92, 0.82],
    "JaroAtThresholds": [0.92, 0.82],
    "LevenshteinAtThresholds": [1, 2],
    "DamerauLevenshteinAtThresholds": [1, 2],
    "JaccardAtThresholds": [0.85, 0.70],
    "AbsoluteDateDifferenceAtThresholds": [30, 365],
}

FLOAT_THRESHOLD_COMPARATORS = {"JaroWinklerAtThresholds", "JaroAtThresholds", "JaccardAtThresholds"}
DATE_FORMAT = "%Y-%m-%d"


def normalize_thresholds(comparator: str, raw_thresholds: Any) -> list[int | float]:
    """Validate thresholds and put them in the order Splink needs (strictest level first)."""
    if comparator not in THRESHOLD_DEFAULTS:
        return []
    if not isinstance(raw_thresholds, list) or not raw_thresholds:
        return list(THRESHOLD_DEFAULTS[comparator])
    cleaned: list[int | float] = []
    for value in raw_thresholds:
        text = str(value).strip()
        if not text:
            continue
        try:
            if comparator in FLOAT_THRESHOLD_COMPARATORS:
                numeric: int | float = round(float(text), 3)
                if not 0 < numeric <= 1:
                    raise ValueError
            else:
                numeric = int(float(text))
                if numeric < 0:
                    raise ValueError
        except ValueError as exc:
            kind = "decimal values in (0, 1]" if comparator in FLOAT_THRESHOLD_COMPARATORS else "non-negative integers"
            raise ValueError(f"{comparator} thresholds must be {kind}.") from exc
        cleaned.append(numeric)
    if not cleaned:
        return list(THRESHOLD_DEFAULTS[comparator])
    # Similarities: highest first. Distances: smallest first.
    return sorted(set(cleaned), reverse=comparator in FLOAT_THRESHOLD_COMPARATORS)


def build_splink_comparison(field: str, comparator_name: str, thresholds: list[Any] | None = None) -> Any:
    import splink.comparison_library as cl

    if comparator_name not in ALL_SUPPORTED_COMPARATORS:
        raise ValueError(f"Unsupported Splink comparator: {comparator_name}")
    levels = normalize_thresholds(comparator_name, thresholds)
    if comparator_name == "ExactMatch":
        return cl.ExactMatch(field)
    if comparator_name == "JaroWinklerAtThresholds":
        return cl.JaroWinklerAtThresholds(field, levels)
    if comparator_name == "JaroAtThresholds":
        return cl.JaroAtThresholds(field, levels)
    if comparator_name == "LevenshteinAtThresholds":
        return cl.LevenshteinAtThresholds(field, levels)
    if comparator_name == "DamerauLevenshteinAtThresholds":
        return cl.DamerauLevenshteinAtThresholds(field, levels)
    if comparator_name == "JaccardAtThresholds":
        return cl.JaccardAtThresholds(field, levels)
    if comparator_name == "AbsoluteDateDifferenceAtThresholds":
        return cl.AbsoluteDateDifferenceAtThresholds(
            field,
            input_is_string=True,
            metrics=["day"] * len(levels),
            thresholds=levels,
            datetime_format=DATE_FORMAT,
            invalid_dates_as_null=True,
        )
    if comparator_name == "NameComparison":
        return cl.NameComparison(field)
    if comparator_name == "DateOfBirthComparison":
        # Levels must run from tightest to loosest, otherwise the 1-year level
        # swallows every pair the 1-month level should have caught.
        return cl.DateOfBirthComparison(
            field,
            input_is_string=True,
            datetime_format=DATE_FORMAT,
            invalid_dates_as_null=True,
            datetime_metrics=["month", "year"],
            datetime_thresholds=[1, 1],
        )
    if comparator_name == "EmailComparison":
        return cl.EmailComparison(field)
    return cl.PostcodeComparison(field)


@functools.lru_cache(maxsize=512)
def _level_labels(comparator_name: str, thresholds: tuple[Any, ...]) -> tuple[str, ...]:
    comparison = build_splink_comparison("value", comparator_name, list(thresholds)).get_comparison("duckdb")
    return tuple(level.label_for_charts for level in comparison.comparison_levels if not level.is_null_level)


def comparison_level_labels(comparator_name: str, thresholds: list[Any] | None = None) -> list[str]:
    """Non-null levels, strongest first. The first label is gamma = len - 1, the last is gamma 0."""
    return list(_level_labels(comparator_name, tuple(normalize_thresholds(comparator_name, thresholds))))


def comparator_gamma_max(comparator_name: str, thresholds: list[Any] | None = None) -> int:
    return max(1, len(comparison_level_labels(comparator_name, thresholds)) - 1)
