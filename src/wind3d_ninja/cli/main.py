from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import click

from ..config.loader import load_config
from ..manifest.builder import normalize
from ..pipeline.inspector import InputInspector
from ..pipeline.planner import PipelinePlanner
from ..pipeline.runner import PipelineRunner
from ..pipeline.turbulence import TurbulenceBatchRunner
from ..pipeline.assimilation import AssimilationBatchRunner
from ..observations.instruments import InstrumentPolicy, RadarHeightReference, UavHeightReference, WindDirectionReference, WindSpeedUnit
from ..utils.geometry import parse_bounds
from ..utils.timezone import parse_explicit_time_utc, parse_time_range_utc
from ..windninja.installer import install_windninja
from .doctor import doctor_report


def emit(value: Any, json_output: bool) -> None:
    normalized = normalize(value)
    if json_output:
        click.echo(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True))
    elif isinstance(normalized, dict):
        for key, item in normalized.items():
            click.echo(f"{key}: {item}")
    else:
        click.echo(str(normalized))


def execute(ctx: click.Context, callback: Callable[[], Any]) -> Any:
    try:
        value = callback()
    except Exception as error:
        if ctx.obj["json"]:
            emit({"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}, True)
            ctx.exit(1)
        raise click.ClickException(str(error)) from error
    emit(value, ctx.obj["json"])
    return value


def combine_heights(primary: tuple[int, ...], trailing: tuple[int, ...]) -> list[int]:
    return list(dict.fromkeys([*primary, *trailing]))


def default_time_offset(config: Any) -> float:
    source = config.sources.get("uav") or next(iter(config.sources.values()))
    return float(source.tz_offset_hours)


def turbulence_arguments(
    mode: str,
    background_speed_ms: float | None,
    background_direction_deg: float | None,
    background_height_m: float | None,
    simulation_time: str | None,
) -> dict[str, object]:
    supplied = (background_speed_ms, background_direction_deg, background_height_m, simulation_time)
    if mode == "stations":
        if any(value is not None for value in supplied):
            raise ValueError("Background wind options require --mode turbulence")
        return {}
    if any(value is None for value in supplied):
        raise ValueError(
            "Turbulence mode requires --background-speed-ms, --background-direction-deg, "
            "--background-height-m, and --simulation-time"
        )
    return {
        "solver_mode": "turbulence",
        "background_speed_ms": background_speed_ms,
        "background_direction_deg": background_direction_deg,
        "background_height_m": background_height_m,
        "simulation_time": parse_explicit_time_utc(simulation_time),
    }


@click.group(help="Multi-source WindNinja wind-field downscaling.")
@click.option("--json", "json_output", is_flag=True, help="Emit stable JSON output.")
@click.pass_context
def cli(ctx: click.Context, json_output: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output


@cli.command(help="Check dependencies and the WindNinja executable.")
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--install", "install_runtime", is_flag=True, help="Install the pinned WindNinja runtime if missing.")
@click.option("--force", is_flag=True, help="Reinstall WindNinja; requires --install.")
@click.pass_context
def doctor(
    ctx: click.Context,
    config_path: Path | None,
    install_runtime: bool,
    force: bool,
) -> None:
    if force and not install_runtime:
        raise click.UsageError("--force requires --install")
    config = load_config(config_path)

    def operation() -> dict[str, object]:
        installation = None
        if install_runtime:
            installation = install_windninja(
                config.windninja_exe,
                force=force,
                progress=lambda message: click.echo(message, err=True),
            )
        report = doctor_report(config)
        if installation is not None:
            report["installation"] = installation.as_dict()
        return report

    report = execute(ctx, operation)
    if not report["healthy"]:
        ctx.exit(1)


@cli.command(help="Read and summarize all detected input data.")
@click.argument("input_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.pass_context
def inspect(ctx: click.Context, input_dir: Path, config_path: Path | None) -> None:
    execute(ctx, lambda: InputInspector(load_config(config_path)).inspect(input_dir))


@cli.command(help="Show the exact time-by-height execution plan without running WindNinja.")
@click.argument("input_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--height", "heights", "-h", multiple=True, required=True, type=click.IntRange(min=1))
@click.argument("additional_heights", nargs=-1, type=click.IntRange(min=1), metavar="[MORE_HEIGHTS]...")
@click.option(
    "--resolution",
    "mesh_resolutions",
    "-r",
    multiple=True,
    default=(200,),
    show_default=True,
    type=click.IntRange(min=1),
)
@click.option("--buffer", "buffer_km", "-b", default=10.0, show_default=True, type=click.FloatRange(min=0.0))
@click.option(
    "--bounds",
    nargs=4,
    type=float,
    metavar="MIN_LAT MAX_LAT MIN_LON MAX_LON",
)
@click.option(
    "--time-range",
    nargs=2,
    type=str,
    metavar="START END",
    help="Inclusive output time range; timezone-less values use the configured source offset.",
)
@click.option("--mode", type=click.Choice(["stations", "turbulence"]), default="stations", show_default=True)
@click.option("--background-speed-ms", type=float, help="Uniform background wind speed in m/s for turbulence mode.")
@click.option("--background-direction-deg", type=float, help="Uniform wind-from direction in degrees [0, 360) for turbulence mode.")
@click.option("--background-height-m", type=float, help="Background wind reference height in metres for turbulence mode.")
@click.option("--simulation-time", type=str, help="Explicit ISO-8601 simulation time with timezone for turbulence mode.")
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.pass_context
def plan(
    ctx: click.Context,
    input_dir: Path,
    heights: tuple[int, ...],
    additional_heights: tuple[int, ...],
    mesh_resolutions: tuple[int, ...],
    buffer_km: float,
    bounds: tuple[float, float, float, float] | None,
    time_range: tuple[str, str] | None,
    mode: str,
    background_speed_ms: float | None,
    background_direction_deg: float | None,
    background_height_m: float | None,
    simulation_time: str | None,
    config_path: Path | None,
) -> None:
    config = load_config(config_path)
    def operation() -> dict[str, object]:
        mode_args = turbulence_arguments(
            mode, background_speed_ms, background_direction_deg, background_height_m, simulation_time
        )
        if mode == "turbulence" and time_range is not None:
            raise ValueError("--time-range cannot be used with --mode turbulence; use --simulation-time")
        return PipelinePlanner(config).plan(
            input_dir,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            None if mode == "turbulence" else parse_time_range_utc(time_range, default_time_offset(config)),
            **mode_args,
        )
    execute(
        ctx,
        operation,
    )


@cli.command(help="Run the complete time-by-height WindNinja pipeline.")
@click.argument("input_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--height", "heights", "-h", multiple=True, required=True, type=click.IntRange(min=1))
@click.argument("additional_heights", nargs=-1, type=click.IntRange(min=1), metavar="[MORE_HEIGHTS]...")
@click.option("--output", "output_dir", "-o", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--resolution",
    "mesh_resolutions",
    "-r",
    multiple=True,
    default=(200,),
    show_default=True,
    type=click.IntRange(min=1),
)
@click.option("--buffer", "buffer_km", "-b", default=10.0, show_default=True, type=click.FloatRange(min=0.0))
@click.option(
    "--bounds",
    nargs=4,
    type=float,
    metavar="MIN_LAT MAX_LAT MIN_LON MAX_LON",
)
@click.option(
    "--time-range",
    nargs=2,
    type=str,
    metavar="START END",
    help="Inclusive output time range; timezone-less values use the configured source offset.",
)
@click.option("--kmz/--no-kmz", default=True, show_default=True)
@click.option("--mode", type=click.Choice(["stations", "turbulence"]), default="stations", show_default=True)
@click.option("--background-speed-ms", type=float, help="Uniform background wind speed in m/s for turbulence mode.")
@click.option("--background-direction-deg", type=float, help="Uniform wind-from direction in degrees [0, 360) for turbulence mode.")
@click.option("--background-height-m", type=float, help="Background wind reference height in metres for turbulence mode.")
@click.option("--simulation-time", type=str, help="Explicit ISO-8601 simulation time with timezone for turbulence mode.")
@click.option("--config", "config_path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.pass_context
def run(
    ctx: click.Context,
    input_dir: Path,
    heights: tuple[int, ...],
    additional_heights: tuple[int, ...],
    output_dir: Path | None,
    mesh_resolutions: tuple[int, ...],
    buffer_km: float,
    bounds: tuple[float, float, float, float] | None,
    time_range: tuple[str, str] | None,
    kmz: bool,
    mode: str,
    background_speed_ms: float | None,
    background_direction_deg: float | None,
    background_height_m: float | None,
    simulation_time: str | None,
    config_path: Path | None,
) -> None:
    config = load_config(config_path)
    destination = output_dir or input_dir / config.output.default_output_dir_name
    def operation() -> dict[str, object]:
        mode_args = turbulence_arguments(
            mode, background_speed_ms, background_direction_deg, background_height_m, simulation_time
        )
        if mode == "turbulence" and time_range is not None:
            raise ValueError("--time-range cannot be used with --mode turbulence; use --simulation-time")
        if mode == "turbulence" and not kmz:
            raise ValueError("Turbulence mode requires KMZ output; remove --no-kmz")
        return PipelineRunner(config).run(
            input_dir,
            destination,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            kmz,
            time_range=None if mode == "turbulence" else parse_time_range_utc(time_range, default_time_offset(config)),
            **mode_args,
        ).as_dict()
    execute(
        ctx,
        operation,
    )


def research_options(function):
    options = [
        (("--reference-height",), dict(type=click.FloatRange(min=0, min_open=True), default=50.0, show_default=True, help="Common input profile height above vegetation (m).")),
        (("--roughness",), dict(type=click.FloatRange(min=0, min_open=True), default=0.1, show_default=True, help="Roughness for station-profile log extrapolation (m).")),
        (("--displacement-height",), dict(type=click.FloatRange(min=0), default=0.0, show_default=True)),
        (("--allow-profile-extrapolation/--no-profile-extrapolation",), dict(default=False, show_default=True)),
        (("--canopy-height",), dict(type=click.FloatRange(min=0), default=0.0, show_default=True)),
        (("--windmaster-height-reference",), dict(type=click.Choice([item.value for item in RadarHeightReference]), default="agl", show_default=True)),
        (("--windmaster-platform-agl",), dict(type=click.FloatRange(min=0), help="Radar platform height above local ground (m); added to vertical gates in platform_vertical_offset mode.")),
        (("--uav-height-reference",), dict(type=click.Choice([item.value for item in UavHeightReference]), default="unknown", show_default=True)),
        (("--uav-launch-position",), dict(nargs=2, type=float, metavar="LAT LON")),
        (("--uav-launch-ground-altitude",), dict(type=float, help="Launch-ground elevation in the DEM vertical datum (m).")),
        (("--uav-relative-agl-confirmed",), dict(is_flag=True)),
        (("--uav-legacy-gps-is-launch-altitude-confirmed",), dict(is_flag=True)),
        (("--windmaster-direction-reference",), dict(type=click.Choice(["from_true_north", "to_true_north"]), default="from_true_north")),
        (("--uav-direction-reference",), dict(type=click.Choice(["from_true_north", "to_true_north"]), default="from_true_north")),
    ]
    for names, settings in reversed(options):
        function = click.option(*names, **settings)(function)
    return function


def research_parameters(options: dict) -> dict:
    position = options["uav_launch_position"]
    policy = InstrumentPolicy(
        windmaster_direction_reference=WindDirectionReference(options["windmaster_direction_reference"]),
        uav_direction_reference=WindDirectionReference(options["uav_direction_reference"]),
        windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
        uav_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
        windmaster_height_reference=RadarHeightReference(options["windmaster_height_reference"]),
        windmaster_platform_agl_m=options["windmaster_platform_agl"],
        windmaster_is_geographic_wind=True, windmaster_motion_compensated=True,
        uav_is_geographic_wind=True, uav_motion_compensated=True,
        uav_height_reference=UavHeightReference(options["uav_height_reference"]),
        uav_relative_agl_confirmed=options["uav_relative_agl_confirmed"],
        uav_legacy_gps_is_launch_altitude_confirmed=options["uav_legacy_gps_is_launch_altitude_confirmed"],
        uav_launch_lat=position[0] if position else None,
        uav_launch_lon=position[1] if position else None,
        uav_launch_location_confirmed=position is not None,
        uav_launch_ground_altitude_m=options["uav_launch_ground_altitude"],
        canopy_height_m=options["canopy_height"],
    )
    return dict(instrument_policy=policy, reference_height_m=options["reference_height"],
                roughness_m=options["roughness"], displacement_height_m=options["displacement_height"],
                allow_profile_extrapolation=options["allow_profile_extrapolation"])


@cli.command(help="Run the independent WindNinja RANS turbulence batch workflow.")
@research_options
@click.argument("input_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--height", "heights", "-h", multiple=True, required=True, type=click.IntRange(min=1))
@click.argument("additional_heights", nargs=-1, type=click.IntRange(min=1), metavar="[MORE_HEIGHTS]...")
@click.option("--output", "output_dir", "-o", type=click.Path(path_type=Path, file_okay=False))
@click.option("--resolution", "mesh_resolutions", "-r", multiple=True, default=(200,), show_default=True, type=click.IntRange(min=1))
@click.option("--buffer", "buffer_km", "-b", default=10.0, show_default=True, type=click.FloatRange(min=0.0))
@click.option("--bounds", nargs=4, type=float, metavar="MIN_LAT MAX_LAT MIN_LON MAX_LON")
@click.option("--time-range", nargs=2, type=str, metavar="START END")
@click.option("--max-cases", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--validation-window", type=click.IntRange(min=0), default=120, show_default=True, help="Maximum UAV time offset in seconds.")
@click.option("--validation-height-tolerance", type=click.FloatRange(min=0), default=10.0, show_default=True, help="Maximum UAV height offset in meters.")
@click.option("--background-max-height", type=click.FloatRange(min=0, min_open=True), default=200.0, show_default=True, help="Maximum fixed WindMaster gate height in meters.")
@click.option("--product", "selected_product", type=str)
@click.option("--dem", "dem_override", type=click.Path(path_type=Path, exists=True, dir_okay=False), help="Use a DEM covering the requested domain.")
@click.option("--non-neutral/--neutral", default=False, show_default=True)
@click.option("--kmz/--no-kmz", default=True, show_default=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.pass_context
def turbulence(
    ctx: click.Context,
    input_dir: Path,
    heights: tuple[int, ...],
    additional_heights: tuple[int, ...],
    output_dir: Path | None,
    mesh_resolutions: tuple[int, ...],
    buffer_km: float,
    bounds: tuple[float, float, float, float] | None,
    time_range: tuple[str, str] | None,
    max_cases: int,
    validation_window: int,
    validation_height_tolerance: float,
    background_max_height: float,
    selected_product: str | None,
    dem_override: Path | None,
    non_neutral: bool,
    kmz: bool,
    config_path: Path | None,
    **profile_options,
) -> None:
    config = load_config(config_path)
    destination = output_dir or input_dir / "turbulence_output"
    execute(
        ctx,
        lambda: TurbulenceBatchRunner(config).run(
            input_dir,
            destination,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            kmz,
            time_range=parse_time_range_utc(time_range, default_time_offset(config)),
            max_cases=max_cases,
            validation_window_seconds=validation_window,
            validation_height_tolerance_m=validation_height_tolerance,
            background_max_height_m=background_max_height,
            selected_product=selected_product,
            non_neutral_stability=non_neutral,
            dem_override=dem_override,
            **research_parameters(profile_options),
        ).as_dict(),
    )


@cli.command(help="Run station optimal-interpolation analysis on a WindNinja RANS background.")
@research_options
@click.argument("input_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--height", "heights", "-h", multiple=True, required=True, type=click.IntRange(min=1))
@click.argument("additional_heights", nargs=-1, type=click.IntRange(min=1), metavar="[MORE_HEIGHTS]...")
@click.option("--output", "output_dir", "-o", type=click.Path(path_type=Path, file_okay=False))
@click.option("--resolution", "mesh_resolutions", "-r", multiple=True, default=(200,), show_default=True, type=click.IntRange(min=1))
@click.option("--buffer", "buffer_km", "-b", default=10.0, show_default=True, type=click.FloatRange(min=0.0))
@click.option("--bounds", nargs=4, type=float, metavar="MIN_LAT MAX_LAT MIN_LON MAX_LON")
@click.option("--time-range", nargs=2, type=str, metavar="START END")
@click.option("--max-cases", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--validation-window", type=click.IntRange(min=0), default=120, show_default=True, help="Maximum UAV time offset in seconds.")
@click.option("--validation-height-tolerance", type=click.FloatRange(min=0), default=10.0, show_default=True, help="Maximum UAV height offset in meters.")
@click.option("--background-max-height", type=click.FloatRange(min=0, min_open=True), default=200.0, show_default=True, help="Maximum fixed WindMaster gate height in meters.")
@click.option("--product", "selected_product", type=str)
@click.option("--dem", "dem_override", type=click.Path(path_type=Path, exists=True, dir_okay=False), help="Use a DEM covering the requested domain.")
@click.option("--non-neutral/--neutral", default=False, show_default=True)
@click.option("--kmz/--no-kmz", default=True, show_default=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--station-observations", type=click.Path(path_type=Path, exists=True, dir_okay=False), help="CSV of independent fixed-station observations.")
@click.option("--horizontal-length-scale", default=1000.0, show_default=True, type=click.FloatRange(min=0, min_open=True), help="Horizontal correlation length (m).")
@click.option("--background-wind-sigma", default=2.0, show_default=True, type=click.FloatRange(min=0, min_open=True), help="Background wind standard deviation (m/s).")
@click.option("--observation-wind-sigma", default=0.5, show_default=True, type=click.FloatRange(min=0, min_open=True), help="Default observation wind standard deviation (m/s).")
@click.option("--vertical-length-scale", default=30.0, show_default=True, type=click.FloatRange(min=0, min_open=True), help="Vertical correlation length (m).")
@click.pass_context
def assimilation(
    ctx: click.Context,
    input_dir: Path,
    heights: tuple[int, ...],
    additional_heights: tuple[int, ...],
    output_dir: Path | None,
    mesh_resolutions: tuple[int, ...],
    buffer_km: float,
    bounds: tuple[float, float, float, float] | None,
    time_range: tuple[str, str] | None,
    max_cases: int,
    validation_window: int,
    validation_height_tolerance: float,
    background_max_height: float,
    selected_product: str | None,
    dem_override: Path | None,
    non_neutral: bool,
    kmz: bool,
    config_path: Path | None,
    station_observations: Path | None,
    horizontal_length_scale: float,
    background_wind_sigma: float,
    observation_wind_sigma: float,
    vertical_length_scale: float,
    **profile_options,
) -> None:
    config = load_config(config_path)
    destination = output_dir or input_dir / "assimilation_output"
    execute(
        ctx,
        lambda: AssimilationBatchRunner(config).run(
            input_dir,
            destination,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            kmz,
            station_observations=station_observations,
            horizontal_length_scale_m=horizontal_length_scale,
            background_wind_sigma_ms=background_wind_sigma,
            observation_wind_sigma_ms=observation_wind_sigma,
            vertical_length_scale_m=vertical_length_scale,
            time_range=parse_time_range_utc(time_range, default_time_offset(config)),
            max_cases=max_cases,
            validation_window_seconds=validation_window,
            validation_height_tolerance_m=validation_height_tolerance,
            background_max_height_m=background_max_height,
            selected_product=selected_product,
            non_neutral_stability=non_neutral,
            dem_override=dem_override,
            **research_parameters(profile_options),
        ).as_dict(),
    )


@cli.command("native-assimilation", help="Run Windows native RANS coupled station assimilation.")
@research_options
@click.argument("input_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--height", "heights", "-h", multiple=True, required=True, type=click.IntRange(min=1))
@click.argument("additional_heights", nargs=-1, type=click.IntRange(min=1), metavar="[MORE_HEIGHTS]...")
@click.option("--output", "output_dir", "-o", type=click.Path(path_type=Path, file_okay=False))
@click.option("--resolution", "mesh_resolutions", "-r", multiple=True, default=(200,), show_default=True, type=click.IntRange(min=1))
@click.option("--buffer", "buffer_km", "-b", default=10.0, show_default=True, type=click.FloatRange(min=0.0))
@click.option("--bounds", nargs=4, type=float, metavar="MIN_LAT MAX_LAT MIN_LON MAX_LON")
@click.option("--time-range", nargs=2, type=str, metavar="START END")
@click.option("--max-cases", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--validation-window", type=click.IntRange(min=0), default=120, show_default=True, help="Maximum UAV time offset in seconds.")
@click.option("--validation-height-tolerance", type=click.FloatRange(min=0), default=10.0, show_default=True, help="Maximum UAV height offset in meters.")
@click.option("--background-max-height", type=click.FloatRange(min=0, min_open=True), default=200.0, show_default=True, help="Maximum fixed WindMaster gate height in meters.")
@click.option("--product", "selected_product", type=str)
@click.option("--dem", "dem_override", type=click.Path(path_type=Path, exists=True, dir_okay=False), help="Use a DEM covering the requested domain.")
@click.option("--non-neutral/--neutral", default=False, show_default=True)
@click.option("--kmz/--no-kmz", default=True, show_default=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--station-observations", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True, help="CSV with k and epsilon or mixing length.")
@click.option("--native-runtime", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--horizontal-radius-m", type=click.FloatRange(min=0, min_open=True), default=300.0, show_default=True)
@click.option("--vertical-radius-m", type=click.FloatRange(min=0, min_open=True), default=50.0, show_default=True)
@click.option("--velocity-relaxation-seconds", type=click.FloatRange(min=0, min_open=True), default=10.0, show_default=True)
@click.option("--tke-relaxation-seconds", type=click.FloatRange(min=0, min_open=True), default=10.0, show_default=True)
@click.option("--epsilon-relaxation-seconds", type=click.FloatRange(min=0, min_open=True), default=10.0, show_default=True)
@click.option("--max-iterations", type=click.IntRange(min=51), default=1000, show_default=True)
@click.option("--residual-tolerance", type=click.FloatRange(min=0, min_open=True), default=1e-05, show_default=True)
@click.pass_context
def native_assimilation(
    ctx: click.Context,
    input_dir: Path,
    heights: tuple[int, ...],
    additional_heights: tuple[int, ...],
    output_dir: Path | None,
    mesh_resolutions: tuple[int, ...],
    buffer_km: float,
    bounds: tuple[float, float, float, float] | None,
    time_range: tuple[str, str] | None,
    max_cases: int,
    validation_window: int,
    validation_height_tolerance: float,
    background_max_height: float,
    selected_product: str | None,
    dem_override: Path | None,
    non_neutral: bool,
    kmz: bool,
    config_path: Path | None,
    station_observations: Path | None,
    native_runtime: Path | None,
    horizontal_radius_m: float,
    vertical_radius_m: float,
    velocity_relaxation_seconds: float,
    tke_relaxation_seconds: float,
    epsilon_relaxation_seconds: float,
    max_iterations: int,
    residual_tolerance: float,
    **profile_options,
) -> None:
    from ..pipeline.native_assimilation import NativeCoupledBatchRunner

    config = load_config(config_path)
    destination = output_dir or input_dir / "native_output"
    execute(
        ctx,
        lambda: NativeCoupledBatchRunner(config).run(
            input_dir,
            destination,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            kmz,
            station_observations=station_observations,
            native_runtime=native_runtime,
            horizontal_radius_m=horizontal_radius_m,
            vertical_radius_m=vertical_radius_m,
            velocity_relaxation_seconds=velocity_relaxation_seconds,
            tke_relaxation_seconds=tke_relaxation_seconds,
            epsilon_relaxation_seconds=epsilon_relaxation_seconds,
            max_iterations=max_iterations,
            residual_tolerance=residual_tolerance,
            time_range=parse_time_range_utc(time_range, default_time_offset(config)),
            max_cases=max_cases,
            validation_window_seconds=validation_window,
            validation_height_tolerance_m=validation_height_tolerance,
            background_max_height_m=background_max_height,
            selected_product=selected_product,
            non_neutral_stability=non_neutral,
            dem_override=dem_override,
            **research_parameters(profile_options),
        ).as_dict(),
    )
