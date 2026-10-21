from __future__ import annotations

import json

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSignalBlocker, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..cli.doctor import doctor_report
from ..config.loader import load_config
from ..observations.instruments import (
    InstrumentPolicy,
    InstrumentTimePolicy,
    RadarHeightReference,
    UavHeightReference,
    WindDirectionReference,
    WindSpeedUnit,
    scan_instrument_time_candidates,
)
from ..pipeline.inspector import InputInspector
from ..pipeline.planner import PipelinePlanner
from ..pipeline.runner import PipelineRunner
from ..pipeline.turbulence import TurbulenceBatchRunner
from ..utils.geometry import parse_bounds
from ..utils.timezone import parse_explicit_time_utc, parse_time_range_utc


AssimilationBatchRunner = None
NativeCoupledBatchRunner = None


class Worker(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, action: Callable[[], object]):
        super().__init__()
        self.action = action

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self.action())
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    _time_scan_result = Signal(object, int)
    _time_scan_error = Signal(str, int)
    _time_scan_completion = Signal(object, int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wind3D-Ninja")
        self.resize(1050, 820)
        self.config = load_config()
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.action_buttons: list[QPushButton] = []
        self.last_output_dir: Path | None = None
        self._assimilation_defaults_applied = False
        self._native_defaults_applied = False
        self._time_lookup: dict[str, str] = {}
        self._time_scan_thread: QThread | None = None
        self._time_scan_worker: Worker | None = None
        self._time_scans: list[tuple[QThread, Worker]] = []
        self._finished_time_scans: list[tuple[QThread, Worker]] = []
        self._time_scan_generation = 0
        self._time_scan_pending = False
        self._updating_time_controls = False
        self._time_refresh_timer = QTimer(self)
        self._time_refresh_timer.setSingleShot(True)
        self._time_refresh_timer.setInterval(250)
        self._time_refresh_timer.timeout.connect(self.refresh_observation_times)
        self._time_scan_result.connect(self._apply_time_scan, Qt.ConnectionType.QueuedConnection)
        self._time_scan_error.connect(self._time_scan_failed, Qt.ConnectionType.QueuedConnection)
        self._time_scan_completion.connect(self._time_scan_finished, Qt.ConnectionType.QueuedConnection)
        self._build_ui()

    def _build_ui(self) -> None:
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 12, 16, 12)
        self.pages = QTabWidget()
        root.addWidget(self.pages, 1)
        self.home_page = QWidget()
        home_layout = QVBoxLayout(self.home_page)
        self.pages.addTab(self.home_page, "首页 · 开始运行")
        advanced_content = QWidget()
        advanced_layout = QVBoxLayout(advanced_content)
        self.advanced_page = QScrollArea()
        self.advanced_page.setWidgetResizable(True)
        self.advanced_page.setWidget(advanced_content)
        self.pages.addTab(self.advanced_page, "高级设置")
        self.help_view = QTextBrowser()
        self.pages.addTab(self.help_view, "使用方法")
        mode_form = QFormLayout()
        home_layout.addLayout(mode_form)
        self.mode_hint = QLabel()
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("font-size: 15px; padding: 8px 0;")
        home_layout.addWidget(self.mode_hint)
        step_bar = QHBoxLayout()
        home_layout.addLayout(step_bar)
        self.step_stack = QStackedWidget()
        home_layout.addWidget(self.step_stack, 1)
        self.step_buttons = []
        self.step_titles = ("选择数据", "确认时间", "确认高度与风向", "设置输出")
        page_layouts = []
        for index, title in enumerate(self.step_titles):
            button = QPushButton(title)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, target=index: self._set_step(target))
            step_bar.addWidget(button)
            self.step_buttons.append(button)
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setSpacing(12)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(page)
            self.step_stack.addWidget(scroll)
            page_layouts.append(page_layout)
        layout, time_layout, instrument_layout, output_layout = page_layouts
        self.time_page = self.step_stack.widget(1)
        self.instrument_page = self.step_stack.widget(2)
        self.data_requirements = QLabel()
        self.data_requirements.setWordWrap(True)
        layout.addWidget(self.data_requirements)
        navigation = QHBoxLayout()
        self.previous_step = QPushButton("上一步")
        self.next_step = QPushButton("下一步")
        self.previous_step.clicked.connect(lambda: self._move_step(-1))
        self.next_step.clicked.connect(lambda: self._move_step(1))
        navigation.addWidget(self.previous_step)
        navigation.addStretch()
        navigation.addWidget(self.next_step)
        home_layout.addLayout(navigation)
        self._workflow_signature = None
        runtime_box = QGroupBox("运行库位置（通常无需修改）")
        runtime_form = QFormLayout(runtime_box)
        advanced_layout.addWidget(runtime_box)

        paths = QGroupBox("文件位置")
        path_form = QFormLayout(paths)
        self.input_edit = self._directory_row(
            path_form, "输入数据文件夹", on_selected=self.refresh_observation_times
        )
        self.output_edit = self._directory_row(path_form, "输出文件夹（不填则自动创建）")
        self.dem_edit = self._file_row(
            path_form,
            "DEM 文件（可选；不填则自动匹配或下载）",
            "DEM (*.tif *.tiff *.asc *.img);;所有文件 (*)",
        )
        self.wind_edit = self._file_row(
            runtime_form,
            "WindNinja_cli.exe",
            "WindNinja_cli.exe (WindNinja_cli.exe);;程序 (*.exe);;所有文件 (*)",
        )
        self.wind_edit.setText(str(self.config.windninja_exe))
        self.input_label = path_form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()
        layout.addWidget(paths)

        basic = QGroupBox("模拟任务")
        basic_grid = QGridLayout(basic)
        self.mode = QComboBox()
        self.mode.addItem("常规风场", "standard")
        self.mode.addItem("湍流模拟（RANS）", "turbulence")
        self.mode.addItem("逐站观测同化", "assimilation")
        self.mode.addItem("原生 RANS 耦合同化", "native_assimilation")
        self.heights = QLineEdit("10 20 50")
        self.resolutions = QLineEdit("50 100 200")
        self.buffer = QDoubleSpinBox()
        self.buffer.setRange(0, 1000)
        self.buffer.setValue(10)
        self.buffer.setSuffix(" km")
        self.start = QComboBox()
        self.start.setPlaceholderText("从输入观测刷新后选择；不填则使用全部时间")
        self.start.setCurrentIndex(-1)
        self.end = QComboBox()
        self.end.setPlaceholderText("从输入观测刷新后选择")
        self.end.setCurrentIndex(-1)
        self.bounds = QLineEdit()
        self.bounds.setPlaceholderText("min_lat max_lat min_lon max_lon")
        self.kmz = QCheckBox("生成 KMZ")
        self.kmz.setChecked(True)
        rows = [
            ("输出高度（米，空格分隔）", self.heights),
            ("网格分辨率（米，空格分隔）", self.resolutions),
        ]
        mode_form.addRow("我想做什么", self.mode)
        for row, (label, widget) in enumerate(rows, start=1):
            basic_grid.addWidget(QLabel(label), row, 0)
            basic_grid.addWidget(widget, row, 1)
        self.refresh_times_button = QPushButton("刷新可用观测时间")
        self.refresh_times_button.clicked.connect(self.refresh_observation_times)
        basic_grid.addWidget(self.kmz, len(rows) + 1, 1)
        output_layout.addWidget(basic)
        region_box = QGroupBox("计算区域")
        region_form = QFormLayout(region_box)
        region_form.addRow("自动范围外扩", self.buffer)
        bounds_box = QGroupBox("计算区域")
        bounds_form = QFormLayout(bounds_box)
        self.bounds_label = QLabel("手动区域范围（可选）")
        bounds_form.addRow(self.bounds_label, self.bounds)
        output_layout.addWidget(bounds_box)
        advanced_layout.addWidget(region_box)
        time_range_box = QGroupBox("计算时段（留空使用全部可用时间）")
        time_range_form = QFormLayout(time_range_box)
        time_range_form.addRow(self.refresh_times_button)
        time_range_form.addRow("开始时间", self.start)
        time_range_form.addRow("结束时间", self.end)
        time_layout.addWidget(time_range_box)

        time_policy_box = QGroupBox("时间解析（扫描后选择，设置会重算时间）")
        time_policy_form = QFormLayout(time_policy_box)
        self.uav_time_field = QComboBox()
        self.windmaster_time_field = QComboBox()
        self.uav_source_timezone = self._timezone_spinbox(8.0)
        self.windmaster_source_timezone = self._timezone_spinbox(8.0)
        self.dat_source_timezone = self._timezone_spinbox(8.0)
        self.display_timezone = self._timezone_spinbox(8.0)
        self.time_scan_summary = QPlainTextEdit()
        self.time_scan_summary.setReadOnly(True)
        self.time_scan_summary.setMaximumBlockCount(100)
        self.time_scan_summary.setMinimumHeight(110)
        time_policy_form.addRow("UAV 时间字段", self.uav_time_field)
        time_policy_form.addRow("UAV 原始时间 UTC 偏移", self.uav_source_timezone)
        time_policy_form.addRow("WindMaster 时间字段", self.windmaster_time_field)
        time_policy_form.addRow("WindMaster 原始时间 UTC 偏移", self.windmaster_source_timezone)
        time_policy_form.addRow("DAT/固定站原始时间 UTC 偏移", self.dat_source_timezone)
        time_policy_form.addRow("开始/结束时间显示 UTC 偏移", self.display_timezone)
        time_policy_form.addRow("扫描预览（原始 → UTC）", self.time_scan_summary)
        self.time_overlap_status = QLabel("扫描后显示观测源时间对齐状态。")
        self.time_overlap_status.setWordWrap(True)
        time_policy_form.addRow("时间对齐", self.time_overlap_status)
        time_layout.addWidget(time_policy_box)
        self.time_policy_form = time_policy_form
        for control in (
            self.uav_time_field,
            self.windmaster_time_field,
            self.uav_source_timezone,
            self.windmaster_source_timezone,
            self.dat_source_timezone,
        ):
            signal = control.currentIndexChanged if isinstance(control, QComboBox) else control.valueChanged
            signal.connect(self._schedule_time_policy_refresh)
        self.display_timezone.valueChanged.connect(self._refresh_display_timezone)

        advanced = QGroupBox("WindNinja 高级参数")
        self.advanced_options = advanced
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_form = QFormLayout(advanced)
        self.vegetation = QComboBox()
        self.vegetation.addItems(["trees", "grass", "brush"])
        self.vegetation.setCurrentText(self.config.windninja.vegetation)
        self.diurnal = QCheckBox("启用日变化坡谷风")
        self.non_neutral = QCheckBox("启用非中性大气稳定度")
        self.alpha = QLineEdit()
        self.alpha.setPlaceholderText("可选；明确知道稳定度系数时填写")
        self.input_height = QLineEdit()
        self.input_height.setPlaceholderText("可选；站点已有各自高度时通常留空")
        self.radius = QDoubleSpinBox()
        self.radius.setRange(-1, 10000)
        self.radius.setValue(-1)
        self.radius.setSuffix(" km（-1=自动）")
        self.clip = QDoubleSpinBox()
        self.clip.setRange(0, 49)
        self.clip.setSuffix(" %")
        self.threads = QSpinBox()
        self.threads.setRange(1, 128)
        self.threads.setValue(self.config.windninja.num_threads)
        advanced_form.addRow("主导下垫面", self.vegetation)
        advanced_form.addRow("日变化风", self.diurnal)
        advanced_form.addRow("大气稳定度", self.non_neutral)
        advanced_form.addRow("alpha_stability", self.alpha)
        advanced_form.addRow("统一输入风高度（米）", self.input_height)
        advanced_form.addRow("站点影响半径", self.radius)
        advanced_form.addRow("输出边缘裁剪", self.clip)
        advanced_form.addRow("线程数", self.threads)
        advanced_layout.addWidget(advanced)

        self.rans_compatibility_hint = QLabel(
            "当前 RANS 模式仅支持中性大气：日变化风、非中性稳定度和 alpha_stability 已自动关闭。"
        )
        self.rans_compatibility_hint.setWordWrap(True)
        layout.addWidget(self.rans_compatibility_hint)

        self.turbulence_options = QGroupBox("RANS 背景与独立验证")
        turbulence_form = QFormLayout(self.turbulence_options)
        self.background_source = QComboBox()
        self.background_source.addItem("逐站剖面生成背景", "profiles")
        self.background_source.addItem("手动输入背景风（无需站点观测）", "manual")
        self.background_box = QGroupBox("湍流背景")
        background_form = QFormLayout(self.background_box)
        background_form.addRow("背景来源", self.background_source)
        layout.insertWidget(1, self.background_box)
        turbulence_hint = QLabel(
            "WindMaster 观测初始化 RANS 平均风背景（仅使用不高于背景最大高度的 gate）；"
            "UAV 移动观测仅用于同高度独立验证。背景湍流 KMZ 展示列最大扰动，"
            "耦合同化输出使用指定高度的原生场。切换回常规模式后才能使用 inspect 和 plan。"
        )
        turbulence_hint.setWordWrap(True)
        self.background_max_height = QDoubleSpinBox()
        self.background_max_height.setRange(1.0, 10000.0)
        self.background_max_height.setValue(200.0)
        self.background_max_height.setSuffix(" m")
        self.validation_height_tolerance = QDoubleSpinBox()
        self.validation_height_tolerance.setRange(0.0, 1000.0)
        self.validation_height_tolerance.setValue(10.0)
        self.validation_height_tolerance.setSuffix(" m")
        self.validation_window = QSpinBox()
        self.validation_window.setRange(0, 86400)
        self.validation_window.setValue(120)
        self.validation_window.setSuffix(" s")
        self.max_cases = QSpinBox()
        self.max_cases.setRange(1, 10000)
        self.max_cases.setValue(1)
        self.turbulence_product = QLineEdit()
        self.turbulence_product.setPlaceholderText("可选；留空时由数据目录自动选择")
        turbulence_form.addRow(turbulence_hint)
        turbulence_form.addRow("WindMaster 背景最大高度", self.background_max_height)
        turbulence_form.addRow("UAV 验证高度容差", self.validation_height_tolerance)
        turbulence_form.addRow("UAV 验证时间窗口", self.validation_window)
        turbulence_form.addRow("最多背景时次", self.max_cases)
        turbulence_form.addRow("数据产品", self.turbulence_product)
        advanced_layout.addWidget(self.turbulence_options)

        self.manual_background = QGroupBox("独立湍流背景风")
        manual_form = QFormLayout(self.manual_background)
        self.manual_speed = QDoubleSpinBox()
        self.manual_speed.setRange(0.0, 100.0)
        self.manual_speed.setValue(8.0)
        self.manual_speed.setSuffix(" m/s")
        self.manual_direction = QDoubleSpinBox()
        self.manual_direction.setRange(0.0, 360.0)
        self.manual_direction.setValue(270.0)
        self.manual_direction.setSuffix(" °（来向）")
        self.manual_height = QDoubleSpinBox()
        self.manual_height.setRange(0.1, 10000.0)
        self.manual_height.setValue(50.0)
        self.manual_height.setSuffix(" m（植被顶部以上）")
        self.manual_time = QLineEdit()
        self.manual_time.setPlaceholderText("2024-04-03T08:06:00Z（必须带时区）")
        manual_form.addRow("背景风速", self.manual_speed)
        manual_form.addRow("背景风向", self.manual_direction)
        manual_form.addRow("背景高度", self.manual_height)
        manual_form.addRow("模拟时间", self.manual_time)
        layout.addWidget(self.manual_background)
        self.background_source.currentIndexChanged.connect(self._mode_changed)

        self.assimilation_options = QGroupBox("逐站观测同化（RANS背景分析）配置")
        assimilation_form = QFormLayout(self.assimilation_options)
        assimilation_hint = QLabel(
            "逐站观测分析独立于原生 RANS coupled 求解；UAV 始终仅作留出验证。"
            "仅在明确 k 或完整三分量统计可用时分析 k，不把平均风或 KMZ 列最大值视为 k。"
            "低于最小 WindMaster gate 的高度需显式允许中性对数廓线外推。"
        )
        assimilation_hint.setWordWrap(True)
        self.station_input_box = QGroupBox("补充站点观测")
        station_input_form = QFormLayout(self.station_input_box)
        layout.addWidget(self.station_input_box)
        self.station_observations_edit = self._file_row(
            station_input_form,
            "附加固定站点 CSV（可选）",
            "CSV (*.csv);;所有文件 (*)",
        )
        self.horizontal_length_scale = QDoubleSpinBox()
        self.horizontal_length_scale.setRange(1.0, 100000.0)
        self.horizontal_length_scale.setValue(1000.0)
        self.horizontal_length_scale.setSuffix(" m")
        self.vertical_length_scale = QDoubleSpinBox()
        self.vertical_length_scale.setRange(0.001, 100000.0)
        self.vertical_length_scale.setDecimals(3)
        self.vertical_length_scale.setValue(30.0)
        self.vertical_length_scale.setSuffix(" m")
        self.background_wind_sigma = QDoubleSpinBox()
        self.background_wind_sigma.setRange(0.001, 100.0)
        self.background_wind_sigma.setDecimals(3)
        self.background_wind_sigma.setValue(2.0)
        self.background_wind_sigma.setSuffix(" m/s")
        self.observation_wind_sigma = QDoubleSpinBox()
        self.observation_wind_sigma.setRange(0.001, 100.0)
        self.observation_wind_sigma.setDecimals(3)
        self.observation_wind_sigma.setValue(0.5)
        self.observation_wind_sigma.setSuffix(" m/s")
        assimilation_form.insertRow(0, assimilation_hint)
        assimilation_form.addRow("水平长度尺度", self.horizontal_length_scale)
        assimilation_form.addRow("垂直长度尺度", self.vertical_length_scale)
        assimilation_form.addRow("背景风标准差", self.background_wind_sigma)
        assimilation_form.addRow("观测风标准差", self.observation_wind_sigma)
        advanced_layout.addWidget(self.assimilation_options)

        self.native_options = QGroupBox("原生RANS耦合同化（Windows）配置")
        native_form = QFormLayout(self.native_options)
        native_hint = QLabel("随软件提供原生求解运行库。CSV 可混合平均风站；至少一个站需含 k 或完整三分量标准差，并含 epsilon_m2s3 或 mixing_length_m。UAV 仅用于验证。")
        native_hint.setWordWrap(True)
        native_form.addRow(native_hint)
        self.native_input_box = QGroupBox("同化观测")
        native_input_form = QFormLayout(self.native_input_box)
        self.native_station_observations_edit = self._file_row(native_input_form, "固定站点 CSV（必填）", "CSV (*.csv);;所有文件 (*)")
        layout.addWidget(self.native_input_box)
        self.native_runtime_edit = self._directory_row(native_form, "求解运行库（留空使用随包版本）")
        for name, label, default, suffix in (
            ("horizontal_radius_m", "水平影响半径", 300.0, " m"),
            ("vertical_radius_m", "垂直影响半径", 50.0, " m"),
            ("velocity_relaxation_seconds", "速度松弛时间", 10.0, " s"),
            ("tke_relaxation_seconds", "k 松弛时间", 10.0, " s"),
            ("epsilon_relaxation_seconds", "epsilon 松弛时间", 10.0, " s"),
        ):
            control = QDoubleSpinBox()
            control.setRange(0.001, 100000.0)
            control.setDecimals(3)
            control.setValue(default)
            control.setSuffix(suffix)
            setattr(self, "native_" + name, control)
            native_form.addRow(label, control)
        self.native_max_iterations = QSpinBox()
        self.native_max_iterations.setRange(51, 1000000)
        self.native_max_iterations.setValue(1000)
        native_form.addRow("最大迭代次数", self.native_max_iterations)
        self.native_residual_tolerance = QDoubleSpinBox()
        self.native_residual_tolerance.setDecimals(9)
        self.native_residual_tolerance.setRange(0.000000001, 1.0)
        self.native_residual_tolerance.setValue(1e-5)
        native_form.addRow("残差容差", self.native_residual_tolerance)
        advanced_layout.addWidget(self.native_options)

        self.research_options = QGroupBox("确认数据含义（按仪器记录填写）")
        research_form = QFormLayout(self.research_options)
        research_hint = QLabel(
            "请选择风向含义和高度基准。导入数据按 m/s、地理风向和已完成运动补偿处理。"
        )
        research_hint.setWordWrap(True)
        self.reference_height = QDoubleSpinBox()
        self.reference_height.setRange(0.1, 10000.0)
        self.reference_height.setValue(50.0)
        self.reference_height.setSuffix(" m")
        self.roughness = QDoubleSpinBox()
        self.roughness.setRange(0.0001, 100.0)
        self.roughness.setDecimals(4)
        self.roughness.setValue(0.1)
        self.roughness.setSuffix(" m")
        self.canopy_height = QDoubleSpinBox()
        self.canopy_height.setRange(0.0, 1000.0)
        self.canopy_height.setValue(0.0)
        self.canopy_height.setSuffix(" m")
        self.displacement_height = QDoubleSpinBox()
        self.displacement_height.setRange(0.0, 1000.0)
        self.displacement_height.setValue(0.0)
        self.displacement_height.setSuffix(" m")
        self.allow_profile_extrapolation = QCheckBox("允许对数廓线外推")

        self.windmaster_direction = QComboBox()
        self.windmaster_direction.addItem("来向", WindDirectionReference.FROM_TRUE_NORTH)
        self.windmaster_direction.addItem("去向", WindDirectionReference.TO_TRUE_NORTH)
        self.windmaster_height_reference = QComboBox()
        self.windmaster_height_reference.addItem("对地高度", RadarHeightReference.AGL)
        self.windmaster_height_reference.addItem("相对雷达平台", RadarHeightReference.PLATFORM_VERTICAL_OFFSET)
        self.windmaster_height_reference.addItem("未知", RadarHeightReference.UNKNOWN)
        self.windmaster_platform_agl = QLineEdit("0")
        self.windmaster_platform_agl.setPlaceholderText("米；地面安装填 0")

        self.uav_direction = QComboBox()
        self.uav_direction.addItem("来向", WindDirectionReference.FROM_TRUE_NORTH)
        self.uav_direction.addItem("去向", WindDirectionReference.TO_TRUE_NORTH)
        self.uav_height_reference = QComboBox()
        self.uav_height_reference.addItem("请选择", UavHeightReference.UNKNOWN)
        self.uav_height_reference.addItem("飞行器海拔减 DEM", UavHeightReference.GPS_ALTITUDE_MINUS_DEM)
        self.uav_height_reference.addItem("相对起飞点", UavHeightReference.RELATIVE_TO_LAUNCH)
        self.uav_height_reference.addItem("对地高度", UavHeightReference.RELATIVE_AGL)
        self.uav_legacy_gps_is_launch_altitude_confirmed = QCheckBox("确认旧版 GPS 字段为起飞高程")
        self.uav_launch_ground_altitude = QLineEdit()
        self.uav_launch_ground_altitude.setPlaceholderText("可选，m")
        self.uav_launch_lat = QLineEdit()
        self.uav_launch_lat.setPlaceholderText("可选，纬度")
        self.uav_launch_lon = QLineEdit()
        self.uav_launch_lon.setPlaceholderText("可选，经度")

        research_form.addRow(research_hint)
        self.profile_options = QGroupBox("垂直剖面参数（确认含义后再调整）")
        profile_form = QFormLayout(self.profile_options)
        advanced_layout.addWidget(self.profile_options)
        profile_form.addRow("参考高度", self.reference_height)
        profile_form.addRow("粗糙度 z0", self.roughness)
        profile_form.addRow("冠层高度", self.canopy_height)
        profile_form.addRow("位移高度", self.displacement_height)
        profile_form.addRow(self.allow_profile_extrapolation)
        research_form.addRow("WindMaster 风向", self.windmaster_direction)
        research_form.addRow("WindMaster 高度", self.windmaster_height_reference)
        research_form.addRow("雷达平台离地高度", self.windmaster_platform_agl)
        research_form.addRow("UAV 风向", self.uav_direction)
        research_form.addRow("UAV 高度", self.uav_height_reference)
        profile_form.addRow(self.uav_legacy_gps_is_launch_altitude_confirmed)
        research_form.addRow("UAV 起点地面海拔", self.uav_launch_ground_altitude)
        research_form.addRow("UAV 起飞点纬度", self.uav_launch_lat)
        research_form.addRow("UAV 起飞点经度", self.uav_launch_lon)
        instrument_layout.addWidget(self.research_options)
        self.instrument_form = research_form
        self.uav_height_reference.currentIndexChanged.connect(self._height_definition_changed)
        self._height_definition_changed()

        buttons = QHBoxLayout()
        for text, handler in (
            ("检查运行环境", self.doctor),
            ("检查数据", self.inspect),
            ("预览任务", self.plan),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            self.action_buttons.append(button)
            if handler == self.doctor:
                advanced_layout.addWidget(button)
            else:
                buttons.addWidget(button)
            if handler == self.inspect:
                self.inspect_button = button
            elif handler == self.plan:
                self.plan_button = button
        self.run_button = QPushButton("开始常规运行")
        self.run_button.clicked.connect(self.run_pipeline)
        self.action_buttons.append(self.run_button)
        footer = QHBoxLayout()
        footer.addStretch()
        self.run_button.setMinimumHeight(38)
        footer.addWidget(self.run_button)
        self.open_output_button = QPushButton("打开输出文件夹")
        self.open_output_button.clicked.connect(self.open_output)
        footer.addWidget(self.open_output_button)
        output_layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)
        self.log_view = QPlainTextEdit()
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("运行日志显示在这里")
        self.pages.addTab(self.log_view, "运行日志")
        for page_layout in [*page_layouts, advanced_layout]:
            page_layout.addStretch()
        root.addLayout(footer)
        self.setCentralWidget(content)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.advanced_options.toggled.connect(self._mode_changed)
        self._mode_changed()

    def _set_step(self, step: int) -> None:
        if step not in self.active_steps:
            step = self.active_steps[0]
        self.step_stack.setCurrentIndex(step)
        for index, button in enumerate(self.step_buttons):
            button.setVisible(index in self.active_steps)
            button.setChecked(index == step)
            if index in self.active_steps:
                number = self.active_steps.index(index) + 1
                button.setText(f"{number}. {self.step_titles[index]}")
        position = self.active_steps.index(step)
        self.previous_step.setEnabled(position > 0)
        self.next_step.setEnabled(position < len(self.active_steps) - 1)
        self.next_step.setText("下一步" if self.next_step.isEnabled() else "准备完成，点击底部开始运行")

    def _move_step(self, direction: int) -> None:
        position = self.active_steps.index(self.step_stack.currentIndex()) + direction
        if 0 <= position < len(self.active_steps):
            self._set_step(self.active_steps[position])

    def _height_definition_changed(self) -> None:
        reference = self.uav_height_reference.currentData()
        for widget in (self.uav_launch_ground_altitude, self.uav_launch_lat, self.uav_launch_lon):
            visible = reference == UavHeightReference.RELATIVE_TO_LAUNCH
            widget.setVisible(visible)
            self.instrument_form.labelForField(widget).setVisible(visible)

    def _directory_row(
        self,
        form: QFormLayout,
        label: str,
        *,
        on_selected: Callable[[], None] | None = None,
    ) -> QLineEdit:
        edit = QLineEdit()
        button = QPushButton("浏览…")
        button.clicked.connect(lambda: self._choose_directory(edit, on_selected=on_selected))
        row = QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(button)
        form.addRow(label, row)
        return edit

    def _file_row(self, form: QFormLayout, label: str, file_filter: str) -> QLineEdit:
        edit = QLineEdit()
        button = QPushButton("浏览…")
        button.clicked.connect(lambda: self._choose_file(edit, file_filter))
        row = QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(button)
        form.addRow(label, row)
        return edit

    def _choose_directory(
        self, edit: QLineEdit, *, on_selected: Callable[[], None] | None = None
    ) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            edit.setText(path)
            if on_selected is not None:
                on_selected()

    def _choose_file(self, edit: QLineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", filter=file_filter)
        if path:
            edit.setText(path)

    def _input_dir(self) -> Path:
        value = self.input_edit.text().strip()
        if not value:
            raise ValueError("请先选择输入数据文件夹")
        path = Path(value)
        if not path.is_dir():
            raise ValueError(f"输入数据文件夹不存在：{path}")
        return path

    def _dem_override(self) -> Path | None:
        value = self.dem_edit.text().strip()
        if not value:
            return None
        path = Path(value)
        if not path.is_file():
            raise ValueError(f"DEM 文件不存在：{path}")
        return path

    @staticmethod
    def _timezone_spinbox(value: float) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setRange(-12.0, 14.0)
        control.setDecimals(2)
        control.setSingleStep(0.25)
        control.setValue(value)
        control.setPrefix("UTC+" if value >= 0 else "UTC")
        control.valueChanged.connect(lambda offset: control.setPrefix("UTC+" if offset >= 0 else "UTC"))
        return control

    def _schedule_time_policy_refresh(self, *_args: object) -> None:
        if self._updating_time_controls or not self.input_edit.text().strip():
            return
        self._time_refresh_timer.start()

    def _refresh_display_timezone(self, *_args: object) -> None:
        values = [
            self._display_time(parse_time_range_utc((utc, utc), 0)[0])
            for utc in self._time_lookup.values()
        ]
        self._set_time_options(values)

    def _time_policy(self, *, allow_unselected: bool = False) -> InstrumentTimePolicy:
        uav_field = self.uav_time_field.currentData()
        windmaster_field = self.windmaster_time_field.currentData()
        if not allow_unselected and self.uav_time_field.count() and not isinstance(uav_field, str):
            raise ValueError("请扫描后选择 UAV 时间字段；缺失字段不会自动回退。")
        if not allow_unselected and self.windmaster_time_field.count() and not isinstance(windmaster_field, str):
            raise ValueError("请扫描后选择 WindMaster 时间字段；缺失字段不会自动回退。")
        defaults = InstrumentTimePolicy()
        return InstrumentTimePolicy(
            uav_time_field=uav_field if isinstance(uav_field, str) else defaults.uav_time_field,
            uav_timezone_offset_hours=self.uav_source_timezone.value(),
            windmaster_time_field=(
                windmaster_field if isinstance(windmaster_field, str) else defaults.windmaster_time_field
            ),
            windmaster_timezone_offset_hours=self.windmaster_source_timezone.value(),
            dat_timezone_offset_hours=self.dat_source_timezone.value(),
        )

    def _parameters(self):
        if self._time_scans or self._time_refresh_timer.isActive() or not self.refresh_times_button.isEnabled():
            raise ValueError("时间策略正在刷新，请等待扫描完成后再运行或验证。")
        heights = [int(value) for value in self.heights.text().replace(",", " ").split()]
        resolutions = [int(value) for value in self.resolutions.text().replace(",", " ").split()]
        if not heights or any(value <= 0 for value in heights):
            raise ValueError("至少填写一个正整数高度")
        if not resolutions or any(value <= 0 for value in resolutions):
            raise ValueError("至少填写一个正整数分辨率")
        time_range = None
        start_display = self.start.currentText().strip()
        end_display = self.end.currentText().strip()
        start = self._time_lookup.get(start_display, start_display)
        end = self._time_lookup.get(end_display, end_display)
        if start or end:
            if not start or not end:
                raise ValueError("开始时间和结束时间必须同时填写")
            time_range = parse_time_range_utc((start, end), 0)
        manual_bounds = parse_bounds(self.bounds.text().replace(",", " ").split()) if self.bounds.text().strip() else None
        return heights, resolutions, self.buffer.value(), time_range, manual_bounds

    def _display_time(self, value: datetime) -> tuple[str, str]:
        utc_value = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
        local_value = utc_value.astimezone(timezone(timedelta(hours=self.display_timezone.value())))
        return (
            local_value.strftime("%Y-%m-%d %H:%M") + f" (UTC{self.display_timezone.value():+g})",
            utc_value.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _set_time_options(self, values: list[tuple[str, str]]) -> None:
        selected_start = self.start.currentText()
        selected_end = self.end.currentText()
        selected_utc = {
            "开始": self._time_lookup.get(selected_start),
            "结束": self._time_lookup.get(selected_end),
        }
        self._time_lookup = dict(values)
        displays = [display for display, _ in values]
        for label, combo, selected in (
            ("开始", self.start, selected_start),
            ("结束", self.end, selected_end),
        ):
            combo.clear()
            combo.addItems(displays)
            previous_utc = selected_utc[label]
            if previous_utc and previous_utc in self._time_lookup.values():
                combo.setCurrentIndex(next(
                    index for index, (_, utc) in enumerate(values) if utc == previous_utc
                ))
            else:
                combo.setCurrentIndex(-1)
                if previous_utc:
                    self.log_view.appendPlainText(
                        f"时间策略已改变，已清空{label}时间（原选择 {previous_utc} 不在新时间轴中）。"
                    )
        self.log_view.appendPlainText(
            f"已加载 {len(displays)} 个观测时刻（显示 UTC{self.display_timezone.value():+g}）。"
        )

    def refresh_observation_times(self) -> None:
        self._time_refresh_timer.stop()
        if self._time_scans:
            self._time_scan_pending = True
            self.refresh_times_button.setEnabled(False)
            return
        try:
            input_dir = self._input_dir()
            policy = self._time_policy(allow_unselected=True)
            instrument_policy = self._instrument_policy(time_policy=policy)
        except Exception as error:
            self._failed(f"读取观测时间失败：{type(error).__name__}: {error}")
            return
        self._time_scan_generation += 1
        generation = self._time_scan_generation
        self.refresh_times_button.setEnabled(False)

        def scan() -> tuple[dict[str, object], list[datetime]]:
            report = scan_instrument_time_candidates(input_dir, policy=policy)
            sources = report.get("sources", {})
            selected_times = (
                value
                for source in sources.values() if isinstance(sources, dict) and isinstance(source, dict)
                for value in source.get("selected_utc_times", [])
            )
            values = [
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                for value in selected_times
                if isinstance(value, str)
            ]
            return report, values

        thread = QThread(self)
        worker = Worker(scan)
        self._time_scan_thread = thread
        self._time_scan_worker = worker
        self._time_scans.append((thread, worker))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(lambda result: self._time_scan_result.emit(result, generation))
        worker.failed.connect(lambda error: self._time_scan_error.emit(error, generation))
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._time_scan_completion.emit(thread, generation))
        thread.start()

    @Slot(str, int)
    def _time_scan_failed(self, error: str, generation: int) -> None:
        if generation == self._time_scan_generation:
            self._failed(f"读取观测时间失败：{error}")

    @Slot(object, int)
    def _time_scan_finished(self, thread: QThread, generation: int) -> None:
        finished = [(item_thread, item_worker) for item_thread, item_worker in self._time_scans if item_thread is thread]
        self._time_scans = [(item_thread, item_worker) for item_thread, item_worker in self._time_scans if item_thread is not thread]
        self._finished_time_scans.extend(finished)
        if generation == self._time_scan_generation:
            self._time_scan_thread = None
            if self._time_scan_pending:
                self._time_scan_pending = False
                QTimer.singleShot(0, self.refresh_observation_times)
            else:
                self.refresh_times_button.setEnabled(True)

    @Slot(object, int)
    def _apply_time_scan(self, result: object, generation: int) -> None:
        if generation != self._time_scan_generation:
            return
        report, values = result
        if not isinstance(report, dict) or not isinstance(values, list):
            raise TypeError("时间扫描返回格式无效")
        self._updating_time_controls = True
        try:
            sources = report.get("sources", {})
            if not isinstance(sources, dict):
                raise ValueError("时间扫描未返回观测源信息")
            self._set_time_field_options(self.uav_time_field, sources.get("uav"), "UAV")
            self._set_time_field_options(self.windmaster_time_field, sources.get("windmaster"), "WindMaster")
        finally:
            self._updating_time_controls = False
        self._set_time_options(sorted({self._display_time(value) for value in values}))
        summary = self._format_time_scan_summary(sources)
        self.time_scan_summary.setPlainText(summary)
        self.time_overlap_status.setText(next(
            (line for line in summary.splitlines() if line.startswith(("警告", "观测源 UTC 范围交集"))),
            "未发现两个可比较的观测源时间范围。",
        ))

    @staticmethod
    def _set_time_field_options(combo: QComboBox, source: object, label: str) -> None:
        source_data = source if isinstance(source, dict) else {}
        candidates = source_data.get("candidates", [])
        selected = source_data.get("selected_field")
        previous = combo.currentData()
        blocker = QSignalBlocker(combo)
        combo.clear()
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("field"), str):
                continue
            field = candidate["field"]
            count = candidate.get("value_count", 0)
            combo.addItem(f"{candidate.get('raw_field', field)}（{count} 条）", field)
        target = previous if isinstance(previous, str) else selected
        index = combo.findData(target)
        combo.setCurrentIndex(index)
        if index < 0:
            combo.setToolTip(f"{label} 尚无可用的已选时间字段；请明确选择有效字段。")
        del blocker

    @staticmethod
    def _format_time_scan_summary(sources: dict[str, object]) -> str:
        lines: list[str] = []
        ranges: list[tuple[str, str, str]] = []
        for source_name, display_name in (("uav", "UAV"), ("windmaster", "WindMaster"), ("dat", "DAT"), ("generic_station", "通用站点")):
            source = sources.get(source_name)
            if not isinstance(source, dict):
                continue
            selected = source.get("selected_field")
            candidates = source.get("candidates", [])
            selected_candidate = next(
                (item for item in candidates if isinstance(item, dict) and item.get("field") == selected), None
            ) if isinstance(candidates, list) else None
            if not isinstance(selected_candidate, dict):
                lines.append(f"{display_name}: 未选择有效字段（不会自动回退）")
                continue
            raw_min, raw_max = selected_candidate.get("raw_min"), selected_candidate.get("raw_max")
            utc_min, utc_max = selected_candidate.get("utc_preview_min"), selected_candidate.get("utc_preview_max")
            missing = selected_candidate.get("missing_count", 0)
            invalid = selected_candidate.get("invalid_count", 0)
            anomalies = selected_candidate.get("anomalies", 0)
            lines.append(
                f"{display_name} / {selected_candidate.get('raw_field', selected)}: 原始 {raw_min} — {raw_max}; UTC {utc_min} — {utc_max}; "
                f"缺失 {missing}，无效 {invalid}，异常 {anomalies}"
            )
            if isinstance(utc_min, str) and isinstance(utc_max, str):
                ranges.append((display_name, utc_min, utc_max))
        if len(ranges) >= 2:
            intersection_start = max(item[1] for item in ranges)
            intersection_end = min(item[2] for item in ranges)
            if intersection_start <= intersection_end:
                lines.append(f"观测源 UTC 范围交集：{intersection_start} — {intersection_end}")
            else:
                lines.append("警告：所选观测源 UTC 时间范围不重叠；请检查字段和来源时区。")
        return "\n".join(lines) or "未发现可预览的仪器时间字段。"

    def _runtime_config(self):
        config = load_config()
        research_mode = self.mode.currentData() in {"turbulence", "assimilation", "native_assimilation"}
        executable = self.wind_edit.text().strip()
        windninja = replace(
            config.windninja,
            vegetation=self.vegetation.currentText(),
            num_threads=self.threads.value(),
            diurnal_winds=self.diurnal.isChecked() if not research_mode else False,
            non_neutral_stability=self.non_neutral.isChecked() if not research_mode else False,
            alpha_stability=float(self.alpha.text()) if not research_mode and self.alpha.text().strip() else None,
            input_wind_height_m=float(self.input_height.text()) if self.input_height.text().strip() else None,
            station_radius_of_influence_km=self.radius.value(),
            output_buffer_clipping_pct=self.clip.value(),
            turbulence_output=False,
        )
        return replace(
            config,
            windninja_exe=Path(executable) if executable else config.windninja_exe,
            windninja=windninja,
        )

    def _mode_changed(self) -> None:
        turbulence_mode = self.mode.currentData() == "turbulence"
        assimilation_mode = self.mode.currentData() == "assimilation"
        native_mode = self.mode.currentData() == "native_assimilation"
        research_mode = turbulence_mode or assimilation_mode or native_mode
        manual_mode = turbulence_mode and self.background_source.currentData() == "manual"
        self.background_box.setVisible(turbulence_mode)
        self.native_input_box.setVisible(native_mode)
        self.station_input_box.setVisible(assimilation_mode)
        self.input_label.setText("工作文件夹（可为空文件夹）" if manual_mode else "输入数据文件夹（必选）")
        self.dat_source_timezone.setVisible(not research_mode)
        self.time_policy_form.labelForField(self.dat_source_timezone).setVisible(not research_mode)
        from .mode_help import MODE_HELP, render_help
        help_key = "manual" if manual_mode else self.mode.currentData()
        guide = MODE_HELP[help_key]
        self.mode_hint.setText(guide["purpose"])
        self.data_requirements.setText("需要准备：" + guide["inputs"])
        self.help_view.setHtml(render_help(help_key))
        self.bounds_label.setText("区域范围（必填：最小纬度 最大纬度 最小经度 最大经度）" if manual_mode
                                  else "手动区域范围（可选；留空按观测位置确定）")
        self.profile_options.setVisible(research_mode)
        self.active_steps = [0, 3] if manual_mode else ([0, 1, 2, 3] if research_mode else [0, 1, 3])
        signature = (self.mode.currentData(), manual_mode)
        step = 0 if signature != self._workflow_signature else self.step_stack.currentIndex()
        self._workflow_signature = signature
        self._set_step(step)
        self.inspect_button.setVisible(not research_mode)
        self.plan_button.setVisible(not research_mode)
        self.turbulence_options.setVisible(research_mode)
        self.background_source.setEnabled(turbulence_mode)
        self.manual_background.setVisible(manual_mode)
        self.assimilation_options.setVisible(assimilation_mode)
        self.native_options.setVisible(native_mode)
        self.research_options.setVisible(research_mode and not manual_mode)
        self.start.setEnabled(not manual_mode)
        self.end.setEnabled(not manual_mode)
        if assimilation_mode and not self._assimilation_defaults_applied:
            if self.heights.text().strip() == "10 20 50":
                self.heights.setText("50")
            if self.resolutions.text().strip() == "50 100 200":
                self.resolutions.setText("200")
            self._assimilation_defaults_applied = True
        if native_mode and not self._native_defaults_applied:
            if self.heights.text().strip() == "10 20 50":
                self.heights.setText("50")
            if self.resolutions.text().strip() == "50 100 200":
                self.resolutions.setText("200")
            self._native_defaults_applied = True
        if research_mode:
            self.diurnal.setChecked(False)
            self.non_neutral.setChecked(False)
            self.alpha.clear()
        explanation = self.rans_compatibility_hint.text() if research_mode else ""
        for control in (self.diurnal, self.non_neutral, self.alpha):
            control.setEnabled(not research_mode and self.advanced_options.isChecked())
            control.setToolTip(explanation)
        self.rans_compatibility_hint.setVisible(research_mode)
        self.inspect_button.setEnabled(not research_mode)
        self.plan_button.setEnabled(not research_mode)
        if turbulence_mode:
            self.run_button.setText("开始湍流 RANS 运行")
        elif native_mode:
            self.run_button.setText("开始原生RANS耦合同化")
        elif assimilation_mode:
            self.run_button.setText("开始逐站观测同化")
        else:
            self.run_button.setText("开始常规运行")

    def _turbulence_parameters(self) -> dict[str, object]:
        product = self.turbulence_product.text().strip()
        return {
            "max_cases": self.max_cases.value(),
            "validation_window_seconds": self.validation_window.value(),
            "selected_product": product or None,
            "non_neutral_stability": False,
            "background_max_height_m": self.background_max_height.value(),
            "validation_height_tolerance_m": self.validation_height_tolerance.value(),
            "reference_height_m": self.reference_height.value(),
            "roughness_m": self.roughness.value(),
            "displacement_height_m": self.displacement_height.value(),
            "allow_profile_extrapolation": self.allow_profile_extrapolation.isChecked(),
            "instrument_policy": self._instrument_policy(require_uav_height=True),
        }

    def _optional_float(self, edit: QLineEdit, label: str) -> float | None:
        value = edit.text().strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError as error:
            raise ValueError(f"{label}必须是数字") from error

    def _instrument_policy(
        self,
        require_uav_height: bool = False,
        time_policy: InstrumentTimePolicy | None = None,
    ) -> InstrumentPolicy:
        launch_lat = self._optional_float(self.uav_launch_lat, "UAV 起飞点纬度")
        launch_lon = self._optional_float(self.uav_launch_lon, "UAV 起飞点经度")
        if (launch_lat is None) != (launch_lon is None):
            raise ValueError("UAV 起飞点纬度和经度必须同时填写")
        uav_height_reference = UavHeightReference(self.uav_height_reference.currentData())
        if require_uav_height and uav_height_reference is UavHeightReference.UNKNOWN:
            raise ValueError("UAV 高度定义未知；请选择 GPS 高程减 DEM、相对起飞点或已确认的相对 AGL")
        if require_uav_height and RadarHeightReference(self.windmaster_height_reference.currentData()) is RadarHeightReference.UNKNOWN:
            raise ValueError("WindMaster 高度基准未知；请选择已确认的 AGL 或平台垂直偏移")
        return InstrumentPolicy(
            windmaster_direction_reference=WindDirectionReference(
                self.windmaster_direction.currentData()
            ),
            uav_direction_reference=WindDirectionReference(self.uav_direction.currentData()),
            windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
            uav_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
            windmaster_height_reference=RadarHeightReference(
                self.windmaster_height_reference.currentData()
            ),
            windmaster_platform_agl_m=self._optional_float(self.windmaster_platform_agl, "WindMaster 平台对地高度"),
            windmaster_is_geographic_wind=True,
            windmaster_motion_compensated=True,
            uav_is_geographic_wind=True,
            uav_motion_compensated=True,
            uav_height_reference=uav_height_reference,
            uav_relative_agl_confirmed=(
                uav_height_reference is UavHeightReference.RELATIVE_AGL
            ),
            uav_legacy_gps_is_launch_altitude_confirmed=(
                self.uav_legacy_gps_is_launch_altitude_confirmed.isChecked()
            ),
            uav_launch_ground_altitude_m=self._optional_float(
                self.uav_launch_ground_altitude, "UAV 起点地面海拔"
            ),
            uav_launch_lat=launch_lat,
            uav_launch_lon=launch_lon,
            uav_launch_location_confirmed=launch_lat is not None and launch_lon is not None,
            canopy_height_m=self.canopy_height.value(),
            time_policy=time_policy or self._time_policy(),
        )

    def _assimilation_parameters(self) -> dict[str, object]:
        station_value = self.station_observations_edit.text().strip()
        station_observations = Path(station_value) if station_value else None
        if station_observations is not None and not station_observations.is_file():
            raise ValueError(f"附加固定站点 CSV 不存在：{station_observations}")
        product = self.turbulence_product.text().strip()
        return {
            "station_observations": station_observations,
            "horizontal_length_scale_m": self.horizontal_length_scale.value(),
            "vertical_length_scale_m": self.vertical_length_scale.value(),
            "background_wind_sigma_ms": self.background_wind_sigma.value(),
            "observation_wind_sigma_ms": self.observation_wind_sigma.value(),
            "max_cases": self.max_cases.value(),
            "validation_window_seconds": self.validation_window.value(),
            "selected_product": product or None,
            "non_neutral_stability": False,
            "background_max_height_m": self.background_max_height.value(),
            "validation_height_tolerance_m": self.validation_height_tolerance.value(),
            "reference_height_m": self.reference_height.value(),
            "roughness_m": self.roughness.value(),
            "displacement_height_m": self.displacement_height.value(),
            "allow_profile_extrapolation": self.allow_profile_extrapolation.isChecked(),
            "instrument_policy": self._instrument_policy(require_uav_height=True),
        }

    def _assimilation_runner(self):
        global AssimilationBatchRunner
        if AssimilationBatchRunner is None:
            from ..pipeline.assimilation import AssimilationBatchRunner as runner
            AssimilationBatchRunner = runner
        return AssimilationBatchRunner

    def _start(self, action: Callable[[Callable[[str], None]], object]) -> None:
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(self, "正在运行", "请等待当前操作完成。")
            return
        self.pages.setCurrentWidget(self.log_view)
        self.progress.show()
        self.log_view.appendPlainText("开始…")
        self.mode.setEnabled(False)
        for button in self.action_buttons:
            button.setEnabled(False)
        self.thread = QThread(self)
        worker: Worker

        def action_with_log() -> object:
            return action(worker.log.emit)

        worker = Worker(action_with_log)
        self.worker = worker
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.log.connect(self.log_view.appendPlainText)
        worker.done.connect(self._done)
        worker.failed.connect(self._failed)
        worker.finished.connect(self.thread.quit)
        worker.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    def _start_prepared(
        self,
        prepare: Callable[[], Callable[[Callable[[str], None]], object]],
    ) -> None:
        try:
            action = prepare()
        except Exception as error:
            self._failed(f"{type(error).__name__}: {error}")
            return
        self._start(action)

    @Slot(object)
    def _done(self, value: object) -> None:
        message = json.dumps(value, ensure_ascii=False, indent=2, default=str) if isinstance(value, (dict, list)) else str(value)
        self.log_view.appendPlainText(message)

    @Slot(str)
    def _failed(self, error: str) -> None:
        self.log_view.appendPlainText(f"错误：{error}")
        QMessageBox.critical(self, "运行失败", error)

    @Slot()
    def _thread_finished(self) -> None:
        self.progress.hide()
        for button in self.action_buttons:
            button.setEnabled(True)
        self.mode.setEnabled(True)
        self._mode_changed()
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None

    def closeEvent(self, event) -> None:
        self._time_refresh_timer.stop()
        if self._time_scans:
            QMessageBox.information(self, "时间扫描中", "请等待时间扫描完成后再关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)

    def doctor(self) -> None:
        def prepare() -> Callable[[Callable[[str], None]], object]:
            config = self._runtime_config()
            return lambda _log: doctor_report(config)

        self._start_prepared(prepare)

    def inspect(self) -> None:
        def prepare() -> Callable[[Callable[[str], None]], object]:
            config = self._runtime_config()
            input_dir = self._input_dir()
            return lambda _log: InputInspector(config, self._instrument_policy()).inspect(input_dir)

        self._start_prepared(prepare)

    def plan(self) -> None:
        def prepare() -> Callable[[Callable[[str], None]], object]:
            heights, resolutions, buffer_km, time_range, bounds = self._parameters()
            config = self._runtime_config()
            input_dir = self._input_dir()
            dem_override = self._dem_override()
            return lambda _log: PipelinePlanner(config).plan(
                input_dir, heights, resolutions, buffer_km, bounds, time_range, dem_override,
                instrument_policy=self._instrument_policy(),
            )

        self._start_prepared(prepare)

    def run_pipeline(self) -> None:
        if self.mode.currentData() == "native_assimilation":
            self.run_native_assimilation()
            return
        if self.mode.currentData() == "turbulence":
            self.run_turbulence()
            return
        if self.mode.currentData() == "assimilation":
            self.run_assimilation()
            return

        def prepare() -> Callable[[Callable[[str], None]], object]:
            heights, resolutions, buffer_km, time_range, bounds = self._parameters()
            input_dir = self._input_dir()
            config = self._runtime_config()
            output_dir = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else input_dir / config.output.default_output_dir_name
            self.last_output_dir = output_dir
            generate_kmz = self.kmz.isChecked()
            dem_override = self._dem_override()
            return lambda log: PipelineRunner(config, progress_callback=log).run(
                input_dir, output_dir, heights, resolutions, buffer_km, bounds,
                generate_kmz, dem_override=dem_override, time_range=time_range,
                instrument_policy=self._instrument_policy(),
            ).as_dict()

        self._start_prepared(prepare)

    def run_turbulence(self) -> None:
        def prepare() -> Callable[[Callable[[str], None]], object]:
            if self.background_source.currentData() == "manual":
                if not self.kmz.isChecked():
                    raise ValueError("手动背景湍流模式需要勾选 KMZ 输出")
                if not self.bounds.text().strip():
                    raise ValueError("手动背景湍流模式需要填写模拟范围")
                simulation_time = parse_explicit_time_utc(self.manual_time.text().strip())
                selected = (self.start.currentIndex(), self.end.currentIndex())
                self.start.setCurrentIndex(-1)
                self.end.setCurrentIndex(-1)
                try:
                    heights, resolutions, buffer_km, _, bounds = self._parameters()
                finally:
                    self.start.setCurrentIndex(selected[0])
                    self.end.setCurrentIndex(selected[1])
                input_dir = self._input_dir()
                output_dir = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else input_dir / "turbulence_output"
                self.last_output_dir = output_dir
                config = self._runtime_config()
                dem_override = self._dem_override()
                return lambda log: PipelineRunner(config, progress_callback=log).run(
                    input_dir, output_dir, heights, resolutions, buffer_km, bounds, True,
                    dem_override=dem_override, solver_mode="turbulence",
                    background_speed_ms=self.manual_speed.value(),
                    background_direction_deg=self.manual_direction.value(),
                    background_height_m=self.manual_height.value(), simulation_time=simulation_time,
                ).as_dict()
            heights, resolutions, buffer_km, time_range, bounds = self._parameters()
            input_dir = self._input_dir()
            config = self._runtime_config()
            output_dir = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else input_dir / "turbulence_output"
            self.last_output_dir = output_dir
            generate_kmz = self.kmz.isChecked()
            dem_override = self._dem_override()
            turbulence_parameters = self._turbulence_parameters()
            return lambda log: TurbulenceBatchRunner(config, progress_callback=log).run(
                input_dir,
                output_dir,
                heights,
                resolutions,
                buffer_km,
                bounds,
                generate_kmz,
                time_range=time_range,
                dem_override=dem_override,
                **turbulence_parameters,
            ).as_dict()

        self._start_prepared(prepare)

    def run_assimilation(self) -> None:
        def prepare() -> Callable[[Callable[[str], None]], object]:
            heights, resolutions, buffer_km, time_range, bounds = self._parameters()
            input_dir = self._input_dir()
            config = self._runtime_config()
            output_dir = (
                Path(self.output_edit.text().strip())
                if self.output_edit.text().strip()
                else input_dir / "assimilation_output"
            )
            self.last_output_dir = output_dir
            generate_kmz = self.kmz.isChecked()
            dem_override = self._dem_override()
            assimilation_parameters = self._assimilation_parameters()
            runner = self._assimilation_runner()
            return lambda log: runner(config, progress_callback=log).run(
                input_dir,
                output_dir,
                heights,
                resolutions,
                buffer_km,
                bounds,
                generate_kmz,
                time_range=time_range,
                dem_override=dem_override,
                **assimilation_parameters,
            ).as_dict()

        self._start_prepared(prepare)

    def run_native_assimilation(self) -> None:
        def prepare():
            heights, resolutions, buffer_km, time_range, bounds = self._parameters()
            input_dir = self._input_dir()
            config = self._runtime_config()
            value = self.native_station_observations_edit.text().strip()
            if not value or not Path(value).is_file():
                raise ValueError("原生耦合同化必须提供存在的固定站点 CSV")
            runtime = self.native_runtime_edit.text().strip()
            if runtime and not Path(runtime).is_dir():
                raise ValueError("原生 runtime 文件夹不存在")
            parameters = self._turbulence_parameters()
            parameters.update(station_observations=Path(value), native_runtime=Path(runtime) if runtime else None,
                              max_iterations=self.native_max_iterations.value(), residual_tolerance=self.native_residual_tolerance.value())
            for name in ("horizontal_radius_m", "vertical_radius_m", "velocity_relaxation_seconds", "tke_relaxation_seconds", "epsilon_relaxation_seconds"):
                parameters[name] = getattr(self, "native_" + name).value()
            output_dir = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else input_dir / "native_output"
            generate_kmz = self.kmz.isChecked()
            dem_override = self._dem_override()
            global NativeCoupledBatchRunner
            if NativeCoupledBatchRunner is None:
                from ..pipeline.native_assimilation import NativeCoupledBatchRunner as runner
                NativeCoupledBatchRunner = runner
            runner = NativeCoupledBatchRunner
            self.last_output_dir = output_dir
            return lambda log: runner(config, progress_callback=log).run(
                input_dir, output_dir, heights, resolutions, buffer_km, bounds, generate_kmz,
                time_range=time_range, dem_override=dem_override, **parameters,
            ).as_dict()
        self._start_prepared(prepare)

    def open_output(self) -> None:
        path = self.last_output_dir
        if path is None:
            value = self.output_edit.text().strip()
            if value:
                path = Path(value)
            elif self.input_edit.text().strip():
                path = Path(self.input_edit.text().strip()) / self.config.output.default_output_dir_name
        if path is None or not path.exists():
            QMessageBox.information(self, "输出文件夹", "输出文件夹尚未生成。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
