from pathlib import Path
from datetime import datetime, timezone

from click.testing import CliRunner

from wind3d_ninja.cli.main import cli
from wind3d_ninja.pipeline.planner import PipelinePlanner
from wind3d_ninja.pipeline.runner import PipelineRunner


def test_plan_accepts_space_separated_heights_and_multiple_resolutions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_plan(
        self,
        input_dir,
        heights,
        mesh_resolutions_m,
        buffer_km,
        manual_bounds,
        time_range,
    ):
        captured.update(
            {
                "input_dir": input_dir,
                "heights": heights,
                "resolutions": mesh_resolutions_m,
                "buffer": buffer_km,
                "bounds": manual_bounds,
                "time_range": time_range,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(PipelinePlanner, "plan", fake_plan)
    result = CliRunner().invoke(
        cli,
        [
            "plan",
            str(tmp_path),
            "--height",
            "10",
            "20",
            "50",
            "--resolution",
            "50",
            "--resolution",
            "100",
            "--resolution",
            "200",
            "--bounds",
            "24.9",
            "25.1",
            "102.3",
            "102.5",
            "--time-range",
            "2024-04-03 16:16",
            "2024-04-03 16:30",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["heights"] == [10, 20, 50]
    assert captured["resolutions"] == [50, 100, 200]
    assert captured["buffer"] == 10.0
    assert captured["bounds"].as_dict() == {
        "min_lat": 24.9,
        "max_lat": 25.1,
        "min_lon": 102.3,
        "max_lon": 102.5,
    }
    assert captured["time_range"] == (
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        datetime(2024, 4, 3, 8, 30, tzinfo=timezone.utc),
    )


def test_run_help_does_not_expose_time_or_dem_overrides() -> None:
    result = CliRunner().invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--height" in result.output
    assert "--resolution" in result.output
    assert "--output" in result.output
    assert "--bounds" in result.output
    assert "--time-range START END" in result.output
    assert "--kmz / --no-kmz" in result.output
    assert "[default: kmz]" in result.output
    assert "\n  --time " not in result.output
    assert "--dem" not in result.output


def test_doctor_help_exposes_pinned_runtime_installer() -> None:
    result = CliRunner().invoke(cli, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "--install" in result.output
    assert "--force" in result.output


def test_run_generates_kmz_by_default(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Result:
        @staticmethod
        def as_dict() -> dict[str, object]:
            return {"ok": True}

    def fake_run(
        self,
        input_dir,
        output_dir,
        output_heights,
        mesh_resolutions_m,
        buffer_km,
        manual_bounds,
        generate_kmz,
        time_range=None,
    ):
        captured["generate_kmz"] = generate_kmz
        captured["time_range"] = time_range
        return Result()

    monkeypatch.setattr(PipelineRunner, "run", fake_run)
    result = CliRunner().invoke(
        cli,
        ["run", str(tmp_path), "--height", "20", "--resolution", "50"],
    )

    assert result.exit_code == 0, result.output
    assert captured["generate_kmz"] is True
    assert captured["time_range"] is None
