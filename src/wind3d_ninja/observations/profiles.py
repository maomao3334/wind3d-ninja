from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite, radians, sin, cos
from typing import Any

import numpy as np
from pyproj import Geod

from .models import Observation


_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class ProfileResult:
    stations: tuple[Observation, ...]
    background_speed_ms: float | None
    background_direction_deg: float | None
    reference_height_m: float
    diagnostics: tuple[Mapping[str, Any], ...]
    rejected: Mapping[str, int]

    @property
    def station_count(self) -> int:
        return len(self.stations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stations": [station.as_dict() for station in self.stations],
            "station_count": self.station_count,
            "background_speed_ms": self.background_speed_ms,
            "background_direction_deg": self.background_direction_deg,
            "reference_height_m": self.reference_height_m,
            "height_reference": "above_vegetation",
            "station_weighting": "equal_physical_station",
            "diagnostics": [dict(item) for item in self.diagnostics],
            "rejected": dict(self.rejected),
        }


def _components(observation: Observation) -> tuple[float, float]:
    angle = radians(observation.direction_deg)
    return -observation.speed_ms * sin(angle), -observation.speed_ms * cos(angle)


def _polar(u: float, v: float) -> tuple[float, float]:
    speed = hypot(u, v)
    if speed < 1e-12:
        return 0.0, 0.0
    return speed, degrees(atan2(-u, -v)) % 360.0


