# wind3d-ninja

`wind3d-ninja` 是基于 WindNinja 的观测风场降尺度与地形风场模拟工具。

默认的多站点模式自动识别 WindMaster ZIP、超声波 DAT、UAV XLS/XLSX 和 DEM，自动生成全部 UTC 分钟时间轴，并对每个“时间 × 高度 × 分辨率”执行独立 WindNinja 运行。独立湍流模式使用你明确给出的背景风和模拟时间。

## 第一次使用：先弄清楚模式

DEM 是地形高程图，告诉程序哪里有山、山有多高。观测文件则告诉程序在某个位置、时间和高度测到了多大的风。输出高度是你想查看的高度，网格分辨率是结果中每个格子的大小。

| 模式或选项 | 用通俗的话解释 | 你需要准备什么 | 适合什么时候用 |
|---|---|---|---|
| 多站点模式（默认） | 把多个地点测到的风交给模型，再结合地形计算周围的风场 | 带时间、位置、观测高度、风速和风向的观测文件，以及 DEM | 你有 WindMaster、DAT、UAV 或站点表，希望利用这些观测生成风场 |
| 非中性稳定度（多站点模式的附加选项） | 考虑空气更容易上下混合，或更不容易上下混合时，地形附近的风会怎样变化 | 多站点模式的输入；自动计算还会使用时间、位置、温度和云量信息 | 你希望考虑稳定度影响，并能判断温度、云量等输入或假设是否适合本次模拟 |
| 湍流模式（独立的动量求解流程） | 给模型一个背景来风，让它计算风经过山地后的速度变化和湍流分布 | DEM、背景风速、风向、该风速对应的高度，以及模拟时间 | 你关注地形引起的复杂气流和湍流，并有可代表整个区域的背景风条件 |

第一次处理自己的实测数据，先用默认的多站点模式。非中性稳定度不是一个全新的输入模式，而是给多站点计算增加一个物理选项。日变化风是另一个选项，用来考虑白天受热和夜间冷却形成的坡面风；它与稳定度的作用不同，也不代表完整的山谷环流或海陆风模拟。

湍流模式使用独立的动量求解器。它的输入是明确给出的背景来风，不会自动把多个站点的风取平均；多站点观测也不会以原来的点初始化方式进入这次求解。当前 WindNinja 不支持把点初始化与动量求解器组合，也不支持在同一次动量求解中开启非中性稳定度。

湍流产品是模型根据背景风、地形和湍流模型预测的结果，不能直接解释成某一秒会出现多大的实测阵风。两种模式各自依赖不同的输入和物理假设，不能仅凭“选项更多”判断哪种结果更可靠。

项目默认采用 20°C 温度和 0% 云量；默认不会信任源文件温度，即使文件里有温度列也不保证会用于模型。这些是模型假设，不是实测天气。使用热力或稳定度选项前，应检查这些假设是否符合数据时刻；温度信任规则和云量假设可通过配置调整。手动 `alpha` 属于高级设置，不懂其含义时应使用经过输入检查的自动计算方式，而不是随意填写数值。

