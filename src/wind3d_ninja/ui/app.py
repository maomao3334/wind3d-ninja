from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

from ..cli.doctor import doctor_report
from ..config.loader import load_config
from ..manifest.builder import normalize
from ..pipeline.inspector import InputInspector
from ..pipeline.planner import PipelinePlanner
from ..pipeline.runner import PipelineRunner
from ..utils.geometry import parse_bounds
from ..utils.timezone import parse_time_range_utc


class Wind3DApp(tk.Tk):
    """Windows desktop frontend using the standard-library Tk runtime."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Wind3D Ninja")
        self.geometry("1040x820")
        self.minsize(900, 700)
        self.base_config = load_config()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.action_buttons: list[ttk.Button] = []
        self.last_output_dir: Path | None = None
        self._time_options: list[str] = []
        self._time_lookup: dict[str, str] = {}
        self._create_variables()
        self._build_ui()
        self.after(100, self._poll_events)

    def _create_variables(self) -> None:
        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.dem_path = tk.StringVar()
        self.windninja_path = tk.StringVar(value=str(self.base_config.windninja_exe))
        self.heights = tk.StringVar(value="10 20 50")
        self.resolutions = tk.StringVar(value="50 100 200")
        self.buffer_km = tk.StringVar(value="10")
        self.start_time = tk.StringVar()
        self.end_time = tk.StringVar()
        self.bounds = tk.StringVar()
        self.generate_kmz = tk.BooleanVar(value=True)
        self.vegetation = tk.StringVar(value=self.base_config.windninja.vegetation)
        self.diurnal = tk.BooleanVar(value=False)
        self.non_neutral = tk.BooleanVar(value=False)
        self.alpha = tk.StringVar()
        self.input_height = tk.StringVar()
        self.radius = tk.StringVar(value="-1")
        self.clip = tk.StringVar(value="0")
        self.threads = tk.StringVar(value=str(self.base_config.windninja.num_threads))
        self.turbulence = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪")

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.X)
        basic_tab = ttk.Frame(notebook, padding=12)
        advanced_tab = ttk.Frame(notebook, padding=12)
        notebook.add(basic_tab, text="基本设置")
        notebook.add(advanced_tab, text="WindNinja 高级设置")

        self._path_row(
            basic_tab,
            0,
            "输入数据文件夹",
            self.input_dir,
            directory=True,
            on_selected=self._queue_time_refresh,
        )
        self._path_row(basic_tab, 1, "输出文件夹（可选）", self.output_dir, directory=True)
        self._path_row(
            basic_tab,
            2,
            "DEM 文件（可选）",
            self.dem_path,
            filetypes=[("DEM", "*.tif *.tiff *.asc *.img"), ("所有文件", "*.*")],
        )
        self._path_row(
            basic_tab,
            3,
            "WindNinja（安装包已内置）",
            self.windninja_path,
            filetypes=[("WindNinja", "WindNinja_cli.exe"), ("程序", "*.exe")],
        )
        self._entry_row(basic_tab, 4, "输出高度（米，空格分隔）", self.heights)
        self._entry_row(basic_tab, 5, "分辨率（米，空格分隔）", self.resolutions)
        self._entry_row(basic_tab, 6, "区域外扩 Buffer（公里）", self.buffer_km)
        self._time_row(basic_tab, 7, "开始时间（从已有观测选择，UTC+8）", self.start_time)
        self._time_row(basic_tab, 8, "结束时间（从已有观测选择，UTC+8）", self.end_time)
        ttk.Button(
            basic_tab,
            text="刷新可用时间",
            command=self._queue_time_refresh,
        ).grid(row=9, column=2, sticky=tk.W, padx=(8, 0), pady=5)
        self._entry_row(basic_tab, 10, "区域范围（可选）", self.bounds)
        ttk.Label(basic_tab, text="格式：min_lat max_lat min_lon max_lon").grid(
            row=11, column=1, sticky=tk.W, pady=(0, 6)
        )
        ttk.Checkbutton(basic_tab, text="生成 KMZ", variable=self.generate_kmz).grid(
            row=12, column=1, sticky=tk.W, pady=4
        )
        basic_tab.columnconfigure(1, weight=1)

        ttk.Label(advanced_tab, text="主导下垫面").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 10), pady=5
        )
        ttk.Combobox(
            advanced_tab,
            textvariable=self.vegetation,
            values=("trees", "grass", "brush"),
            state="readonly",
        ).grid(row=0, column=1, sticky=tk.EW, pady=5)
        ttk.Checkbutton(
            advanced_tab, text="启用日变化坡谷风", variable=self.diurnal
        ).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Checkbutton(
            advanced_tab, text="启用非中性大气稳定度", variable=self.non_neutral
        ).grid(row=2, column=1, sticky=tk.W, pady=5)
        self._entry_row(advanced_tab, 3, "alpha_stability（可选）", self.alpha)
        self._entry_row(advanced_tab, 4, "统一输入风高度（米，可选）", self.input_height)
        self._entry_row(advanced_tab, 5, "站点影响半径（公里，-1=自动）", self.radius)
        self._entry_row(advanced_tab, 6, "输出边缘裁剪（%）", self.clip)
        self._entry_row(advanced_tab, 7, "线程数", self.threads)
        ttk.Checkbutton(
            advanced_tab, text="输出 WindNinja 湍流结果", variable=self.turbulence
        ).grid(row=8, column=1, sticky=tk.W, pady=5)
        advanced_tab.columnconfigure(1, weight=1)

        actions = ttk.Frame(root, padding=(0, 10, 0, 6))
        actions.pack(fill=tk.X)
        for text, command in (
            ("环境检查 doctor", self.doctor),
            ("检查数据 inspect", self.inspect_data),
            ("预览计划 plan", self.plan),
            ("开始运行 run", self.run_pipeline),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.action_buttons.append(button)
        ttk.Button(actions, text="打开输出文件夹", command=self.open_output).pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(0, 6))
        self.log = scrolledtext.ScrolledText(root, height=22, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, textvariable=self.status, anchor=tk.W).pack(fill=tk.X, pady=(5, 0))

    @staticmethod
    def _entry_row(parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky=tk.EW, pady=5)

    def _time_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=5)
        combo = ttk.Combobox(
            parent,
            textvariable=variable,
            values=self._time_options,
            state="readonly",
        )
        combo.grid(row=row, column=1, sticky=tk.EW, pady=5)
        if variable is self.start_time:
            self._start_combo = combo
        else:
            self._end_combo = combo

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        directory: bool = False,
        filetypes: list[tuple[str, str]] | None = None,
        on_selected: Callable[[], None] | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=5)
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky=tk.EW, pady=5)
        ttk.Entry(frame, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def browse() -> None:
            selected = (
                filedialog.askdirectory(title=label)
                if directory
                else filedialog.askopenfilename(
                    title=label, filetypes=filetypes or [("所有文件", "*.*")]
                )
            )
            if selected:
                variable.set(selected)
                if on_selected is not None:
                    on_selected()

        ttk.Button(frame, text="浏览…", command=browse).pack(side=tk.LEFT, padx=(6, 0))

    def _collect_common(self):
        input_text = self.input_dir.get().strip()
        input_path = Path(input_text)
        if not input_text or not input_path.is_dir():
            raise ValueError("请选择有效的输入数据文件夹")
        heights = [int(value) for value in self.heights.get().replace(",", " ").split()]
        resolutions = [
            int(value) for value in self.resolutions.get().replace(",", " ").split()
        ]
        if not heights or any(value <= 0 for value in heights):
            raise ValueError("输出高度必须是正整数")
        if not resolutions or any(value <= 0 for value in resolutions):
            raise ValueError("分辨率必须是正整数")
        start_display, end_display = self.start_time.get().strip(), self.end_time.get().strip()
        start = self._time_lookup.get(start_display, start_display)
        end = self._time_lookup.get(end_display, end_display)
        if bool(start) != bool(end):
            raise ValueError("开始时间和结束时间必须同时选择")
        time_range = parse_time_range_utc((start, end), 8) if start else None
        bounds = (
            parse_bounds(self.bounds.get().replace(",", " ").split())
            if self.bounds.get().strip()
            else None
        )
        dem = Path(self.dem_path.get().strip()) if self.dem_path.get().strip() else None
        if dem is not None and not dem.is_file():
            raise ValueError(f"DEM 文件不存在：{dem}")
        return (
            input_path,
            heights,
            resolutions,
            float(self.buffer_km.get()),
            time_range,
            bounds,
            dem,
        )

    @staticmethod
    def _display_time(value: datetime) -> tuple[str, str]:
        utc_value = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
        local_value = utc_value.astimezone(timezone(timedelta(hours=8)))
        return (
            local_value.strftime("%Y-%m-%d %H:%M"),
            utc_value.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _queue_time_refresh(self) -> None:
        input_text = self.input_dir.get().strip()
        input_path = Path(input_text)
        if not input_text or not input_path.is_dir():
            self._set_time_options([])
            return
        if self.worker is not None and self.worker.is_alive():
            return
        self.status.set("正在读取观测时间…")

        def load_times() -> list[tuple[str, str]]:
            loaded = InputInspector(self.base_config).load(input_path)
            raw_uav = [record for _, records in loaded.uav_inputs for record in records]
            values = [item.time_utc for item in loaded.fixed_observations]
            values.extend(record.time_utc for record in raw_uav)
            values.extend(item.time_utc for item in loaded.generic_observations)
            return sorted({self._display_time(value) for value in values})

        def execute() -> None:
            try:
                self.events.put(("times", load_times()))
            except Exception as error:
                self.events.put(("time_error", f"读取观测时间失败：{type(error).__name__}: {error}"))
            finally:
                self.events.put(("time_finished", None))

        self.worker = threading.Thread(target=execute, daemon=True)
        self.worker.start()

    def _set_time_options(self, values: list[tuple[str, str]]) -> None:
        self._time_options = [display for display, _ in values]
        self._time_lookup = dict(values)
        for combo in (
            getattr(self, "_start_combo", None),
            getattr(self, "_end_combo", None),
        ):
            if combo is not None:
                combo.configure(values=self._time_options)
        if self.start_time.get() not in self._time_options:
            self.start_time.set("")
        if self.end_time.get() not in self._time_options:
            self.end_time.set("")
        self.status.set(f"已加载 {len(self._time_options)} 个观测时刻")

    def _runtime_config(self):
        config = load_config()
        executable = Path(self.windninja_path.get().strip())
        if not executable.is_file():
            raise ValueError(f"WindNinja 程序不存在：{executable}")
        windninja = replace(
            config.windninja,
            vegetation=self.vegetation.get(),
            num_threads=int(self.threads.get()),
            diurnal_winds=self.diurnal.get(),
            non_neutral_stability=self.non_neutral.get(),
            alpha_stability=float(self.alpha.get()) if self.alpha.get().strip() else None,
            input_wind_height_m=(
                float(self.input_height.get()) if self.input_height.get().strip() else None
            ),
            station_radius_of_influence_km=float(self.radius.get()),
            output_buffer_clipping_pct=float(self.clip.get()),
            turbulence_output=self.turbulence.get(),
        )
        return replace(config, windninja_exe=executable, windninja=windninja)

    def _start_task(self, label: str, action: Callable[[], object]) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("正在运行", "请等待当前任务完成。")
            return
        self.status.set(label)
        self.progress.start(12)
        for button in self.action_buttons:
            button.configure(state=tk.DISABLED)

        def execute() -> None:
            try:
                self.events.put(("result", action()))
            except Exception as error:
                self.events.put(("error", f"{type(error).__name__}: {error}"))
            finally:
                self.events.put(("finished", None))

        self.worker = threading.Thread(target=execute, daemon=True)
        self.worker.start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "result":
                    self._append_log(
                        json.dumps(normalize(value), ensure_ascii=False, indent=2)
                    )
                elif kind == "error":
                    self._append_log(f"错误：{value}")
                    messagebox.showerror("运行失败", str(value))
                elif kind == "times":
                    self._set_time_options(value)  # type: ignore[arg-type]
                elif kind == "time_error":
                    self._append_log(str(value))
                    messagebox.showerror("时间读取失败", str(value))
                elif kind == "finished":
                    self.progress.stop()
                    self.status.set("就绪")
                    for button in self.action_buttons:
                        button.configure(state=tk.NORMAL)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _append_log(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message.rstrip() + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def doctor(self) -> None:
        try:
            config = self._runtime_config()
        except Exception as error:
            messagebox.showerror("参数错误", str(error))
            return
        self._start_task("正在检查环境…", lambda: doctor_report(config))

    def inspect_data(self) -> None:
        try:
            input_path = self._collect_common()[0]
            config = self._runtime_config()
        except Exception as error:
            messagebox.showerror("参数错误", str(error))
            return
        self._start_task(
            "正在检查数据…", lambda: InputInspector(config).inspect(input_path)
        )

    def plan(self) -> None:
        try:
            input_path, heights, resolutions, buffer_km, time_range, bounds, dem = (
                self._collect_common()
            )
            config = self._runtime_config()
        except Exception as error:
            messagebox.showerror("参数错误", str(error))
            return
        self._start_task(
            "正在生成计划…",
            lambda: PipelinePlanner(config).plan(
                input_path, heights, resolutions, buffer_km, bounds, time_range, dem
            ),
        )

    def run_pipeline(self) -> None:
        try:
            input_path, heights, resolutions, buffer_km, time_range, bounds, dem = (
                self._collect_common()
            )
            config = self._runtime_config()
            output_path = (
                Path(self.output_dir.get().strip())
                if self.output_dir.get().strip()
                else input_path / config.output.default_output_dir_name
            )
            self.last_output_dir = output_path
            kmz = self.generate_kmz.get()
        except Exception as error:
            messagebox.showerror("参数错误", str(error))
            return

        def operation() -> object:
            return PipelineRunner(
                config,
                progress_callback=lambda message: self.events.put(("log", message)),
            ).run(
                input_path,
                output_path,
                heights,
                resolutions,
                buffer_km,
                bounds,
                kmz,
                dem_override=dem,
                time_range=time_range,
            ).as_dict()

        self._start_task("正在运行 WindNinja…", operation)

    def open_output(self) -> None:
        path = self.last_output_dir
        if path is None and self.output_dir.get().strip():
            path = Path(self.output_dir.get().strip())
        if path is None and self.input_dir.get().strip():
            path = Path(self.input_dir.get().strip()) / self.base_config.output.default_output_dir_name
        if path is None or not path.exists():
            messagebox.showinfo("输出文件夹", "输出文件夹尚未生成。")
            return
        os.startfile(path.resolve())


def main() -> int:
    app = Wind3DApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