def build_station_profiles(
    observations: Iterable[Observation],
    *,
    reference_height_m: float,
    roughness_m: float = 0.1,
    allow_log_extrapolation: bool = False,
    location_tolerance_m: float = 2.0,
    group_key: str | Callable[[Observation], str | None] | None = None,
    displacement_height_m: float = 0.0,
) -> ProfileResult:
    for name, value in (
        ("reference_height_m", reference_height_m), ("roughness_m", roughness_m),
        ("location_tolerance_m", location_tolerance_m),
    ):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not isfinite(displacement_height_m) or displacement_height_m < 0:
        raise ValueError("displacement_height_m must be finite and non-negative")
    rejected: Counter[str] = Counter()
    buckets: dict[tuple[str, str, str, str | None], list[list[Observation]]] = {}
    for observation in sorted(observations, key=lambda item: (
        item.source, str(item.metadata.get("product", "")),
        item.lat if item.lat is not None else 0.0,
        item.lon if item.lon is not None else 0.0, item.height_m, item.time_utc, item.station,
    )):
        values = (observation.height_m, observation.speed_ms, observation.direction_deg,
                  observation.lat, observation.lon)
        if any(value is None or not isfinite(value) for value in values) or observation.height_m <= 0:
            rejected["invalid_profile_observation"] += 1
            continue
        if observation.metadata.get("wind_direction_reference", "from_true_north") != "from_true_north":
            rejected["profile_requires_from_true_north"] += 1
            continue
        if callable(group_key):
            explicit_key = group_key(observation)
        elif isinstance(group_key, str):
            explicit_key = observation.metadata.get(group_key)
        else:
            explicit_key = observation.metadata.get("station_group_id")
        key = (
            observation.source, str(observation.metadata.get("product", "")),
            str(observation.metadata.get("flight_id", "")),
            None if explicit_key is None else str(explicit_key),
        )
        groups = buckets.setdefault(key, [])
        matching = None
        for group in groups:
            if explicit_key is not None:
                matching = group
                break
            _, _, distance = _GEOD.inv(group[0].lon, group[0].lat, observation.lon, observation.lat)
            if distance <= location_tolerance_m:
                matching = group
                break
        if matching is None:
            groups.append([observation])
        else:
            matching.append(observation)
    stations: list[Observation] = []
    diagnostics: list[Mapping[str, Any]] = []
    for bucket_key, groups in sorted(buckets.items(), key=lambda pair: str(pair[0])):
        for index, group in enumerate(groups):
            group_id = (
                f"{bucket_key[0]}:{bucket_key[1]}:{bucket_key[2]}:"
                f"{bucket_key[3] if bucket_key[3] is not None else f'{group[0].lat:.7f}:{group[0].lon:.7f}'}:{index}"
            )
            canopy_values = [float(item.metadata.get("canopy_height_m", 0.0)) for item in group]
            if any(not isfinite(value) or value < 0 for value in canopy_values) or not np.allclose(
                canopy_values, canopy_values[0], rtol=0, atol=1e-9
            ):
                rejected["inconsistent_or_invalid_station_canopy"] += 1
                continue
            canopy = canopy_values[0]
            by_height: dict[float, list[tuple[float, float]]] = {}
            for item in group:
                by_height.setdefault(item.height_m, []).append(_components(item))
            heights = np.array(sorted(by_height), dtype=float)
            uv = np.array([np.mean(by_height[height], axis=0) for height in heights])
            effective_heights = heights + canopy - displacement_height_m
            effective_target = reference_height_m + canopy - displacement_height_m
            log_fit_rmse = None
            log_coefficients = None
            if np.all(effective_heights > roughness_m) and effective_target > roughness_m:
                logs = np.log(effective_heights / roughness_m)
                log_coefficients = np.sum(logs[:, None] * uv, axis=0) / np.dot(logs, logs)
                log_fit_rmse = float(np.sqrt(np.mean(np.sum(
                    (uv - logs[:, None] * log_coefficients) ** 2, axis=1
                ))))
            rejected_diagnostic = {
                "group_id": group_id, "source": bucket_key[0], "product": bucket_key[1],
                "input_count": len(group), "gate_count": len(heights),
                "original_heights_av_m": [float(z) for z in heights],
                "reference_height_m": reference_height_m, "height_reference": "above_vegetation",
                "input_times_utc": sorted({item.time_utc.isoformat() for item in group}),
                "extrapolated": True, "fit_residual_ms": log_fit_rmse,
            }
            extrapolated = not heights[0] <= reference_height_m <= heights[-1]
            residual = None
            if extrapolated:
                if not allow_log_extrapolation:
                    rejected["reference_height_outside_observed_profile"] += 1
                    diagnostics.append({**rejected_diagnostic, "errors": ["reference_height_outside_observed_profile"]})
                    continue
                if log_coefficients is None:
                    rejected["neutral_log_height_not_above_roughness_plane"] += 1
                    diagnostics.append({**rejected_diagnostic, "errors": ["neutral_log_height_not_above_roughness_plane"]})
                    continue
                target_uv = log_coefficients * np.log(effective_target / roughness_m)
                method = "neutral_log_uv_fit"
                residual = log_fit_rmse
            else:
                target_uv = np.array([np.interp(reference_height_m, heights, uv[:, axis]) for axis in (0, 1)])
                method = "observed_gate" if reference_height_m in by_height else "linear_uv_interpolation"
                if len(heights) >= 3:
                    held_out = np.array([
                        [np.interp(heights[i], np.delete(heights, i), np.delete(uv[:, axis], i))
                         for axis in (0, 1)]
                        for i in range(1, len(heights) - 1)
                    ])
                    residual = float(np.sqrt(np.mean(np.sum(
                        (uv[1:-1] - held_out) ** 2, axis=1
                    ))))
            speed, direction = _polar(float(target_uv[0]), float(target_uv[1]))
            diagnostic = {
                "group_id": group_id, "source": bucket_key[0], "product": bucket_key[1],
                "flight_id": bucket_key[2], "original_heights_av_m": [float(z) for z in heights],
                "input_times_utc": sorted({item.time_utc.isoformat() for item in group}),
                "input_record_ids": [item.station for item in group],
                "input_count": len(group), "gate_count": len(heights),
                "observed_min_height_m": float(heights[0]), "observed_max_height_m": float(heights[-1]),
                "reference_height_m": reference_height_m, "height_reference": "above_vegetation",
                "method": method, "extrapolated": bool(extrapolated),
                "fit_residual_ms": residual,
                "residual_definition": ("neutral_log_vector_rmse" if extrapolated else "interior_gate_leave_one_out_linear_vector_rmse"),
                "neutral_log_fit_rmse_ms": log_fit_rmse,
                "neutral_log_assumption": bool(extrapolated), "roughness_m": roughness_m,
                "canopy_height_m": canopy, "displacement_height_m": displacement_height_m,
                "log_formula": "log((height_av_m + canopy_height_m - displacement_height_m)/roughness_m)",
                "location_tolerance_m": location_tolerance_m,
                "u_ms": float(target_uv[0]), "v_ms": float(target_uv[1]),
                "errors": [],
            }
            metadata = {
                **dict(group[0].metadata), "profile": diagnostic,
                "canopy_height_m": canopy, "height_above_vegetation_m": reference_height_m,
                "instrument_agl_m": reference_height_m + canopy,
                "wind_direction_reference": "from_true_north",
                "station_group_id": group_id, "height_reference": "above_vegetation",
            }
            station = Observation(
                source=group[0].source, station=group_id,
                time_utc=max(item.time_utc for item in group),
                lat=float(np.mean([item.lat for item in group])),
                lon=float(np.mean([item.lon for item in group])),
                height_m=reference_height_m, speed_ms=speed, direction_deg=direction,
                moving=any(item.moving for item in group), metadata=metadata,
            )
            stations.append(station)
            diagnostics.append(diagnostic)
    if stations:
        background_uv = np.mean([_components(station) for station in stations], axis=0)
        background_speed, background_direction = _polar(*background_uv)
    else:
        background_speed = background_direction = None
    return ProfileResult(
        tuple(stations), background_speed, background_direction, reference_height_m,
        tuple(diagnostics), dict(sorted(rejected.items())),
    )
