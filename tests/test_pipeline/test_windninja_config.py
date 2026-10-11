from datetime import datetime, timezone
from math import nan
from pathlib import Path

import pytest

from wind3d_ninja.windninja.config_writer import WindNinjaConfigWriter


def _write_config(tmp_path: Path, **options: object) -> str:
    output = tmp_path / "run" / "windninja.cfg"
    WindNinjaConfigWriter().write(
        output,
        tmp_path / "terrain.tif",
        tmp_path / "stations.csv",
        tmp_path / "run",
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        10,
        200,
        "trees",
        4,
        True,
        **options,
    )
    return output.read_text(encoding="utf-8")


def test_point_initialization_omits_momentum_options(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    assert "initialization_method = pointInitialization" in config
    assert "wx_station_filename =" in config
    assert "momentum_flag" not in config
    assert "turbulence_output_flag" not in config


def test_point_initialization_supports_non_neutral_stability(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        non_neutral_stability=True,
        alpha_stability=2.5,
    )

    assert "initialization_method = pointInitialization" in config
    assert "non_neutral_stability = true" in config
    assert "alpha_stability = 2.5" in config
    assert "momentum_flag" not in config


@pytest.mark.parametrize("alpha", [0, -0.1, 5.1, nan])
def test_alpha_stability_requires_supported_range(tmp_path: Path, alpha: float) -> None:
    with pytest.raises(ValueError, match="0 < alpha_stability <= 5"):
        _write_config(tmp_path, non_neutral_stability=True, alpha_stability=alpha)


def test_alpha_stability_requires_non_neutral_stability(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires non_neutral_stability"):
        _write_config(tmp_path, alpha_stability=2.5)


def test_turbulence_output_fails_before_writing_incompatible_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires the momentum solver"):
        _write_config(tmp_path, turbulence_output=True)


def test_turbulence_solver_writes_momentum_and_native_kmz_settings(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        solver_mode="turbulence",
        background_speed_ms=8.0,
        background_direction_deg=270.0,
        background_height_m=20.0,
    )

    assert "initialization_method = domainAverageInitialization" in config
    assert "input_speed = 8.0" in config
    assert "input_direction = 270.0" in config
    assert "input_wind_height = 20.0" in config
    assert "momentum_flag = true" in config
    assert "turbulence_output_flag = true" in config
    assert "write_goog_output = true" in config
    assert "wx_station_filename" not in config
    assert "non_neutral_stability" not in config
    assert "diurnal_winds" not in config


def test_turbulence_solver_rejects_non_neutral_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="incompatible with non_neutral_stability"):
        _write_config(
            tmp_path,
            solver_mode="turbulence",
            background_speed_ms=8.0,
            background_direction_deg=270.0,
            background_height_m=20.0,
            non_neutral_stability=True,
        )
