# Wind3D Ninja v1.0.4

Wind3D Ninja v1.0.4 adds clearly separated WindNinja workflows while preserving the existing station-observation point-initialization default.

## Changes

- Enables non-neutral stability for point-initialized station observations.
- Validates explicit `alpha_stability` values as `0 < alpha <= 5`; leaving alpha empty uses WindNinja's automatic path.
- Adds an explicit turbulence workflow rather than silently changing station observations into a momentum-solver run.
- Updates the Windows x64 installer and the Chinese user guide for the new workflow choices.

## Validation

- 43 source tests passed.
- A fresh Windows installation completed default station, non-neutral station, and turbulence runs; each produced NetCDF and KMZ outputs.
- A copied installation completed station and turbulence runs, including `doctor` and `plan` checks, using its bundled runtime.
- The turbulence KMZ contains WindNinja's native `Column Max Velocity Fluctuations` layer; its rendered legend reports metres per second.
- The installer GUI started successfully and the uninstaller removed the installed copy.
- The turbulence input directory remained unchanged after the installed run. Separate source-run evidence also covers a repeated run using the same DEM-only directory.

Validation ran on Windows with the bundled runtime. It does not substitute for independent hardware, operating-system, or production-data validation.

## Installer

- File: `Wind3D-Ninja-Setup-1.0.4-x64.exe`
- SHA-256: `8a5d80cd0e1eff848fc7cf7e4f7295217ede60291ce804308fb86a00e7248dda`
