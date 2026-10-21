from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class StationConstraint:
    station_id: str
    x: float
    y: float
    height_m: float
    u_ms: float
    v_ms: float
    wind_sigma_ms: float = 1.0
    k_m2s2: float | None = None
    k_sigma_m2s2: float | None = None
    source: str = "fixed_station"


@dataclass(frozen=True)
class StationCsvRecord:
    station_id: str
    time_utc: datetime
    latitude: float
    longitude: float
    height_m: float
    height_reference: str
    u_ms: float
    v_ms: float
    wind_sigma_ms: float = 1.0
    k_m2s2: float | None = None
    k_sigma_m2s2: float | None = None
    source: str = "fixed_station"


@dataclass(frozen=True)
class ObservationStencil:
    indices: np.ndarray
    weights: np.ndarray
    outside: bool = False


@dataclass(frozen=True)
class StationAnalysis:
    station_id: str
    observation: float | None
    background: float | None
    analysis: float | None
    innovation: float | None
    used: bool
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class ScalarAnalysisResult:
    field: np.ndarray
    per_station: tuple[StationAnalysis, ...]
    used_station_ids: tuple[str, ...]
    excluded: dict[str, int]
    summary: dict[str, int]


@dataclass(frozen=True)
class VectorStationAnalysis:
    station_id: str
    observed_u_ms: float | None
    observed_v_ms: float | None
    background_u_ms: float | None
    background_v_ms: float | None
    analysis_u_ms: float | None
    analysis_v_ms: float | None
    innovation_u_ms: float | None
    innovation_v_ms: float | None
    used: bool
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class VectorAnalysisResult:
    u_field: np.ndarray
    v_field: np.ndarray
    per_station: tuple[VectorStationAnalysis, ...]
    used_station_ids: tuple[str, ...]
    excluded: dict[str, int]
    summary: dict[str, int]


