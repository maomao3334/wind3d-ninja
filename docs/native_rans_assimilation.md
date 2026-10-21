# 原生 RANS 逐站观测同化

UI 的第四个模式“原生 RANS 耦合同化（Windows）”和 CLI `native-assimilation` 使用 Windows 原生 OpenFOAM 8。背景案例由 WindNinja 3.12.2 生成，再恢复完整地形网格和收敛检查点。先求解无观测源的 OpenFOAM 8 基线，再加入逐站源项重新求解。

原生求解同时包含连续性、动量、k 和 ε 方程；νt = Cμ k²/ε 反馈到动量方程。每个站点在指定水平半径和垂直半径内建立独立 cellSet，源项为 `λ(q_obs − q)`，λ=1/松弛时间，单位 s⁻¹。自带 C++ `stationNudging` 模型将阻尼项以 `−fvm::Sp(λ,q)` 隐式加入原生方程右侧。重叠站点的源项相加。该方法是稳态 RANS 中的局部松弛同化；松弛时间控制约束强度。

输出 U/V/W、TKE、ε、νt 来自重新求解后的原生场，通过 OpenFOAM cellPoint 采样；同时保留同一 OpenFOAM 8 基线。求解器必须报告各方程初始残差收敛、有限质量连续性误差、正 k/ε 和一致的 νt；迭代耗尽会报错。

独立 UAV 验证在同一实际 XYZ 上直接采样原生基线与同化场：MSL=局部 DEM+观测物理 AGL，AV 高度仅用于匹配容差。它不依赖固定网格的四角插值；网格节点若处于 CFD 地面以下，仍保留为缺测，不填洞。报告记录采样方法、实际高度、有效覆盖数与基线/同化误差。当前真实地形配合合成站约束的检查有 30/30 个有效 UAV 匹配；平均风 MAE 从 0.9038 变为 0.9044 m/s，未证明精度改善。

`stationKEpsilon` 保留标准 k–ε 的输运、生成、耗散与黏性反馈；为防止 ε 观测源被壁面矩阵覆盖，在壁面操作后加入 ε 恢复源，并保存上轮 ε 作为线性求解初值。近壁 ε 的精确壁面值约束因此成为壁面 prior 与观测的有限强度折中；原壁面操作已清除的近壁 ε 扩散连接不会重新生成。这是明确的近壁闭合适配，需用实测和网格敏感性验证。每次求解先运行 50 个耦合步，再检查真实初始残差；总迭代预算至少为 51。

## 观测 CSV

必需列：`station_id,time_utc,latitude,longitude,height_m,height_reference`。风可使用 `u_ms,v_ms` 或 `speed_ms,direction_deg`。高度参考为 `agl`（地形以上）或 `av`（植被顶以上）；求解网格使用 MSL=DEM+物理 AGL。

湍流站提供 `k_m2s2`，或同时提供 `sigma_u_ms,sigma_v_ms,sigma_w_ms` 以计算 k。另提供 `epsilon_m2s3`，或提供正的 `mixing_length_m`，明确采用 ε=Cμ^(3/4) k^(3/2)/L。后者是长度尺度闭合假设，报告会记录推导来源。

纯风站可以与湍流站混合，但每个求解时次至少要有一个兼容的 k/ε 观测站。缺少 `w_ms` 时，该站采用 w=0 的模型假设，会影响局部垂直速度；已有垂直风观测应填写。UAV 作为独立验证数据保留。每个站点、每个高度门仅取时间窗口内最近时次，等距选更早时次，防止采样频率倍增源强度。

## Windows 可迁移封装

运行包需要 Python 应用、WindNinja 3.12.2 背景引擎和 OpenFOAM 8 原生运行库。`runtime/openfoam8/native_runtime.json` 只使用包内相对路径；应用直接启动 exe，子进程环境单独设置。用户电脑无需安装 WSL、Linux、MSYS 编译器或 MS-MPI。

原生求解字典以 ASCII DLL 文件名加载包内运行库，支持中文案例和运行库路径。GUI 使用 [Windows 系统 ICU](https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-)；封装时排除与它冲突的通用 `icuuc.dll`，保留 netCDF4 带版本及哈希名称的私有 ICU。1.1.9 已完成宿主机路径搬迁及全新 Windows 10 安装版计算输出检查；原生场景曾超过测试脚本等待上限，随后完成并补验输出，原始进程退出码未取得。完整边界见 [1.1.9 验证范围](../packaging/validation-v1.1.9.md)。

开发构建顺序：

```powershell
rtk proxy python scripts/download_native_windows.py
rtk proxy python scripts/extract_native_windows.py
rtk proxy python scripts/build_native_windows.py
rtk proxy python scripts/package_native_windows.py
rtk proxy python scripts/package_windows_app.py --windninja-root 'C:\Path\To\WindNinja-3.12.2'
```

SDK 安装器仅用于提取，不执行安装。运行库包含 exe、DLL、etc 字典及许可文件；编译器和开发 shell 不进入运行包。对应源码归档随发布包提供。Linux/macOS 的移植需各自编译同版本边界库和求解器，不能复用 Windows exe。

当前数值方案采用标准 k–ε、稳态、指定粗糙度边界。增加原生方程耦合不等于已经证明湍流预测精度提高。必须以独立实测 k/ε、留站验证、网格加密及约束强度敏感性实验评价；合成站点仅用于机制验证，UAV 平均风误差不代表湍流误差。

1.1.9 既有现场数据的基线/同化风向圆周 MAE 分别为 103.410125°/103.384928°，没有显示明显改善。844 条记录仅 30 条通过当前时间、高度筛选，覆盖 11 个不同 XYZ 位置；输入基准与软件解释尚未独立确认，不能将误差归因于单一数据或模型问题。详见[风向精度限制](wind_direction_calibration.md)。

依赖依据：[blueCFD 2020-1 发行说明](https://bluecfd.github.io/Core/ReleaseNotes/bluecfd-core-2020-1/)、[OpenFOAM 8 源码](https://github.com/blueCFD/OpenFOAM-dev/tree/blueCFD-Core-8)、[WindNinja 8 边界源码](https://github.com/firelab/windninja/tree/f3e9923daf622154258ccf573b419a028672b787/src/ninjafoam/8)。
