from pathlib import Path

import pytest

from wind3d_ninja.windninja.runner import WindNinjaRunner, _display_log_path


def test_windninja_runner_streams_output_and_writes_log(tmp_path: Path, capsys) -> None:
    script = tmp_path / "fake_windninja.py"
    script.write_text(
        "from pathlib import Path\n"
        "work = Path.cwd()\n"
        "print('phase one', flush=True)\n"
        "print('phase two', flush=True)\n"
        "(work / 'fixture_vel.asc').write_text('vel', encoding='utf-8')\n"
        "(work / 'fixture_ang.asc').write_text('ang', encoding='utf-8')\n"
        "(work / 'fixture_vel.prj').write_text('projection', encoding='utf-8')\n"
        "(work / 'fixture.kmz').write_bytes(b'kmz')\n",
        encoding="utf-8",
    )
    config = tmp_path / "windninja.cfg"
    config.write_text("fixture=true\n", encoding="utf-8")

    result = WindNinjaRunner(script).run(config, tmp_path)

    captured = capsys.readouterr().err
    assert "[WindNinja] phase one" in captured
    assert "[WindNinja] phase two" in captured
    assert result.log_file.read_text(encoding="utf-8") == "phase one\nphase two\n"
    assert result.kmz == tmp_path / "fixture.kmz"


def test_error_log_reference_is_relative_to_output_tree(tmp_path: Path) -> None:
    log_file = (
        tmp_path
        / "output"
        / "windninja"
        / "20240101T000000Z"
        / "h010m"
        / "r0200m"
        / "windninja.log"
    )

    assert _display_log_path(log_file) == str(
        Path("windninja")
        / "20240101T000000Z"
        / "h010m"
        / "r0200m"
        / "windninja.log"
    )


def test_windninja_runner_failure_reports_relocatable_log_path(tmp_path: Path) -> None:
    script = tmp_path / "failing_windninja.py"
    script.write_text(
        "print('windninja failed', flush=True)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    config = tmp_path / "windninja.cfg"
    config.write_text("fixture=true\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"see windninja\.log"):
        WindNinjaRunner(script).run(config, tmp_path)