def _axis(axis: Sequence[float], *, name: str, increasing: bool) -> np.ndarray:
    values = np.asarray(axis, dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError(f"{name}_axis must be a non-empty finite one-dimensional array")
    if values.size > 1:
        delta = np.diff(values)
        if not np.all(delta > 0 if increasing else delta < 0):
            direction = "increasing" if increasing else "decreasing"
            raise ValueError(f"{name}_axis must be strictly {direction}")
    return values


def observation_stencil(
    x_axis: Sequence[float], y_axis: Sequence[float], x: float, y: float
) -> ObservationStencil:
    xs = _axis(x_axis, name="x", increasing=True)
    ys = _axis(y_axis, name="y", increasing=False)
    if not isfinite(x) or not isfinite(y) or x < xs[0] or x > xs[-1] or y > ys[0] or y < ys[-1]:
        return ObservationStencil(np.empty(0, dtype=int), np.empty(0, dtype=float), True)

    def bracket(values: np.ndarray, value: float, descending: bool = False) -> tuple[tuple[int, float], ...]:
        if values.size == 1:
            return ((0, 1.0),)
        probe = -values if descending else values
        target = -value if descending else value
        upper = int(np.searchsorted(probe, target, side="right"))
        if upper == 0:
            return ((0, 1.0),)
        if upper >= values.size:
            return ((values.size - 1, 1.0),)
        lower = upper - 1
        fraction = (target - probe[lower]) / (probe[upper] - probe[lower])
        return ((lower, 1.0 - fraction), (upper, fraction))

    x_terms = bracket(xs, float(x))
    y_terms = bracket(ys, float(y), descending=True)
    entries: dict[int, float] = {}
    for row, wy in y_terms:
        for col, wx in x_terms:
            index = row * xs.size + col
            entries[index] = entries.get(index, 0.0) + wx * wy
    return ObservationStencil(
        np.fromiter(entries.keys(), dtype=int), np.fromiter(entries.values(), dtype=float), False
    )


def apply_observation_stencil(field: np.ndarray, stencil: ObservationStencil) -> float:
    if stencil.outside:
        raise ValueError("Cannot sample an outside observation stencil")
    values = np.asarray(field)
    return float(np.dot(values.ravel(order="C")[stencil.indices], stencil.weights))


def _coerce_mask(nodata: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray:
    if nodata is None:
        return np.zeros(shape, dtype=bool)
    mask = np.asarray(nodata, dtype=bool)
    if mask.shape != shape:
        raise ValueError("nodata mask shape must match the background field")
    return mask


def _validate_backgrounds(
    backgrounds: Sequence[np.ndarray], x_axis: Sequence[float], y_axis: Sequence[float], nodata: np.ndarray | None
) -> tuple[tuple[np.ndarray, ...], np.ndarray, np.ndarray, np.ndarray]:
    xs = _axis(x_axis, name="x", increasing=True)
    ys = _axis(y_axis, name="y", increasing=False)
    fields = tuple(np.asarray(item, dtype=float) for item in backgrounds)
    if not fields or any(item.ndim != 2 or item.shape != (ys.size, xs.size) for item in fields):
        raise ValueError("background fields must be two-dimensional [y, x] arrays matching the axes")
    invalid = _coerce_mask(nodata, fields[0].shape).copy()
    for item in fields:
        invalid |= ~np.isfinite(item)
    return fields, xs, ys, invalid


def _finite_positive(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _sequence(value: Sequence[float] | float | None, count: int, *, name: str, default: float | None = None) -> np.ndarray:
    if value is None:
        if default is None:
            raise ValueError(f"{name} is required")
        return np.full(count, default, dtype=float)
    data = np.asarray(value, dtype=float)
    if data.ndim == 0:
        return np.full(count, float(data), dtype=float)
    if data.shape != (count,):
        raise ValueError(f"{name} must be a scalar or one value per constraint")
    return data


def _covariance(
    left_xy: np.ndarray, right_xy: np.ndarray, left_z: np.ndarray, right_z: np.ndarray,
    *, background_sigma: float, horizontal_length_scale_m: float, vertical_length_scale_m: float | None,
) -> np.ndarray:
    dx = left_xy[:, None, 0] - right_xy[None, :, 0]
    dy = left_xy[:, None, 1] - right_xy[None, :, 1]
    exponent = (dx * dx + dy * dy) / (horizontal_length_scale_m * horizontal_length_scale_m)
    if vertical_length_scale_m is not None:
        dz = left_z[:, None] - right_z[None, :]
        exponent += (dz * dz) / (vertical_length_scale_m * vertical_length_scale_m)
    return background_sigma * background_sigma * np.exp(-0.5 * exponent)


def _support_coordinates(stencil: ObservationStencil, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    cols = stencil.indices % xs.size
    rows = stencil.indices // xs.size
    return np.column_stack((xs[cols], ys[rows]))


def _cross_covariance(
    grid_xy: np.ndarray, stencils: Sequence[ObservationStencil], offsets: np.ndarray,
    xs: np.ndarray, ys: np.ndarray, *, background_sigma: float, horizontal_length_scale_m: float,
    vertical_length_scale_m: float | None,
) -> np.ndarray:
    result = np.empty((grid_xy.shape[0], len(stencils)), dtype=float)
    zero = np.zeros(grid_xy.shape[0], dtype=float)
    for index, stencil in enumerate(stencils):
        support = _support_coordinates(stencil, xs, ys)
        covariance = _covariance(
            grid_xy, support, zero, np.full(support.shape[0], offsets[index]),
            background_sigma=background_sigma, horizontal_length_scale_m=horizontal_length_scale_m,
            vertical_length_scale_m=vertical_length_scale_m,
        )
        result[:, index] = covariance @ stencil.weights
    return result


def _observation_covariance(
    stencils: Sequence[ObservationStencil], offsets: np.ndarray, xs: np.ndarray, ys: np.ndarray,
    *, background_sigma: float, horizontal_length_scale_m: float, vertical_length_scale_m: float | None,
) -> np.ndarray:
    count = len(stencils)
    result = np.empty((count, count), dtype=float)
    support = [_support_coordinates(stencil, xs, ys) for stencil in stencils]
    for left in range(count):
        for right in range(left, count):
            covariance = _covariance(
                support[left], support[right],
                np.full(support[left].shape[0], offsets[left]),
                np.full(support[right].shape[0], offsets[right]),
                background_sigma=background_sigma, horizontal_length_scale_m=horizontal_length_scale_m,
                vertical_length_scale_m=vertical_length_scale_m,
            )
            value = float(stencils[left].weights @ covariance @ stencils[right].weights)
            result[left, right] = value
            result[right, left] = value
    return result


def _analyse(
    backgrounds: Sequence[np.ndarray], x_axis: Sequence[float], y_axis: Sequence[float], constraints: Sequence[StationConstraint],
    values: np.ndarray, sigmas: np.ndarray, *, length_scale_m: float, background_sigma: float,
    vertical_length_scale_m: float | None, vertical_offsets_m: Sequence[float] | float | None,
    nodata: np.ndarray | None, block_rows: int,
) -> tuple[tuple[np.ndarray, ...], list[dict[str, object]], Counter[str]]:
    fields, xs, ys, invalid_grid = _validate_backgrounds(backgrounds, x_axis, y_axis, nodata)
    length_scale_m = _finite_positive("length_scale_m", length_scale_m)
    background_sigma = _finite_positive("background_sigma", background_sigma)
    if vertical_length_scale_m is not None:
        vertical_length_scale_m = _finite_positive("vertical_length_scale_m", vertical_length_scale_m)
    if not isinstance(block_rows, int) or block_rows <= 0:
        raise ValueError("block_rows must be a positive integer")
    offsets = _sequence(vertical_offsets_m, len(constraints), name="vertical_offsets_m", default=0.0)
    if not np.all(np.isfinite(offsets)):
        raise ValueError("vertical_offsets_m must be finite")
    if values.shape != (len(constraints), len(fields)) or sigmas.shape != (len(constraints),):
        raise ValueError("internal constraint values do not match constraints")

    diagnostics: list[dict[str, object]] = [
        {"station_id": item.station_id, "used": False, "reason": None,
         "observation": values[index].copy(), "background": None, "analysis": None}
        for index, item in enumerate(constraints)
    ]
    rejected: Counter[str] = Counter()
    stencils: list[ObservationStencil] = []
    used_indices: list[int] = []
    seen_station_ids: set[str] = set()
    for index, item in enumerate(constraints):
        reason: str | None = None
        if item.source.lower() == "uav":
            reason = "uav_not_assimilation_source"
        elif not item.station_id:
            reason = "duplicate_station_constraint"
        elif item.station_id in seen_station_ids:
            reason = "duplicate_station_constraint"
        else:
            seen_station_ids.add(item.station_id)
        if reason is None and not all(isfinite(float(value)) for value in (item.x, item.y, item.height_m)):
            reason = "nonfinite_station_coordinate_or_height"
        elif reason is None and not np.all(np.isfinite(values[index])):
            reason = "nonfinite_observation_value"
        elif reason is None and (not isfinite(float(sigmas[index])) or sigmas[index] <= 0):
            reason = "invalid_observation_sigma"
        elif reason is None:
            stencil = observation_stencil(xs, ys, float(item.x), float(item.y))
            if stencil.outside:
                reason = "outside_grid"
            elif np.any(invalid_grid.ravel(order="C")[stencil.indices]):
                reason = "observation_stencil_touches_nodata"
        if reason is not None:
            diagnostics[index]["reason"] = reason
            rejected[reason] += 1
            continue
        diagnostics[index]["used"] = True
        stencils.append(stencil)
        used_indices.append(index)

    analyses = tuple(item.copy() for item in fields)
    if not used_indices:
        return analyses, diagnostics, rejected

    used_offsets = offsets[used_indices]
    used_values = values[used_indices]
    used_sigmas = sigmas[used_indices]
    background_samples = np.asarray(
        [[apply_observation_stencil(field, stencil) for field in fields] for stencil in stencils], dtype=float
    )
    observation_covariance = _observation_covariance(
        stencils, used_offsets, xs, ys, background_sigma=background_sigma,
        horizontal_length_scale_m=length_scale_m, vertical_length_scale_m=vertical_length_scale_m,
    )
    system = observation_covariance + np.diag(used_sigmas * used_sigmas)
    try:
        weights = np.linalg.solve(system, used_values - background_samples)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Observation covariance system could not be solved") from exc

    for row_start in range(0, ys.size, block_rows):
        row_end = min(ys.size, row_start + block_rows)
        chunk_xy = np.column_stack((np.tile(xs, row_end - row_start), np.repeat(ys[row_start:row_end], xs.size)))
        gain = _cross_covariance(
            chunk_xy, stencils, used_offsets, xs, ys, background_sigma=background_sigma,
            horizontal_length_scale_m=length_scale_m, vertical_length_scale_m=vertical_length_scale_m,
        )
        valid = ~invalid_grid[row_start:row_end].ravel(order="C")
        for component, field in enumerate(analyses):
            correction = gain @ weights[:, component]
            destination = field[row_start:row_end].reshape(-1)
            destination[valid] += correction[valid]

    for local_index, original_index in enumerate(used_indices):
        diagnostics[original_index]["background"] = background_samples[local_index]
        diagnostics[original_index]["analysis"] = np.asarray(
            [apply_observation_stencil(field, stencils[local_index]) for field in analyses], dtype=float
        )
    return analyses, diagnostics, rejected


def analyse_scalar(
    background: np.ndarray,
    x_axis: Sequence[float],
    y_axis: Sequence[float],
    constraints: Sequence[StationConstraint],
    *,
    values: Sequence[float] | float | None = None,
    observation_sigmas: Sequence[float] | float | None = None,
    length_scale_m: float,
    background_sigma: float,
    vertical_length_scale_m: float | None = None,
    vertical_offsets_m: Sequence[float] | float | None = None,
    nodata: np.ndarray | None = None,
    block_rows: int = 256,
) -> ScalarAnalysisResult:
    items = tuple(constraints)
    if values is None:
        scalar_values = np.asarray([np.nan if item.k_m2s2 is None else item.k_m2s2 for item in items], dtype=float)
    else:
        scalar_values = _sequence(values, len(items), name="values")
    if observation_sigmas is None:
        scalar_sigmas = np.asarray([np.nan if item.k_sigma_m2s2 is None else item.k_sigma_m2s2 for item in items], dtype=float)
    else:
        scalar_sigmas = _sequence(observation_sigmas, len(items), name="observation_sigmas")
    fields, diagnostics, rejected = _analyse(
        (background,), x_axis, y_axis, items, scalar_values[:, None], scalar_sigmas,
        length_scale_m=length_scale_m, background_sigma=background_sigma,
        vertical_length_scale_m=vertical_length_scale_m, vertical_offsets_m=vertical_offsets_m,
        nodata=nodata, block_rows=block_rows,
    )
    records: list[StationAnalysis] = []
    for index, item in enumerate(items):
        report = diagnostics[index]
        observation = float(scalar_values[index]) if np.isfinite(scalar_values[index]) else None
        before = report["background"]
        after = report["analysis"]
        background_value = None if before is None else float(np.asarray(before)[0])
        analysis_value = None if after is None else float(np.asarray(after)[0])
        records.append(StationAnalysis(
            item.station_id, observation, background_value, analysis_value,
            None if background_value is None or observation is None else observation - background_value,
            bool(report["used"]), report["reason"] if isinstance(report["reason"], str) else None,
        ))
    summary = {"constraint_count": len(items), "used_count": len(items) - sum(rejected.values()), "excluded_count": sum(rejected.values())}
    return ScalarAnalysisResult(fields[0], tuple(records), tuple(item.station_id for item in records if item.used), dict(rejected), summary)


def analyse_vector(
    u_background: np.ndarray,
    v_background: np.ndarray,
    x_axis: Sequence[float],
    y_axis: Sequence[float],
    constraints: Sequence[StationConstraint],
    *,
    observation_sigmas: Sequence[float] | float | None = None,
    length_scale_m: float,
    background_sigma: float,
    vertical_length_scale_m: float | None = None,
    vertical_offsets_m: Sequence[float] | float | None = None,
    nodata: np.ndarray | None = None,
    block_rows: int = 256,
) -> VectorAnalysisResult:
    items = tuple(constraints)
    values = np.asarray([[item.u_ms, item.v_ms] for item in items], dtype=float)
    sigmas = _sequence(observation_sigmas, len(items), name="observation_sigmas") if observation_sigmas is not None else np.asarray([item.wind_sigma_ms for item in items], dtype=float)
    fields, diagnostics, rejected = _analyse(
        (u_background, v_background), x_axis, y_axis, items, values, sigmas,
        length_scale_m=length_scale_m, background_sigma=background_sigma,
        vertical_length_scale_m=vertical_length_scale_m, vertical_offsets_m=vertical_offsets_m,
        nodata=nodata, block_rows=block_rows,
    )
    records: list[VectorStationAnalysis] = []
    for index, item in enumerate(items):
        report = diagnostics[index]
        before = report["background"]
        after = report["analysis"]
        before_pair = (None, None) if before is None else tuple(float(value) for value in np.asarray(before))
        after_pair = (None, None) if after is None else tuple(float(value) for value in np.asarray(after))
        records.append(VectorStationAnalysis(
            item.station_id, float(values[index, 0]), float(values[index, 1]), *before_pair, *after_pair,
            None if before_pair[0] is None else float(values[index, 0] - before_pair[0]),
            None if before_pair[1] is None else float(values[index, 1] - before_pair[1]),
            bool(report["used"]), report["reason"] if isinstance(report["reason"], str) else None,
        ))
    summary = {"constraint_count": len(items), "used_count": len(items) - sum(rejected.values()), "excluded_count": sum(rejected.values())}
    return VectorAnalysisResult(fields[0], fields[1], tuple(records), tuple(item.station_id for item in records if item.used), dict(rejected), summary)


_CSV_REQUIRED = {"station_id", "time_utc", "latitude", "longitude", "height_m", "height_reference"}


def _csv_float(row: dict[str, str], column: str, row_number: int) -> float:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"CSV row {row_number}: {column} must be numeric") from exc
    if not isfinite(value):
        raise ValueError(f"CSV row {row_number}: {column} must be finite")
    return value


def _csv_optional_float(row: dict[str, str], column: str, row_number: int) -> float | None:
    value = row.get(column)
    if value is None or not value.strip():
        return None
    return _csv_float(row, column, row_number)


def _parse_time_utc(value: str, row_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"CSV row {row_number}: time_utc must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"CSV row {row_number}: time_utc must include a timezone")
    return parsed.astimezone(timezone.utc)


def _station_location_distance_m(
    first_latitude: float,
    first_longitude: float,
    latitude: float,
    longitude: float,
) -> float:
    first_latitude_rad, latitude_rad = radians(first_latitude), radians(latitude)
    latitude_delta = latitude_rad - first_latitude_rad
    longitude_delta = radians(longitude - first_longitude)
    haversine = (
        sin(latitude_delta / 2.0) ** 2
        + cos(first_latitude_rad) * cos(latitude_rad) * sin(longitude_delta / 2.0) ** 2
    )
    return 2.0 * 6_371_008.8 * asin(min(1.0, sqrt(haversine)))


def load_station_csv(path: str | Path) -> tuple[StationCsvRecord, ...]:
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or ())
        missing = sorted(_CSV_REQUIRED - headers)
        has_polar = {"speed_ms", "direction_deg"} <= headers
        has_vector = {"u_ms", "v_ms"} <= headers
        if missing:
            raise ValueError(f"Station CSV missing required columns: {', '.join(missing)}")
        if not has_polar and not has_vector:
            raise ValueError("Station CSV needs speed_ms/direction_deg or u_ms/v_ms; units must be explicit")
        variance_columns = {"sigma_u_ms", "sigma_v_ms", "sigma_w_ms"}
        present_variances = variance_columns & headers
        if present_variances and present_variances != variance_columns:
            raise ValueError("Station CSV must provide all of sigma_u_ms, sigma_v_ms, sigma_w_ms")
        records: list[StationCsvRecord] = []
        station_locations: dict[str, tuple[float, float]] = {}
        duplicate_keys: set[tuple[str, datetime, float, str]] = set()
        for row_number, row in enumerate(reader, start=2):
            station_id = (row.get("station_id") or "").strip()
            if not station_id:
                raise ValueError(f"CSV row {row_number}: station_id is required")
            time_utc = _parse_time_utc(row.get("time_utc") or "", row_number)
            latitude = _csv_float(row, "latitude", row_number)
            longitude = _csv_float(row, "longitude", row_number)
            height_m = _csv_float(row, "height_m", row_number)
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or height_m < 0:
                raise ValueError(f"CSV row {row_number}: invalid latitude, longitude, or height_m")
            prior_location = station_locations.setdefault(station_id, (latitude, longitude))
            if _station_location_distance_m(*prior_location, latitude, longitude) > 2.0:
                raise ValueError(
                    f"CSV row {row_number}: station_id {station_id!r} location must remain within 2 m; use a new station_id"
                )
            height_reference = (row.get("height_reference") or "").strip().lower()
            if height_reference not in {"agl", "vegetation_top"}:
                raise ValueError(f"CSV row {row_number}: height_reference must be agl or vegetation_top")
            source = (row.get("source") or "fixed_station").strip().lower()
            if source == "uav":
                raise ValueError(f"CSV row {row_number}: uav observations cannot be assimilation constraints")
            if has_vector and (row.get("u_ms") or "").strip() and (row.get("v_ms") or "").strip():
                u_ms, v_ms = _csv_float(row, "u_ms", row_number), _csv_float(row, "v_ms", row_number)
            elif has_polar:
                speed, direction = _csv_float(row, "speed_ms", row_number), _csv_float(row, "direction_deg", row_number)
                if speed < 0 or not 0 <= direction <= 360:
                    raise ValueError(f"CSV row {row_number}: speed_ms/direction_deg out of range")
                angle = radians(direction)
                u_ms, v_ms = -speed * sin(angle), -speed * cos(angle)
            else:
                raise ValueError(f"CSV row {row_number}: provide both u_ms and v_ms, or speed_ms and direction_deg")
            wind_sigma = _csv_optional_float(row, "wind_sigma_ms", row_number)
            if wind_sigma is None:
                wind_sigma = 1.0
            if wind_sigma <= 0:
                raise ValueError(f"CSV row {row_number}: wind_sigma_ms must be positive")
            k_value = _csv_optional_float(row, "k_m2s2", row_number)
            k_sigma = _csv_optional_float(row, "k_sigma_m2s2", row_number)
            if k_value is not None and k_value < 0:
                raise ValueError(f"CSV row {row_number}: k_m2s2 cannot be negative")
            if k_sigma is not None and k_sigma <= 0:
                raise ValueError(f"CSV row {row_number}: k_sigma_m2s2 must be positive")
            if present_variances:
                variances = [_csv_float(row, column, row_number) for column in sorted(variance_columns)]
                if any(value < 0 for value in variances):
                    raise ValueError(f"CSV row {row_number}: component standard deviations cannot be negative")
                if k_value is None:
                    k_value = 0.5 * sum(value * value for value in variances)
            key = (station_id, time_utc, height_m, height_reference)
            if key in duplicate_keys:
                raise ValueError(f"CSV row {row_number}: duplicate station_id/time_utc/height_m constraint")
            duplicate_keys.add(key)
            records.append(StationCsvRecord(station_id, time_utc, latitude, longitude, height_m, height_reference,
                                            u_ms, v_ms, wind_sigma, k_value, k_sigma, source))
    return tuple(records)


load_station_constraints_csv = load_station_csv
read_station_csv = load_station_csv


__all__ = [
    "ObservationStencil", "ScalarAnalysisResult", "StationAnalysis", "StationConstraint", "StationCsvRecord",
    "VectorAnalysisResult", "VectorStationAnalysis", "analyse_scalar", "analyse_vector", "apply_observation_stencil",
    "load_station_constraints_csv", "load_station_csv", "observation_stencil", "read_station_csv",
]
