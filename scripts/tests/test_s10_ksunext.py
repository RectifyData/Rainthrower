#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise source pinning and fail-closed manual KernelSU hook preparation."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_s10_ksunext as prepare
import build_oneui9_s10_kernel as builder


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.DEVNULL).decode().strip()


def repository(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    git(path, 'config', 'user.name', 'Fixture')
    git(path, 'config', 'user.email', 'fixture@example.invalid')


def commit(path):
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'fixture')
    return git(path, 'rev-parse', 'HEAD')


class KernelSUNextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='s10-ksu-fixture-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.kernel, self.ksu = root / 'kernel', root / 'ksu'
        repository(self.kernel); repository(self.ksu)
        (self.kernel / 'drivers').mkdir()
        (self.kernel / 'drivers/Kconfig').write_text('original\n')
        (self.ksu / 'kernel').mkdir()
        (self.ksu / 'kernel/Kconfig').write_text('config KSU\n')
        (self.ksu / 'kernel/Kbuild').write_text('fixture\n')
        kernel_commit, ksu_commit = commit(self.kernel), commit(self.ksu)
        (self.ksu / 'kernel/Kbuild').write_text('fixture-fixed\n')
        self.ksu_patch = root / 'ksu.patch'
        self.ksu_patch.write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=self.ksu))
        git(self.ksu, 'restore', 'kernel/Kbuild')
        (self.kernel / 'drivers/Kconfig').write_text('original\nsource kernelsu\n')
        self.patch = root / 'hooks.patch'
        self.patch.write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=self.kernel))
        git(self.kernel, 'restore', 'drivers/Kconfig')
        self.record = root / 'integration.json'
        for module, name, value in ((prepare, 'PIN', kernel_commit), (builder, 'PIN', kernel_commit),
                                    (prepare, 'KSU_PIN', ksu_commit), (prepare, 'PATCH', self.patch), (prepare, 'KSU_PATCH', self.ksu_patch),
                                    (prepare, 'HOOK_FILES', ('drivers/Kconfig',))):
            handle = patch.object(module, name, value); handle.start(); self.addCleanup(handle.stop)

    def test_preparation_build_validation_and_no_duplicate_hooks(self):
        record = prepare.prepare(self.kernel, self.ksu, self.record)
        self.assertFalse(record['susfs_integrated'])
        self.assertTrue((self.kernel / 'drivers/kernelsu').is_symlink())
        verified, baseline = builder.validate_ksunext_source(self.kernel, self.record, prepare.diff(self.kernel))
        self.assertEqual(baseline, record['before_diff_sha256'])
        self.assertEqual(verified['ksu_commit'], record['ksu_commit'])
        with self.assertRaisesRegex(ValueError, 'already present'):
            prepare.prepare(self.kernel, self.ksu, self.record.parent / 'other.json')

    def test_kernel_or_ksu_changes_are_rejected(self):
        prepare.prepare(self.kernel, self.ksu, self.record)
        config = self.kernel / 'drivers/Kconfig'
        config.write_text(config.read_text() + 'changed\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            builder.validate_ksunext_source(self.kernel, self.record, prepare.diff(self.kernel))
        config.write_text('original\nsource kernelsu\n')
        (self.ksu / 'kernel/Kbuild').write_text('modified\n')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            builder.validate_ksunext_source(self.kernel, self.record, prepare.diff(self.kernel))

    def test_incompatible_existing_hook_is_not_overwritten(self):
        (self.kernel / 'drivers/Kconfig').write_text('ksu_handle_old\n')
        with self.assertRaisesRegex(ValueError, 'Existing KernelSU hooks'):
            prepare.prepare(self.kernel, self.ksu, self.record)
        self.assertFalse((self.kernel / 'drivers/kernelsu').is_symlink())
        self.assertFalse(self.record.exists())


if __name__ == '__main__':
    unittest.main()
