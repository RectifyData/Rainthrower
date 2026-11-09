#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail closed if any installed APK lacks a valid signature for the target SDK."""
import os
from pathlib import Path
import subprocess
from s10_image_binding import file_digest


def apk_inventory(tree, *, allow_empty=False):
    tree = Path(tree)
    apks = {}
    for directory, dirs, files in os.walk(tree, followlinks=False):
        dirs.sort(); files.sort()
        for name in dirs + files:
            if not name.endswith('.apk'):
                continue
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('APK is not a regular file; review the link: ' + str(path.relative_to(tree)))
            apks[str(path.relative_to(tree))] = file_digest(path)
    if not apks and not allow_empty:
        raise ValueError('No APKs found in the system tree')
    return apks


def verify_apks(tree, apksigner, log_path, *, allow_empty=False):
    tree, apksigner, log_path = Path(tree), Path(apksigner), Path(log_path)
    if not apksigner.is_file() or not os.access(apksigner, os.X_OK):
        raise ValueError('An executable Android apksigner is required')
    apks = apk_inventory(tree, allow_empty=allow_empty)
    with log_path.open('xb') as log:
        for relative in apks:
            # A retained but invalid OEM signing block is not a valid signature.
            log.write(('Verifying ' + relative + '\n').encode())
            log.flush()
            result = subprocess.run([str(apksigner), 'verify', '--min-sdk-version', '37',
                                     '--max-sdk-version', '37', str(tree / relative)], stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise ValueError('Invalid APK signature; see external signature log: ' + relative)
    if apk_inventory(tree, allow_empty=allow_empty) != apks:
        raise ValueError('APK inventory changed during signature verification')
    return apks
