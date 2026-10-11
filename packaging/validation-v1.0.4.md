# v1.0.4 validation summary

## Build identity

- Installer: `Wind3D-Ninja-Setup-1.0.4-x64.exe`
- Size: 248,729,637 bytes
- SHA-256: `8a5d80cd0e1eff848fc7cf7e4f7295217ede60291ce804308fb86a00e7248dda`

## Automated checks

- 43 source tests passed.
- Fresh-install validation exercised `doctor`, `inspect`, `plan`, default station `run`, and a non-neutral station `run` configured with `non_neutral_stability: true`.
- Fresh-install turbulence validation exercised `plan` and `run`, checked NetCDF and KMZ results, and verified the native `Column Max Velocity Fluctuations` KMZ layer and its metres-per-second legend.
- A copied installation exercised bundled-runtime `doctor`, station `run`, turbulence `plan`, and turbulence `run`; all required NetCDF and KMZ results were present.
- The desktop UI started successfully; uninstall removed the installed copy.
- The installed turbulence run left its DEM-only input directory unchanged. A separate source-run check also completed two consecutive turbulence runs against the same DEM-only directory without modifying that input.

## Privacy review

Release notes, checksum material, the included Chinese guide, installed configuration, and packaged application-owned text were checked for workstation paths, user-directory references, private endpoints, and credential-like values. The compiled code filenames of 43 packaged application modules were also checked for build-machine paths. No sensitive residue was found in that reviewed scope. The review is scoped to application-owned release content; it does not claim to audit every third-party binary or upstream metadata file.

## Limitations

These checks ran on Windows with the bundled WindNinja runtime and a compact DEM fixture. They do not replace validation on every supported Windows version, hardware configuration, or production dataset.
