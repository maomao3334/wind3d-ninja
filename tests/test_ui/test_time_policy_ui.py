from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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


def _wait_for_scan(window: main_window.MainWindow) -> None:
    for _ in range(100):
        if (
            window.refresh_times_button.isEnabled()
            and not window._time_refresh_timer.isActive()
            and not window._time_scans
        ):
            return
        QTest.qWait(20)
    pytest.fail("time scan did not finish")


def test_time_policy_scan_controls_parsing_and_display_timezone(
    window: main_window.MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_policies = []

    def candidate(field: str, start: str, end: str) -> dict[str, object]:
        return {
            "field": field,
            "raw_min": start.replace("Z", ""),
            "raw_max": end.replace("Z", ""),
            "utc_preview_min": start,
            "utc_preview_max": end,
            "value_count": 2,
            "missing_count": 1,
            "invalid_count": 2,
            "anomalies": 1,
        }

    def scan(_: Path, policy: object) -> dict[str, object]:
        seen_policies.append(policy)
        return {
            "sources": {
                "uav": {
                    "selected_field": policy.uav_time_field,
                    "selected_utc_times": ["2024-04-03T08:00:00Z"],
                    "candidates": [
                        candidate("device_sampling_time", "2024-04-03T08:00:00Z", "2024-04-03T08:10:00Z"),
                        candidate("pc_acquisition_time", "2024-04-03T13:40:00Z", "2024-04-03T13:50:00Z"),
                    ],
                },
                "windmaster": {
                    "selected_field": policy.windmaster_time_field,
                    "selected_utc_times": ["2024-04-03T16:00:00Z"],
                    "candidates": [candidate("sampling_time", "2024-04-03T16:00:00Z", "2024-04-03T16:10:00Z")],
                },
                "dat": {"selected_field": None, "candidates": []},
            }
        }

    class FakeInspector:
        def __init__(self, _config: object, instrument_policy: object | None = None) -> None:
            self.policy = instrument_policy

        def load(self, _input_dir: Path) -> object:
            return type(
                "Loaded",
                (),
                {
                    "fixed_observations": [],
                    "uav_inputs": [
                        (None, (type("Uav", (), {"time_utc": datetime(2024, 4, 3, 8, 0, tzinfo=timezone.utc)})(),))
                    ],
                    "generic_observations": [],
                },
            )()

    monkeypatch.setattr(main_window, "scan_instrument_time_candidates", scan)
    monkeypatch.setattr(main_window, "InputInspector", FakeInspector)
    window.input_edit.setText(str(tmp_path))
    window.refresh_observation_times()
    _wait_for_scan(window)

    assert window.uav_time_field.currentData() == "device_sampling_time"
    assert window.windmaster_time_field.currentData() == "sampling_time"
    assert "不重叠" in window.time_scan_summary.toPlainText()
    assert "异常 1" in window.time_scan_summary.toPlainText()
    assert window.start.itemText(0) == "2024-04-03 16:00 (UTC+8)"

    window.start.setCurrentIndex(0)
    window.end.setCurrentIndex(0)
    assert window._parameters()[3] == (
        datetime(2024, 4, 3, 8, 0, tzinfo=timezone.utc),
        datetime(2024, 4, 3, 8, 0, tzinfo=timezone.utc),
    )

    window.uav_time_field.setCurrentIndex(window.uav_time_field.findData("pc_acquisition_time"))
    QTest.qWait(350)
    _wait_for_scan(window)
    assert seen_policies[-1].uav_time_field == "pc_acquisition_time"

    window.display_timezone.setValue(0)
    QTest.qWait(350)
    _wait_for_scan(window)
    assert window.start.itemText(0) == "2024-04-03 08:00 (UTC+0)"

    window.uav_source_timezone.setValue(7)
    with pytest.raises(ValueError, match="时间策略正在刷新"):
        window._parameters()
    QTest.qWait(350)
    _wait_for_scan(window)


def test_time_policy_rejects_missing_selected_field(window: main_window.MainWindow) -> None:
    window.uav_time_field.clear()
    window.uav_time_field.addItem("device sampling time", "device_sampling_time")
    window.uav_time_field.setCurrentIndex(-1)
    window.windmaster_time_field.clear()
    with pytest.raises(ValueError, match="UAV 时间字段"):
        window._instrument_policy()
