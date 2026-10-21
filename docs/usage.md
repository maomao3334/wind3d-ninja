# 源码运行与 Windows 打包

用户直接安装 `Wind3D-Ninja-Setup-1.1.9.exe`，界面操作见 [使用说明](user_guide_zh.md)。

## 源码运行

```powershell
py -3.11 -m pip install -e ".[ui,test]"
wind3d-ninja-ui
wind3d-ninja --help
```

命令行支持 `inspect`、`plan`、`run`、`turbulence`、`assimilation`、`native-assimilation`。用各命令的 `--help` 查看完整参数。兼容此前的 `run --mode turbulence` 手动背景方式：

```powershell
.\Wind3D-cli.exe run D:\wind\input --height 50 --resolution 200 --mode turbulence --background-speed-ms 8 --background-direction-deg 270 --background-height-m 50 --simulation-time 2024-04-03T08:06:00Z --bounds 25.0 25.1 102.3 102.5 --output D:\wind\output
```

逐站及原生同化的仪器/高度、CSV 和输出约定见 [逐站说明](station_assimilation.md) 与 [原生说明](native_rans_assimilation.md)。

## 打包同一个软件

在 Windows 上使用已准备好的 WindNinja 3.12.2 和原生 OpenFOAM 8 运行库：

```powershell
py -3.11 -m pip install pyinstaller
py -3.11 scripts/package_windows_app.py --windninja-root D:/tools/WindNinja-3.12.2 --openfoam-root D:/runtime/openfoam8 --native-source-archive D:/runtime/native-corresponding-source.tar.gz
powershell -NoProfile -File packaging/build_installer.ps1 -IsccPath "D:/tools\InnoSetup\ISCC.exe"
```

默认冻结目录为 `dist/Wind3D`，安装文件为 `installer_output/Wind3D-Ninja-Setup-1.1.9.exe`。Qt 是默认图形界面；旧 Tk 构建 spec 保留供历史参考，不用于该安装包。

原生 DLL 的重建/对应源码说明在原生方法文档与 scripts 内；最终用户无需此开发工具链。许可证、文档和对应源码必须随分发包保留。
