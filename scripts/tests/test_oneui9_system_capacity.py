#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise complete-image budget decisions without real firmware or mounts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import make_oneui9_s10_images as images


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / 'tree'
        self.tree.mkdir()
        self.app = self.tree / 'example.apk'
        self.app.write_bytes(b'app stays intact')
        self.image = self.root / 'system.img'
        self.log = self.root / 'system-image.log'
        self.calls = []

    def fake_mkfs(self, sizes):
        def run(command, **kwargs):
            codec = command[command.index('-z') + 1]
            self.calls.append(codec)
            self.assertEqual(command[-1], str(self.tree))
            self.assertEqual(command[command.index('-E') + 1], 'legacy-compress')
            with self.image.open('wb') as output:
                output.truncate(sizes[codec])
        return run

    def test_fits_first_attempt_and_capacity_is_inclusive(self):
        with patch.object(images.subprocess, 'run', self.fake_mkfs({'lz4': 100})):
            self.assertEqual(images.pack_system_image(self.tree, self.image, 100, self.log),
                             [{'compression': 'lz4', 'bytes': 100}])
        self.assertEqual(self.calls, ['lz4'])
        self.assertEqual(self.app.read_bytes(), b'app stays intact')

    def test_retries_complete_tree_with_compatible_high_compression(self):
        with patch.object(images.subprocess, 'run', self.fake_mkfs({'lz4': 101, 'lz4hc,9': 99})):
            result = images.pack_system_image(self.tree, self.image, 100, self.log)
        self.assertEqual(result, [{'compression': 'lz4', 'bytes': 101}, {'compression': 'lz4hc,9', 'bytes': 99}])
        self.assertEqual(self.image.stat().st_size, 99)
        self.assertEqual(self.app.read_bytes(), b'app stays intact')

    def test_neither_fits_reports_both_and_leaves_no_partial_image(self):
        with patch.object(images.subprocess, 'run', self.fake_mkfs({'lz4': 120, 'lz4hc,9': 101})):
            with self.assertRaisesRegex(RuntimeError, 'lz4: 120 bytes, lz4hc,9: 101 bytes'):
                images.pack_system_image(self.tree, self.image, 100, self.log)
        self.assertFalse(self.image.exists())
        self.assertEqual(self.app.read_bytes(), b'app stays intact')

    def test_mkfs_failure_removes_partial_image(self):
        def fail(*args, **kwargs):
            self.image.write_bytes(b'partial')
            raise subprocess.CalledProcessError(1, args[0])
        with patch.object(images.subprocess, 'run', fail):
            with self.assertRaises(subprocess.CalledProcessError):
                images.pack_system_image(self.tree, self.image, 100, self.log)
        self.assertFalse(self.image.exists())

    def test_real_layout_is_5700_mib(self):
        layout = json.loads((ROOT / 'target/beyond1lte/oneui9/stock-layout.json').read_text())
        self.assertEqual(layout['partitions']['system']['bytes'], 5700 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
