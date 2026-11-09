#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate standalone DEX before repacking critical Android components."""
import hashlib
import struct
import zlib

# Minimum compatible assembler API for each standalone DEX format. DEX 041
# uses container headers and is deliberately unsupported by these rewriters.
API_FOR_VERSION = {b'035': 23, b'037': 25, b'038': 27, b'039': 28, b'040': 30}


def validate_dex(data, expected_version=None):
    if len(data) < 112 or data[:4] != b'dex\n' or data[7] != 0:
        raise ValueError('Invalid standalone DEX header')
    version = data[4:7]
    if version not in API_FOR_VERSION or (expected_version is not None and version != expected_version):
        raise ValueError('Unsupported or changed standalone DEX version')
    length, header, endian = struct.unpack_from('<III', data, 32)
    if (length != len(data) or header != 112 or endian != 0x12345678
            or data[12:32] != hashlib.sha1(data[32:]).digest()
            or struct.unpack_from('<I', data, 8)[0] != (zlib.adler32(data[12:]) & 0xffffffff)):
        raise ValueError('Invalid standalone DEX size, header or checksum')
    return version


def assembler_api(data):
    return API_FOR_VERSION[validate_dex(data)]
