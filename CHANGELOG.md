# Changelog

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
