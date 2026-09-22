# wind3d-ninja

`wind3d-ninja` 是基于 WindNinja Point Initialization 的多源观测风场降尺度 CLI。

它自动识别 WindMaster ZIP、超声波 DAT、UAV XLS/XLSX 和 DEM，自动生成全部 UTC 分钟时间轴，并对每个“时间 × 高度 × 分辨率”执行独立 WindNinja 运行。

## 核心行为

1. 时间轴自动从全部有效观测中提取，正式运行不输入时间。
2. UAV 高度严格按 `(GPS 当前高度 + 对地高度) - DEM 高程` 转换为 AGL；DEM 会在 `plan` 阶段优先匹配或自动下载。
3. 高度支持 `--height 10 20 50` 的空格分隔写法。
4. DEM 优先从输入目录自动匹配；缺失或覆盖不足时自动下载 SRTM。
5. `--output` 可指定输出目录；不写时输出到输入目录下的 `wind3d_output`。
6. 支持一个或多个分辨率，以及自动缓冲区或手动覆盖范围。
7. NetCDF 只保存 `U_wind` 和 `V_wind`；KMZ 默认同时生成。
8. 每个目标时刻会将时间窗口内所有来源、所有有效高度的站点数据输入 WindNinja，再分别计算用户指定的输出高度层；不会只挑选某一个“优选高度”。

## UAV 高度优化算法

UAV 数据集必须包含 GPS 当前高度、对地高度、经纬度和时间；DEM 由 `plan` 阶段解析后传入 UAV 读取器。

对每条 UAV 记录计算：

```text
AGL_raw = (GPS 当前高度 + 对地高度) - DEM 高程
```

应用质量规则：

```text
AGL_raw < -4 m          → 剔除
-4 m <= AGL_raw <= 0 m → AGL = 0.1 m
AGL_raw > 0 m           → AGL = AGL_raw
```

manifest 的观测元数据会保留当前海拔、对地高度、DEM 高程、原始 AGL 和高度算法名称，便于追溯。

## Git Bash 安装

```bash
git clone https://github.com/maomao3334/wind3d-ninja.git
cd wind3d-ninja
py -3.11 -m pip install -e .
wind3d-ninja doctor --install
wind3d-ninja --help
```

如果当前 Git Bash 还没有识别新安装的命令，可以直接使用模块入口，例如：

```bash
py -3.11 -m wind3d_ninja doctor
```

## WindNinja 运行时

项目固定使用 WindNinja `3.12.2`。第一次安装本项目后只需执行：

```bash
wind3d-ninja doctor --install
```

该命令会从 FireLab 官方地址下载 Windows 64 位安装包、校验固定 SHA256，然后静默安装到：

```text
~/.wind3d-ninja/windninja/3.12.2/
```

官方安装器需要管理员批准；Windows 弹出 UAC 对话框时点击“是”即可。

如果不想安装到用户主目录，可在 Git Bash 中先指定其他磁盘：

```bash
export WINDNINJA_HOME="/path/to/WindNinja-3.12.2"
wind3d-ninja doctor --install
```

已经安装过 WindNinja 时，可以直接指定现有可执行文件：

```bash
export WINDNINJA_BIN="/path/to/WindNinja_cli.exe"
```

