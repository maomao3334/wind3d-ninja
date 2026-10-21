# 逐站观测分析与高度修正

逐站模式先运行 WindNinja RANS 背景，再用独立 Gaussian optimal interpolation（OI，最优插值）分析平均水平风。每个有效固定站分别进入观测算子和协方差系统。它输出的是观测约束后的统计分析场，**没有重新联立求解 RANS 的质量、动量、k、epsilon 方程**；产品标识为 `station_optimal_interpolation_analysis`，报告记录 `coupled_to_rans=false`。UAV始终保留作独立验证，不能作为逐站CSV约束。

## 1. 逐站剖面与背景输入

`profiles.py` 把同一来源、产品、飞行标识和实际站点的多高度观测组成剖面；显式 `station_group_id` 优先，否则按默认2 m位置容差分组。同一高度重复值先在u/v分量上平均，再转换到统一参考高度：

- 目标恰落观测gate：使用该gate的u/v。
- 目标被剖面包围：在高度上对u/v线性插值，避免跨0/360°直接插值风向。
- 目标在剖面之外：默认拒绝；仅明确启用 `--allow-profile-extrapolation` 后，采用近中性对数剖面拟合。记录外推标志、拟合残差、粗糙度、冠层和位移高度。

对数自变量为 `log((height_av + canopy_height - displacement_height)/roughness)`，有效高度必须高于粗糙度平面。未知冠层、粗糙度或稳定度仍是研究假设，不能把外推当实际高度观测。

背景输入先将每个站的剖面换算至 `--reference-height`（默认50 m，植被顶部以上），再按**物理站等权**平均u/v；不会因为一个站有更多gate而让它得到更多站权重。逐站分析则继续使用各站约束，不把这个站均值当成唯一观测。

## 2. UAV高度必须先确认

研究模式默认 `--uav-height-reference unknown`，会阻止运行。选择的定义与原字段、DEM垂直基准一致后，再进行AGL换算，最后统一减去冠层高度得到植被顶部以上高度AV。GPS高程若是椭球高，不能未经转换与正高DEM混减。

| UAV定义 | AGL换算 | 所需确认或参数 |
|---|---|---|
| `gps_altitude_minus_dem` | 当前GPS绝对高程 − 当前位置DEM | GPS字段确实是当前绝对高度，且垂直基准与DEM相同 |
| `relative_to_launch` | 起飞地面海拔 + 相对起飞高度 − 当前位置DEM | `--uav-launch-position LAT LON` 用DEM取起飞地面海拔，或 `--uav-launch-ground-altitude` 直接给定同基准海拔 |
| `relative_agl` | 已确认的相对AGL字段 | 同时指定 `--uav-relative-agl-confirmed`；相对起飞高度不能冒充当前位置AGL |
| `gps_plus_relative_minus_dem` | 旧GPS起飞海拔 + 相对高度 − 当前位置DEM | 仅兼容旧字段约定，必须指定 `--uav-legacy-gps-is-launch-altitude-confirmed` |

`relative_to_launch` 未提供起飞位置或起飞地面海拔时会阻止运行；不自动把第一条UAV坐标当起飞点。地面以下/冠层以下等非法高度会拒绝并计数。WindMaster可选 `agl` 或 `platform_vertical_offset`；后者按平台海拔 + gate垂直偏移 − DEM换算。风向可显式选择真北来向/去向，归一化后统一为气象来向。

CLI当前为方向、m/s、地理风及运动补偿提供固定默认假设；GUI研究设置要求明确确认地理风/运动补偿。无论从哪个入口运行，都应保留仪器资料与假设记录。软件选项不是仪器已经完成运动补偿的证据。

## 3. Gaussian OI的统计含义

本批数据的高度约定已由用户确认：UAV GPS 字段是飞行器海拔，CSV“对地高度”实际相对起飞点；雷达安装在地面、与地同高。GPS 海拔不能再次加相对起飞高度。采用起飞点方案时仍须提供起飞位置或同 DEM 基准的起飞地面海拔；这里没有自动用 GPS 海拔替代 DEM 起飞地面海拔。

