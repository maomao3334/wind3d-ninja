import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication
from wind3d_ninja.ui.main_window import MainWindow
from wind3d_ninja.windninja.config_writer import WindNinjaConfigWriter


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    value = MainWindow()
    value.diurnal.parentWidget().setChecked(True)
    yield value
    value._time_refresh_timer.stop()
    value.close()
    app.processEvents()


@pytest.mark.parametrize("mode,background", [
    ("turbulence", "profiles"),
    ("turbulence", "manual"),
    ("assimilation", "profiles"),
    ("native_assimilation", "profiles"),
])
def test_switching_to_rans_clears_and_disables_conflicting_options(window, mode, background, tmp_path):
    window.diurnal.setChecked(True)
    window.non_neutral.setChecked(True)
    window.alpha.setText("0.25")
    window.background_source.setCurrentIndex(window.background_source.findData(background))
    window.mode.setCurrentIndex(window.mode.findData(mode))
    assert not window.diurnal.isChecked()
    assert not window.non_neutral.isChecked()
    assert window.alpha.text() == ""
    for control in (window.diurnal, window.non_neutral, window.alpha):
        assert not control.isEnabled()
        assert control.toolTip()
    assert not window.rans_compatibility_hint.isHidden()
    window.diurnal.parentWidget().setChecked(False)
    window.diurnal.parentWidget().setChecked(True)
    assert not window.diurnal.isEnabled()
    assert not window.alpha.isEnabled()
    options = window._runtime_config().windninja
    target = WindNinjaConfigWriter().write(
        tmp_path / "neutral.cfg", tmp_path / "dem.tif", tmp_path / "stations.csv",
        tmp_path / "case", datetime(2024, 4, 3, 8, 6, tzinfo=timezone.utc),
        50, 200, "grass", 1, False, solver_mode="turbulence",
        background_speed_ms=5, background_direction_deg=270, background_height_m=50,
        diurnal_winds=options.diurnal_winds,
        non_neutral_stability=options.non_neutral_stability,
        alpha_stability=options.alpha_stability,
    )
    assert target.is_file()


@pytest.mark.parametrize("mode", ["turbulence", "assimilation", "native_assimilation"])
def test_rans_config_remains_neutral_if_disabled_controls_have_stale_values(window, mode):
    window.mode.setCurrentIndex(window.mode.findData(mode))
    window.diurnal.setChecked(True)
    window.non_neutral.setChecked(True)
    window.alpha.setText("invalid stale alpha")
    config = window._runtime_config().windninja
    assert config.diurnal_winds is False
    assert config.non_neutral_stability is False
    assert config.alpha_stability is None


def test_standard_mode_can_use_stability_and_diurnal_options_after_rans(window):
    window.mode.setCurrentIndex(window.mode.findData("turbulence"))
    window.mode.setCurrentIndex(window.mode.findData("standard"))
    assert window.rans_compatibility_hint.isHidden()
    for control in (window.diurnal, window.non_neutral, window.alpha):
        assert control.isEnabled()
    window.diurnal.setChecked(True)
    window.non_neutral.setChecked(True)
    window.alpha.setText("0.25")
    config = window._runtime_config().windninja
    assert config.diurnal_winds is True
    assert config.non_neutral_stability is True
    assert config.alpha_stability == 0.25


def test_standard_stability_controls_follow_the_advanced_group_switch(window):
    group = window.diurnal.parentWidget()
    group.setChecked(False)
    window._mode_changed()
    for control in (window.diurnal, window.non_neutral, window.alpha):
        assert not control.isEnabled()
    group.setChecked(True)
    for control in (window.diurnal, window.non_neutral, window.alpha):
        assert control.isEnabled()
