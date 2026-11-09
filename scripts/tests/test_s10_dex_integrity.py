#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ensure unsupported, corrupted and changed DEX formats are rejected."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from s10_dex_integrity import assembler_api, validate_dex


def fixture(version):
    data = bytearray(112)
    data[:8] = b'dex\n' + version + b'\0'
    struct.pack_into('<III', data, 32, len(data), 112, 0x12345678)
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into('<I', data, 8, zlib.adler32(data[12:]) & 0xffffffff)
    return bytes(data)


class DexIntegrityTests(unittest.TestCase):
    def test_supported_versions_keep_their_format(self):
        for version, api in [(b'035', 23), (b'037', 25), (b'038', 27), (b'039', 28), (b'040', 30)]:
            with self.subTest(version=version):
                self.assertEqual(assembler_api(fixture(version)), api)
                self.assertEqual(validate_dex(fixture(version), version), version)
                with self.assertRaises(ValueError):
                    validate_dex(fixture(version), b'999')

    def test_rejects_container_format_and_damaged_checksums(self):
        for bad in (fixture(b'041'), fixture(b'039')[:-1], fixture(b'039')[:50] + b'x' + fixture(b'039')[51:]):
            with self.subTest(bad=bad[:8]), self.assertRaises(ValueError):
                validate_dex(bad)


if __name__ == '__main__':
    unittest.main()
