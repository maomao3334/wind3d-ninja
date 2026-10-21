# Changelog

## 1.1.9 - 2026-10-21

- 为中文、空格和较长输出路径使用短 ASCII 私有工作区，规避旧版 WindNinja 写图形产物时的访问异常，计算后回存用户目录。
- 隔离外部求解器子进程的冻结包 Qt/GDAL/PROJ 路径，保留包外用户配置。
- 完成 226 项自动回归、宿主机七组界面流程及全新 Windows 10 的安装、三组计算输出、GUI 和卸载保留检查。
- 原生场景超过测试脚本等待上限后完成，已补验输出；原始退出码未取得，不能记为 0。CLI `--json` 仍有进度提示前缀。
- 明确现场风向精度未通过验收：同化后圆周 MAE 103.384928°，基线 103.410125°，有效记录 30/844，11 个不同 XYZ 位置。输入基准、软件解释与模型误差尚未完成归因，不用验证误差拟合固定偏角。
- 新增 1.1.9 发布说明、验证范围与安装包 SHA256；Windows 可用性通过不等于科学精度通过。

## 1.1.8

- OpenFOAM DLL 按版本放到各自求解器 EXE 旁，并记录副本哈希，修复部分 Windows 启动上下文不搜索 PATH 时的加载失败。
- 修正投影网格北与真北混用：NetCDF、网格验证和原生求解采样统一输出真实东/北风分量。
- 固定站真北风先旋转到原生网格坐标再进入方程约束；KMZ 箭头几何与真北风向标签一致。
- 分开识别 SinsAve 与 SinsVectorAve，避免同一产品集合混入两种平均方法。
- 新增独立大地线方向、原生约束、验证采样和箭头几何回归；不使用 UAV 验证误差拟合额外角度。
- 该修正不等于外场绝对风向精度认证；仪器参考方向、偏置应用和当次飞行元数据仍需确认。

## 1.1.7

- 修复观测时间扫描在后台线程更新界面造成的 Windows 堆损坏退出。
- 扫描结果、错误和完成通知统一回到界面线程，保留按代次丢弃过期结果与待刷新队列。
- 新增成功及失败扫描回调线程的回归测试，更新简化高度控件的测试定位。

## 1.1.6

- “确认高度与风向”仅保留风向、高度基准和雷达平台高度，删除重复的速度单位、地理方位、运动补偿和相对 AGL 确认项。
- 导入气象数据统一按 m/s、地理风向和已完成运动补偿处理，去掉“显式假设”等开发说明。

## 1.1.5

- 首页按当前模式展示必需数据，分步确认时间、高度与输出，附加站点 CSV 移到首页。
- 高级设置统一放第二页，新增随模式切换的“使用方法”页，按用途、准备材料、操作步骤、结果解释组织。
- 手动背景风跳过观测校准，标明区域必填；保留 RANS 稳定度互斥保护。

## 1.1.4

- 将长表单拆为开始运行、时间校准、高度与仪器、高级设置和运行日志；按模式显示相关项。
- 常驻运行与输出按钮，必填原生站点 CSV 保留在首页，任务启动自动切换日志页。
- 保留时间解析、高度校验及 RANS 中性大气兼容保护，计算默认值不变。

- 修复湍流及同化模式可携带日变化风和 alpha_stability，导致原生求解前报不兼容错误。
- RANS 模式自动清除并禁用日变化风、非中性稳定度和 alpha，显示中性条件提示；常规模式仍可以配置这些选项。

# 1.1.2

- 扫描后选择 UAV、WindMaster 的实际时间字段及来源时区，预览原始/UTC 范围与异常记录。
- 时间配置贯通扫描、常规模式、研究背景与 UAV 独立验证；保留原始时间及配置来源。
- UAV 可明确选择设备取样时间或 PC 采集时间，不按误差自动切换。

# 1.1.1

- 修复中文和空格输出目录导致 Windows 原生 DEM 转 STL 失败；计算工作区与结果路径自动转换。

# 1.1.0

- 统一 Qt Windows 界面：常规、独立湍流、逐站 OI、原生 U/k/epsilon 方程耦合同化。
- 保留观测时间选择、站点稳定度和手动背景风；修正飞行器 GPS 高度重复累加。
- Windows 原生运行库随安装包分发，无需 WSL；新增完整新功能操作说明。

# Changelog

## v1.0.4 - 2026-10-11

- Supports non-neutral stability with point-initialized station observations, including validated explicit alpha bounds.
- Adds an explicit turbulence workflow while preserving the existing station point-initialization workflow.
- Keeps turbulence solver files in the run output's staged DEM area, so repeating a turbulence run does not modify a DEM-only input directory.
- Prepares the Windows x64 installer for the updated workflows.

## v1.0.3 - 2026-10-10

- Fixed WindNinja 3.12.2 compatibility while preserving the default `pointInitialization` workflow for station observations.
- Reject `turbulence_output` and `non_neutral_stability` before execution; omit momentum-only flags from the normal point-station configuration.
- Made the Windows build script include the current checkout's `src` directory ahead of any stale editable Python install.
- Rebuilt the portable UI/CLI and installer as `Wind3D-Ninja-Setup-1.0.3-x64.exe`.

## v1.0.2 - 2026-09-23

- Made bundled WindNinja discovery relocatable when an installation is copied to another computer.
- Ignored stale machine-specific `WINDNINJA_BIN`/`WINDNINJA_HOME` values when the portable runtime is available.
- Removed the default absolute WindNinja path from the desktop UI and made runtime errors use relative log references.

## v1.0.1 - 2026-09-23

- Desktop UI time ranges now use dropdowns populated from timestamps found in the selected input data.
- Added a refresh action for available observation times and documented the new workflow.
- Published the Windows installer as `Wind3D-Ninja-Setup-1.0.1-x64.exe`.

## v1.0.0 - 2026-09-22

- Added the Windows x64 installer with bundled WindNinja 3.12.2.
- Added the Tk desktop UI and isolated CLI/UI runtimes.
- Added reproducible full-install verification for `doctor`, `inspect`, `plan`, `run`, UI startup, and uninstall.
- Fixed WindNinja 3.12.x configuration compatibility for the default point-initialization path.
- Ignored auxiliary WindMaster CSV/TXT files that do not contain station observations.

## v0.1.1 - 2026-08-16

- Fixed duplicate station files caused by the +/-120-second selection window; each WindNinja station now contributes only its nearest record for a target time.

## v0.1.0 - 2026-08-15

Initial fixed release of `wind3d-ninja`.

- Added `doctor`, `inspect`, `plan`, and `run` commands.
- Added WindMaster ZIP, ultrasonic DAT, and UAV XLS/XLSX readers.
- Added local-DEM-first resolution with automatic SRTM download and projection.
- Added DEM-based UAV AGL calculation and quality filtering.
- Added source-specific observation time windows using preserved timestamp seconds.
- Added multi-time, multi-height, and multi-resolution WindNinja execution.
- Added U/V-only Wind3D NetCDF output, native KMZ retention, and manifests.
- Added real-time run progress, reference-compatible one-dimensional latitude and longitude axes, tests, and documentation.
- Added a checksum-pinned WindNinja 3.12.2 Windows runtime installer through `doctor --install`.
- Added source provenance for the `maomao3334/windninja` fork and third-party license notices.
