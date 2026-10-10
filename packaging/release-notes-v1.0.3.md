# Wind3D Ninja v1.0.3

Wind3D Ninja v1.0.3 updates the Windows x64 installer and the WindNinja configuration safeguards.

## Changes

- Preserves the default `pointInitialization` workflow for station observations with WindNinja 3.12.2.
- Rejects `turbulence_output` and `non_neutral_stability` before execution, and omits momentum-only flags from the normal point-station configuration.
- Ensures Windows builds package the current checkout source ahead of any stale editable Python installation.
- Includes the relocatable portable UI/CLI and bundled WindNinja 3.12.2 runtime.

## Validation

- 32 source tests passed with `PYTHONPATH=src`.
- Local fresh-install, migration, pipeline, UI startup, and uninstall checks passed.
- A remote CLI replacement in the existing bundle passed pipeline and UI startup checks; a fresh remote installation of this exact installer was not performed.
- Independent virtual-machine validation was not performed.

## Installer

- File: `Wind3D-Ninja-Setup-1.0.3-x64.exe`
- SHA-256: `72f139fa6e301ffc55327f0c9a5183ea791f134304a1b698fed552c94e3f9f06`
