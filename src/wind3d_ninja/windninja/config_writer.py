from __future__ import annotations

from datetime import datetime, timezone
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
    ) -> Path:
        if turbulence_output or non_neutral_stability:
            requested_options = []
            if non_neutral_stability:
                requested_options.append("non-neutral stability")
            if turbulence_output:
                requested_options.append("turbulence output")
            options = ", ".join(requested_options)
            raise ValueError(
                f"WindNinja 3.12.2 cannot combine {options} with pointInitialization. "
                "This workflow uses pointInitialization to assimilate the input station observations; "
                "disable these momentum-solver options and run again."
            )

        value = target_time.astimezone(timezone.utc)
        settings = {
            "num_threads": num_threads,
            "elevation_file": str(dem_path.resolve()),
            "initialization_method": "pointInitialization",
            "wx_station_filename": str(station_file.resolve()),
            "match_points": "false",
            "input_speed_units": "mps",
            "output_speed_units": "mps",
            "output_wind_height": output_height_m,
            "units_output_wind_height": "m",
            "vegetation": vegetation,
            "diurnal_winds": str(diurnal_winds).lower(),
            "non_neutral_stability": str(non_neutral_stability).lower(),
            "output_buffer_clipping": output_buffer_clipping_pct,
            "mesh_resolution": mesh_resolution_m,
            "units_mesh_resolution": "m",
            "write_ascii_output": "true",
            "ascii_out_resolution": mesh_resolution_m,
            "units_ascii_out_resolution": "m",
            "ascii_out_utm": "true",
            "write_goog_output": "true" if generate_kmz else "false",
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
