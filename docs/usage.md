# 使用指南

## 输入文件夹

把以下文件放进同一个输入目录，文件名不需要固定：

- `*.zip`：WindMaster 数据。
- `*.dat`：超声波测风仪数据。
- `*.xls`、`*.xlsx`：UAV 数据。
- `*.tif`、`*.tiff`：可选 DEM。

程序会递归扫描输入目录。DEM 若存在且覆盖观测范围则优先使用；若不存在或覆盖不足，则自动下载并缓存 SRTM DEM。

UAV 文件中的 GPS 当前高度和对地高度与 DEM 高程共同计算 AGL：`(GPS 当前高度 + 对地高度) - DEM 高程`。结果小于 `-4 m` 的记录丢弃，`-4 m` 到 `0 m` 的记录修正为 `0.1 m`，大于 `0 m` 时保留计算值。DEM 在 `plan` 阶段优先匹配或自动下载。

## Git Bash 准备

```bash
git clone https://github.com/maomao3334/wind3d-ninja.git
cd wind3d-ninja
py -3.11 -m pip install -e .
wind3d-ninja doctor --install
wind3d-ninja --help
```

`doctor --install` 会下载并校验固定的 WindNinja `3.12.2` Windows 运行时。默认安装到 `~/.wind3d-ninja/windninja/3.12.2/`。官方安装器需要管理员批准，Windows 弹出 UAC 对话框时点击“是”即可。若要放到其他磁盘，先在 Git Bash 中设置：

```bash
export WINDNINJA_HOME="/g/tools/WindNinja-3.12.2"
wind3d-ninja doctor --install
```

## 检查输入

```bash
wind3d-ninja doctor
wind3d-ninja inspect ./input_data
```

`inspect` 只读取和汇总输入，不运行 WindNinja，也不要求用户输入时间。

## 查看自动计划

```bash
wind3d-ninja plan ./input_data --height 10 20 50
```

时间轴从经过 DEM/UAV 高度筛选的有效观测中自动提取并按 UTC 分钟去重。`plan` 会先解析或下载 DEM，再列出目标时次、输出高度、分辨率、覆盖范围、实际 DEM 路径和目标文件名。

同时规划多个分辨率：

```bash
wind3d-ninja plan ./input_data --height 10 20 50 \
  --resolution 50 \
  --resolution 100 \
  --resolution 200
```

## 正式运行

最简命令：

```bash
wind3d-ninja run ./input_data --height 10 20 50
```

它会自动处理全部观测时次，默认分辨率为 `200 m`，并同时生成 NetCDF 和 KMZ。默认输出到：

```text
./input_data/wind3d_output
```

运行期间会实时显示 `[当前任务/总任务]`、目标 UTC 时间、高度、分辨率，以及 WindNinja 的求解百分比和每阶段日志。

### 自定义时间区间

不写时间区间时处理全部自动提取的观测时次。以下命令只输出 UTC+8 的 `2024-04-03 16:16` 到 `16:30`，区间两端都包含：

```bash
wind3d-ninja run ./input_data --height 10 20 50 \
  --resolution 50 \
  --time-range "2024-04-03 16:16" "2024-04-03 16:30"
```

无时区时间使用数据源配置的时区偏移，当前默认 UTC+8。显式 UTC 时间也可以写成：

```bash
--time-range "2024-04-03T08:16:00Z" "2024-04-03T08:30:00Z"
```

多分辨率运行：

```bash
wind3d-ninja run ./input_data --height 10 20 50 \
  --resolution 50 \
  --resolution 100 \
  --resolution 200
```

自定义输出位置和覆盖范围：

```bash
wind3d-ninja run ./input_data --height 10 20 50 \
  --resolution 100 \
  --output ./results \
  --bounds 24.9 25.1 102.3 102.5
```

`--bounds` 四个值依次为：`最小纬度 最大纬度 最小经度 最大经度`。

如果不写 `--bounds`，程序根据全部观测坐标和 `--buffer` 自动计算覆盖范围：

```bash
wind3d-ninja run ./input_data --height 10 20 50 --buffer 10
```

## 输出

```text
wind3d_output/
├── nc/Wind3D_20240403T081600Z_100m_h010m.nc
├── nc/Wind3D_20240403T081600Z_100m_h020m.nc
├── nc/Wind3D_20240403T081600Z_100m_h050m.nc
├── kmz/Wind3D_20240403T081600Z_100m_h010m.kmz
├── kmz/Wind3D_20240403T081600Z_100m_h020m.kmz
├── kmz/Wind3D_20240403T081600Z_100m_h050m.kmz
├── stations/<UTC时间>/<高度>/
├── windninja/<UTC时间>/<高度>/<分辨率>/
├── dem/
├── observations.csv
└── manifest.json
```

每个 NetCDF 文件只对应一个真实 WindNinja 运行时次、一个高度和一个分辨率。文件名中的时间统一使用 UTC。

NetCDF 只保存 `U_wind` 和 `V_wind`，不重复保存风速和风向。KMZ 原样复制 WindNinja 的原生输出并采用标准文件名。KMZ 默认开启，可用 `--no-kmz` 关闭。
