#!/usr/bin/env python3
"""Ensure signature failures and APK changes stop packing before image writes."""
from pathlib import Path
import os
import shutil
import zipfile
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s10_apk_signatures as gate


class ApkSignatureGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / 'tree'
        self.tree.mkdir()
        (self.tree / 'systemui.apk').write_bytes(b'apk')
        self.signer = self.root / 'apksigner'
        self.signer.write_text('#!/bin/sh\nexit 0\n')
        self.signer.chmod(0o755)
        self.log = self.root / 'signature.log'

    def test_all_apks_checked_for_target_sdk(self):
        (self.tree / 'second.apk').write_bytes(b'apk2')
        commands = []
        def run(cmd, **kwargs):
            commands.append(cmd)
            return type('Result', (), {'returncode': 0})()
        with patch.object(gate.subprocess, 'run', run):
            result = gate.verify_apks(self.tree, self.signer, self.log)
        self.assertEqual(set(result), {'systemui.apk', 'second.apk'})
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(cmd[1:6] == ['verify', '--min-sdk-version', '37', '--max-sdk-version', '37'] for cmd in commands))

    def test_invalid_signature_rejected(self):
        self.signer.write_text('#!/bin/sh\nexit 1\n')
        with self.assertRaisesRegex(ValueError, 'Invalid APK signature'):
            gate.verify_apks(self.tree, self.signer, self.log)

    def test_modified_apk_during_verification_rejected(self):
        def mutate(*args, **kwargs):
            (self.tree / 'systemui.apk').write_bytes(b'changed')
            return type('Result', (), {'returncode': 0})()
        with patch.object(gate.subprocess, 'run', mutate):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                gate.verify_apks(self.tree, self.signer, self.log)

    @unittest.skipUnless(os.environ.get('S10_APKSIGNER'), 'set S10_APKSIGNER to exercise real cryptographic verification')
    def test_real_prebuilt_signature_and_tampering(self):
        root = Path(__file__).resolve().parents[2]
        fixture = root / 'prebuilts/samsung/b0qxxx/product/overlay/UwbRROverlay.apk'
        shutil.copyfile(fixture, self.tree / 'systemui.apk')
        signer = Path(os.environ['S10_APKSIGNER'])
        self.assertEqual(len(gate.verify_apks(self.tree, signer, self.log)), 1)
        self.log.unlink()
        with zipfile.ZipFile(self.tree / 'systemui.apk', 'a') as archive:
            archive.writestr('added-after-signing', b'modified')
        with self.assertRaisesRegex(ValueError, 'Invalid APK signature'):
            gate.verify_apks(self.tree, signer, self.log)

    def test_symlink_missing_signer_and_empty_tree_fail_closed(self):
        (self.tree / 'link.apk').symlink_to('systemui.apk')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            gate.verify_apks(self.tree, self.signer, self.log)
        (self.tree / 'link.apk').unlink()
        with self.assertRaisesRegex(ValueError, 'apksigner'):
            gate.verify_apks(self.tree, self.root / 'absent', self.log)
        (self.tree / 'systemui.apk').unlink()
        with self.assertRaisesRegex(ValueError, 'No APKs'):
            gate.verify_apks(self.tree, self.signer, self.log)
        self.assertEqual(gate.verify_apks(self.tree, self.signer, self.log, allow_empty=True), {})


if __name__ == '__main__':
    unittest.main()
