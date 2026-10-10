# wind3d-ninja 中文使用指南

适用于 `wind3d-ninja v1.0.3` 与随 Windows 安装包提供的 WindNinja `3.12.2`。本项目把 WindMaster ZIP、DAT、UAV 与通用站点表转换成点初始化站点文件，按有效观测分钟 × 输出高度 × 网格分辨率运行独立 WindNinja 作业，输出 NetCDF 和可选 KMZ。

## 安装与启动

Windows 安装版无需另装 Python。双击 `Wind3D-Ninja-Setup-1.0.3-x64.exe` 安装。开始菜单桌面程序实际路径为安装目录下 `ui/Wind3D-Ninja.exe`；命令行程序为 `cli/wind3d-ninja.exe`。通常安装目录是 `%LOCALAPPDATA%\Programs\Wind3D-Ninja`。在 PowerShell 检查：

```powershell
$cli = "$env:LOCALAPPDATA\Programs\Wind3D-Ninja\cli\wind3d-ninja.exe"
& $cli doctor
& $cli --help
```

`doctor` 会报告依赖和实际解析到的 WindNinja 路径。迁移时复制整个 `Wind3D-Ninja` 安装目录，不能只拷贝单个 EXE 或 `cli` 子目录。源码开发环境可用 Python 3.10+ 执行 `py -3.11 -m pip install -e .`，再运行 `wind3d-ninja doctor --install` 安装项目固定的 WindNinja 3.12.2。

## Git Bash 快速操作

安装完成后，在 Git Bash 中先定义命令路径；如果安装时改了目录，替换下面的路径：

```bash
cli="$LOCALAPPDATA/Programs/Wind3D-Ninja/cli/wind3d-ninja.exe"
"$cli" doctor
```

把原始观测文件放在 `D:/wind/input`，直接运行：

```bash
"$cli" run D:/wind/input -h 10 -o D:/wind/output
```

这条命令默认使用 200 m 网格、10 km 范围外扩，处理全部有效观测分钟并生成 NetCDF 和 KMZ；没有可用 DEM 时自动下载。`doctor`、`inspect`、`plan` 是排查工具，不要求每次运行前依次执行。

第一次试算可先检查时间，再把开始和结束设为同一分钟，以减少计算量：

```bash
"$cli" inspect D:/wind/input
"$cli" run D:/wind/input -h 10 -r 200 \
  --time-range "2024-04-04 00:16" "2024-04-04 00:16" \
  -o D:/wind/output
```

示例时间必须替换成自己数据中的有效时刻；没有时区的时间默认按 UTC+8 解释，`inspect` 返回的 UTC 时间可以保留 `Z` 后缀使用。多高度、多分辨率示例：

```bash
"$cli" run D:/wind/input -h 10 -h 20 -h 50 -r 100 -r 200 \
  -o D:/wind/output --no-kmz
```

