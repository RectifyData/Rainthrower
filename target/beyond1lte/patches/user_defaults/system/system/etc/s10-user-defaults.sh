#!/system/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Seed missing animation settings only; preserve user preferences on upgrades.
# A new marker permits already-migrated installations to adopt this safe rule.
MARKER=artisanrom_s10_animation_defaults_version_2
if [ "$(settings get global "$MARKER")" = "1" ]; then
    exit 0
fi

for KEY in window_animation_scale transition_animation_scale animator_duration_scale; do
    VALUE="$(settings get global "$KEY")" || exit 1
    case "$VALUE" in
        null|'')
            settings put global "$KEY" 0.5 || exit 1
            VALUE="$(settings get global "$KEY")" || exit 1
            case "$VALUE" in 0.5|0.50|0.500) ;; *) exit 1 ;; esac
            ;;
        *) : ;; # Existing values, including accessibility zero, belong to the user.
    esac
done
settings put global "$MARKER" 1
