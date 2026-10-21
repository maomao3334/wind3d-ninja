import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QLabel
from wind3d_ninja.ui.main_window import MainWindow
from wind3d_ninja.observations.instruments import UavHeightReference

@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    yield window
    window._time_refresh_timer.stop()
    window.close()
    app.processEvents()


def test_home_is_compact_and_run_stays_visible_on_every_page(window):
    assert [window.pages.tabText(i) for i in range(window.pages.count())] == [
        "首页 · 开始运行", "高级设置", "使用方法", "运行日志"]
    assert window.pages.currentWidget() is window.home_page
    assert window.step_stack.currentWidget().verticalScrollBar().maximum() == 0
    assert window.input_edit.isVisible()
    assert not window.heights.isVisible()
    assert not window.wind_edit.isVisible()
    assert not window.start.isVisible()
    assert not window.diurnal.isVisible()
    window.next_step.click()
    assert window.start.isVisible()
    window.next_step.click()
    assert window.heights.isVisible()
    assert not window.next_step.isEnabled()
    window.previous_step.click()
    assert window.start.isVisible()
    for index in range(window.pages.count()):
        window.pages.setCurrentIndex(index)
        QApplication.processEvents()
        assert window.run_button.isVisible()
        assert window.open_output_button.isVisible()
        assert window.run_button.geometry().bottom() <= window.centralWidget().height()


def test_mode_required_inputs_and_page_switches_preserve_values(window):
    window.heights.setText("80")
    window.native_station_observations_edit.setText("station.csv")
    window.mode.setCurrentIndex(window.mode.findData("native_assimilation"))
    assert window.native_station_observations_edit.isVisible()
    assert window.home_page.isAncestorOf(window.native_station_observations_edit)
    assert window.active_steps == [0, 1, 2, 3]
    window._set_step(1)
    window.display_timezone.setValue(0)
    window._set_step(2)
    window.uav_height_reference.setCurrentIndex(1)
    window._set_step(0)
    assert window.heights.text() == "80"
    assert window.native_station_observations_edit.text() == "station.csv"
    assert window.display_timezone.value() == 0
    assert window.uav_height_reference.currentIndex() == 1
    window.mode.setCurrentIndex(window.mode.findData("assimilation"))
    assert window.station_observations_edit.isVisible()
    assert window.home_page.isAncestorOf(window.station_observations_edit)
    window.mode.setCurrentIndex(window.mode.findData("turbulence"))
    window.background_source.setCurrentIndex(window.background_source.findData("manual"))
    assert window.manual_time.isVisible()
    assert window.active_steps == [0, 3]
    assert not window.step_buttons[1].isVisible()
    assert not window.step_buttons[2].isVisible()
    assert not window.native_station_observations_edit.isVisible()
    window.next_step.click()
    assert window.bounds.isVisible()
    assert "必填" in window.bounds_label.text()


@pytest.mark.parametrize("mode", ["standard", "turbulence", "assimilation", "native_assimilation"])
def test_help_starts_with_purpose_then_requirements_and_steps(window, mode):
    window.mode.setCurrentIndex(window.mode.findData(mode))
    text = window.help_view.toPlainText()
    assert text.index("这个模式是干什么的") < text.index("需要准备什么") < text.index("一步一步怎么用")
    assert window.mode_hint.text() in text
    assert window.data_requirements.text().removeprefix("需要准备：") in text


def test_height_fields_only_show_for_relevant_definition(window):
    window.mode.setCurrentIndex(window.mode.findData("turbulence"))
    window._set_step(2)
    window.uav_height_reference.setCurrentIndex(window.uav_height_reference.findData(UavHeightReference.RELATIVE_TO_LAUNCH))
    assert window.uav_launch_lat.isVisible()
    window.uav_height_reference.setCurrentIndex(window.uav_height_reference.findData(UavHeightReference.RELATIVE_AGL))
    assert not window.uav_launch_lat.isVisible()
    assert not window.roughness.isVisible()


def test_height_step_keeps_only_user_decisions(window):
    window.mode.setCurrentIndex(window.mode.findData("turbulence"))
    window._set_step(2)
    visible_text = " ".join(
        widget.text() for widget in window.findChildren(QLabel) if widget.isVisible()
    )
    assert "速度单位" not in visible_text
    assert "显式假设" not in visible_text
    assert "已完成运动补偿" in visible_text
    window.uav_height_reference.setCurrentIndex(
        window.uav_height_reference.findData(UavHeightReference.RELATIVE_AGL)
    )
    policy = window._instrument_policy()
    assert policy.windmaster_motion_compensated is True
    assert policy.uav_motion_compensated is True
    assert policy.uav_relative_agl_confirmed is True
