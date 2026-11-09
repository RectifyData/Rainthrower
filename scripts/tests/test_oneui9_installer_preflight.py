#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host checks for the original-layout installer preflight; no flashing."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from s10_models import render_installer, layout_for

INSTALLER = ROOT / 'scripts/oneui9_installer/update-binary'


class InstallerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        source = INSTALLER.read_text()
        self.guard = source[source.index('for directory in system system_ce'):source.index("ui 'Format Data removes")]

    def check_data(self):
        script = 'abort() { echo "$*" >&2; exit 1; }\n' + self.guard
        return subprocess.run(['sh', '-c', script], env={**os.environ, 'DATA': str(self.data)}, capture_output=True, text=True)

    def test_empty_and_recovery_created_empty_directories(self):
        self.assertEqual(self.check_data().returncode, 0)
        for name in ('system', 'system_ce', 'system_de', 'misc_ce', 'misc_de', 'user', 'user_de', 'data'):
            (self.data / name).mkdir()
        self.assertEqual(self.check_data().returncode, 0)

    def test_contents_including_hidden_entries_are_rejected(self):
        directory = self.data / 'system'
        directory.mkdir()
        for name in ('settings.xml', '.hidden', '..hidden'):
            with self.subTest(name=name):
                path = directory / name
                path.touch()
                self.assertNotEqual(self.check_data().returncode, 0)
                path.unlink()
        (directory / 'dangling').symlink_to('missing')
        self.assertNotEqual(self.check_data().returncode, 0)

    def test_non_directory_and_symlink_are_rejected(self):
        path = self.data / 'system'
        path.touch()
        self.assertNotEqual(self.check_data().returncode, 0)
        path.unlink()
        path.symlink_to('missing')
        self.assertNotEqual(self.check_data().returncode, 0)
        path.unlink()
        path.symlink_to(self.data, target_is_directory=True)
        self.assertNotEqual(self.check_data().returncode, 0)

    def test_partition_sizes_are_scoped_to_s10_plus(self):
        system = 5976883200 // 512
        vendor = 1153433600 // 512
        for model in ('SM-G970F', 'SM-G973F', 'SM-G975F'):
            source = render_installer(model)
            function = source[source.index('capacity_compatible() {'):source.index('# All checks and all payload hashes')]
            larger = 'true' if model == 'SM-G975F' else 'false'
            self.assertIn('ALLOW_LARGER_OS_PARTITIONS=' + larger, source)
            def accepts(actual, expected, part):
                script = 'ALLOW_LARGER_OS_PARTITIONS=' + larger + '\n' + function + '\ncapacity_compatible "$1" "$2" "$3"\n'
                return subprocess.run(['sh', '-c', script, 'fixture', str(actual), str(expected), part]).returncode == 0
            for part, expected in (('system', system), ('vendor', vendor)):
                self.assertTrue(accepts(expected, expected, part))
                self.assertFalse(accepts(expected - 1, expected, part))
                self.assertEqual(accepts(expected + 1, expected, part), model == 'SM-G975F')
                self.assertFalse(accepts('bad', expected, part))
            self.assertFalse(accepts(10**16, system, 'system'))
            self.assertFalse(accepts(2049, 2048, 'boot'))
            self.assertTrue(accepts(2048, 2048, 'boot'))
            if model == 'SM-G975F':
                # Both decimal MB and MiB interpretations of the supplied layout exceed stock.
                self.assertTrue(accepts(10240 * 1000000 // 512, system, 'system'))
                self.assertTrue(accepts(5600 * 1000000 // 512, vendor, 'vendor'))

    def test_manifest_capacities_match_pinned_layout_for_all_models(self):
        for model in ('SM-G970F', 'SM-G973F', 'SM-G975F'):
            source = render_installer(model)
            function = source[source.index('manifest_capacity() {'):source.index('# All checks and all payload hashes')]
            for part in ('system', 'vendor', 'vbmeta', 'dtb', 'dtbo', 'boot'):
                result = subprocess.run(['sh', '-c', function + '\nmanifest_capacity "$1"', 'fixture', part],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(int(result.stdout), layout_for(model)['partitions'][part]['bytes'] // 512)
            self.assertNotEqual(subprocess.run(['sh', '-c', function + '\nmanifest_capacity userdata'],
                                               capture_output=True).returncode, 0)

    def test_recovery_property_only_warns_and_safety_checks_remain(self):
        source = INSTALLER.read_text()
        self.assertIn("[ -n \"$(getprop ro.twrp.version)\" ] || ui 'WARNING:", source)
        for marker in ('Only Exynos SM-G973F is supported', 'An unlocked bootloader is required',
                       'Unsupported $part partition size', 'Corrupt image chunk', 'Readback mismatch',
                       'fresh ext4 /data', 'newer bootloader is required'):
            self.assertIn(marker, source)
        self.assertEqual(subprocess.run(['sh', '-n', str(INSTALLER)]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