模型依据参见官方的 [求解器说明](https://ninjastorm.firelab.org/windninja/howdoes/solvers.html)、[热力参数说明](https://ninjastorm.firelab.org/windninja/howdoes/thermal.html)和项目固定版本的 [CLI 源码](https://github.com/firelab/windninja/blob/94c20125a7e8850c543fc7b511469d2e27a97470/src/ninja/cli.cpp)。具体操作和当前支持范围见下方示例及 [中文使用指南](docs/user_guide_zh.md)。

### 多站点模式：用自己的观测生成风场

把原始观测放在 `input_data` 目录，直接运行：

```bash
wind3d-ninja run ./input_data --height 10 --output ./station_results
```

这是默认的 `--mode stations`，不写模式也可以。程序会从有效观测中取得时间和位置；DEM 优先使用输入目录中的文件，缺失或覆盖不足时自动下载。`--height 10` 表示输出 10 m 高度的风场，不会把观测仪器高度改成 10 m。

要考虑非中性稳定度，可以在文本文件 `stability.yaml` 中写：

```yaml
windninja:
  non_neutral_stability: true
```

然后在原命令后加配置文件：

```bash
wind3d-ninja run ./input_data --height 10 --output ./stable_results \
  --config ./stability.yaml
```

省略 `alpha_stability` 时由 WindNinja 自动计算稳定度。请先确认温度和云量假设；手动指定 `alpha_stability` 时必须同时开启非中性稳定度，且数值满足 `0 < alpha <= 5`。稳定度选项已与动量求解选项分开，不需要开启湍流模式。

### 湍流模式：用明确的背景来风做地形模拟

在 `terrain_data` 目录放入 DEM，例如 `terrain.tif`。这个模式不要求站点表，也不会读取或平均目录中的观测。示例：

```bash
wind3d-ninja run ./terrain_data --mode turbulence \
  --background-speed-ms 5 \
  --background-direction-deg 270 \
  --background-height-m 10 \
  --simulation-time "2024-04-03T08:16:00Z" \
  --height 10 --resolution 200 --output ./turbulence_results
```

| 必填输入 | 示例的意思 |
|---|---|
| `--background-speed-ms 5` | 背景风速为 5 m/s |
| `--background-direction-deg 270` | 风从西边吹来；风向按气象来向解释 |
| `--background-height-m 10` | 这份背景风速对应的参考高度为 10 m，按 WindNinja 植被以上高度定义 |
| `--simulation-time ...Z` | 本次模拟的 UTC 时间；带 `Z` 的写法可以避免时区误解 |
| `--height 10` | 想查看的输出高度；它与背景风参考高度分别设置 |

这些风速、风向和时间只是语法示例，需要换成你这次模拟的真实背景条件。用同样参数把 `run` 改为 `plan` 可以先看执行计划。一个背景条件只对应明确给出的模拟时间；多个输出高度和分辨率会分别运行。

湍流模式需要原生 KMZ 湍流产品，因此不能同时使用 `--no-kmz`。NetCDF 仍输出 `U_wind`、`V_wind`，不能把这两个变量当作湍流强度。模式会在 manifest 中标记背景风和求解器，帮助区分它与多站点结果。

打开湍流 KMZ 后，可以查看原生的 `Column Max Velocity Fluctuations` 图层，图例标为 `Speed Fluctuation (m/s)`。这是列最大速度波动指标，用来查看模型预测的波动分布；不能直接当作某一秒的阵风，也不能仅凭文件名中的输出高度把列最大指标解释为该高度上的波动值。它不是 NetCDF 中新增的湍流动能变量。

## 核心行为

1. 多站点模式的时间轴自动从全部有效观测中提取；湍流模式使用显式模拟时间。
2. UAV 高度严格按 `(GPS 当前高度 + 对地高度) - DEM 高程` 转换为 AGL；DEM 会在 `plan` 阶段优先匹配或自动下载。
3. 高度支持 `--height 10 20 50` 的空格分隔写法。
4. DEM 优先从输入目录自动匹配；缺失或覆盖不足时自动下载 SRTM。
5. `--output` 可指定输出目录；不写时输出到输入目录下的 `wind3d_output`。
6. 支持一个或多个分辨率，以及自动缓冲区或手动覆盖范围。
7. NetCDF 只保存 `U_wind` 和 `V_wind`；KMZ 默认同时生成。
8. 多站点模式在每个目标时刻会将时间窗口内所有来源、所有有效高度的站点数据输入 WindNinja，再分别计算用户指定的输出高度层；不会只挑选某一个“优选高度”。

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

### 路径可迁移性

安装包不会把开发机的绝对路径写入程序。UI 和 CLI 每次启动都会根据当前可执行文件的位置查找同级的 `windninja/bin/WindNinja_cli.exe`；因此把整个安装目录复制到另一台电脑后仍可运行。只有在 UI 中主动选择自定义 WindNinja，或设置 `WINDNINJA_BIN`/`WINDNINJA_HOME` 时，才会使用外部路径；如果这些变量指向已经不存在的旧电脑路径，程序会自动回退到安装包内置版本。

运行时生成的 `windninja.cfg` 为了让 Windows 版 WindNinja 稳定读取输入文件，可能包含当前电脑上的绝对临时路径。这些路径不是源码硬编码，每次运行都会按当前输入/输出目录重新生成；迁移时请移动整个安装目录，重新选择数据目录后再次运行。

## Windows 最终安装包

Windows 用户直接下载 GitHub Releases 中的 `Wind3D-Ninja-Setup-1.0.4-x64.exe`，双击安装即可。安装包已经包含 WindNinja 3.12.2、CLI、桌面 UI 和运行依赖，不需要另外安装 Python 或 WindNinja。

安装完成后可以从开始菜单启动 UI。CLI 位于安装目录下的 `cli/wind3d-ninja.exe`；Git Bash 示例：

```bash
CLI="$LOCALAPPDATA/Programs/Wind3D-Ninja/cli/wind3d-ninja.exe"
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

默认多站点模式的 `plan` 和 `run` 自动使用全部有效观测时次；湍流模式使用 `--simulation-time` 指定的单个时刻。

`run` 会实时显示任务总进度和 WindNinja 日志，例如：

```text
[4/117] 2024-04-03T01:33:00Z | height=20m | resolution=50m | starting WindNinja
[WindNinja] Run 0 (solver): 68% complete
[4/117] 2024-04-03T01:33:00Z | height=20m | resolution=50m | complete
```

## 自定义输出时间区间

多站点模式不写 `--time-range` 时，自动处理全部观测时次。需要限制输出时间时，使用闭区间：

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

多站点模式未知温度使用配置的 `20.0 C` 模型假设，并在 manifest 中标记为 `configured_model_assumption_not_observed`，不会伪装成实测温度。

详细说明：

- [使用指南](docs/usage.md)
- [中文完整使用指南](docs/user_guide_zh.md)
- [架构说明](docs/architecture.md)
- [数据格式](docs/data_formats.md)

## 版本与许可证

当前固定版本为 `v1.0.4`。Windows 最终交付安装器为 `Wind3D-Ninja-Setup-1.0.4-x64.exe`，已内置 WindNinja 3.12.2；本项目使用 MIT License，WindNinja 是独立上游项目，其许可证和再分发声明见 [THIRD_PARTY_NOTICES/WindNinja-LICENSE.txt](THIRD_PARTY_NOTICES/WindNinja-LICENSE.txt)。

## Legacy 10 m converter

仓库中的 `windninja_10m_pipeline.py` 用于把已有 WindNinja 单高度 ASCII 结果转换成保留投影信息的 CF-NetCDF。它不会复制或外推不存在的垂直层。

```bash
py -3.11 windninja_10m_pipeline.py convert \
  --speed-asc ./windninja_work/terrain_180_5_200m_vel.asc \
  --direction-asc ./windninja_work/terrain_180_5_200m_ang.asc \
  --output ./output/windninja_10m.nc
```
## 桌面界面

Windows 安装版可以从开始菜单打开界面；源码环境可在 Git Bash 中启动：

```bash
py -3.11 -m pip install -e .
wind3d-ninja-ui
```

先选择模式，再填写输入和输出目录。多站点模式选择包含观测的目录后，可以点击“刷新可用时间”，从实际观测时间中选择开始和结束时刻。湍流模式使用包含 DEM 的目录，并填写背景风速、风向、参考高度和模拟时间。高度和分辨率都可以填写多个值；第一次试算建议各选一个值。

界面允许明确选择 DEM 文件。多站点模式中，留空时会搜索输入目录，没有合适 DEM 时再自动下载。湍流模式没有观测点来确定区域，应提供 DEM 或明确的覆盖范围。按“环境检查”“预览计划”“开始运行”可以先确认环境和任务数量；运行日志会实时显示在窗口中。

高级选项包括植被、日变化风、非中性稳定度、手动 alpha、站点影响半径、边缘裁剪和线程数。非中性稳定度用于多站点模式；湍流由独立动量模式提供。旧的“统一输入风高”参数在当前点初始化分支不生效，建议留空；各条观测仍使用自身高度。湍流模式的背景风高度则由专用字段设置。具体参数和互斥关系见 [中文使用指南](docs/user_guide_zh.md)。

多站点模式也支持通用 `.csv`、`.txt` 站点表，识别常用中英文时间、站名、纬度、经度、高度、风速和风向列。长表可以使用明确的单位列换算 `km/h`、`mph`、`knots`、`cm`、`ft`；多高度宽表建议使用整数米高度和 m/s 风速。通用长表的单位转换路径当前不读取温度列，`generic_station.max_height_m` 也尚未执行高度过滤；使用前可通过 `inspect` 核对站点数量和时间范围。
