# 架构说明

```text
doctor --install
→ WindNinjaInstaller (download → SHA256 verification → silent install)

DataScanner
→ WindMasterReader / DatReader / UavReader.read_metadata
→ DemManager
→ UavReader.read
→ complete_coordinates
→ TimeAxis
→ ObservationSelector
→ StationFileWriter / StationListWriter
→ WindNinjaConfigWriter / WindNinjaRunner
→ Wind3DNetcdfWriter / KmzWriter
→ ManifestBuilder
```

## WindNinja 运行时

`wind3d-ninja` 固定 WindNinja `3.12.2`，但不把约 250 MB 的安装结果提交到 Git 仓库。`doctor --install` 下载 FireLab 官方 Windows ZIP，严格校验固定 SHA256，只提取其中的 NSIS 安装器，并安装到可配置的运行时目录。默认 CLI 路径为 `~/.wind3d-ninja/windninja/3.12.2/bin/WindNinja_cli.exe`；`WINDNINJA_HOME` 可修改安装根目录，`WINDNINJA_BIN` 可直接覆盖可执行文件路径。

源代码 fork 位于 `maomao3334/windninja`，用于跟踪上游和维护必要补丁。普通用户使用预编译运行时，不需要克隆或编译 WindNinja C++ 源码。

## 自动时间轴

WindMaster、DAT 和 UAV 中的本地时间按数据源时区转换为 UTC，并保留原始秒数。只有 `TimeAxis` 在生成输出时间轴时才截断到分钟；`ObservationSelector` 使用原始秒数执行数据源配置的时间偏移窗口。

可选的 `--time-range START END` 会在自动时间轴上执行闭区间筛选，并在准备 DEM 和自动覆盖范围之前过滤观测。未指定时仍使用全部自动提取时次。

## UAV 高度

UAV 先读取经纬度、GPS 当前高度、对地高度、时间、风速和风向。DEM 在规划阶段由 `DemManager` 优先匹配输入目录文件，覆盖不足时自动下载，再传入 `UavReader`。

```text
AGL_raw = (GPS 当前高度 + 对地高度) - DEM 高程
```

`AGL_raw < -4 m` 的记录丢弃；`-4 m <= AGL_raw <= 0 m` 时设为 `0.1 m`；`AGL_raw > 0 m` 时保留原计算值。

## 规划阶段 DEM

`PipelinePlanner` 在生成目标时次和产品列表前解析 DEM。输入目录中有覆盖有效 DEM 时直接使用；没有覆盖有效 DEM 时下载并缓存 SRTM。规划结果包含实际使用的 `dem_path`，随后 `run` 会复用同一缓存结果。

## DEM

`DataScanner` 自动识别输入目录中的所有 `.tif` 和 `.tiff`。`DemManager` 按以下顺序处理：

1. 检查输入目录中的 DEM 是否覆盖全部观测及缓冲范围。
2. 若本地 DEM 为经纬度坐标，自动转换为当地 UTM 投影。
3. 若不存在有效 DEM，自动下载并缓存 SRTM。
4. 将最终使用的 DEM 复制到输出目录，并写入 manifest。

## 时间 × 高度 × 分辨率

`PipelineRunner` 对每个自动提取的 UTC 时次、每个用户高度和每个用户分辨率执行独立 WindNinja 模型运行：

```text
for target_time in automatic_time_axis:
    for height in requested_heights:
        for resolution in requested_resolutions:
            run WindNinja
            write NetCDF
```

每个 NetCDF 都来自一次真实的单高度 WindNinja 运行，不复制或外推垂直层。

## 观测时间选择窗口

`ObservationSelector` 按数据源读取 `QualityRule.max_time_offset_seconds`。目标时刻会匹配该窗口内（含边界）的观测；当前默认 WindMaster 和 DAT 均为 ±120 秒。匹配后仍按 `preferred_heights_m` 执行高度优选，并且每个唯一站点只保留窗口内离目标时刻最近的一条记录，确保 WindNinja 的 `Recent_Station_File_List` 满足“一站点一文件”。

## 覆盖范围

未提供 `--bounds` 时，根据全部观测坐标和 `--buffer` 自动生成覆盖范围。提供 `--bounds MIN_LAT MAX_LAT MIN_LON MAX_LON` 时使用指定范围，并校验 DEM 覆盖。

## 输出命名

```text
Wind3D_{UTC时间}_{分辨率}m_h{高度:03d}m.nc
```

例如：

```text
Wind3D_20240403T081600Z_100m_h050m.nc
```
