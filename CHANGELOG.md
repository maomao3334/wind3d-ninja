# Changelog

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
