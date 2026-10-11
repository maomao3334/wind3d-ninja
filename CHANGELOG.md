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
