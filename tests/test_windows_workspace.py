from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import pytest

from wind3d_ninja.utils.windows_workspace import solver_safe_dem, windows_safe_output

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows CFD path compatibility")


@dataclass(frozen=True)
class Result:
    output_dir: Path
    nc: tuple[Path, ...]
    report: Path
    command: tuple[str, ...]


class Runner:
    seen: Path | None = None

    @windows_safe_output
    def run(self, input_dir: Path, output_dir: Path, *, fail: bool = False) -> Result:
        self.seen = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        dem = solver_safe_dem(input_dir / "\u5730\u5f62 DEM.tif", output_dir)
        assert str(dem).isascii() and not any(ch.isspace() for ch in str(dem))
        assert dem.read_bytes() == b"original terrain"
        checkpoint = output_dir / "terrain/NINJAFOAM_test/100/U"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(b"retained fields")
        os.utime(checkpoint, ns=(1700000000123456700, 1700000000123456700))
        log = output_dir / "windninja.log"
        log.write_text("Executed in " + str(output_dir), encoding="utf-8")
        if fail:
            raise ValueError("Failed; see " + str(log))
        native = json.dumps({"checkpoint": str(checkpoint)})
        report = output_dir / "report.json"
        report.write_text(json.dumps({"output_dir": str(output_dir), "checkpoint": str(checkpoint), "native": native}), encoding="utf-8")
        (output_dir / "windninja.cfg").write_text("elevation_file = " + str(dem), encoding="utf-8")
        (output_dir / "validation.csv").write_text("checkpoint\n" + str(checkpoint) + "\n", encoding="utf-8")
        nc = output_dir / "field.nc"
        with netCDF4.Dataset(nc, "w") as ds:
            ds.createDimension("n", 2)
            ds.createVariable("U_wind", "f8", ("n",))[:] = [1.25, 2.5]
            ds.native_case = native
        return Result(output_dir, (nc,), report, ("solver.exe", str(checkpoint)))


@pytest.mark.parametrize("name", ["\u4e2d\u6587_output", "ascii output"])
def test_windows_output_relocated_without_changing_fields_or_case_mtime(tmp_path: Path, name: str):
    inputs = tmp_path / "input"
    inputs.mkdir()
    original = inputs / "\u5730\u5f62 DEM.tif"
    original.write_bytes(b"original terrain")
    final = tmp_path / name
    final.mkdir()
    (final / "user_notes.txt").write_text("keep me")
    runner = Runner()
    result = runner.run(inputs, final)
    assert runner.seen is not None and runner.seen != final
    assert str(runner.seen).isascii() and not any(ch.isspace() for ch in str(runner.seen))
    assert not runner.seen.exists()
    assert result.output_dir == final.resolve()
    checkpoint = final / "terrain/NINJAFOAM_test/100/U"
    assert checkpoint.read_bytes() == b"retained fields"
    assert checkpoint.stat().st_mtime_ns == 1700000000123456700
    assert result.nc == (final.resolve() / "field.nc",)
    assert result.command == ("solver.exe", str(checkpoint.resolve()))
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["checkpoint"] == str(checkpoint.resolve())
    assert json.loads(report["native"])["checkpoint"] == str(checkpoint.resolve())
    assert str(runner.seen) not in (final / "windninja.cfg").read_text(encoding="utf-8")
    assert str(checkpoint.resolve()) in (final / "validation.csv").read_text(encoding="utf-8")
    assert str(runner.seen) in (final / "windninja.log").read_text(encoding="utf-8")
    assert (final / "user_notes.txt").read_text() == "keep me"
    assert original.read_bytes() == b"original terrain"
    # netCDF4 C file APIs cannot open Unicode Windows paths; read the actual exported bytes.
    with netCDF4.Dataset("exported.nc", memory=result.nc[0].read_bytes()) as ds:
        assert ds["U_wind"][:].tolist() == [1.25, 2.5]
        assert json.loads(ds.native_case)["checkpoint"] == str(checkpoint.resolve())


def test_windows_failure_preserves_original_exception_and_exported_log(tmp_path: Path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "\u5730\u5f62 DEM.tif").write_bytes(b"original terrain")
    final = tmp_path / "\u5931\u8d25 output"
    runner = Runner()
    with pytest.raises(ValueError) as error:
        runner.run(inputs, final, fail=True)
    assert str(final.resolve() / "windninja.log") in str(error.value)
    assert (final / "windninja.log").is_file()
    assert runner.seen is not None and not runner.seen.exists()


def test_windows_long_ascii_output_relocated_and_unsafe_dem_copied(tmp_path: Path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "\u5730\u5f62 DEM.tif").write_bytes(b"original terrain")
    final = tmp_path / "ascii"
    runner = Runner()
    result = runner.run(inputs, final)
    assert len(str(final.resolve())) > 50
    assert runner.seen != final.resolve()
    assert len(str(runner.seen)) <= 50
    assert result.output_dir == final.resolve()
    assert (final / "terrain/solver_dem.tif").read_bytes() == b"original terrain"


def test_windows_copy_failure_retains_recoverable_results(tmp_path: Path, monkeypatch):
    import shutil
    from wind3d_ninja.utils import windows_workspace

    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "\u5730\u5f62 DEM.tif").write_bytes(b"original terrain")
    final = tmp_path / "failed export"
    runner = Runner()

    def failed_copy(*args, **kwargs):
        raise PermissionError("export destination is read-only")

    monkeypatch.setattr(windows_workspace, "_copy_staged_output", failed_copy)
    try:
        with pytest.raises(PermissionError) as error:
            runner.run(inputs, final)
        assert runner.seen is not None
        assert (runner.seen / "field.nc").is_file()
        assert str(runner.seen.parent) in str(error.value)
    finally:
        if runner.seen is not None:
            shutil.rmtree(runner.seen.parent)


def test_explicit_windows_workspace_override_is_honored(tmp_path: Path, monkeypatch):
    from wind3d_ninja.utils import windows_workspace

    custom = tmp_path / "configured"
    monkeypatch.setattr(windows_workspace, "_MAX_WORKSPACE_ROOT_LENGTH", len(str(custom.resolve())))
    monkeypatch.setenv("WIND3D_WORK_ROOT", str(custom))
    root, private_acl = windows_workspace._windows_workspace_root()
    assert root == custom.resolve()
    assert private_acl is False


def test_overlong_windows_workspace_root_is_not_used(tmp_path: Path, monkeypatch):
    from wind3d_ninja.utils import windows_workspace

    custom = tmp_path / "configured-too-long-for-native-rendering"
    monkeypatch.setenv("WIND3D_WORK_ROOT", str(custom))
    root, _ = windows_workspace._windows_workspace_root()
    assert root != custom.resolve()
    assert len(str(root)) <= windows_workspace._MAX_WORKSPACE_ROOT_LENGTH
