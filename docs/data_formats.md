# 数据格式

## WindMaster

读取 ZIP 中 `level2` 目录下的 `_Ave01min_` CSV。文件头解析经纬度，数据列解析不同高度的风速、风向和数据获取率。

## 超声波 DAT

解析 GPS 行、时间行和垂直廓线数据。DAT 缺少有效坐标时，只使用相同 UTC 分钟的 WindMaster 坐标补齐。

## UAV

支持标准 Excel，以及使用 `.xls` 后缀的 GB18030 制表符文本。

真实样例使用以下位置列：

| 位置 | 含义 |
|---|---|
| 第 2 列 | 飞行记录编号 |
| 第 5 列 | 纬度 |
| 第 6 列 | 经度 |
| 第 7 列 | 当前海拔，单位 m |
| 第 8 列 | 相对高度，单位 m |
| 第 9 列 | 温度 |
| 第 12 列 | 风速，单位 m/s |
| 第 13 列 | 风向，单位度 |
| 第 22 列 | PC 采集时间 |

代码索引从 `0` 开始，因此对应索引为 `1、4、5、6、7、8、11、12、21`。

### UAV AGL 算法

DEM 在 `plan` 阶段解析或下载后传入 UAV 读取器。每条记录计算：

```text
AGL_raw = (GPS 当前高度 + 对地高度) - DEM 高程
```

质量规则：

```text
AGL_raw < -4 m          → 剔除
-4 m <= AGL_raw <= 0 m → AGL = 0.1 m
AGL_raw > 0 m           → AGL = AGL_raw
```

manifest 会保留 GPS 当前高度、对地高度、DEM 高程和原始 AGL。

## WindNinja 站点 CSV

站点文件使用 WindNinja 官方 16 列格式。归档文件保留 UTC 时间；单步 Point Initialization 运行副本由 CFG 指定目标时刻。

## NetCDF

- 维度：`height, lat, lon`
- 风场：`U_wind, V_wind`
- 坐标：投影 `x/y` 与 WGS84 一维 `lat/lon`；`lat` 使用纬向轴，`lon` 使用经向轴
- 属性：`target_time_utc, output_height_m, mesh_resolution_m, source, Conventions`
- 文件名：`Wind3D_{UTC时间}_{分辨率}m_h{高度:03d}m.nc`

`wind_speed` 和 `wind_from_direction` 不写入 NetCDF，避免与 U/V 重复。KMZ 使用 WindNinja 的原生输出，不从 NetCDF 重建。
