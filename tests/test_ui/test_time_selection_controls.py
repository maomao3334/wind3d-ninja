from datetime import datetime, timezone
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QThread, Slot
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from wind3d_ninja.ui import main_window
from wind3d_ninja.ui.main_window import MainWindow

@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    value = MainWindow()
    yield value
    value._time_refresh_timer.stop()
    value.close()
    app.processEvents()

def test_display_offset_preserves_selected_utc(window):
    utc = datetime(2024, 4, 3, 8, 6, tzinfo=timezone.utc)
    window._set_time_options([window._display_time(utc)])
    window.start.setCurrentIndex(0)
    window.end.setCurrentIndex(0)
    window.display_timezone.setValue(0)
    assert "08:06" in window.start.currentText()
    assert window._time_lookup[window.start.currentText()] == "2024-04-03T08:06:00Z"
    assert window._time_lookup[window.end.currentText()] == "2024-04-03T08:06:00Z"

def test_pending_source_refresh_blocks_old_time_lookup(window, tmp_path):
    window.input_edit.setText(str(tmp_path))
    window._time_refresh_timer.stop()
    window.uav_source_timezone.setValue(0)
    assert window._time_refresh_timer.isActive()
    with pytest.raises(ValueError, match="刷新"):
        window._parameters()

def test_missing_instrument_sources_allow_fixed_station_or_manual_mode(window):
    assert window.uav_time_field.count() == 0
    assert window.windmaster_time_field.count() == 0
    policy = window._time_policy()
    assert policy.uav_time_field == "device_sampling_time"
    assert policy.windmaster_time_field == "sampling_time"

def test_fractional_timezone_offsets_are_not_rounded(window):
    window.uav_source_timezone.setValue(5.75)
    assert window.uav_source_timezone.value() == 5.75
    assert window.uav_source_timezone.prefix() == "UTC+"
    window.uav_source_timezone.setValue(-3.5)
    assert window.uav_source_timezone.value() == -3.5
    assert window.uav_source_timezone.prefix() == "UTC"


def test_completion_result_is_multiline_and_does_not_wrap_long_reports(window):
    from PySide6.QtWidgets import QPlainTextEdit
    window._done({"mode": "native_rans_coupled_assimilation", "validation": {"mean_abs_direction_error_deg": 102.87}, "path": "a" * 10000})
    assert window.log_view.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
    assert window.log_view.toPlainText().count(chr(10)) > 3
    assert "102.87" in window.log_view.toPlainText()


@pytest.mark.parametrize("fail", [False, True])
def test_scan_callbacks_execute_on_gui_thread(tmp_path, monkeypatch, fail):
    app = QApplication.instance() or QApplication([])
    observed = []

    class RecordingWindow(MainWindow):
        @Slot(object, int)
        def _apply_time_scan(self, result, generation):
            observed.append(("result", QThread.currentThread() == app.thread()))
            super()._apply_time_scan(result, generation)

        @Slot(str, int)
        def _time_scan_failed(self, error, generation):
            observed.append(("error", QThread.currentThread() == app.thread()))

        @Slot(object, int)
        def _time_scan_finished(self, thread, generation):
            observed.append(("finished", QThread.currentThread() == app.thread()))
            super()._time_scan_finished(thread, generation)

    def scan(_input_dir, policy):
        if fail:
            raise ValueError("scan fixture failed")
        return {"sources": {"generic_station": {
            "candidates": [], "selected_utc_times": ["2024-04-03T08:06:00Z"],
        }}}

    monkeypatch.setattr(main_window, "scan_instrument_time_candidates", scan)
    value = RecordingWindow()
    value.input_edit.setText(str(tmp_path))
    value.refresh_observation_times()
    deadline = time.monotonic() + 5
    try:
        while value._time_scans and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not value._time_scans
        assert observed == [("error" if fail else "result", True), ("finished", True)]
        assert value.refresh_times_button.isEnabled()
        if not fail:
            assert value.start.count() == 1
    finally:
        value._time_refresh_timer.stop()
        value.close()
        app.processEvents()
