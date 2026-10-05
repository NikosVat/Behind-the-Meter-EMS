"""Small, auditable day-ahead forecaster using only pre-origin hourly observations.

Ridge competes against persistence on seven chronological validation days. This
module requires only NumPy; research MLPs and firmware models are separate models.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np


class ForecastUnavailable(ValueError):
    """No sufficiently complete, unambiguous history for this horizon."""


@dataclass
class LoadForecast:
    load_kw: list[float]
    model: str
    origin: str
    validation_mae_kw: dict[str, float]
    training_hours: int
    warnings: list[str]


def _fit(x, y):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.
    z = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    penalty = np.eye(z.shape[1]) * 1.
    penalty[0, 0] = 0.
    weights = np.linalg.solve(z.T @ z + penalty, z.T @ y)
    return mean, scale, weights


def _predict(model, x):
    mean, scale, weights = model
    return np.maximum(0., np.column_stack((np.ones(len(x)), (x - mean) / scale)) @ weights)


def forecast_day_ahead(hourly: Mapping[datetime, float], target: date, zone: str) -> LoadForecast:
    """Forecast a fixed 24-hour local day, issued at that day's midnight.

    Each observation represents an hourly sample mean. Missing hours remain
    missing and zero is valid. Duplicate local DST hours invalidate that day.
    DST target days are rejected until variable-length scheduling is supported.
    """
    tz = ZoneInfo(zone)
    origin = datetime.combine(target, time(), tz)
    next_origin = datetime.combine(target + timedelta(days=1), time(), tz)
    if (next_origin.astimezone(timezone.utc) - origin.astimezone(timezone.utc)).total_seconds() != 86400:
        raise ForecastUnavailable("DST transition days require a 23/25-hour scheduler; use a normal 24-hour date")
    by_day: dict[date, dict[int, float]] = {}
    ambiguous = set()
    for stamp, value in hourly.items():
        if stamp.tzinfo is None:
            raise ForecastUnavailable("Hourly timestamps must include a timezone")
        local = stamp.astimezone(tz)
        if local >= origin or not math.isfinite(value) or value < 0:
            continue
        day = local.date()
        slots = by_day.setdefault(day, {})
        if local.hour in slots:
            ambiguous.add(day)
        slots[local.hour] = float(value)
    days = {d: np.array([values[h] for h in range(24)]) for d, values in by_day.items()
            if len(values) == 24 and d not in ambiguous}
    if target - timedelta(days=1) not in days:
        raise ForecastUnavailable("A complete immediately preceding day of hourly telemetry is required")

    def features(d):
        if any(d - timedelta(days=n) not in days for n in (1, 2, 7)):
            return None
        previous, before, week = (days[d - timedelta(days=n)] for n in (1, 2, 7))
        hours = np.arange(24)
        return np.column_stack((previous, before, week,
                                np.full(24, previous.mean()),
                                np.sin(hours * 2 * np.pi / 24), np.cos(hours * 2 * np.pi / 24),
                                np.full(24, np.sin(d.weekday() * 2 * np.pi / 7)),
                                np.full(24, np.cos(d.weekday() * 2 * np.pi / 7))))

    candidate_days = [d for d in sorted(days) if features(d) is not None]
    target_features = features(target)
    warnings = ["Forecast is a whole-facility hourly sample-mean estimate, not a calibrated background-load measurement.",
                "Validation MAE measures historical prediction error; it is not a capacity or savings guarantee."]
    if len(candidate_days) < 21 or target_features is None:
        warnings.append("Insufficient complete history for ML selection; using previous-day persistence.")
        return LoadForecast(days[target - timedelta(days=1)].tolist(), "previous_day",
                            origin.isoformat(), {}, 0, warnings)

    training_days, validation_days = candidate_days[:-7], candidate_days[-7:]
    x_train = np.vstack([features(d) for d in training_days])
    y_train = np.concatenate([days[d] for d in training_days])
    x_validation = np.vstack([features(d) for d in validation_days])
    actual = np.concatenate([days[d] for d in validation_days])
    predictions = {"ridge": _predict(_fit(x_train, y_train), x_validation),
                   "previous_day": x_validation[:, 0], "previous_week": x_validation[:, 2]}
    scores = {name: float(np.mean(np.abs(values - actual))) for name, values in predictions.items()}
    baseline = min(("previous_day", "previous_week"), key=scores.get)
    selected = "ridge" if scores["ridge"] < .95 * scores[baseline] else baseline
    if selected == "ridge":
        model = _fit(np.vstack([features(d) for d in candidate_days]), np.concatenate([days[d] for d in candidate_days]))
        forecast = _predict(model, target_features)
    else:
        forecast = target_features[:, 0 if selected == "previous_day" else 2]
        warnings.append("ML did not improve validation MAE by 5%; using the better seasonal baseline.")
    return LoadForecast(forecast.tolist(), selected, origin.isoformat(), scores,
                        len(candidate_days) * 24, warnings)
