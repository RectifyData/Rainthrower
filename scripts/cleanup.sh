#!/usr/bin/env bash
# Copyright (c) 2023 Salvo Giangreco
# SPDX-License-Identifier: GPL-3.0-or-later

# [
source "$SRC_DIR/scripts/utils/log_utils.sh"

PRINT_USAGE()
{
    echo "Usage: cleanup <type> (<type>...)" >&2
    echo " - all ($OUT_DIR)" >&2
    echo " - odin ($ODIN_DIR)" >&2
    echo " - fw ($FW_DIR)" >&2
    echo " - work_dir ($WORK_DIR)" >&2
    echo " - logs ($OUT_DIR/**.log)" >&2
    echo " - tools ($TOOLS_DIR)" >&2
}

SAFE_RM()
{
    # Never delete an empty, root or out-of-tree path, even if a variable is unset.
    local TARGET="$1"
    if [ -z "$SRC_DIR" ] || [ -z "$TARGET" ] || [[ "$TARGET" == "/" ]] || [[ "$TARGET" != "$SRC_DIR/"* ]]; then
        LOGE "Refusing to remove \"$TARGET\": it is empty or outside of \"$SRC_DIR\""
        return 1
    fi
    rm -rf -- "$TARGET"
}
# ]

if [ "$#" == 0 ]; then
    PRINT_USAGE
    exit 1
fi

while [ "$#" != 0 ]; do
    case "$1" in
        "all")
            LOG "- Cleaning everything..."
            SAFE_RM "$OUT_DIR" || exit 1
            break
            ;;
        "odin")
            LOG "- Cleaning Odin firmwares dir..."
            SAFE_RM "$ODIN_DIR" || exit 1
            ;;
        "fw")
            LOG "- Cleaning extracted firmwares dir..."
            SAFE_RM "$FW_DIR" || exit 1
            ;;
        "work_dir")
            LOG "- Cleaning ROM work dir..."
            SAFE_RM "$(dirname "$WORK_DIR")" || exit 1
            mkdir -p "$(dirname "$WORK_DIR")"
            ;;
        "logs")
            LOG "- Cleaning log files..."
            [ -n "$OUT_DIR" ] && [ -d "$OUT_DIR" ] && find "$OUT_DIR" -type f -name "*.log" -delete
            ;;
        "tools")
            LOG "- Cleaning dependencies dir..."
            SAFE_RM "$TOOLS_DIR" || exit 1
            git submodule foreach --recursive "git clean -f -d -x" &> /dev/null
            ;;
        *)
            LOGE "\"$1\" is not valid."
            PRINT_USAGE
            exit 1
        ;;
    esac

    shift
done

exit 0
