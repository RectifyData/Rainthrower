# SPDX-License-Identifier: GPL-3.0-or-later
# Keep the S10's source firmware A2DP compatibility default, but do not
# overwrite a persisted user choice during every boot. The stock_blobs module
# seeds persist.bluetooth.a2dp_offload.disabled=true for fresh installs.
# Software offload remains the tested fallback; other codecs need live testing.
SET_METADATA "system" "system/etc/init/s10-user-defaults.rc" 0 0 644 "u:object_r:system_file:s0" || return 1
SET_METADATA "system" "system/etc/s10-user-defaults.sh" 0 0 644 "u:object_r:system_file:s0" || return 1
