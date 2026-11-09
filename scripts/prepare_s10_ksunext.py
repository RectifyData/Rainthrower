#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply the pinned KernelSU-Next Legacy manual hooks to an external S10 kernel tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from build_oneui9_s10_kernel import PIN, digest

ROOT = Path(__file__).resolve().parent.parent
KSU_PIN = 'b933b1f728116a456fbebf27b6a8772a640cdf23'
PATCH = ROOT / 'scripts/ksunext/s10-legacy-manual-hooks.patch'
KSU_PATCH = ROOT / 'scripts/ksunext/ksunext-exec-fd.patch'
HOOK_FILES = ('drivers/input/input.c', 'fs/exec.c', 'fs/open.c', 'fs/read_write.c', 'fs/stat.c', 'kernel/reboot.c')


def command(*args, cwd):
    return subprocess.check_output(args, cwd=cwd).decode().strip()


def diff(kernel):
    return subprocess.check_output(['git', 'diff', '--binary', 'HEAD', '--'], cwd=kernel)


def prepare(kernel, ksu, record):
    kernel, ksu = Path(kernel).resolve(), Path(ksu).resolve()
    record = Path(record).resolve()
    if kernel.is_relative_to(ROOT) or ksu.is_relative_to(ROOT) or record.is_relative_to(ROOT):
        raise ValueError('Kernel, KernelSU and integration record must be outside the ROM checkout')
    if record.exists() or not record.parent.is_dir():
        raise ValueError('Use a new integration record in an existing directory')
    if command('git', 'rev-parse', 'HEAD', cwd=kernel) != PIN:
        raise ValueError('The pinned Exynos9820 kernel commit is required')
    link = kernel / 'drivers/kernelsu'
    if link.exists() or link.is_symlink():
        raise ValueError('KernelSU is already present; do not overlay another integration')
    if (command('git', 'rev-parse', 'HEAD', cwd=ksu) != KSU_PIN
            or command('git', 'status', '--porcelain', '--untracked-files=all', cwd=ksu)):
        raise ValueError('An unchanged pinned KernelSU-Next Legacy checkout is required')
    source = ksu / 'kernel'
    if not (source / 'Kconfig').is_file() or not (source / 'Kbuild').is_file():
        raise ValueError('Missing KernelSU-Next kernel sources')
    for name in HOOK_FILES:
        if 'ksu_handle_' in (kernel / name).read_text():
            raise ValueError('Existing KernelSU hooks require manual review: ' + name)
    before = hashlib.sha256(diff(kernel)).hexdigest()
    subprocess.run(['git', 'apply', '--check', str(PATCH)], cwd=kernel, check=True)
    subprocess.run(['git', 'apply', '--check', str(KSU_PATCH)], cwd=ksu, check=True)
    subprocess.run(['git', 'apply', str(KSU_PATCH)], cwd=ksu, check=True)
    try:
        os.symlink(os.path.relpath(source, link.parent), link)
    except BaseException:
        subprocess.run(['git', 'apply', '--reverse', str(KSU_PATCH)], cwd=ksu, check=True)
        raise
    # The source stays in its pinned Git checkout for KernelSU version detection.
    # Remove the symlink if applying the kernel patch fails.
    try:
        subprocess.run(['git', 'apply', str(PATCH)], cwd=kernel, check=True)
    except BaseException:
        link.unlink()
        subprocess.run(['git', 'apply', '--reverse', str(KSU_PATCH)], cwd=ksu, check=True)
        raise
    result = {'schema': 1, 'kernel_commit': PIN, 'ksu_commit': KSU_PIN,
              'ksu_patch_sha256': digest(KSU_PATCH),
              'hooks_patch_sha256': digest(PATCH), 'before_diff_sha256': before,
              'after_diff_sha256': hashlib.sha256(diff(kernel)).hexdigest(),
              'hook_mode': 'manual', 'susfs_integrated': False,
              'kernel_build_tested': False, 'device_tested': False}
    try:
        with record.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    except BaseException:
        subprocess.run(['git', 'apply', '--reverse', str(PATCH)], cwd=kernel, check=True)
        link.unlink()
        subprocess.run(['git', 'apply', '--reverse', str(KSU_PATCH)], cwd=ksu, check=True)
        raise
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('kernel', 'ksu', 'record'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.kernel, args.ksu, args.record), indent=2))