Git Bash 用行末 `\` 续行；后面的 PowerShell 示例使用反引号续行，不能混用。路径包含空格时必须加双引号。

## 输入表格

将一次运行的原始文件放在一个输入目录中，程序递归扫描它。支持 ZIP 内 WindMaster `level2/*_Ave01min_*.csv`、DAT、UAV XLS/XLSX、CSV/TXT 通用站点表以及 GeoTIFF/ASCII/IMG DEM。避免把旧的输出目录放在输入目录下面，以免旧 CSV 等再次被扫描。

通用站点表至少需要时间、纬度、经度、风速、风向，以及高度。识别常用英文/中文别名，如 `time`/`timestamp`/`时间`、`lat`/`纬度`、`lon`/`经度`、`height`/`高度`、`speed`/`风速`、`direction`/`风向`。站名可选。通用长表的单位转换读取路径当前不读取温度列，不能依靠增加温度列就改变模型温度；默认使用配置中的假定温度。示例长表（UTF-8 CSV）：

```csv
time,station,lat,lon,height_m,speed_ms,direction_deg
2024-04-04 00:16:00,STA_A,39.9000,116.3000,10,3.2,270
2024-04-04 00:16:00,STA_B,39.9100,116.3100,20,4.1,285
```

长表可在每行提供 `speed_unit` 和 `height_unit` 列：风速单位支持 `km/h`、`kmh`、`kph`、`mph`、`kt`、`kts`、`knot`、`knots`，高度支持 `cm`、`ft`、`feet`。也能从列名中的 `kmh/km/h/kph`、`mph`、`knot/kts` 及 `cm/ft/feet` 推断单位；仅在列名中写 `kt` 不会触发节到 m/s 的转换，建议明确提供单位列。没有单位标记时按 m/s 与 m 解释。风向是气象来向，按度数输入：0°/360° 北风、90° 东风、180° 南风、270° 西风。宽表也支持每个高度一对风速/风向列：

```csv
time,station,lat,lon,10m_WindSpeed,10m_WindDirection,20m_WindSpeed,20m_WindDirection
2024-04-04 00:16:00,STA_A,39.9000,116.3000,3.2,270,4.1,285
```

宽表列名需包含高度数字以及 `speed`/`WindSpeed`、`direction`/`WindDirection`（或对应中文风速、风向）。宽表走另一条解析路径，请使用整数米高度、m/s 风速，不要依赖长表的单位列转换功能。通用 CSV/TXT 每行的无时区时间按 `sources.generic_station.tz_offset_hours` 解释（默认 UTC+8）；带 `Z` 的 ISO 时间是 UTC。WindMaster、DAT、UAV 分别使用各自 source 时区配置。观测时间转换为 UTC 后保留原始秒数，只有输出时间轴截断到分钟。WindMaster 和 DAT 默认用原始时间匹配目标分钟前后 120 秒内观测，同源同站多记录取距离目标时刻最近的一条；默认未配置时间窗口的其他源按同一分钟匹配。

WindMaster 默认只收录不高于 200 m 且 `DataObtainRate >= 0.8` 的数据；DAT 默认最大高度 200 m，速度除以 100 转成 m/s。UAV AGL 由 GPS 高程 + 对地高度 - DEM 高程计算：小于 -4 m 丢弃，[-4, 0] m 修正为 0.1 m。DEM 缺失时模型无法完成 UAV AGL 计算。通用站点的 `max_height_m` 配置默认 1000，但当前适配器没有执行这一高度上限，需自行预先过滤。

## CLI 流程

```powershell
$cli = "$env:LOCALAPPDATA\Programs\Wind3D-Ninja\cli\wind3d-ninja.exe"
$inputDir = "D:\wind\input"
$outputDir = "D:\wind\output"
& $cli doctor
& $cli inspect $inputDir
& $cli plan $inputDir --height 10
& $cli run $inputDir --height 10 --output $outputDir
```

先用 `inspect` 检查识别文件、站点数量及时间范围；`plan` 显示所选时刻、范围、DEM、任务数和输出目标，但不运行 WindNinja；`run` 执行任务。计划作业数约为筛选后的有效时刻数 × 高度数 × 分辨率数。所有命令支持全局 `--json`，必须放在子命令之前，例如 `& $cli --json inspect $inputDir`。

```powershell
& $cli plan $inputDir --height 10 20 50 --resolution 50 --resolution 100
& $cli run $inputDir --height 10 20 50 `
  --resolution 50 --resolution 100 --buffer 10 `
  --time-range "2024-04-04 00:16" "2024-04-04 00:30" `
  --output $outputDir --no-kmz
```

`--height` 可重复，或在输入目录后追加其他高度；CLI 高度、分辨率必须为正整数米。`--resolution` 可重复，默认 200 m；`--buffer` 是非负公里值，默认 10 km。注意这两个 CLI 默认值由命令行硬编码，配置中的 `windninja.default_mesh_resolution_m/default_buffer_km` 不会覆盖它们。`--bounds MIN_LAT MAX_LAT MIN_LON MAX_LON` 手动定义所需 DEM 覆盖范围；`--time-range START END` 两端包含。无时区时间默认按 UAV source 时区（默认 UTC+8）解析，显式 `Z` 表示 UTC。PowerShell 含空格的时间字符串必须加引号。

`doctor` 支持 `--config FILE`、`--install`、`--force`；`--force` 必须与 `--install` 同用。`inspect` 的 `INPUT_DIR` 必须是存在目录，可用 `--config FILE`。`plan`/`run` 都需要至少一个 `--height`；`run` 另有 `--output DIR`（默认输入目录下 `wind3d_output`）、`--kmz/--no-kmz`（默认生成 KMZ）和 `--config FILE`。准确语法以 `& $cli plan --help`、`& $cli run --help` 为准。

| 参数 | 所属命令 | 默认值/用法 |
|---|---|---|
| `--help` | 所有命令 | 查看对应命令帮助；`-h` 在 plan/run 中表示高度，不是帮助。 |
| `--json` | 全局 | 放在子命令前，输出适合脚本读取的 JSON。 |
| `INPUT_DIR` | inspect/plan/run | 必填，已经存在的输入目录。 |
| `--config FILE` | doctor/inspect/plan/run | 加载 YAML；未写出的字段使用项目默认值。 |
| `--install` | doctor | 安装固定 WindNinja 3.12.2 运行时；完整 Windows 安装版通常无需执行。 |
| `--force` | doctor | 配合 `--install` 强制重装运行时。 |
| `--height / -h` | plan/run | 必填，正整数米；可重复，如 `-h 10 -h 20`，也可 `--height 10 20 50`。这是输出高度，不是观测仪器高度。 |
| `--resolution / -r` | plan/run | 默认 200 m，正整数；可重复，如 `-r 50 -r 100`，每个分辨率独立运行。 |
| `--buffer / -b` | plan/run | 默认 10 km，非负值；在观测范围外增加 DEM 覆盖需求。 |
| `--bounds` | plan/run | `MIN_LAT MAX_LAT MIN_LON MAX_LON`，以度为单位；指定 DEM 需求范围。 |
| `--time-range` | plan/run | `START END`，包含两端；省略时使用全部有效观测分钟。 |
| `--output / -o` | run | 默认 `INPUT_DIR/wind3d_output`；建议显式设置到输入目录之外。 |
| `--kmz / --no-kmz` | run | 默认开启；只影响 KMZ，NetCDF 仍生成。 |

当前 CLI 没有 `--dem` 或单独的 `--time` 参数。显式选择 DEM 使用桌面界面；CLI 可把 DEM 放进输入目录供扫描。模型高级参数通过 YAML 或 UI 设置，没有对应的同名 CLI 开关。

JSON 成功结果直接序列化命令的结果对象：`inspect` 包含 `input_dir`、`files`、`fixed_observations`、`generic_station_observations`、`uav_metadata_count`、`time_count`、`time_start_utc`、`time_end_utc`；`plan` 是计划对象，包含筛选时间、所需范围/DEM、任务和输出目标；`run` 包含 `output_dir`、`manifest`、`netcdf_files`、`kmz_files`。错误返回非零状态码，JSON 为 `{"ok": false, "error": {"type": "...", "message": "..."}}`。`doctor` 成功结构以健康状态、依赖和 WindNinja 可执行路径为核心；带 `--install` 时附带 `installation`。日志/进度写入 stderr，不属于成功 JSON 对象。

## 桌面界面

安装包启动 `ui/Wind3D-Ninja.exe`。基本页包括输入目录、输出目录、可选 DEM/WindNinja 文件、输出高度、分辨率、buffer、开始/结束时间、可选 bounds、KMZ 勾选框。点击刷新时间后，下拉列表显示 UTC+8 时刻；选择范围两端均包含。高度和分辨率可用空格或逗号分隔。UI 初始高度为 `10 20 50`，分辨率 `50 100 200`，buffer `10`，KMZ 开启；与 CLI 默认分辨率 200 不同。

高级页的控件包括主导植被（trees/grass/brush）、diurnal winds、non-neutral、alpha、统一输入风高、站点影响半径、边缘裁剪百分比、线程数和 turbulence output。只有植被和线程的初值读取基础配置（默认 trees、4）；三个开关初值均为关闭，alpha 和统一输入风高为空，影响半径为 -1，裁剪为 0。UI 会用这些控件值覆盖对应模型配置，不能假定它已继承 YAML 中其他高级值。先后使用“环境检查”“检查数据”“预览计划”“开始运行”可逐步确认结果。

## 配置项

`--config` 接受 YAML；下面是可直接加载的模型参数覆盖示例（省略字段沿用项目默认值）。保存为 `my.yaml` 后用 `& $cli inspect $inputDir --config .\my.yaml` 验证：

```yaml
windninja:
  vegetation: trees
  num_threads: 4
  diurnal_winds: false
  non_neutral_stability: false
  alpha_stability: null
  input_wind_height_m: null
  station_radius_of_influence_km: -1
  output_buffer_clipping_pct: 0
  turbulence_output: false
```

以下为仓库 `config/defaults.yaml` 与程序内置默认配置的字段、默认值和含义。配置加载会合并默认值并构造配置对象，但不保证每个业务范围都在加载阶段检查；运行仍受下游库和 WindNinja 校验约束。

| 配置路径 | 默认值 | 单位/作用与限制 |
|---|---:|---|
| `windninja_exe` | `~/.wind3d-ninja/windninja/3.12.2/bin/WindNinja_cli.exe` | 可执行文件路径；安装包会优先发现随包程序。`WINDNINJA_BIN`/`WINDNINJA_HOME` 可显式覆盖。 |
| `sources.<source>.tz_offset_hours` | 各源 `8` | 无时区源时间相对 UTC 的小时偏移；显式时区时间按其时区解析。 |
| `sources.<source>.station_prefix` | `WM`/`DAT`/`UAV`/`STA` | 观测无站名时生成站名的前缀。 |
| `sources.windmaster.max_height_m` | `200` | WindMaster 纳入的最大观测高度，m。 |
| `sources.windmaster.min_obtain_rate` | `0.8` | 最低 `DataObtainRate`，比例值。 |
| `sources.dat.max_height_m` | `200` | DAT 最大观测高度，m。 |
| `sources.dat.speed_divisor` | `100` | DAT 速度换算除数；原始值除以此数得到 m/s。 |
| `sources.uav.agl_threshold_drop_m` | `-4` | UAV AGL 低于此值丢弃，m。 |
| `sources.uav.agl_threshold_fix_m` | `0.1` | 未达到丢弃阈值的非正 AGL 修正为此高度，m；默认 [-4, 0] m 修正为 0.1 m。 |
| `sources.uav.max_height_m` | `1000` | UAV 最大观测高度，m。 |
| `sources.generic_station.max_height_m` | `1000` | 保留的高度上限配置，m；当前通用站点适配器未执行，需自行过滤。 |
| `quality_rules.windmaster/dat.preferred_heights_m` | `[]` | 旧兼容项，不再筛除高度；所有其他质量有效的高度均传给 WindNinja。 |
| `quality_rules.windmaster/dat.max_time_offset_seconds` | `120` | 目标分钟的匹配窗口，秒。 |
| `dem.cache_dir` | `~/.wind3d-ninja/dem_cache` | 缓存目录。 |
| `dem.source` | `srtm` | 自动下载 DEM 源；SRTM 下载覆盖纬度 -60 至 60 度。 |
| `temperature.trust_source` | `false` | 是否采用有效范围内、确实由适配器读入的源温度；通用长表单位转换路径当前不读取温度列。 |
| `temperature.valid_range_c` | `[-40, 50]` | 源温度有效区间，°C。 |
| `temperature.configured_assumption_c` | `20` | 不信任/缺少源温度时写入站点文件的假定温度，°C。 |
| `temperature.cloud_cover_assumption_pct` | `0` | 写入站点文件的云量假定，百分比。 |
| `windninja.default_mesh_resolution_m` | `200` | 声明的默认网格尺寸，m；CLI 和 UI 当前通过各自参数输入，不自动读取此值。 |
| `windninja.default_buffer_km` | `10` | 声明的默认区域外扩，km；CLI/UI 当前默认值由各自界面实现。 |
| `windninja.vegetation` | `trees` | 主导下垫面枚举：`grass`、`brush`、`trees`。不是逐像元土地覆盖图。 |
| `windninja.num_threads` | `4` | WindNinja 线程数。 |
| `windninja.diurnal_winds` | `false` | 请求日变化坡谷风计算；该热力流程未在本项目流水线验证。 |
| `windninja.non_neutral_stability` | `false` | 当前项目点初始化明确拒绝启用；这是本项目实现限制，不代表上游 WindNinja 不支持。 |
| `windninja.alpha_stability` | `null` | 可选稳定度参数；官方 CLI 源码对相关分支给出 `0 < alpha <= 5`。当前项目禁用非中性稳定度路径，建议留空；填写它不代表启用了稳定度计算。 |
| `windninja.input_wind_height_m` | `null` | 写配置时传给上游 `input_wind_height`；官方 CLI 3.12.2 的点初始化分支不应用它。不会改写观测文件的 `Height`；每条记录仍用原始 `observation.height_m`。 |
| `windninja.station_radius_of_influence_km` | `-1` | 站点影响半径 km，写入每个 station CSV。`-1` 是无限影响半径，不是自动选择。官方 point initialization 要求至少一个负 radius 才能保证空间全覆盖；全部使用有限正半径可能留下未覆盖区。 |
| `windninja.output_buffer_clipping_pct` | `0` | 输出边缘裁剪百分比。 |
| `windninja.turbulence_output` | `false` | 当前项目点初始化直接拒绝启用。上游 non-neutral stability 选项与动量求解器功能不可混为一谈。 |
| `output.default_output_dir_name` | `wind3d_output` | 默认结果目录名。 |
| `output.netcdf_compression` | `true` | 是否对 NetCDF 风场变量启用 zlib level 4 压缩。 |
| `output.netcdf_naming_format` | `Wind3D_{time}_{resolution}m_h{height:03d}m.nc` | 当前保留的配置字段；文件名由固定格式生成器产生，此字段尚未接入。 |
| `output.kmz_colormap` | `viridis` | 当前保留字段；KMZ 沿用 WindNinja 原生输出，未应用此色带配置。 |

`diurnal_winds`、`non_neutral_stability`、`alpha_stability` 是上游模型选项，不等价于已验证的热力建模能力。当前项目明确在生成 WindNinja 配置前拒绝 `non_neutral_stability=true` 或 `turbulence_output=true` 任一情形。虽然 WindNinja 3.12.2 上游在其他求解配置中有相应选项，但本项目点初始化路径尚不支持它们。不要将上游 non-neutral stability 描述成 momentum solver 本身。

## DEM、范围与高度语义

程序依据站点点位和 buffer 计算需覆盖范围。`--bounds` 替代这个需求范围并用于 DEM 覆盖检查/下载请求，不是强制裁切最终计算域的输出选项。若输入 DEM 已覆盖需求范围，项目会直接使用整个已有 DEM（非投影 DEM 会先投影转换），不会按 bounds 裁小；若需下载 DEM，则下载所需区域并投影。`plan` 用于核对最终选中的 DEM 与边界。

官方 CLI 将 `input_wind_height` 与 `output_wind_height` 定义为植被顶部以上的高度（above vegetation）。本项目把 `--height` 原样传给 `output_wind_height`，却在 NetCDF 中把 `height` 维标注为 AGL；未进行冠层高度换算。因此，林地等有明显植被高度的区域不能把该输出标签直接视为严格裸地 AGL。项目站点表以观测 `height_m` 原值写出并标注 meters，UAV 高度则是相对 DEM 地表计算的 AGL；观测仪器高度的参考面也应由使用者确认。

输出高度与输入观测高度分别作用于模型输出和站点记录。例如 `--height 10 20 50` 会运行三个独立高度的风场作业，不会把所有观测高度改成这三个值。时间序列也是逐分钟独立计算，多个单高度 NetCDF 不等同于一个带垂直动量演化的完整三维瞬态模型。

## 产品解释

默认输出目录包含 `nc/`、可选 `kmz/`、`stations/`、`windninja/`、`dem/`、`observations.csv` 和 `manifest.json`。每个 NetCDF 对应一次真实单高度模型运行。核心变量 `U_wind`、`V_wind` 分别是向东、向北分量，单位 m/s：

```text
speed = sqrt(U_wind^2 + V_wind^2)
气象风向来向 = (atan2(-U_wind, -V_wind) * 180 / pi + 360) % 360
```

静风时风向没有意义。文件还包含投影坐标 `x/y`、CRS 和 `lat/lon`。`x/y/crs` 是精确 GIS 定位依据；`lat/lon` 是投影网格中心线换算得到的 1D 近似轴，并非对每个格点重采样的规则经纬度网格。可选 KMZ 保留上游原生内容。

`manifest.json` 记录输入、假设、选用 DEM、任务、命令和输出文件；它是复现实验的索引。温度默认采用配置假设值而不是信任源值，manifest 会记录该假设。`match_points=false`，所以点观测不会被强制拟合到观测风速；不能据此承诺模拟值与站点吻合到特定精度。

## 常见问题

| 现象 | 检查与处理 |
|---|---|
| 移动目录后找不到 WindNinja | 确认复制整个安装目录，然后执行 `doctor`；检查实际解析路径及旧 `WINDNINJA_BIN/WINDNINJA_HOME` 设置。 |
| 检查不到有效观测 | 检查表头、时间、坐标、风速/风向、高度及数据源识别结果；先用最小长表排除格式问题。 |
| 时间范围筛选结果为空 | 对照 `inspect` 的 UTC 时间；无时区 CLI 范围默认按 UTC+8 解释，可用带 `Z` 的 ISO 时间消除歧义。 |
| DEM 不覆盖站点或无法下载 | 在输入目录放入覆盖需求范围的 DEM，或在 UI 显式选择；自动 SRTM 下载需网络且本实现纬度范围为 -60 至 60 度。 |
| `pointInitialization cannot be combined...` | 将本项目的 `non_neutral_stability` 和 `turbulence_output` 设为 false；这是当前实现限制。 |
| 单次运行耗时或占用内存很高 | 先预览任务数，减少时间/高度/分辨率组合或先用 200 m 网格；更小网格会增加计算量。 |
| 结果域比 bounds 大 | 覆盖足够的已有 DEM 会全幅使用，bounds 不强制裁切；需要更小域时预先准备较小 DEM。 |
| WindNinja 执行失败 | 检查输出目录下 `windninja/` 的该任务配置与日志，结合 `manifest.json` 定位；失败日志路径可随输出目录迁移。 |

## 官方依据与适用边界

项目参数与初始化分支参照 [WindNinja 3.12.2 固定版本 CLI 源码](https://github.com/firelab/windninja/blob/94c20125a7e8850c543fc7b511469d2e27a97470/src/ninja/cli.cpp)；站点影响半径的负值语义参照同版本的 [pointInitialization.cpp](https://github.com/firelab/windninja/blob/94c20125a7e8850c543fc7b511469d2e27a97470/src/ninja/pointInitialization.cpp)。点初始化概念参考 [WindNinja Tutorial 3](https://ninjastorm.firelab.org/windninja/tutorials/WindNinja_tutorial3.pdf)；命令行和模型参数参考 [CLI Instructions](https://ninjastorm.firelab.org/windninja/tutorials/CLI_instructions.pdf)、[Tutorial 1](https://ninjastorm.firelab.org/windninja/tutorials/WindNinja_tutorial1.pdf)、[Tutorial 2](https://ninjastorm.firelab.org/windninja/tutorials/WindNinja_tutorial2.pdf)。在线教程会随上游版本更新（当前 Tutorial 3 示例为 WindNinja 4.0.0）；本项目兼容性以固定的 3.12.2 源码和本项目实现为准。上游通用能力不代表本项目 v1.0.3 的 pointInitialization 路径均已实现或验证。

本项目采用 MIT License。WindNinja 是独立上游软件，其随包再分发许可见仓库 `THIRD_PARTY_NOTICES/WindNinja-LICENSE.txt`。
