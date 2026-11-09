#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add the sync permissions missing from the original SystemUI manifest.

Only the preinstalled, checksum-pinned APK is patched. The OEM signing block is
retained for system-image certificate collection, not for installing an update;
the modified APK's original signature no longer verifies against its contents.
"""
import copy
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from build_artisan_control import retain_oem_certificate, signing_block
from s10_models import ROOT, digest

APK = "system_ext/priv-app/SystemUI/SystemUI.apk"
READ = "android.permission.READ_SYNC_SETTINGS"
WRITE = "android.permission.WRITE_SYNC_SETTINGS"
NO_INDEX = 0xffffffff
ANDROID_NS = "http://schemas.android.com/apk/res/android"


def u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def chunks(data):
    if len(data) < 8 or u16(data, 0) != 3 or u16(data, 2) != 8 or u32(data, 4) != len(data):
        raise ValueError("Invalid binary XML header")
    at = 8
    while at < len(data):
        if at + 8 > len(data):
            raise ValueError("Truncated binary XML chunk")
        kind, header, size = struct.unpack_from("<HHI", data, at)
        if header < 8 or size < header or size % 4 or at + size > len(data):
            raise ValueError("Invalid binary XML chunk")
        yield kind, data[at:at + size]
        at += size
    if at != len(data):
        raise ValueError("Invalid binary XML size")


def length8(data, pos):
    first = data[pos]
    if first & 0x80:
        return ((first & 0x7f) << 8) | data[pos + 1], pos + 2
    return first, pos + 1


def length16(data, pos):
    first = u16(data, pos)
    if first & 0x8000:
        return ((first & 0x7fff) << 16) | u16(data, pos + 2), pos + 4
    return first, pos + 2


def read_pool(pool):
    if len(pool) < 28 or u16(pool, 2) != 28:
        raise ValueError("Unsupported string pool header")
    count, styles, flags, start, style_start = struct.unpack_from("<IIIII", pool, 8)
    table_end = 28 + 4 * (count + styles)
    end = style_start or len(pool)
    if table_end > start or not start <= end <= len(pool) or (styles and not style_start):
        raise ValueError("Invalid string pool offsets")
    offsets = [u32(pool, 28 + 4 * i) for i in range(count)]
    strings = []
    try:
        for offset in offsets:
            pos = start + offset
            if not start <= pos < end:
                raise ValueError("Invalid string offset")
            if flags & 0x100:
                char_len, pos = length8(pool, pos)
                size, pos = length8(pool, pos)
                if pos + size >= end or pool[pos + size] != 0:
                    raise ValueError("Invalid UTF-8 string")
                value = pool[pos:pos + size].decode("utf-8")
                if len(value.encode("utf-16-le")) // 2 != char_len:
                    raise ValueError("Invalid UTF-8 string length")
            else:
                char_len, pos = length16(pool, pos)
                size = char_len * 2
                if pos + size + 2 > end or pool[pos + size:pos + size + 2] != b"\0\0":
                    raise ValueError("Invalid UTF-16 string")
                value = pool[pos:pos + size].decode("utf-16-le")
            strings.append(value)
    except (IndexError, struct.error, UnicodeError) as exc:
        raise ValueError("Malformed binary XML string") from exc
    return strings, offsets, count, styles, flags, start, end, style_start


def encode_length8(n):
    if n > 0x7fff:
        raise ValueError("String too long")
    return bytes((n,)) if n < 0x80 else bytes(((n >> 8) | 0x80, n & 0xff))


def encode_length16(n):
    if n > 0x7fffffff:
        raise ValueError("String too long")
    return struct.pack("<H", n) if n < 0x8000 else struct.pack("<HH", (n >> 16) | 0x8000, n & 0xffff)


def extend_pool(pool, additions):
    strings, offsets, count, styles, flags, start, end, style_start = read_pool(pool)
    payload = bytearray(pool[start:end])
    for text in additions:
        offsets.append(len(payload))
        encoded = text.encode("utf-8" if flags & 0x100 else "utf-16-le")
        length = len(text.encode("utf-16-le")) // 2
        if flags & 0x100:
            payload += encode_length8(length) + encode_length8(len(encoded)) + encoded + b"\0"
        else:
            payload += encode_length16(length) + encoded + b"\0\0"
    payload += b"\0" * (-len(payload) % 4)
    new_start = start + 4 * len(additions)
    new_style_start = new_start + len(payload) if style_start else 0
    header = bytearray(pool[:28])
    struct.pack_into("<IIIII", header, 8, count + len(additions), styles, flags & ~1, new_start, new_style_start)
    table = b"".join(struct.pack("<I", n) for n in offsets)
    table += pool[28 + 4 * count:start]
    result = header + table + payload + (pool[style_start:] if style_start else b"")
    struct.pack_into("<I", result, 4, len(result))
    return bytes(result), strings + list(additions)


def patch_manifest(data):
    parts = list(chunks(data))
    if not parts or parts[0][0] != 1 or sum(kind == 1 for kind, _ in parts) != 1:
        raise ValueError("Expected exactly one leading binary XML string pool")
    pool, strings = extend_pool(parts[0][1], [READ, WRITE])
    def string(index):
        if index == NO_INDEX:
            return None
        if index >= len(strings) - 2:
            raise ValueError("Invalid original XML string index")
        return strings[index]

    depth, template, end_template, declared, package = 0, None, None, set(), None
    stack = []
    for index, (kind, chunk) in enumerate(parts[1:], 1):
        if kind == 0x102:
            if len(chunk) < 36 or u16(chunk, 2) != 16:
                raise ValueError("Invalid start element")
            name = string(u32(chunk, 20))
            attr_start, attr_size, count = u16(chunk, 24), u16(chunk, 26), u16(chunk, 28)
            base = 16 + attr_start
            if attr_size != 20 or base < 36 or base + count * attr_size > len(chunk):
                raise ValueError("Unsupported element attributes")
            attrs = []
            for j in range(count):
                pos = base + j * attr_size
                ns, attr_name, raw = struct.unpack_from("<III", chunk, pos)
                size, zero, kind_value, value = struct.unpack_from("<HBBI", chunk, pos + 12)
                if size != 8 or zero:
                    raise ValueError("Invalid XML typed value")
                attrs.append((string(ns), string(attr_name), string(raw), kind_value, value, pos))
            if depth == 0 and name == "manifest":
                package = next((a[2] for a in attrs if a[0] is None and a[1] == "package"), None)
            if depth == 1 and name in ("uses-permission", "uses-permission-sdk-23", "uses-permission-sdk-m"):
                attr = next((a for a in attrs if a[0] == ANDROID_NS and a[1] == "name"), None)
                if attr and attr[2] in (READ, WRITE):
                    if name != "uses-permission" or len(attrs) != 1 or attr[3] != 3:
                        raise ValueError("Sync permission has a conditional or unsupported declaration")
                    declared.add(attr[2])
                if name == "uses-permission" and len(attrs) == 1 and attr and attr[3] == 3 and attr[4] != NO_INDEX:
                    if index + 1 < len(parts) and parts[index + 1][0] == 0x103:
                        end = parts[index + 1][1]
                        if len(end) == 24 and u32(end, 20) == u32(chunk, 20):
                            template, end_template = (chunk, attr[5]), end
            stack.append(u32(chunk, 20))
            depth += 1
        elif kind == 0x103:
            if len(chunk) != 24 or u16(chunk, 2) != 16 or not stack or stack.pop() != u32(chunk, 20):
                raise ValueError("Unbalanced binary XML")
            depth -= 1
    if depth or package != "com.android.systemui" or template is None:
        raise ValueError("Unexpected SystemUI manifest structure")
    # A declared normal permission should already be granted. In that case the
    # observed denial needs runtime investigation, not a duplicate declaration.
    if READ in declared:
        raise ValueError("SystemUI already requests READ_SYNC_SETTINGS; investigate the runtime denial")
    added = []
    for offset, permission in enumerate((READ, WRITE)):
        if permission in declared:
            continue
        entry = bytearray(template[0])
        string_index = len(strings) - 2 + offset
        struct.pack_into("<I", entry, template[1] + 8, string_index)
        struct.pack_into("<I", entry, template[1] + 16, string_index)
        added.extend((bytes(entry), end_template))
    # Insert before the first root-level application element, keeping permissions
    # as manifest children and leaving all existing chunks byte-for-byte intact.
    output, depth, inserted = [pool], 0, False
    for kind, chunk in parts[1:]:
        if kind == 0x102:
            if depth == 1 and string(u32(chunk, 20)) == "application":
                if inserted:
                    raise ValueError("Multiple application elements")
                output.extend(added)
                inserted = True
            depth += 1
        elif kind == 0x103:
            depth -= 1
        output.append(chunk)
    if not inserted:
        raise ValueError("SystemUI application element missing")
    body = b"".join(output)
    return struct.pack("<HHI", 3, 8, len(body) + 8) + body


def configure(system, expected_sha256, tools=None):
    apk = system / APK
    if not apk.is_file() or apk.is_symlink() or digest(apk) != expected_sha256:
        raise ValueError("SystemUI does not match the verified donor input")
    tools = tools or ROOT / "out/host-tools/android-sdk-36/build-tools/android-16"
    zipalign, aapt2 = tools / "zipalign", tools / "aapt2"
    if not zipalign.is_file() or not aapt2.is_file():
        raise ValueError("Android build-tools zipalign and aapt2 are required for the SystemUI sync patch")
    with tempfile.TemporaryDirectory(prefix="s10-qs-sync-") as tmp:
        raw, aligned = Path(tmp) / "raw.apk", Path(tmp) / "aligned.apk"
        with zipfile.ZipFile(apk) as source, zipfile.ZipFile(raw, "w") as dest:
            names = source.namelist()
            if names.count("AndroidManifest.xml") != 1 or len(names) != len(set(names)):
                raise ValueError("Unexpected SystemUI APK inventory")
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename == "AndroidManifest.xml":
                    content = patch_manifest(content)
                dest.writestr(copy.copy(info), content)
        subprocess.run([str(zipalign), "-f", "-p", "4", str(raw), str(aligned)], check=True)
        retain_oem_certificate(apk, aligned)
        subprocess.run([str(zipalign), "-c", "-p", "4", str(aligned)], check=True)
        permission_dump = subprocess.check_output([str(aapt2), "dump", "permissions", str(aligned)], text=True)
        lines = permission_dump.splitlines()
        for permission in (READ, WRITE):
            if lines.count("uses-permission: name='" + permission + "'") != 1:
                raise ValueError("Patched SystemUI permission verification failed: " + permission)
        if not signing_block(apk.read_bytes()) or signing_block(apk.read_bytes()) != signing_block(aligned.read_bytes()):
            raise ValueError("SystemUI OEM signing block was not retained")
        with zipfile.ZipFile(apk) as old, zipfile.ZipFile(aligned) as new:
            if old.namelist() != new.namelist():
                raise ValueError("SystemUI APK inventory changed")
            for name in old.namelist():
                if name != "AndroidManifest.xml" and old.read(name) != new.read(name):
                    raise ValueError("Unexpected SystemUI payload change: " + name)
        # In-place write preserves the original owner, mode and SELinux xattr.
        apk.write_bytes(aligned.read_bytes())
    return {"files": {APK: digest(apk)}, "input_sha256": expected_sha256,
            "required_permissions": [READ, WRITE], "original_signature_valid": False,
            "device_tested": False}
