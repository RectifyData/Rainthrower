#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check guarded mobile-data transformation and DEX integrity checks."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s10_qs_mobile_data as mobile


def sample():
    method = ('.method public final ' + mobile.METHOD + '\n    .locals 13\n\n'
              + mobile.ORIGINAL_NULL + '\n    # preserved policy checks\n'
              + mobile.ORIGINAL_STATE + '\n    xor-int/2addr v1, v3\n'
              + '    invoke-virtual {v0, v1}, Landroid/telephony/TelephonyManager;->setDataEnabled(Z)V\n'
              + '    :cond_2ed\n    return-object v0\n.end method')
    text = ('.class public final ' + mobile.OWNER + '\n.super Ljava/lang/Object;\n\n'
            + method + '\n\n.method public untouched()V\n    return-void\n.end method\n')
    fingerprint = hashlib.sha256(mobile.semantics(method).encode()).hexdigest()
    return text, fingerprint


def dex_fixture():
    data = bytearray(112)
    data[:8] = b'dex\n039\0'
    struct.pack_into('<III', data, 32, len(data), 112, 0x12345678)
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into('<I', data, 8, zlib.adler32(data[12:]) & 0xffffffff)
    return bytes(data)


class MobileDataTests(unittest.TestCase):
    def test_fallback_is_guarded_and_keeps_existing_handler(self):
        source, fingerprint = sample()
        with patch.object(mobile, 'METHOD_SHA256', fingerprint):
            result = mobile.patch_smali(source)
        self.assertIn('if-nez v1, :cond_11', result)
        self.assertIn('getDefaultDataSubscriptionId()I', result)
        self.assertIn('getActiveSubscriptionInfo(I)Landroid/telephony/SubscriptionInfo;', result)
        self.assertIn('getRepoForSubId(I)', result)
        self.assertIn('.catch Ljava/lang/SecurityException;', result)
        self.assertIn('# preserved policy checks', result)
        self.assertIn('createForSubscriptionId(I)Landroid/telephony/TelephonyManager;', result)
        self.assertIn('->isDataEnabled()Z', result)
        self.assertIn('->setDataEnabled(Z)V', result)
        self.assertNotIn(mobile.ORIGINAL_STATE, result)
        self.assertIn('.method public untouched()V\n    return-void\n.end method', result)
        self.assertLess(result.index('getActiveSubscriptionInfo'), result.index('# preserved policy checks'))
        self.assertLess(result.index('# preserved policy checks'), result.index('->setDataEnabled(Z)V'))

    def test_unexpected_method_or_class_is_rejected(self):
        source, fingerprint = sample()
        with patch.object(mobile, 'METHOD_SHA256', fingerprint):
            with self.assertRaisesRegex(ValueError, 'Unknown mobile-data handler'):
                mobile.patch_smali(source.replace('preserved policy checks', 'changed policy checks'))
            with self.assertRaisesRegex(ValueError, 'Unexpected mobile-data interactor'):
                mobile.patch_smali(source.replace(mobile.OWNER, 'Lother/Class;', 1))
            with self.assertRaisesRegex(ValueError, 'Unknown mobile-data handler'):
                mobile.patch_smali(source.replace(mobile.ORIGINAL_NULL, ''))

    def test_dex_header_and_checksums_fail_closed(self):
        valid = dex_fixture()
        mobile.validate_dex(valid, b'039')
        for value in (b'dex\n041\0' + valid[8:], valid[:-1], valid[:20] + b'x' + valid[21:]):
            with self.assertRaises(ValueError):
                mobile.validate_dex(value, b'039')


if __name__ == '__main__':
    unittest.main()
