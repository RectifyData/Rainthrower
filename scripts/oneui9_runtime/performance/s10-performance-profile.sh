#!/vendor/bin/sh
# The measured profile is valid only for this exact OC-capable boot image.
set -eu
SYS=/sys
STATE=/data/vendor/s10-performance
CONTROL=/data/system/artisan-control
BOOT=/dev/block/platform/13d60000.ufs/by-name/boot
BOOT_SHA=99bf2e7d589f9907e47861e1c8755264e32c39c108467b497f638aa93b4b7f10

state() { setprop vendor.s10.performance.state "$1"; }

# The broker and this script use the same atomic directory lock for *every*
# sysfs transaction. A crashed holder leaves a fail-closed lock until reboot;
# init removes the empty stale directory during post-fs-data, before services.
LOCK="$STATE/nodes.lock"
unlock() { rmdir "$LOCK" || :; }
lock() {
    count=0
    while ! mkdir "$LOCK" 2>/dev/null; do
        count=$((count + 1))
        [ "$count" -lt 10 ] || return 1
        sleep 1
    done
}

compatible() {
    [ "$(id -u)" = 0 ] &&
    [ "$(getprop ro.product.model)" = SM-G973F ] &&
    [ "$(sha256sum "$BOOT" | cut -d ' ' -f 1)" = "$BOOT_SHA" ]
}

# Capture only the fixed sysfs nodes that this service changes. The saved
# snapshot belongs to one boot, so a prior boot's values are never replayed.
nodes() {
    for domain in big mid lit g3d; do echo "power/percent_margin/${domain}_margin_percent"; done
    for cpu in 0 4 6; do
        path="devices/system/cpu/cpufreq/policy$cpu"
        echo "$path/schedutil/up_rate_limit_us"
        echo "$path/schedutil/down_rate_limit_us"
        echo "$path/scaling_min_freq"
        echo "$path/scaling_max_freq"
    done
    echo kernel/gpu/gpu_min_clock
    echo kernel/gpu/gpu_governor
    echo kernel/gpu/gpu_max_clock
}

snapshot() {
    rm -rf "$STATE/before.new"
    mkdir -p "$STATE/before.new"
    for node in $(nodes); do
        value=$(cat "$SYS/$node") || return 1
        case "$node" in
            kernel/gpu/gpu_governor) case "$value" in ''|*[!A-Za-z0-9_]*) return 1 ;; esac ;;
            *) case "$value" in ''|*[!0-9-]*) return 1 ;; esac ;;
        esac
        mkdir -p "$STATE/before.new/$(dirname "$node")"
        printf '%s\n' "$value" > "$STATE/before.new/$node"
    done
    cat /proc/sys/kernel/random/boot_id > "$STATE/before.new/boot_id"
    rm -rf "$STATE/before"
    mv "$STATE/before.new" "$STATE/before"
}