已核对用户提供《机载雷达-用户手册》版本 1.1、2022.11，第 2.7.2/2.7.3 节（PDF 第 28/29 页，印刷第 27/28 页）：Level1/2 的列为高度门，不对这些高度列再次乘扫描俯仰角；Level2 的 WindSpeed StdDev 和垂直速度 StdDev 仍不等于完整三分量 TKE。手册没有为各数据文件证明安装基准，当前 AGL 默认依据用户对地面安装的确认。

保留 `--windmaster-height-reference platform_vertical_offset --windmaster-platform-agl <米>`，将平台对地高度加到垂直 gate；如文件已有同 DEM 基准的平台绝对海拔，可省略平台 AGL 参数，按“平台海拔 + gate − DEM”换算。GUI 可选择平台垂直偏移并填写平台对地高度；未知基准选项会阻止运行。0 m 也必须显式填写，不能把空值自动当零。


分析对u、v使用同一观测位置与双线性采样算子H：

`analysis = background + B Hᵀ (H B Hᵀ + R)⁻¹ (observations − H background)`。

水平方向B采用Gaussian距离相关；R为每站误差方差组成的对角阵。不同站即使在同一网格附近，也分别参与 `H B Hᵀ`，并记录观测、背景、分析和拒绝原因。观测不确定度是正数，分析并不强制逐站精确命中。

| 参数 | 默认 | 作用 |
|---|---:|---|
| `--horizontal-length-scale` | 1000 m | 水平协方差相关长度 |
| `--background-wind-sigma` | 2 m/s | 平均风背景标准差 |
| `--observation-wind-sigma` | 0.5 m/s | 默认WindMaster约束标准差；额外CSV可逐站指定 |
| `--vertical-length-scale` | 30 m | TKE分析的垂直相关长度；平均风先通过站点剖面转换到输出高度 |

这些是统计设置，不是已标定仪器精度。默认参数不能保证减小独立验证误差；禁止用保留UAV不断调参，再把该UAV称作未参与选择的独立测试集。分析不保证质量/动量守恒，也不能当已经重新收敛的RANS状态。

## 4. 额外固定站CSV

`--station-observations` 指向UTF-8 CSV。必需字段为 `station_id,time_utc,latitude,longitude,height_m,height_reference`，并提供 `u_ms,v_ms` 或 `speed_ms,direction_deg`。`time_utc` 必须为带时区ISO-8601；方向为真北气象来向，速度m/s。`height_reference` 只接受 `agl` 或 `vegetation_top`；AGL减去本批次冠层高度后应为正。

以下仅展示字段，不代表实际观测：

```csv
station_id,time_utc,latitude,longitude,height_m,height_reference,speed_ms,direction_deg,wind_sigma_ms
station_A,2024-04-03T08:06:00Z,25.0255,102.3720,50,vegetation_top,5.0,110,0.5
station_B,2024-04-03T08:06:00Z,25.0300,102.3800,50,vegetation_top,4.5,125,0.8
```

可选 `source`（不接受 `uav`）、`wind_sigma_ms`（CSV未给定时默认1 m/s）、`k_m2s2`、`k_sigma_m2s2`。同一 `station_id/time_utc/height_m` 重复键会拒绝；站点在网格之外或采样触及无效值会记录排除原因。额外固定站CSV是在背景生成后加入OI，不改变原生RANS背景驱动。

## 5. 真实TKE与分析先验

只有已有物理定义与平均窗口的 `k_m2s2`，或完整三分量统计 `sigma_u_ms,sigma_v_ms,sigma_w_ms`，才形成TKE约束。三者是分量标准差（m/s），代码计算 `k = 0.5*(sigma_u² + sigma_v² + sigma_w²)`；输入方差时应先转换为标准差，不能把方差直接写进sigma字段。仅有水平两分量或标量风速 `StdDev` 不构成完整TKE。

TKE当前先验是有效训练固定站k的**空间常数均值**，背景标准差为 `max(0.1, k_prior)`；未给 `k_sigma_m2s2` 时采用 `max(0.1, 0.2*k)`。OI使用实际观测高度与输出高度差计算Gaussian垂直协方差。分析后负k截为0并记录数量。这些均为统计假设，尤其常数先验没有从原生RANS提取高度k；它不应称为“原生湍流场同化”。

