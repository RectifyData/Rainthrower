#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove clean-data display defaults are seeded once without overriding user choices."""
import os
import json
import re
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory(prefix="s10-display-defaults-test-") as temporary:
        root = Path(temporary)
        binary = root / "bin"
        binary.mkdir()
        for name, text in {
            "getprop": 'case "$1" in ro.product.model) echo "${MODEL:-SM-G973F}";; ro.product.device) echo "${DEVICE:-beyond1}";; ro.build.version.sdk) echo 37;; esac',
            "app_process": '[ "${OVERLAY_FAIL:-0}" = 0 ] || exit 1; echo overlay >> "$FIXTURE/actions"',
            "wm": 'if [ "$#" = 1 ]; then echo "Physical density: 560"; [ ! -f "$FIXTURE/density" ] || echo "Override density: $(cat "$FIXTURE/density")"; else if [ "${WM_WRONG:-0}" = 1 ]; then echo "${2}0" > "$FIXTURE/density"; else echo "$2" > "$FIXTURE/density"; fi; echo density >> "$FIXTURE/actions"; fi',
            "cmd": 'case "$2" in get-user-preferred-display-mode) if [ -f "$FIXTURE/preferred" ]; then cat "$FIXTURE/preferred"; else echo "User preferred display mode: ${UNSET:--1 -1 0.0}"; fi;; set-user-preferred-display-mode) echo mode >> "$FIXTURE/actions"; echo "User preferred display mode: 1080 2280 60.000004" > "$FIXTURE/preferred";; *) exit 1;; esac',
        }.items():
            path = binary / name
            path.write_text("#!/bin/sh\n" + text + "\n")
            path.chmod(0o755)
        script = root / "defaults.sh"
        source = (ROOT / "scripts/oneui9_runtime/display/s10-display-defaults.sh").read_text()
        script.write_text(source.replace("directory=/data/misc/s10-display-defaults", "directory=" + str(root / "state")))
        environment = dict(os.environ, PATH=str(binary) + ":" + os.environ["PATH"], FIXTURE=str(root))
        def run(**extra):
            return subprocess.run(["sh", str(script)], env=dict(environment, **extra), capture_output=True)
        assert run(MODEL="SM-G975F").returncode == 0 and not (root / "actions").exists()
        assert run(OVERLAY_FAIL="1").returncode != 0 and not (root / "state/applied-v1").exists()
        assert run().returncode == 0
        assert (root / "actions").read_text().splitlines() == ["overlay", "mode", "density"]
        (root / "preferred").write_text("User preferred display mode: 1440 3040 60.000004\n")
        assert run().returncode == 0 and (root / "actions").read_text().splitlines() == ["overlay", "mode", "density"]
        (root / "state/applied-v1").unlink()
        (root / "state/density-v1").unlink()
        (root / "density").write_text("390\n")
        assert run().returncode == 0
        assert (root / "density").read_text().strip() == "390"
        assert (root / "actions").read_text().splitlines() == ["overlay", "mode", "density", "overlay"]
        assert "1440 3040" in (root / "preferred").read_text()
        # Upgrade from the original marker should seed density without replaying overlay/mode.
        (root / "state/density-v1").unlink()
        (root / "density").unlink()
        before = len((root / "actions").read_text().splitlines())
        assert run().returncode == 0
        assert (root / "actions").read_text().splitlines()[before:] == ["density"]
        assert (root / "density").read_text().strip() == "420"
        # A prefix match such as 4200 must not be accepted as 420.
        (root / "state/density-v1").unlink()
        (root / "density").unlink()
        assert run(WM_WRONG="1").returncode != 0
        assert not (root / "state/density-v1").exists()
        (root / "density").unlink()
        assert run().returncode == 0
        assert (root / "density").read_text().strip() == "420"
        profiles = json.loads((ROOT / "scripts/oneui9_runtime/display/profiles.json").read_text())["models"]
        for model, profile in profiles.items():
            (root / "state/applied-v1").unlink()
            (root / "preferred").unlink()
            (root / "density").unlink()
            (root / "state/density-v1").unlink()
            before = len((root / "actions").read_text().splitlines())
            assert run(MODEL=model, DEVICE=profile["device"], UNSET="null").returncode == 0
            assert (root / "actions").read_text().splitlines()[before:] == ["overlay", "mode", "density"]
            assert (root / "density").read_text().strip() == str(profile["default_density"])
            width = float(re.search(r"H -([0-9.]+)", profile["cutout"])[1])
            assert profile["portrait_indicator_padding_dp"] - width >= 6
            assert profile["default_density"] == (480 if model == "SM-G970F" else 420)
    print("PASS: all model guards, retry after failure, both unset-mode representations, once-only FHD seeding, existing choices and per-model camera clearance")


if __name__ == "__main__":
    main()
