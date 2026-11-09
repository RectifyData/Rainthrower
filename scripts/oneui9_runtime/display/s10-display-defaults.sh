#!/system/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Seed model-specific stock geometry once per fresh userdata installation.
set -eu
case "$(getprop ro.product.model):$(getprop ro.product.device)" in
    SM-G970F:beyond0|SM-G970F:beyond0lte|SM-G973F:beyond1|SM-G973F:beyond1lte|SM-G975F:beyond2|SM-G975F:beyond2lte) ;;
    *) exit 0 ;;
esac
[ "$(getprop ro.build.version.sdk)" = 37 ] || exit 0
directory=/data/misc/s10-display-defaults
marker="$directory/applied-v1"
density_marker="$directory/density-v1"
if [ ! -f "$marker" ]; then
export CLASSPATH=/system/framework/s10-display-defaults.jar
app_process /system/bin S10DisplayOverlay
preferred="$(cmd display get-user-preferred-display-mode 0)"
case "$preferred" in 'User preferred display mode: -1 -1 0.0'|'User preferred display mode: null')
    cmd display set-user-preferred-display-mode 1080 2280 60.000004 0
    preferred="$(cmd display get-user-preferred-display-mode 0)"
    case "$preferred" in 'User preferred display mode: 1080 2280 60.'*) ;; *) exit 1 ;; esac
    ;;
esac
mkdir -p "$directory"
chmod 700 "$directory"
printf '%s\n' 's10-display-defaults-v1' > "$marker"
chmod 600 "$marker"
fi
[ ! -f "$density_marker" ] || exit 0
# Respect a previously selected density; seed the registered model default only
# when Android reports that no user override exists.
case "$(getprop ro.product.model)" in
    SM-G970F) density=480 ;;
    SM-G973F|SM-G975F) density=420 ;;
    *) exit 1 ;;
esac
current_density="$(wm density)"
case "$current_density" in
    *"Override density:"*) ;;
    *"Physical density:"*)
        wm density "$density"
        current_density="$(wm density)"
        printf '%s\n' "$current_density" | grep -Fx "Override density: $density" >/dev/null || exit 1
        ;;
    *) exit 1 ;;
esac
# Keep subsequent user display/overlay choices, rather than forcing each boot.
mkdir -p "$directory"
chmod 700 "$directory"
printf '%s\n' 's10-display-density-v1' > "$density_marker"
chmod 600 "$density_marker"
