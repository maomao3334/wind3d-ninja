from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from wind3d_ninja.observations.instruments import RadarHeightReference
from wind3d_ninja.ui import main_window


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app: QApplication) -> main_window.MainWindow:
    value = main_window.MainWindow()
    yield value
    value.close()
    value.deleteLater()


@pytest.fixture(autouse=True)
def selected_time_policy_fields(window: main_window.MainWindow) -> None:
    window.uav_time_field.addItem("device_sampling_time", "device_sampling_time")
    window.windmaster_time_field.addItem("sampling_time", "sampling_time")


class _Result:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def as_dict(self) -> dict[str, object]:
        return self.value


def test_refresh_observation_times_populates_utc8_selectors(
    window: main_window.MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation_time = datetime(2024, 4, 3, 8, 16, 45, tzinfo=timezone.utc)

    class FakeInspector:
        def __init__(self, config: object, instrument_policy: object | None = None) -> None:
            pass

        def load(self, input_dir: Path) -> object:
            return type(
                "Loaded",
                (),
                {
                    "fixed_observations": [type("Observation", (), {"time_utc": observation_time})()],
                    "uav_inputs": [],
                    "generic_observations": [],
                },
            )()

    monkeypatch.setattr(main_window, "InputInspector", FakeInspector)
    monkeypatch.setattr(
        main_window,
        "scan_instrument_time_candidates",
        lambda _input_dir, policy: {
            "sources": {
                "uav": {
                    "selected_field": policy.uav_time_field,
                    "selected_utc_times": ["2024-04-03T08:16:45Z"],
                    "candidates": [],
                },
                "windmaster": {"selected_field": policy.windmaster_time_field, "candidates": []},
                "dat": {"selected_field": None, "candidates": []},
            }
        },
    )
    window.input_edit.setText(str(tmp_path))
    window.refresh_observation_times()
    for _ in range(100):
        if window.refresh_times_button.isEnabled():
            break
        QTest.qWait(20)

    assert window.start.count() == 1
    assert window.start.itemText(0) == "2024-04-03 16:16 (UTC+8)"
    window.start.setCurrentIndex(0)
    window.end.setCurrentIndex(0)
    assert window._parameters()[3] is not None


def test_standard_mode_button_keeps_existing_pipeline_route(
    window: main_window.MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeRunner:
        def __init__(self, config, progress_callback=None) -> None:
            captured["config"] = config
            captured["callback"] = progress_callback

        def run(self, *args, **kwargs) -> _Result:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _Result({"mode": "standard"})

    monkeypatch.setattr(main_window, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(window, "_start", lambda action: captured.setdefault("action", action))
    window.input_edit.setText(str(tmp_path))
    window.output_edit.setText(str(tmp_path / "standard-output"))
    window.heights.setText("10 20")
    window.resolutions.setText("50 200")

    window.run_button.click()
    window.heights.setText("999")
    captured["action"](lambda _message: None)

    assert window.mode.currentData() == "standard"
    assert window.run_button.text() == "开始常规运行"
    assert captured["args"][0] == tmp_path
    assert captured["args"][1] == tmp_path / "standard-output"
    assert captured["args"][2:4] == ([10, 20], [50, 200])
    assert captured["kwargs"]["dem_override"] is None
    assert captured["kwargs"]["time_range"] is None
    assert captured["kwargs"]["instrument_policy"].time_policy.uav_time_field == "device_sampling_time"


def test_turbulence_mode_button_routes_domain_average_parameters(
    window: main_window.MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeTurbulenceRunner:
        def __init__(self, config, progress_callback=None) -> None:
            captured["config"] = config
            captured["callback"] = progress_callback

        def run(self, *args, **kwargs) -> _Result:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _Result({"mode": "turbulence_rans_domain_average"})

    monkeypatch.setattr(main_window, "TurbulenceBatchRunner", FakeTurbulenceRunner)
    monkeypatch.setattr(window, "_start", lambda action: captured.setdefault("action", action))
    window.input_edit.setText(str(tmp_path))
    window.output_edit.setText(str(tmp_path / "turbulence-output"))
    dem_override = tmp_path / "override.tif"
    dem_override.write_bytes(b"test dem")
    window.dem_edit.setText(str(dem_override))
    window.heights.setText("10")
    window.resolutions.setText("200")
    window.mode.setCurrentIndex(1)
    assert not window.inspect_button.isEnabled()
    assert not window.plan_button.isEnabled()
    window.mode.setCurrentIndex(0)
    assert window.inspect_button.isEnabled()
    assert window.plan_button.isEnabled()
    window.mode.setCurrentIndex(1)
    window.background_max_height.setValue(180.0)
    window.validation_height_tolerance.setValue(7.5)
    window.validation_window.setValue(120)
    window.max_cases.setValue(2)
    window.turbulence_product.setText("windmaster-uav")
    window.non_neutral.setChecked(True)
    window.uav_height_reference.setCurrentIndex(1)
    window.windmaster_height_reference.setCurrentIndex(
        window.windmaster_height_reference.findData(RadarHeightReference.PLATFORM_VERTICAL_OFFSET)
    )
    window.windmaster_platform_agl.setText("3")
    window.reference_height.setValue(60)
    window.roughness.setValue(0.2)
    window.displacement_height.setValue(1)
    window.allow_profile_extrapolation.setChecked(True)

    window.run_button.click()
    window.heights.setText("999")
    window.background_max_height.setValue(999.0)
    captured["action"](lambda _message: None)

    assert not window.turbulence_options.isHidden()
    assert window.run_button.text() == "开始湍流 RANS 运行"
    assert not window.non_neutral.isEnabled()
    assert captured["args"][0] == tmp_path
    assert captured["args"][1] == tmp_path / "turbulence-output"
    assert captured["args"][2:4] == ([10], [200])
    routed = dict(captured["kwargs"])
    policy = routed.pop("instrument_policy")
    assert policy.windmaster_platform_agl_m == 3
    assert policy.windmaster_height_reference.value == "platform_vertical_offset"
    assert routed == {
        "time_range": None,
        "dem_override": dem_override,
        "max_cases": 2,
        "validation_window_seconds": 120,
        "selected_product": "windmaster-uav",
        "non_neutral_stability": False,
        "background_max_height_m": 180.0,
        "validation_height_tolerance_m": 7.5,
        "reference_height_m": 60.0,
        "roughness_m": 0.2,
        "displacement_height_m": 1.0,
        "allow_profile_extrapolation": True,
    }


def test_assimilation_mode_routes_station_analysis_parameters(
    window: main_window.MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeAssimilationRunner:
        def __init__(self, config, progress_callback=None) -> None:
            captured["config"] = config
            captured["callback"] = progress_callback

        def run(self, *args, **kwargs) -> _Result:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _Result({"mode": "station_assimilation"})

    station_csv = tmp_path / "stations.csv"
    station_csv.write_text(
        "time_utc,station_id,latitude,longitude,height_m\n"
        "2024-04-03T08:16:00Z,S1,39.9,116.4,50\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_window, "AssimilationBatchRunner", FakeAssimilationRunner)
    monkeypatch.setattr(window, "_start", lambda action: captured.setdefault("action", action))
    window.input_edit.setText(str(tmp_path))
    window.mode.setCurrentIndex(2)
    assert window.heights.text() == "50"
    assert window.resolutions.text() == "200"
    assert not window.inspect_button.isEnabled()
    assert not window.plan_button.isEnabled()
    assert window.run_button.text() == "开始逐站观测同化"
    window.station_observations_edit.setText(str(station_csv))
    window.horizontal_length_scale.setValue(750.0)
    window.vertical_length_scale.setValue(55.0)
    window.background_wind_sigma.setValue(1.5)
    window.observation_wind_sigma.setValue(0.25)
    window.background_max_height.setValue(180.0)
    window.validation_height_tolerance.setValue(7.5)
    window.validation_window.setValue(90)
    window.max_cases.setValue(2)
    window.turbulence_product.setText("windmaster-uav")
    window.canopy_height.setValue(12.0)
    window.allow_profile_extrapolation.setChecked(True)
    window.uav_height_reference.setCurrentIndex(1)
    window.windmaster_height_reference.setCurrentIndex(
        window.windmaster_height_reference.findData(RadarHeightReference.PLATFORM_VERTICAL_OFFSET)
    )
    window.windmaster_platform_agl.setText("3")
    window.uav_launch_ground_altitude.setText("42.5")
    window.uav_launch_lat.setText("39.9")
    window.uav_launch_lon.setText("116.4")

    window.run_button.click()
    window.heights.setText("999")
    window.horizontal_length_scale.setValue(999.0)
    captured["action"](lambda _message: None)

    assert captured["args"][0] == tmp_path
    assert captured["args"][1] == tmp_path / "assimilation_output"
    assert captured["args"][2:4] == ([50], [200])
    routed = dict(captured["kwargs"])
    policy = routed.pop("instrument_policy")
    assert routed == {
        "time_range": None,
        "dem_override": None,
        "station_observations": station_csv,
        "horizontal_length_scale_m": 750.0,
        "vertical_length_scale_m": 55.0,
        "background_wind_sigma_ms": 1.5,
        "observation_wind_sigma_ms": 0.25,
        "max_cases": 2,
        "validation_window_seconds": 90,
        "selected_product": "windmaster-uav",
        "non_neutral_stability": False,
        "background_max_height_m": 180.0,
        "validation_height_tolerance_m": 7.5,
        "reference_height_m": 50.0,
        "roughness_m": 0.1,
        "displacement_height_m": 0.0,
        "allow_profile_extrapolation": True,
    }
    assert policy.uav_height_reference.value == "gps_altitude_minus_dem"
    assert policy.windmaster_platform_agl_m == 3
    assert policy.canopy_height_m == 12.0
    assert policy.uav_launch_ground_altitude_m == 42.5
    assert policy.uav_launch_location_confirmed


def test_assimilation_rejects_unknown_uav_height_definition(
    window: main_window.MainWindow,
) -> None:
    window.mode.setCurrentIndex(2)

    with pytest.raises(ValueError, match="UAV 高度定义未知"):
        window._assimilation_parameters()


def test_run_button_reports_input_error_after_offscreen_click(
    window: main_window.MainWindow,
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[tuple[object, ...]] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))

    window.run_button.click()
    for _ in range(100):
        qt_app.processEvents()
        QTest.qWait(5)
        if window.thread is None:
            break

    assert window.thread is None
    assert errors
    assert "请先选择输入数据文件夹" in window.log_view.toPlainText()
    assert all(button.isEnabled() for button in window.action_buttons)


def test_research_rejects_unknown_radar_height(window: main_window.MainWindow) -> None:
    window.uav_height_reference.setCurrentIndex(1)
    window.windmaster_height_reference.setCurrentIndex(
        window.windmaster_height_reference.findData(RadarHeightReference.UNKNOWN)
    )
    with pytest.raises(ValueError, match="WindMaster 高度基准未知"):
        window._turbulence_parameters()

def test_native_mode_snapshots_parameters_before_worker(window, tmp_path, monkeypatch):
    captured = {}
    class FakeRunner:
        def __init__(self, config, progress_callback=None):
            pass
        def run(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _Result({"mode": "native_assimilation"})
    monkeypatch.setattr(main_window, "NativeCoupledBatchRunner", FakeRunner)
    monkeypatch.setattr(window, "_start", lambda action: captured.setdefault("action", action))
    monkeypatch.setattr(window, "_instrument_policy", lambda **kwargs: "confirmed-policy")
    station = tmp_path / "stations.csv"
    station.write_text("k_m2s2,epsilon_m2s3\n1,0.1\n")
    window.input_edit.setText(str(tmp_path))
    window.mode.setCurrentIndex(window.mode.findData("native_assimilation"))
    window.native_station_observations_edit.setText(str(station))
    window.run_pipeline()
    window.native_horizontal_radius_m.setValue(999)
    window.native_station_observations_edit.clear()
    captured["action"](lambda message: None)
    assert captured["args"][1] == tmp_path / "native_output"
    assert captured["args"][2:4] == ([50], [200])
    assert captured["kwargs"]["station_observations"] == station
    assert captured["kwargs"]["horizontal_radius_m"] == 300
    assert captured["kwargs"]["native_runtime"] is None
    assert captured["kwargs"]["max_iterations"] == 1000
    assert not window.inspect_button.isEnabled()


def test_native_mode_requires_station_csv_before_worker(window, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(window, "_failed", errors.append)
    monkeypatch.setattr(window, "_start", lambda action: pytest.fail("worker must not start"))
    window.input_edit.setText(str(tmp_path))
    window.mode.setCurrentIndex(window.mode.findData("native_assimilation"))
    window.run_pipeline()
    assert "CSV" in errors[0]


def test_manual_background_turbulence_uses_explicit_time_and_wind(window, tmp_path, monkeypatch):
    captured = {}
    class FakeRunner:
        def __init__(self, config, progress_callback=None):
            pass
        def run(self, *args, **kwargs):
            captured.update(kwargs)
            return _Result({"mode": "manual"})
    monkeypatch.setattr(main_window, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(window, "_start", lambda action: action(lambda _: None))
    window.mode.setCurrentIndex(window.mode.findData("turbulence"))
    window.background_source.setCurrentIndex(window.background_source.findData("manual"))
    window.input_edit.setText(str(tmp_path))
    window.output_edit.setText(str(tmp_path / "output"))
    window.bounds.setText("25.0 25.1 102.3 102.5")
    window.heights.setText("50")
    window.resolutions.setText("200")
    window.manual_time.setText("2024-04-03T08:06:00Z")
    window.manual_speed.setValue(9.0)
    window.manual_direction.setValue(210.0)
    window.run_button.click()
    assert captured["solver_mode"] == "turbulence"
    assert captured["simulation_time"] == datetime(2024, 4, 3, 8, 6, tzinfo=timezone.utc)
    assert captured["background_speed_ms"] == 9.0
    assert captured["background_direction_deg"] == 210.0
    assert not window.start.isEnabled()
