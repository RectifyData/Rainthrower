# Rainthrower development One UI 9 — G973F clean-install-only package

**Not the upstream R32 no-wipe upgrade installer.** Only a clean installation is supported by this source-built ZIP; never use it as an update over existing encrypted userdata. Physical model-specific acceptance remains pending.

## Supported hardware

This package is for **Exynos SM-G973F**, with the original partition layout,
an unlocked bootloader, compatible TWRP and **G973FXXSGHWC1 firmware base**.
It contains full images and does not depend on the previously installed Android
version. It does not downgrade or install bootloader/modem firmware.

Other S10 models, S10 5G, Korean and Snapdragon variants are not supported by this
package. The G975F package also accepts larger System and Vendor partitions;
other written partitions must retain their stock capacities. Product is not written.
This enlarged layout has not been tested on a phone.

## Clean installation

1. Back up personal files externally and make a restorable TWRP backup of the
   current ROM, including Boot, System, Vendor, DTB, DTBO, VBMeta and EFS.
2. Put both ZIPs on **exFAT** microSD/USB OTG or transfer them after formatting.
    The payload ZIP exceeds 4 GiB and cannot be stored on FAT32.
    TWRP 3.7.0_9-1 rejects ZIP64 archives. Keep the matching current
    `*_INSTALLER_r4.zip` and `*_TWRP.zip` together in the same directory and
    install the small `*_INSTALLER_r4.zip`. The launcher checks the full payload SHA256 and runs its
    original installer. This two-file method requires mounted storage and does
    not support ADB sideload.
3. In TWRP choose **Wipe → Format Data → yes**. **This permanently removes
   apps, accounts and internal-storage files, including ZIPs stored there.**
4. Reboot into TWRP, mount the fresh ext4 Data partition and unmount System and
   Vendor. Install the ZIP. Do not restore old ROM app/system data before boot.
5. Wait for image verification to finish, then reboot System manually.

The ZIP never formats userdata. Do not use Advanced Wipe as a substitute for
Format Data when migrating the old Samsung ICE encryption format.

## Validation status

The SM-G975F candidate removes YuROM Settings and restores donor apps; the other
models retain their existing controls. The G975F Sync and Mobile data Quick
Settings source patches require device validation. The modified SystemUI keeps
the OEM signing block but its original APK signature no longer verifies.
System-image size is checked against 5700 MiB; fitting all restored apps has
not been verified without building the actual image.
The package verifies every image chunk before writing and verifies each flashed
chunk afterwards. It writes only System, Vendor, Boot, DTB, DTBO and VBMeta.

This is a **Beta package**. Clean TWRP installation and first boot require
separate physical acceptance for each model; consult the matching external
validation record. SELinux enforcing, physical DeX reconnect and TV audio
playback are not accepted release tests. Retain the matching backup.

## Returning to another ROM

Restore the matching boot/system/vendor/device-tree/AVB set or install the
appropriate complete stock firmware. Reverting from this FBE format to a ROM
using Samsung ICE can require Format Data again; restore personal files from the
external backup afterwards.
