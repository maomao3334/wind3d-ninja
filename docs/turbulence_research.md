# 独立湍流模式：质量复核与下一轮实验

复核日期：2026-10-12。固定 WindMaster 只用于驱动，UAV 只用于独立验证。本文区分历史结果、已查证的求解器含义与拟开展实验；运行成功不等于湍流预测通过验证。

## 1. 可复现输入与历史结果

输入目录：`G:/weather_nowcasting/demo_project/data`。

可复用的实际执行环境：`G:/weather_nowcasting/tools/WindNinja-3.12.2/bin/WindNinja_cli.exe`；历史 DEM 为 `C:/Users/Administrator/.wind3d-ninja/dem_cache/dem_02c44707b3e8e902.tif`（实读存在，EPSG:32648，28.6147358 m 像元，715×403，高程1776–2533 m）。其UTM范围为 x 227449.833–247909.369、y 2767243.260–2778774.998 m；使用新域时须再次确认覆盖，不能无条件复用。

| 输入 | 已读取内容 |
|---|---|
| `WindMaster20240403.zip` | 默认产品 `sins_level2_ave01min`，34692 条目录记录，UTC 2024-04-03 01:30–08:50，gate 28–5032 m |
| `UAV20240403-215643.xls` | 856 条目录记录，UTC 07:53:28–08:21:58，相对高度 -109.3–856.4 m；正相对高度 272 条；0–200 m 有 102 条 |
| `20240403.dat` | 本轮独立模式不作为驱动或验证源 |

这些是解析目录的范围，不是每分钟均有有效观测，也不是通过所有质量控制后的数量。WindMaster 在 UAV 时段内最早可用目标分钟是 08:06；07:53–08:03 的多数 UAV 正高度记录没有同时刻 WindMaster，不应外推填成同步验证。

历史产物位于 `G:/weather_nowcasting/demo_project/data/turbulence_output`。报告与 CSV 实读结果：

| 项目 | 历史值 |
|---|---|
| 目标 | 2024-04-03 08:16:00 UTC，即北京时间 16:16 |
| 输出高度 / 网格 | 10 m / 200 m |
| 有效 CSV 行 | 20 |
| 风速绝对误差均值 | 1.315 m/s |
| 圆周风向绝对误差均值 | 93.2° |
| 20 个采样点的模型值 | 全为 4.4 m/s、99° |
| 配对 UAV 时间 | 08:06:36–08:21:58 UTC |
| 配置背景 | 19.85452 m/s、92.06881°、输入高度 2529.757 m |

上述误差验证的是平均水平风。CSV 没有观测或模型 TKE、速度脉动、湍流强度列，不能据此宣称湍流质量合格。全部点相同可能源于路径集中在同一粗网格附近及 ASCII 数值精度，需检查采样网格索引；不能仅凭这一现象认定求解器返回常数场。

## 2. 历史实现的主要可解释性问题

1. 背景取所有 gate 的风速算术均值、矢量风向和高度均值。历史窗口选中 354 个坐标/gate，包含约 5 km 高度；将其平均为 2529.757 m 高度的单一入流，无法代表近地层观测。限制至 200 m 后，08:16 的 14 个坐标/gate 变为 5.63214 m/s、94.01418°、113.28571 m。该对照只证明输入发生大幅变化，不证明新选择物理上最佳。
2. `_select_fixed` 已按产品/坐标/gate 标识选窗口内最近时刻，不应再次累加同一 gate 的所有时刻。目录存在重复记录，仍应审计 ZIP 成员来源、重复记录和坐标移动，避免把同一仪器重复观测当独立站点。
3. 历史验证允许 ±600 s，且未检查 UAV 高度与输出高度差。08:16 ±120 s 没有正相对高度 UAV；严格同步验证应报告无配对，MAE 留空，而非沿用 20 行历史值。
4. 历史代码直接把 WindMaster 真北来向、m/s、AGL、地理风和运动补偿，以及 UAV 相对 AGL 和运动补偿置为已确认。文件名/产品名本身不足以证明这些定义，研究结论必须列明假设；优先核对仪器手册和标定记录。相对高度存在负值，不能据此自动判断它等于对地高度。
5. 背景风速算术均值与矢量平均风速不等价。需报告 `mean(speed)`、`hypot(mean(u), mean(v))` 与方向集中度，并按固定参考高度定义输入。多高度剖面先换算至同一高度再平均，比直接平均高度更有可解释性；换算又依赖粗糙度和近中性假设，应作为后续控制实验。
6. 历史报告/NetCDF 的 `turbulence_rans_point_initialization` 名称与实际 `domainAverageInitialization` 不一致；站点 CSV 留存作证据，并不表示 momentum 使用 point 同化。

