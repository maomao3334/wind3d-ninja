from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from pathlib import Path


class WindNinjaConfigWriter:
    def write(
        self,
        output_path: Path,
        dem_path: Path,
        station_file: Path,
        work_dir: Path,
        target_time: datetime,
        output_height_m: int,
        mesh_resolution_m: int,
        vegetation: str,
        num_threads: int,
        generate_kmz: bool,
        *,
        diurnal_winds: bool = False,
        non_neutral_stability: bool = False,
        alpha_stability: float | None = None,
        input_wind_height_m: float | None = None,
        output_buffer_clipping_pct: float = 0.0,
        turbulence_output: bool = False,
        solver_mode: str = "stations",
        background_speed_ms: float | None = None,
        background_direction_deg: float | None = None,
        background_height_m: float | None = None,
        input_speed_ms: float | None = None,
        input_direction_deg: float | None = None,
    ) -> Path:
        if solver_mode == "stations" and turbulence_output:
            if input_speed_ms is None or input_direction_deg is None:
                raise ValueError(
                    "WindNinja 3.12.2 turbulence output requires the momentum solver, which is "
                    "incompatible with pointInitialization."
                )
            solver_mode = "turbulence"
            background_speed_ms = input_speed_ms
            background_direction_deg = input_direction_deg
            background_height_m = input_wind_height_m
        if solver_mode not in {"stations", "turbulence"}:
            raise ValueError("solver_mode must be 'stations' or 'turbulence'")
        if solver_mode == "turbulence":
            if non_neutral_stability or alpha_stability is not None or diurnal_winds:
                raise ValueError("Turbulence mode is incompatible with non_neutral_stability (non-neutral stability), alpha_stability, and diurnal_winds")
            values = (background_speed_ms, background_direction_deg, background_height_m)
            if any(value is None or not isfinite(value) for value in values):
                raise ValueError("Turbulence mode requires finite background speed, direction, and height")
            if background_speed_ms <= 0 or background_height_m <= 0:
                raise ValueError("Turbulence background speed and height must be greater than zero")
            if not 0 <= background_direction_deg < 360:
                raise ValueError("Turbulence background direction must be in [0, 360)")
        if alpha_stability is not None:
            if not non_neutral_stability:
                raise ValueError("alpha_stability requires non_neutral_stability=true.")
            if not isfinite(alpha_stability) or not 0 < alpha_stability <= 5:
                raise ValueError("alpha_stability must satisfy 0 < alpha_stability <= 5.")

        if target_time.tzinfo is None or target_time.utcoffset() is None:
            raise ValueError("target_time must include a timezone")
        value = target_time.astimezone(timezone.utc)
        settings = {
            "num_threads": num_threads,
            "elevation_file": str(dem_path.resolve()),
            "input_speed_units": "mps",
            "output_speed_units": "mps",
            "output_wind_height": output_height_m,
            "units_output_wind_height": "m",
            "vegetation": vegetation,
            "output_buffer_clipping": output_buffer_clipping_pct,
            "mesh_resolution": mesh_resolution_m,
            "units_mesh_resolution": "m",
            "write_ascii_output": "true",
            "ascii_out_resolution": mesh_resolution_m,
            "units_ascii_out_resolution": "m",
            "ascii_out_utm": "true",
            "write_goog_output": "true" if generate_kmz or solver_mode == "turbulence" else "false",
            "output_path": str(work_dir.resolve()),
            "time_zone": "UTC",
            "start_year": value.year,
            "start_month": value.month,
            "start_day": value.day,
            "start_hour": value.hour,
            "start_minute": value.minute,
            "stop_year": value.year,
            "stop_month": value.month,
            "stop_day": value.day,
            "stop_hour": value.hour,
            "stop_minute": value.minute,
            "number_time_steps": 1,
        }
        if solver_mode == "stations":
            settings.update(
                {
                    "initialization_method": "pointInitialization",
                    "wx_station_filename": str(station_file.resolve()),
                    "match_points": "false",
                    "diurnal_winds": str(diurnal_winds).lower(),
                    "non_neutral_stability": str(non_neutral_stability).lower(),
                }
            )
        else:
            settings.update(
                {
                    "initialization_method": "domainAverageInitialization",
                    "input_speed": background_speed_ms,
                    "input_direction": background_direction_deg,
                    "input_wind_height": background_height_m,
                    "units_input_wind_height": "m",
                    "momentum_flag": "true",
                    "turbulence_output_flag": "true",
                }
            )
        if alpha_stability is not None:
            settings["alpha_stability"] = alpha_stability
        if input_wind_height_m is not None:
            settings["input_wind_height"] = input_wind_height_m
            settings["units_input_wind_height"] = "m"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(f"{key} = {value}" for key, value in settings.items()) + "\n",
            encoding="utf-8",
        )
        return output_path