`wind3d-ninja doctor` 会检查最终解析到的可执行文件。源码维护在 [maomao3334/windninja](https://github.com/maomao3334/windninja) fork；自动安装使用 FireLab 官方预编译包，普通用户无需编译 C++。

## Windows 最终安装包

Windows 用户直接下载 GitHub Releases 中的 `Wind3D-Ninja-Setup-1.0.0-x64.exe`，双击安装即可。安装包已经包含 WindNinja 3.12.2、CLI、桌面 UI 和运行依赖，不需要另外安装 Python 或 WindNinja。

安装完成后可以从开始菜单启动 UI。CLI 位于安装目录下的 `cli/wind3d-ninja.exe`；Git Bash 示例：

```bash
CLI="$HOME/AppData/Local/Programs/Wind3D-Ninja/cli/wind3d-ninja.exe"
"$CLI" doctor
"$CLI" run ./input_data --height 10 20 50 --resolution 100
```

安装器构建脚本不会保存任何访问令牌、用户名或本机绝对路径；如果从源码重建安装器，需要先设置 `WINDNINJA_SOURCE` 指向本地 WindNinja 3.12.2 目录。

## 四个命令

```bash
wind3d-ninja doctor --install
wind3d-ninja doctor
wind3d-ninja inspect ./input_data
wind3d-ninja plan ./input_data --height 10 20 50
wind3d-ninja run ./input_data --height 10 20 50
```

`plan` 和 `run` 都自动使用全部观测时次。

`run` 会实时显示任务总进度和 WindNinja 日志，例如：

```text
[4/117] 2024-04-03T01:33:00Z | height=20m | resolution=50m | starting WindNinja
[WindNinja] Run 0 (solver): 68% complete
[4/117] 2024-04-03T01:33:00Z | height=20m | resolution=50m | complete
```

## 自定义输出时间区间

不写 `--time-range` 时，自动处理全部观测时次。需要限制输出时间时，使用闭区间：

```bash
wind3d-ninja run ./input_data --height 10 20 50 \
  --resolution 100 \
  --time-range "2024-04-03 16:16" "2024-04-03 16:30"
```

没有写时区的时间按数据源配置解释，当前默认是 UTC+8。上例对应 UTC `08:16–08:30`，开始和结束分钟都包含。也可以直接写带时区的 ISO 时间：

```bash
--time-range "2024-04-03T08:16:00Z" "2024-04-03T08:30:00Z"
```

## 多分辨率

```bash
wind3d-ninja run ./input_data --height 10 20 50 \
  --resolution 50 \
  --resolution 100 \
  --resolution 200
```

## 自定义输出和覆盖范围

```bash
wind3d-ninja run ./input_data --height 10 20 50 \
  --resolution 100 \
  --output ./results \
  --bounds 24.9 25.1 102.3 102.5
```

`--bounds` 依次为：最小纬度、最大纬度、最小经度、最大经度。

若不写 `--bounds`，程序根据所有观测点和默认 `10 km` 缓冲区自动计算范围。缓冲区可用 `--buffer` 修改：

```bash
wind3d-ninja run ./input_data --height 10 20 50 --buffer 8
```

## 输出

```text
wind3d_output/
├── nc/
│   ├── Wind3D_20240403T081600Z_50m_h010m.nc
│   ├── Wind3D_20240403T081600Z_100m_h020m.nc
│   └── Wind3D_20240403T081600Z_200m_h050m.nc
├── kmz/
│   ├── Wind3D_20240403T081600Z_50m_h010m.kmz
│   ├── Wind3D_20240403T081600Z_100m_h020m.kmz
│   └── Wind3D_20240403T081600Z_200m_h050m.kmz
├── stations/<UTC时间>/<高度>/
├── windninja/<UTC时间>/<高度>/<分辨率>/
├── dem/
├── observations.csv
└── manifest.json
```

NetCDF 命名格式：

```text
Wind3D_{UTC时间}_{分辨率}m_h{高度:03d}m.nc
```

NetCDF 风场变量仅包含：

```text
U_wind
V_wind
```

KMZ 默认生成，并原样保留 WindNinja 生成的 KMZ 内容，只把文件名统一成与 NetCDF 相同的时间、分辨率和高度命名。若某次明确不需要 KMZ，可添加 `--no-kmz`。

UAV 高度严格使用上面的 GPS、对地高度和 DEM 算法。

未知温度使用配置的 `20.0 C` 模型假设，并在 manifest 中标记为 `configured_model_assumption_not_observed`，不会伪装成实测温度。

详细说明：

- [使用指南](docs/usage.md)
- [架构说明](docs/architecture.md)
- [数据格式](docs/data_formats.md)

## 版本与许可证

当前固定版本为 `v1.0.0`。Windows 最终交付安装器为 `Wind3D-Ninja-Setup-1.0.0-x64.exe`，已内置 WindNinja 3.12.2；本项目使用 MIT License，WindNinja 是独立上游项目，其许可证和再分发声明见 [THIRD_PARTY_NOTICES/WindNinja-LICENSE.txt](THIRD_PARTY_NOTICES/WindNinja-LICENSE.txt)。

## Legacy 10 m converter

仓库中的 `windninja_10m_pipeline.py` 用于把已有 WindNinja 单高度 ASCII 结果转换成保留投影信息的 CF-NetCDF。它不会复制或外推不存在的垂直层。

```bash
py -3.11 windninja_10m_pipeline.py convert \
  --speed-asc ./windninja_work/terrain_180_5_200m_vel.asc \
  --direction-asc ./windninja_work/terrain_180_5_200m_ang.asc \
  --output ./output/windninja_10m.nc
```
# Desktop UI

Install the optional Tk desktop interface from Git Bash:

```bash
py -3.11 -m pip install -e ".[ui]"
wind3d-ninja-ui
```

The window provides input/output folder selection, WindNinja executable selection, multiple heights and resolutions, buffer, optional UTC+8 time range, manual bounds, KMZ toggle, and `doctor`/`inspect`/`plan`/`run` actions. Run output is streamed into the log panel while the computation runs in a background thread.

The UI also allows an explicit DEM file. If it is left blank, the program first searches the input folder and downloads terrain data only when no suitable DEM is available. Advanced WindNinja controls expose vegetation (`trees`, `grass`, `brush`), diurnal winds, non-neutral stability, optional stability alpha, optional uniform input wind height, station radius of influence, output clipping, thread count, and turbulence output.

Generic station files are also accepted: `.csv` and `.txt` files are automatically treated as station tables. The adapter recognizes common Chinese/English names for time, station, latitude, longitude, height, speed, direction, and temperature, supports long tables and multiple-height columns, and converts `km/h`, `mph`, `knots`, `cm`, and `ft` to the internal `m/s` and metre units. Use `inspect` first to review the detected station counts and time range before running.
