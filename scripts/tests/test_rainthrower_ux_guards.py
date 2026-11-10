#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-level UX regressions, never flashes or builds a device image."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]


class RainthrowerUxGuards(unittest.TestCase):
    def source(self, name):
        return (ROOT / name).read_text()

    def test_thermal_asset_is_distinct_and_mapped_correctly(self):
        path = "unica/patches/dvfs/customize.sh"
        script = self.source(path)
        self.assertNotEqual(
            (ROOT / "unica/patches/dvfs/assets/siop_default.xml").read_bytes(),
            (ROOT / "unica/patches/dvfs/assets/ssrm_default.xml").read_bytes())
        lines = [line for line in script.splitlines() if 'cp -a' in line and 'ssrm_default.xml' in line]
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIn('$MODPATH/assets/ssrm_default.xml', line)
            self.assertNotIn('$MODPATH/assets/siop_default.xml', line)

    def test_essential_stock_apps_survive_generic_debloat(self):
        script = self.source("unica/debloat.sh")
        for name in ("system/app/ClockPackage", "system/app/SamsungCalendar",
                     "system/app/Notes40", "system/app/SmartReminder",
                     "system/app/VoiceAccess", "system/app/LiveTranscribe"):
            self.assertNotRegex(script, r"(?m)^" + re.escape(name) + r"\s*$")

    def test_s10_offload_does_not_override_persisted_values(self):
        custom = self.source("target/beyond1lte/patches/user_defaults/customize.sh")
        init = self.source("target/beyond1lte/patches/user_defaults/system/system/etc/init/s10-user-defaults.rc")
        self.assertNotIn("setprop persist.bluetooth", custom + init)
        self.assertNotIn("load_persist_props", custom)

    def test_wifi_p2p_is_enabled_in_vendor_overlay(self):
        config = self.source("platform/exynos9820/patches/vendor/vendor/etc/wifi/wpa_supplicant_overlay.conf")
        self.assertRegex(config, r"(?m)^p2p_disabled=0\s*$")

    def test_ota_generator_produces_valid_json_and_real_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            roms = folder / "roms"
            roms.mkdir()
            archive = roms / "Rainthrower-SM-G975F.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                packed.writestr("build_info.txt", (
                    "timestamp=1794362400\ndevice=beyond2lte\nversion=v35-beta\n"
                    "security_patch_version=2026-11-05\nincremental=G975FXXSGHWC2\n"))
            environment = dict(os.environ, SRC_DIR=temporary)
            command = ["bash", str(ROOT / "scripts/generate_ota_manifest.sh"),
                       str(roms), "https://downloads.example.org/rainthrower"]
            subprocess.run(command, env=environment, check=True, capture_output=True, text=True)
            document = json.loads((folder / "manifest.json").read_text())
            item = document["response"][0]
            self.assertEqual(item["incremental"], "G975FXXSGHWC2")
            self.assertEqual(item["urls"], ["https://downloads.example.org/rainthrower/" + archive.name])
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(item["id"], item["sha256"])
            previous = (folder / "manifest.json").read_bytes()
            bad = subprocess.run(command[:-1] + ["INSERTURLHERE"], env=environment,
                                 capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertEqual(previous, (folder / "manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
