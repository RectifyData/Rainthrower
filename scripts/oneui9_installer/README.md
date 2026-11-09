# One UI 9 S10 recovery package sources

`update-binary` in this directory is the installer template used by
`s10_models.render_installer()` and `build_s10_twrp.py`. The older file under
`target/beyond1lte/oneui9/installer/` is not the template for these packages.
The two Markdown files here are the package and release instruction templates.

The S10+ renderer accepts enlarged system and vendor partitions, subject to
stock-capacity and payload bounds checks. Other models require stock sizes for
all written partitions. The package never resizes partitions or writes product.
These recovery checks have host loop-device tests, not physical-device acceptance.

Public-release assembly requires an external JSON acceptance record for the
SM-G973F matching the release version, with `device_tested: true`, boolean
`checks` for `boot`, `camera`, `fingerprint`, `display`, `radio`, and
`galaxy_store`, and `payload_sha256` and `launcher_sha256` matching the exact
files tested. This is a gate on recorded claims, not independent proof of a
physical test; other models remain explicitly unaccepted.

Image packing now requires an executable Android `apksigner` (the SDK build-tools
default or `--apksigner`). It verifies system and vendor APKs for the
selected platform, compares every packed entry's contents, ownership, mode and
SELinux label with the source, and records a complete tree/input binding. The
installer and ZIP builder reject older unbound image manifests. This deliberately
blocks the current modified SystemUI APK: copying an OEM signing block does not
make a modified APK's signature valid. A valid signature alone also does not
prove that the signer has the OEM identity or platform privileges; those need
separate verification before device acceptance.

The performance service and adaptive broker use a shared directory lock. If a
writer crashes while holding it, further tuning fails closed until the next
boot's `post-fs-data` cleanup. SELinux access and device behavior remain to be
validated on hardware.
