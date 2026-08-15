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
from ..utils.geometry import parse_bounds
from ..utils.timezone import parse_time_range_utc
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
    config_path: Path | None,
) -> None:
    config = load_config(config_path)
    execute(
        ctx,
        lambda: PipelinePlanner(config).plan(
            input_dir,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            parse_time_range_utc(time_range, default_time_offset(config)),
        ),
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
    config_path: Path | None,
) -> None:
    config = load_config(config_path)
    destination = output_dir or input_dir / config.output.default_output_dir_name
    execute(
        ctx,
        lambda: PipelineRunner(config).run(
            input_dir,
            destination,
            combine_heights(heights, additional_heights),
            list(mesh_resolutions),
            buffer_km,
            parse_bounds(bounds),
            kmz,
            time_range=parse_time_range_utc(time_range, default_time_offset(config)),
        ).as_dict(),
    )