## 3. WindNinja 3.12.2 的官方含义

以下引用固定为 `3.12.2`，避免把较新版本网页的 RNG k-epsilon 描述误用于本机。

- 本机 `G:/weather_nowcasting/tools/WindNinja-3.12.2/share/windninja/ninjafoam/2.2.0/constant/RASProperties` 与官方标记模板均指定标准 `kEpsilon`。这是平均流与闭合湍流模型，不是直接解析瞬时湍流涡的 LES。[官方 RASProperties](https://github.com/firelab/windninja/blob/3.12.2/data/ninjafoam/2.2.0/constant/RASProperties)
- 官方教程规定 momentum 适用于近中性条件，不能匹配输入测点；输入方向为气象来向，并说明高度采用相对植被顶部约定。物理 AGL 与求解器高度需要明确换算；现有 `trees` 是粗糙度假设，不能证明现场冠层高度。[3.12.2 教程，70–72、195–208 行](https://github.com/firelab/windninja/blob/3.12.2/doc/tutorials/tutorial_1/WindNinja_tutorial1.tex#L70)
- `ninjafoam.cpp` 将 `k`（m²/s²）转换为 `sqrt(2*k/3)` 的速度脉动（m/s）；column max 沿 AGL 列取最大值，KMZ 输出选择该列最大网格。这个量不等于 UAV 风速、不等于 10 m TKE，也不等于无量纲湍流强度。默认列高为 300 m。[官方转换与列采样源码](https://github.com/firelab/windninja/blob/3.12.2/src/ninja/ninjafoam.cpp#L2320)，[默认列高](https://github.com/firelab/windninja/blob/3.12.2/src/ninja/WindNinjaInputs.cpp#L154)
- 初始模板地面 `U` 为零固定值、顶部为零梯度；`k` 地面使用 wall function。侧面由程序按照入流方向生成；应保存实际 case 边界文件，而不能把模板占位符当最终配置。[官方 U 模板](https://github.com/firelab/windninja/blob/3.12.2/data/ninjafoam/2.2.0/0/U)，[官方 k 模板](https://github.com/firelab/windninja/blob/3.12.2/data/ninjafoam/2.2.0/0/k)
- 官方 `fvSolution` 对 p/U/k/epsilon 配置残差控制。exit 0 只是一项工程证据，研究记录还需最大迭代、残差、网格质量、DEM 平滑重试与湍流采样告警。[官方求解控制](https://github.com/firelab/windninja/blob/3.12.2/data/ninjafoam/2.2.0/system/fvSolution)
- CLI 明确拒绝 `pointInitialization + momentum`、`non_neutral_stability + momentum`，且湍流标志依赖 `momentum_flag` 与 `write_goog_output`。即使界面不要求最终KMZ副本，也需生成原生Google输出才能取得此湍流产品。`diurnal_winds` 没有同样的冲突，momentum源码有添加日变化风的分支，不能宣称它完全不支持；本轮固定关闭，后续需分别验证热力叠加后的平均风和湍流输出语义。[3.12.2 CLI约束，853–867行](https://github.com/firelab/windninja/blob/3.12.2/src/ninja/cli.cpp#L853)，[日变化分支](https://github.com/firelab/windninja/blob/3.12.2/src/ninja/ninjafoam.cpp#L529)

## 4. 本轮可立即落地的实验

先建立严格配对和低层驱动，再扩大运行数量。建议首个目标为 **08:06 UTC、10 m、200 m**；相同目标 ±120 s 内，正相对高度且离 10 m 不超过 10 m 有 30 条，实际时间 08:04:00–08:06:58、高度 0.3–10.8 m。这包含离 10 m 较远的低层点，建议主分析进一步限高度差 ≤5 m，同时保留 10 m 容差作敏感性对照。08:07 可做第二案例；08:16 用于确认零配对路径。

| 实验 | 控制项与改变项 | 必留证据 / 判断 |
|---|---|---|
| A0：历史审计 | 读取原 cfg、报告、CSV，不重跑 | 保留原93.2°；标记时间/高度不匹配，不把它当基准精度 |
| A1：严格同步 | 08:06，WindMaster gate ≤200 m，UAV ±120 s，输出10 m/r200 | 当前解析对应7 gate：7.91 m/s、100.53226°、113.28571 m；记录来源高度、时间差、输入均值算法和高度假设 |
| A2：单低层驱动 | 同 A1，仅取28 m gate，固定其参考高度 | 当前解析值7.49 m/s、113.4°、28 m；与 A1 比较速度偏差、圆周方向误差、u/v RMSE；不把更小误差自动解释为湍流更准确 |
| A3：时间/高度敏感性 | 同一 A1 风场，UAV时间窗60/120/600 s，高度差5/10 m | 无须新增求解；报告行数、唯一网格数、唯一分钟数、时间/高度偏差分布，避免重复配对伪增样本 |
| A4：无同步负例 | 08:16，±120 s，正相对AGL | 预期零配对、误差null；显式报告原因，验证批次可完成而评价不成立 |
| A5：方向诊断 | 同一已配对数据，离线检查0/±90/180°及u/v符号 | 旧20行平移MAE分别93.2/47.6/132.4/86.8°；只用于定位候选，不写回观测，不凭最低误差推断仪器定义 |

若只能实跑一个案例，选择 A1；A2 是第二优先级。保存每例原始输入定位、筛选前后计数、产品选择、DEM路径/CRS/范围/分辨率、WindNinja版本、完整cfg、日志、原生KMZ及模型ASCII。主结果应注明这是“固定低层风驱动的平均风独立验证”。

### 4.1 本轮已完成的实跑结果

已实读 `output/quality_research_20261012` 下三个 `_final` 批次的报告与 CSV。每批次均完成一例，报告各有1个NetCDF和1个KMZ；模式标识为 `turbulence_rans_domain_average`。以下两个08:06案例使用相同的30个UAV记录（记录ID及顺序已核对），输出均为10 m、200 m网格，时间窗±120 s、高度差容差10 m。

| 实跑 | 背景输入 | 配对数 | 速度MAE (m/s) | 速度RMSE (m/s) | 速度bias (模型−观测，m/s) | 方向MAE |
|---|---|---:|---:|---:|---:|---:|
| [08:06，≤200m背景报告](../output/quality_research_20261012/background_200m_final/turbulence_report.json) | 7 gate，7.91 m/s、100.53226°、113.28571 m | 30 | 1.544000 | 1.941949 | -1.073333 | 58.6° |
| [08:06，28m背景报告](../output/quality_research_20261012/background_28m_final/turbulence_report.json) | 1 gate，7.49 m/s、113.4°、28 m | 30 | 2.023333 | 2.373844 | +1.736667 | 54.2° |
| [08:16，零配对报告](../output/quality_research_20261012/zero_matches_final/turbulence_report.json) | ≤200m背景，同一输出高度/网格/容差 | 0 | null | null | null | null |

在这组相同配对的控制实验中，≤200 m背景的速度误差较小，28 m背景的方向误差较小，没有一个方案在两项指标上均占优。**不能把历史08:16、20行、方向MAE93.2°与本轮08:06、30行直接比较成“精度改善”**：目标时间和配对集合都不同。

这30条UAV记录实际高度为0.3–10.8 m，时间偏差为−120至+58 s，全部落入同一个200 m网格单元；≤200 m背景的采样模型值全为3.37 m/s、111°，28 m背景全为6.18 m/s、126°。30行并不代表30个独立空间位置。10 m高度差容差把接近地面的观测也与10 m模型比较，垂直差异应纳入结果限制。

另对已生成CSV离线收紧高度差至≤5 m，得到同一18条记录；这只是筛选敏感性分析，没有新增求解。≤200 m背景的速度MAE/RMSE/bias为1.568889/2.016270/−1.141111 m/s、方向MAE58.722222°；28 m背景分别为2.033333/2.355504/+1.668889 m/s、53.722222°。结论仍为速度和方向存在取舍，且样本集中于同一格点。

08:16零配对批次的 `validation.csv` 实读包含完整字段表头和0个数据行，误差均为null；报告记录272条正高度UAV位于时间窗外，不能把null转为零误差。两08:06批次的原始配对证据分别为 [≤200m CSV](../output/quality_research_20261012/background_200m_final/validation.csv) 与 [28m CSV](../output/quality_research_20261012/background_28m_final/validation.csv)。

本轮报告仍将真北来向、m/s、地理风/运动补偿、正gate视为AGL、UAV相对高度视为AGL及0 m冠层列为假设。研究仍只验证平均水平风；原生column max速度脉动产品存在，不代表其数值精度已由UAV验证。本轮未建立独立的湍流观测真值。

## 5. 后续实验矩阵

| 研究问题 | 最小实验 | 成功证据 |
|---|---|---|
| 时空取样是否压平差异？ | 记录最近网格行列和中心距；200/100/50 m各一例；角度插值应先转u/v | 同一实际配对集合比较，不把输出栅格分辨率误认为仪器空间分辨率；报告独立网格数 |
| DEM与高度约定是否一致？ | 固定同一DEM，核对仪器位置高程、水平CRS、垂直基准与输入/输出冠层约定；暂不混用GPS高度和相对高度算法 | 高度来源能追到原字段和确认依据；AGL/植被顶部高度分别记录 |
| 入流剖面和地表粗糙度影响多少？ | 单28m与≤200m剖面对照；草/灌木/树仅作为已注明的假设分支 | 在同一UAV保留集计算u/v和方向指标，记录输入高度换算；不能靠验证集反复调参后称独立验证 |
| 边界是否影响观察区？ | 相同驱动、网格、DEM源，在2/5/10km缓冲各一例；比较内区同一网格值 | 内区变化随域增大收敛，边界距离与入口方向可解释；保存最终边界文件 |
| 数值是否收敛？ | 保存SIMPLE残差和重试；固定网格改变迭代限额；不同时改变地形、粗糙度与时间窗 | 速度及速度脉动变化均随迭代趋稳，且无湍流采样失效告警；仅exit0不够 |
| 真正湍流是否可验证？ | 寻找同位置/固定高度高频u/v/w、明确平均窗口；核对原始秒产品及运动补偿 | 计算k_obs=0.5(var(u)+var(v)+var(w))，与同高度k或sqrt(2k/3)比较；缺少w时只能报告水平脉动代理 |

无人机沿路径的风速标准差同时含时间变化、空间梯度、飞行运动、姿态误差及仪器滤波；不能直接作为固定点湍流。`SinsAve01min` 的 `StdDev`/`ZWind StdDev` 也需核对平均窗口、变量和质量规则后才可形成观测指标；不要将 `DataObtainRate`、`Sigma`、`Beta` 等名字猜成湍流参数。原生 column max 产品需要列观测，单高度数据不能直接验证其最大值。

## 6. 结果报告要求

- 先报告可配对数量与拒绝原因，再报告MAE/RMSE。列出源记录ID、原始与UTC时间、时间差、观测/模型高度与差值、坐标、网格索引、背景层范围。
- 风向用圆周差；速度接近零时方向不稳定，应分层报告并给出阈值依据。u/v由明确的气象来向约定转换，报告组件偏差/RMSE与标量速度误差。
- 时间窗共享观测、相邻飞行记录和重复网格均非独立样本。按飞行段或分钟分块评估，不把每个重复CSV行当新增独立证据。
- NetCDF 当前是平均风产品；原生KMZ中的column max速度脉动单独标注单位、列高和求解器版本。未读取出数值湍流字段时，状态应为“湍流产品生成/存在已检查，数值湍流精度未验证”。
- 方向、高度、运动补偿或稳定度尚未确认时，保留假设清单及待核对资料；结论限定在这些假设内。
# Corrected profiles and independent station analysis (2026-10-12)

The follow-up implementation converts each physical station profile to a common vegetation-top reference height using U/V components, then gives physical stations equal background weights. UAV GPS and launch-relative height are separate definitions; radar platform AGL is configurable. The user confirmed aircraft GPS altitude, launch-relative UAV height, and a radar installed level with the ground. Manual evidence and remaining datum/launch-site assumptions are recorded in `output/profile_assimilation_20261012/manual_height_review.md`.

The independent `assimilation` CLI/UI mode analyses each fixed station separately on a RANS background. On the same 30 held-out UAV pairs at 50 m AV and a 200 m mesh, speed MAE was 4.573 m/s for the background and 3.795 m/s for the analysis, while direction MAE increased from 101.562 to 107.548 degrees. These are mean-wind metrics from one flight and do not validate turbulence skill. The previous 10 m results below used different height definitions and matching sets and must not be interpreted as a direct before/after comparison.

See [station analysis method and height options](station_assimilation.md), `output/profile_assimilation_20261012/product_qa.md`, and the paired/synthetic QA figures for reproducible evidence. Synthetic two-station results demonstrate local constraints; compatible TKE is absent from the real-data observations. This analysis is not a coupled RANS U/k/epsilon re-solve.
