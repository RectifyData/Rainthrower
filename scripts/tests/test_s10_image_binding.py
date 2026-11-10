#!/usr/bin/env python3
"""Complete image-tree identity checks using isolated, non-device fixtures."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from s10_image_binding import file_digest, tree_digest, verify_binding, compare_image_tree


class ImageBindingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.system = self.root / 'tree/system'
        self.vendor = self.root / 'vendor'
        self.system.mkdir(parents=True)
        self.vendor.mkdir()
        (self.system / 'a.apk').write_bytes(b'signed apk')
        (self.system / 'alias').symlink_to('a.apk')
        (self.vendor / 'config').write_bytes(b'board config')
        (self.root / 'input.json').write_text('{"model":"test"}')
        self.images = {'schema': 2, 'apk_signatures_verified': True, 'input_sha256': file_digest(self.root / 'input.json'),
                       'tree_sha256': {'system': tree_digest(self.root / 'tree'),
                                       'vendor': tree_digest(self.vendor)}}

    def test_roundtrip_and_old_metadata_rejected(self):
        verify_binding(self.root, self.images)
        with self.assertRaises(ValueError):
            verify_binding(self.root, {'schema': 1})

    def test_unchecked_apk_and_metadata_changes_are_rejected(self):
        (self.system / 'a.apk').write_bytes(b'new bytes')
        with self.assertRaisesRegex(ValueError, 'tree changed'):
            verify_binding(self.root, self.images)
        (self.system / 'a.apk').write_bytes(b'signed apk')
        (self.root / 'input.json').write_text('{"model":"different"}')
        with self.assertRaisesRegex(ValueError, 'candidate binding'):
            verify_binding(self.root, self.images)

    def test_symlink_mode_extra_and_vendor_changes_are_rejected(self):
        original = tree_digest(self.root / 'tree')
        (self.system / 'alias').unlink(); (self.system / 'alias').symlink_to('other')
        self.assertNotEqual(tree_digest(self.root / 'tree'), original)
        (self.system / 'alias').unlink(); (self.system / 'alias').symlink_to('a.apk')
        (self.system / 'a.apk').chmod(0o600)
        self.assertNotEqual(tree_digest(self.root / 'tree'), original)
        (self.system / 'a.apk').chmod(0o644)
        (self.system / 'added').write_bytes(b'extra')
        self.assertNotEqual(tree_digest(self.root / 'tree'), original)
        (self.system / 'added').unlink()
        (self.vendor / 'config').unlink()
        with self.assertRaisesRegex(ValueError, 'vendor'):
            verify_binding(self.root, self.images)

    def test_compares_entire_image_and_allows_only_generated_lost_found(self):
        mounted = self.root / 'mounted'
        shutil.copytree(self.vendor, mounted, symlinks=True)
        (mounted / 'lost+found').mkdir()
        self.assertNotEqual(tree_digest(mounted), tree_digest(self.vendor))
        self.assertEqual(tree_digest(mounted, ignore_lost_found=True), tree_digest(self.vendor))
        self.assertEqual(compare_image_tree(self.vendor, mounted, ext4=True), tree_digest(self.vendor))
        (mounted / 'lost+found' / 'hidden').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, r'lost\+found'):
            compare_image_tree(self.vendor, mounted, ext4=True)
        (mounted / 'lost+found' / 'hidden').unlink()
        (mounted / 'unexpected').write_bytes(b'bad')
        self.assertNotEqual(tree_digest(mounted, ignore_lost_found=True), tree_digest(self.vendor))

    def test_does_not_follow_symlinks_or_accept_symlinked_root(self):
        (self.system / 'external').symlink_to(self.vendor, target_is_directory=True)
        value = tree_digest(self.root / 'tree')
        (self.vendor / 'config').write_bytes(b'changed outside tree')
        self.assertEqual(tree_digest(self.root / 'tree'), value)
        with self.assertRaises(ValueError):
            tree_digest(self.system / 'external')


if __name__ == '__main__':
    unittest.main()
