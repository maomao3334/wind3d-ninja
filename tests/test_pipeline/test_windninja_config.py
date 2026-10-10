from datetime import datetime, timezone
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


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"non_neutral_stability": True}, "non-neutral stability"),
        ({"turbulence_output": True}, "turbulence output"),
        (
            {"non_neutral_stability": True, "turbulence_output": True},
            "non-neutral stability, turbulence output",
        ),
    ],
)
def test_momentum_options_fail_before_writing_invalid_config(
    tmp_path: Path, options: dict[str, bool], expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        _write_config(tmp_path, **options)
