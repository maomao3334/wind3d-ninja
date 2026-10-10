# Wind3D Ninja v1.0.3 validation record

## Release candidate checks

- The source suite passed: 32 tests with `PYTHONPATH=src`.
- The installer checksum matches `SHA256SUMS-v1.0.3.txt`.
- A clean silent installation completed successfully. The installed CLI completed `doctor`, `inspect`, `plan`, and `run`; the bundled WindNinja version check passed; the UI startup check passed; a copied installation completed the CLI and UI checks; and silent uninstall completed successfully.

## Privacy audit

- Scanned 27 changed release text files, including the release notes, checksum manifest, packaging scripts, configuration, and user documentation. No local workspace path, user-profile path, private-network endpoint, or credential assignment was found.
- Scanned four installed product configuration/document files. No such pattern was found.
- Inspected the PyInstaller archives in the published UI and CLI executables. All 88 bundled first-party module `co_filename` values were relative; none contained the checked local identifiers.

## Scope and limitations

The audit covers first-party release material and embedded first-party Python modules. Third-party runtime documentation and binaries were not treated as first-party release content. Validation used a local Windows installation; an independent virtual-machine run was not performed.
