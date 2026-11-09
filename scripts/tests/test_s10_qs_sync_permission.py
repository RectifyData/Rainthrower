#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Binary-manifest fixtures for the SystemUI sync permission repair."""
import hashlib
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s10_qs_sync_permission as qs


def chunk(kind, header, payload):
    return struct.pack('<HHI', kind, header, len(payload) + 8) + payload


def fixture(utf8=True, initial=(), package='com.android.systemui'):
    names = ['manifest', 'package', package, 'name', qs.ANDROID_NS, 'uses-permission',
             'android.permission.INTERNET', 'application', 'android'] + list(initial)
    body, offsets = bytearray(), []
    for text in names:
        offsets.append(len(body))
        data = text.encode('utf-8' if utf8 else 'utf-16-le')
        length = len(text.encode('utf-16-le')) // 2
        if utf8:
            body += qs.encode_length8(length) + qs.encode_length8(len(data)) + data + b'\0'
        else:
            body += qs.encode_length16(length) + data + b'\0\0'
    body += b'\0' * (-len(body) % 4)
    pool = chunk(1, 28, struct.pack('<IIIII', len(names), 0, 0x100 if utf8 else 0,
                                    28 + 4 * len(names), 0)
                 + struct.pack('<' + 'I' * len(names), *offsets) + body)
    resource_map = chunk(0x180, 8, struct.pack('<IIII', 0, 0, 0, 0x01010003))
    node = lambda t, ext: chunk(t, 16, struct.pack('<II', 1, qs.NO_INDEX) + ext)
    def start(name, attrs):
        data = struct.pack('<IIHHHHHH', qs.NO_INDEX, name, 20, 20, len(attrs), 0, 0, 0)
        for ns, key, value in attrs:
            data += struct.pack('<IIIHBBI', ns, key, value, 8, 0, 3, value)
        return node(0x102, data)
    def end(name): return node(0x103, struct.pack('<II', qs.NO_INDEX, name))
    xml = [pool, resource_map, node(0x100, struct.pack('<II', 8, 4)),
           start(0, [(qs.NO_INDEX, 1, 2)]), start(5, [(4, 3, 6)]), end(5)]
    for i in range(len(initial)):
        xml += [start(5, [(4, 3, 9 + i)]), end(5)]
    xml += [start(7, []), end(7), end(0), node(0x101, struct.pack('<II', 8, 4))]
    payload = b''.join(xml)
    return chunk(3, 8, payload)


def permissions(data):
    parts = list(qs.chunks(data))
    strings = qs.read_pool(parts[0][1])[0]
    results = []
    for kind, data in parts:
        if kind == 0x102 and strings[qs.u32(data, 20)] == 'uses-permission':
            start = 16 + qs.u16(data, 24)
            results.append(strings[qs.u32(data, start + 8)])
    return results


class SyncPermissionTests(unittest.TestCase):
    def test_adds_two_permissions_and_preserves_all_original_chunks(self):
        for utf8 in (True, False):
            with self.subTest(utf8=utf8):
                original = fixture(utf8)
                result = qs.patch_manifest(original)
                self.assertEqual(permissions(result), ['android.permission.INTERNET', qs.READ, qs.WRITE])
                old_parts = list(qs.chunks(original))[1:]
                new_parts = list(qs.chunks(result))[1:]
                for kind, data in old_parts:
                    index = new_parts.index((kind, data))
                    new_parts = new_parts[index + 1:]
                self.assertEqual(qs.read_pool(list(qs.chunks(result))[0][1])[0][-2:], [qs.READ, qs.WRITE])

    def test_existing_write_is_not_duplicated(self):
        result = qs.patch_manifest(fixture(initial=[qs.WRITE]))
        self.assertEqual(permissions(result).count(qs.WRITE), 1)
        self.assertEqual(permissions(result).count(qs.READ), 1)

    def test_existing_read_requires_runtime_investigation(self):
        with self.assertRaisesRegex(ValueError, 'already requests'):
            qs.patch_manifest(fixture(initial=[qs.READ]))

    def test_wrong_package_and_truncated_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected SystemUI'):
            qs.patch_manifest(fixture(package='com.example.app'))
        with self.assertRaises(ValueError):
            qs.patch_manifest(fixture()[:-8])

    def test_invalid_or_mismatched_end_element_rejected(self):
        data = bytearray(fixture())
        end = next((part for part in qs.chunks(data) if part[0] == 0x103))[1]
        offset = data.index(end)
        struct.pack_into('<I', data, offset + 20, 0)
        with self.assertRaisesRegex(ValueError, 'Unbalanced'):
            qs.patch_manifest(bytes(data))


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.system = root / 'system'
        self.apk = self.system / qs.APK
        self.apk.parent.mkdir(parents=True)
        self.tools = root / 'tools'
        self.tools.mkdir()
        for name in ('zipalign', 'aapt2'):
            (self.tools / name).touch()
        with zipfile.ZipFile(self.apk, 'w') as apk:
            apk.writestr('AndroidManifest.xml', fixture())
            apk.writestr('classes.dex', b'unchanged code')
        # An empty test signing block exercises copying and ZIP offset handling;
        # the real Android build-tools signing/verification is tested separately.
        data = bytearray(self.apk.read_bytes())
        end = data.rfind(b'PK\x05\x06')
        central = struct.unpack_from('<I', data, end + 16)[0]
        block = struct.pack('<QQ', 24, 24) + b'APK Sig Block 42'
        data[central:central] = block
        struct.pack_into('<I', data, end + len(block) + 16, central + len(block))
        self.apk.write_bytes(data)
        self.apk.chmod(0o640)
        self.before = self.apk.read_bytes()
        self.expected = hashlib.sha256(self.before).hexdigest()

    @staticmethod
    def zipalign(command, **kwargs):
        if '-c' not in command:
            shutil.copyfile(command[-2], command[-1])

    @staticmethod
    def aapt(*args, **kwargs):
        return ("package: com.android.systemui\nuses-permission: name='" + qs.READ
                + "'\nuses-permission: name='" + qs.WRITE + "'\n")

    def test_changes_only_manifest_and_retains_signing_identity_and_mode(self):
        with patch.object(qs.subprocess, 'run', side_effect=self.zipalign), \
             patch.object(qs.subprocess, 'check_output', side_effect=self.aapt):
            result = qs.configure(self.system, self.expected, self.tools)
        self.assertEqual(result['files'][qs.APK], hashlib.sha256(self.apk.read_bytes()).hexdigest())
        self.assertEqual(qs.signing_block(self.before), qs.signing_block(self.apk.read_bytes()))
        self.assertEqual(self.apk.stat().st_mode & 0o777, 0o640)
        self.assertFalse(result['device_tested'])
        with zipfile.ZipFile(self.apk) as apk:
            self.assertEqual(apk.read('classes.dex'), b'unchanged code')
            self.assertEqual(permissions(apk.read('AndroidManifest.xml'))[-2:], [qs.READ, qs.WRITE])

    def test_aapt_or_hash_mismatch_leaves_input_unchanged(self):
        with self.assertRaisesRegex(ValueError, 'verified donor'):
            qs.configure(self.system, '0' * 64, self.tools)
        with patch.object(qs.subprocess, 'run', side_effect=self.zipalign), \
             patch.object(qs.subprocess, 'check_output', return_value='invalid'):
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                qs.configure(self.system, self.expected, self.tools)
        self.assertEqual(self.apk.read_bytes(), self.before)


if __name__ == '__main__':
    unittest.main()