restore() {
    [ -f "$STATE/before/boot_id" ] || return 1
    [ "$(cat "$STATE/before/boot_id")" = "$(cat /proc/sys/kernel/random/boot_id)" ] || return 1
    status=0
    # First remove voltage margins, then restore clocks and the saved margins.
    for domain in big mid lit g3d; do
        echo 0 > "$SYS/power/percent_margin/${domain}_margin_percent" || return 1
        [ "$(cat "$SYS/power/percent_margin/${domain}_margin_percent")" = 0 ] || return 1
    done
    for node in $(nodes); do
        case "$node" in power/percent_margin/*) continue ;; esac
        [ -f "$STATE/before/$node" ] || { status=1; continue; }
        value=$(cat "$STATE/before/$node") || { status=1; continue; }
        echo "$value" > "$SYS/$node" || :
    done
    # Reapply minimums after maximums in case the driver clamped them.
    for cpu in 0 4 6; do
        node="devices/system/cpu/cpufreq/policy$cpu/scaling_min_freq"
        value=$(cat "$STATE/before/$node") || { status=1; continue; }
        echo "$value" > "$SYS/$node" || :
    done
    # Retry maximums after lowering minimums, then minimums after raising maxima.
    for cpu in 0 4 6; do
        node="devices/system/cpu/cpufreq/policy$cpu/scaling_max_freq"
        value=$(cat "$STATE/before/$node") || { status=1; continue; }
        echo "$value" > "$SYS/$node" || :
        node="devices/system/cpu/cpufreq/policy$cpu/scaling_min_freq"
        value=$(cat "$STATE/before/$node") || { status=1; continue; }
        echo "$value" > "$SYS/$node" || :
    done
    # GPU drivers can clamp limits in the same way as CPU policies.
    for node in kernel/gpu/gpu_max_clock kernel/gpu/gpu_min_clock; do
        value=$(cat "$STATE/before/$node") || { status=1; continue; }
        echo "$value" > "$SYS/$node" || :
    done
    for domain in big mid lit g3d; do
        node="power/percent_margin/${domain}_margin_percent"
        value=$(cat "$STATE/before/$node") || { status=1; continue; }
        echo "$value" > "$SYS/$node" || :
    done
    for node in $(nodes); do
        actual=$(cat "$SYS/$node") || { status=1; continue; }
        expected=$(cat "$STATE/before/$node") || { status=1; continue; }
        [ "$actual" = "$expected" ] || status=1
    done
    [ "$status" = 0 ]
}

apply_profile() {
    lit_max=2106000; mid_max=2400000; big_max=3016000; gpu_max=806000; margin=-3
    # Start saved adaptive/stock modes without a static OC/UV interval while
    # the broker waits for this script's boot health window to complete.
    if [ -f "$CONTROL/performance-pending" ] || { [ -f "$CONTROL/performance.json" ] &&
        grep -Eq '"mode"[[:space:]]*:[[:space:]]*"(auto|oc|stock)"' "$CONTROL/performance.json"; }; then
        lit_max=1950000; mid_max=2314000; big_max=2730000; gpu_max=650000; margin=0
    fi
    for domain in big mid lit g3d; do
        case "$(cat "$SYS/power/percent_margin/${domain}_margin_percent")" in
            0|-1|-2|-3) ;; *) return 1 ;;
        esac
    done
    for cpu in 0 4 6; do
        path=$SYS/devices/system/cpu/cpufreq/policy$cpu
        [ "$(cat "$path/scaling_governor")" = schedutil ]
        up=4000
        [ "$cpu" = 6 ] || up=1000
        echo "$up" > "$path/schedutil/up_rate_limit_us"
        echo 4000 > "$path/schedutil/down_rate_limit_us"
        [ "$(cat "$path/schedutil/up_rate_limit_us")" = "$up" ]
        [ "$(cat "$path/schedutil/down_rate_limit_us")" = 4000 ]
    done
    echo 442000 > "$SYS/devices/system/cpu/cpufreq/policy0/scaling_min_freq"
    echo 507000 > "$SYS/devices/system/cpu/cpufreq/policy4/scaling_min_freq"
    echo 520000 > "$SYS/devices/system/cpu/cpufreq/policy6/scaling_min_freq"
    echo "$lit_max" > "$SYS/devices/system/cpu/cpufreq/policy0/scaling_max_freq"
    echo "$mid_max" > "$SYS/devices/system/cpu/cpufreq/policy4/scaling_max_freq"
    echo "$big_max" > "$SYS/devices/system/cpu/cpufreq/policy6/scaling_max_freq"
    [ "$(cat "$SYS/devices/system/cpu/cpufreq/policy0/scaling_min_freq")" = 442000 ]
    [ "$(cat "$SYS/devices/system/cpu/cpufreq/policy4/scaling_min_freq")" = 507000 ]
    [ "$(cat "$SYS/devices/system/cpu/cpufreq/policy6/scaling_min_freq")" = 520000 ]
    [ "$(cat "$SYS/devices/system/cpu/cpufreq/policy0/scaling_max_freq")" = "$lit_max" ]
    [ "$(cat "$SYS/devices/system/cpu/cpufreq/policy4/scaling_max_freq")" = "$mid_max" ]
    [ "$(cat "$SYS/devices/system/cpu/cpufreq/policy6/scaling_max_freq")" = "$big_max" ]
    echo 156000 > "$SYS/kernel/gpu/gpu_min_clock"
    echo Interactive > "$SYS/kernel/gpu/gpu_governor"
    echo "$gpu_max" > "$SYS/kernel/gpu/gpu_max_clock"
    [ "$(cat "$SYS/kernel/gpu/gpu_min_clock")" = 156000 ]
    [ "$(cat "$SYS/kernel/gpu/gpu_max_clock")" = "$gpu_max" ]
    [ "$(cat "$SYS/kernel/gpu/gpu_governor")" = Interactive ]
    for domain in big mid lit g3d; do
        echo "$margin" > "$SYS/power/percent_margin/${domain}_margin_percent"
        [ "$(cat "$SYS/power/percent_margin/${domain}_margin_percent")" = "$margin" ]
    done
}

failed() {
    result=$?
    trap - EXIT HUP INT TERM
    set +e
    if restore; then state failed; else state rollback-failed; fi
    touch "$STATE/disabled"
    unlock
    exit "$result"
}

# Status never writes the device. All other actions hold the lock from before
# checking state until after applying or restoring every node.
case "${1:-status}" in
    boot|enable|disable)
        [ -d "$STATE" ] || exit 1
        lock || { echo 'performance nodes are busy' >&2; exit 1; }
        trap unlock EXIT
        trap 'exit 1' HUP INT TERM
        ;;
esac

case "${1:-status}" in
    disable)
        [ -d "$STATE" ]
        touch "$STATE/disabled"
        state disabling
        if [ -f "$STATE/before/boot_id" ] && [ "$(cat "$STATE/before/boot_id")" = "$(cat /proc/sys/kernel/random/boot_id)" ]; then
            compatible || { state rollback-failed; exit 1; }
            restore || { state rollback-failed; exit 1; }
        fi
        rm -rf "$STATE/before" "$STATE/pending" "$STATE/active" "$STATE/generation"
        state disabled
        exit
        ;;
    enable)
        compatible
        [ -d "$STATE" ]
        if [ -f "$STATE/before/boot_id" ] &&
            [ "$(cat "$STATE/before/boot_id")" = "$(cat /proc/sys/kernel/random/boot_id)" ]; then
            restore || { state rollback-failed; exit 1; }
        fi
        rm -rf "$STATE/disabled" "$STATE/pending" "$STATE/active" "$STATE/before" "$STATE/generation"
        ;;
    boot) ;;
    status)
        getprop vendor.s10.performance.state
        if [ -e "$STATE/disabled" ]; then echo automatic_application=disabled; else echo automatic_application=enabled; fi
        exit
        ;;
    *) exit 1 ;;
esac

[ "$(getprop sys.boot_completed)" = 1 ] || exit 1
if ! compatible; then state skipped-kernel; exit; fi
[ -d "$STATE" ]
if [ -e "$STATE/disabled" ]; then state disabled; exit; fi
if [ -e "$STATE/pending" ]; then
    touch "$STATE/disabled"
    # After a reboot the kernel has reset its nodes; do not replay old values.
    if [ -f "$STATE/before/boot_id" ] && [ "$(cat "$STATE/before/boot_id")" = "$(cat /proc/sys/kernel/random/boot_id)" ]; then
        restore || { state rollback-failed; exit 1; }
    fi
    state interrupted
    exit
fi
boot_id=$(cat /proc/sys/kernel/random/boot_id)
# Do not replace the original snapshot when init restarts this oneshot service.
if [ -f "$STATE/active" ] && [ "$(cat "$STATE/active")" = "$boot_id" ]; then state applied; exit; fi
runtime=$(pidof system_server)
[ -n "$runtime" ]
snapshot
printf '%s\n' "$boot_id" > "$STATE/pending"
sync
trap failed EXIT
trap 'exit 1' HUP INT TERM
state applying
apply_profile
for interval in 1 2 3 4 5 6; do
    sleep 5
    [ ! -e "$STATE/disabled" ]
    [ "$(pidof system_server)" = "$runtime" ]
    [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot_id" ]
done
# A fresh token tells the adaptive broker to invalidate its cached decisions
# after a same-boot disable/enable cycle.
cat /proc/sys/kernel/random/uuid > "$STATE/generation"
printf '%s\n' "$boot_id" > "$STATE/active"
rm "$STATE/pending"
state applied
# Once traps are removed, an abrupt death leaves the lock in place, which is
# safer than running rollback after releasing ownership.
trap - EXIT HUP INT TERM
unlock