无兼容统计时不创建 `TKE_analysis`，报告状态为 `no_compatible_turbulence_observations`。WindNinja原生KMZ的column maximum `sqrt(2*k/3)` 是列最大速度脉动，既非特定高度k，也不能作为 `k_m2s2` 输入。[3.12.2转换源码](https://github.com/firelab/windninja/blob/3.12.2/src/ninja/ninjafoam.cpp#L2320)

## 6. CLI与GUI

明确选择输出高度50 m与网格200 m；参考高度和输出高度是不同设置。下例假定已经核实当前GPS字段与DEM为同一绝对高度基准；如不符合，应按第2节改用正确的定义。

```powershell
python -m wind3d_ninja --json assimilation <input-folder> --height 50 --resolution 200 --reference-height 50 --uav-height-reference gps_altitude_minus_dem --time-range <start> <end> --max-cases 1 --station-observations <fixed-stations.csv> --horizontal-length-scale 1000 --background-wind-sigma 2 --observation-wind-sigma 0.5 --vertical-length-scale 30
```

没有额外CSV可省略 `--station-observations`，使用有效WindMaster剖面约束。时间、高度配对容差、背景gate范围和显式DEM参数与 `turbulence` 相同。输出高度必须被各站观测剖面支持，或明确允许有记录的剖面外推。

GUI选择“逐站观测同化”进入独立分析模式。它首次选择时把原始默认高度组替换为50 m、分辨率组替换为200 m，并显示站CSV和协方差设置。研究设置包含统一参考高度、冠层/位移高度、外推开关、仪器方向与高度定义，以及UAV起飞位置或海拔。高度定义未知、确认不完整或起飞信息缺失时不能运行。研究模式关闭非中性稳定度选项。

## 7. 输出与质量检查

- `background/` 保存原生RANS运行、平均风和column maximum湍流产品。
- `nc/WindAnalysis_*.nc` 保存分析后的 `U_wind,V_wind`，并保留 `U_wind_background,V_wind_background`；有兼容TKE时新增 `TKE_analysis`（m²/s²）及常数先验定义。
- `kmz/WindAnalysis_*.kmz` 显示分析平均风；其图层说明明确区别于原生湍流KMZ。
- `assimilation_report.json` 保存参数、每站分析诊断、剖面换算、TKE先验、仪器政策和守恒限制。
- `validation.csv` 使用保留UAV，同时列出背景与分析误差；二者在相同UAV集合上使用同一双线性H，拒绝时间/高度/域不匹配。零配对误差为null，不能改成零。

应同时审查每站约束是否实际使用、剖面残差、采样边界、训练与验证来源是否独立、UAV时间/高度差，以及背景与分析的同集合指标。单例或同网格的重复记录不能证明普遍精度提高。本说明未给出尚未实跑的精度结论。

## 8. 为什么不称原生RANS耦合同化

[本机可行性记录](../output/profile_assimilation_20261012/native_feasibility.md) 查证了包内OpenFOAM 2.2：动量方程调用 `fvOptions(U)`，标准k-epsilon的k/epsilon方程没有插入同样源项接口；仅写 `system/fvOptions` 不能实现三场逐站耦合。WindNinja的existing-case更新路径还会改写初始化和部分case文件。真正耦合模式需兼容Windows/MinGW ABI的自定义湍流模型/库、保留完整case并重新收敛求解，同时证明三个方程都接收了站点源。

权威依据：[OpenFOAM 2.2.x动量方程](https://github.com/OpenFOAM/OpenFOAM-2.2.x/blob/master/applications/solvers/incompressible/simpleFoam/UEqn.H)，[kEpsilon::correct方程实现](https://github.com/OpenFOAM/OpenFOAM-2.2.x/blob/master/src/turbulenceModels/incompressible/RAS/kEpsilon/kEpsilon.C#L216)，[WindNinja 3.12.2代码](https://github.com/firelab/windninja/tree/3.12.2)。
