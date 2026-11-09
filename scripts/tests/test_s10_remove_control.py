#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check fail-closed removal of the native control bundle on host fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_s10_family_candidate as candidate
from prepare_s10_family_candidate import validate_clean_control_inputs, restore_clean_control_inputs, restore_missing_apps


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RemoveControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.system, self.pristine = root / 'system', root / 'pristine'
        self.system.mkdir()
        self.pristine.mkdir()
        self.bundle = {'inputs': {}}
        self.baseline = {'outputs': {}}
        for label in ('framework', 'services', 'systemui', 'home', 'settings'):
            ext = '.jar' if label in ('framework', 'services') else '.apk'
            relative = 'components/' + label + ext
            old, current = ('original-' + label).encode(), ('patched-' + label).encode()
            for folder, content in ((self.pristine, old), (self.system, current)):
                path = folder / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            self.bundle['inputs'][label] = {'path': relative, 'sha256': sha(old)}
            self.baseline['outputs'][label + ext] = sha(current)
        for relative in ('framework/artisan-control-broker.jar', 'etc/init/artisan-control.rc'):
            target = self.system / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'control')

    def validate(self):
        validate_clean_control_inputs(self.system, self.pristine, self.baseline, self.bundle)

    def test_exact_inputs_restore_and_remove_broker(self):
        self.validate()
        target = self.system / self.bundle['inputs']['settings']['path']
        target.chmod(0o640)
        restore_clean_control_inputs(self.system, self.pristine, self.bundle)
        for spec in self.bundle['inputs'].values():
            self.assertEqual((self.system / spec['path']).read_bytes(), (self.pristine / spec['path']).read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.system / 'framework/artisan-control-broker.jar').exists())
        self.assertFalse((self.system / 'etc/init/artisan-control.rc').exists())

    def test_wrong_original_or_current_rejected_before_mutation(self):
        spec = self.bundle['inputs']['settings']
        for folder in (self.pristine, self.system):
            target = folder / spec['path']
            saved = target.read_bytes()
            target.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                self.validate()
            target.write_bytes(saved)
        self.validate()

    def test_missing_or_unsafe_inventory_rejected(self):
        del self.bundle['inputs']['home']
        with self.assertRaises(ValueError):
            self.validate()

    def test_restore_missing_whole_app_without_overwriting_existing(self):
        donor = self.pristine / 'app'
        target = self.system / 'app'
        for root in (donor, target):
            root.mkdir()
        for name in ('Missing', 'Adapted'):
            (donor / name).mkdir()
            (donor / name / (name + '.apk')).write_bytes(('donor-' + name).encode())
        (donor / 'Missing' / 'dependency.so').write_bytes(b'dependency')
        (target / 'Adapted').mkdir()
        (target / 'Adapted' / 'Adapted.apk').write_bytes(b'port adaptation')
        result = restore_missing_apps(self.system, self.pristine)
        self.assertEqual((target / 'Missing' / 'dependency.so').read_bytes(), b'dependency')
        self.assertEqual((target / 'Adapted' / 'Adapted.apk').read_bytes(), b'port adaptation')
        self.assertEqual(result['files'], {'app/Missing/Missing.apk': sha(b'donor-Missing')})
        self.assertFalse(result['compatibility_device_tested'])

    def test_partial_donor_split_set_fails_closed(self):
        donor = self.pristine / 'app/Split'
        target = self.system / 'app/Split'
        donor.mkdir(parents=True); target.mkdir(parents=True)
        (donor / 'base.apk').write_bytes(b'base')
        (donor / 'split.apk').write_bytes(b'split')
        (target / 'base.apk').write_bytes(b'adapted')
        with self.assertRaisesRegex(ValueError, 'missing donor APKs'):
            restore_missing_apps(self.system, self.pristine)
        self.assertFalse((target / 'split.apk').exists())

    def test_restore_missing_split_only_for_unchanged_donor_set(self):
        donor = self.pristine / 'app/Split'
        target = self.system / 'app/Split'
        donor.mkdir(parents=True); target.mkdir(parents=True)
        (donor / 'base.apk').write_bytes(b'base')
        (donor / 'split.apk').write_bytes(b'split')
        (target / 'base.apk').write_bytes(b'base')
        result = restore_missing_apps(self.system, self.pristine)
        self.assertEqual((target / 'split.apk').read_bytes(), b'split')
        self.assertEqual(result['files']['app/Split/split.apk'], sha(b'split'))
        self.assertEqual((target / 'base.apk').read_bytes(), b'base')

    def test_extra_or_symlinked_split_is_not_mixed(self):
        donor = self.pristine / 'app/Split'
        target = self.system / 'app/Split'
        donor.mkdir(parents=True); target.mkdir(parents=True)
        (donor / 'base.apk').write_bytes(b'base')
        (donor / 'split.apk').write_bytes(b'split')
        (target / 'base.apk').write_bytes(b'base')
        (target / 'extra.apk').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'missing donor APKs'):
            restore_missing_apps(self.system, self.pristine)
        (target / 'extra.apk').unlink()
        (target / 'base.apk').unlink(); (target / 'base.apk').symlink_to(donor / 'base.apk')
        with self.assertRaisesRegex(ValueError, 'missing donor APKs'):
            restore_missing_apps(self.system, self.pristine)
        self.assertFalse((target / 'split.apk').exists())

    def test_only_restored_privileged_packages_receive_donor_declarations(self):
        import xml.etree.ElementTree as ET
        for folder in (self.pristine, self.system):
            (folder / 'etc/permissions').mkdir(parents=True)
        (self.pristine / 'etc/permissions/donor.xml').write_text('''<permissions>
            <privapp-permissions package="com.example.restored"><permission name="android.permission.MODIFY_PHONE_STATE"/></privapp-permissions>
            <privapp-permissions package="com.example.unrelated"><permission name="android.permission.READ_PRIVILEGED_PHONE_STATE"/></privapp-permissions>
            </permissions>''')
        restored = {'priv-app/Restored/Restored.apk': 'checksum', 'app/Ordinary/Ordinary.apk': 'checksum'}
        with patch.object(candidate.subprocess, 'check_output', return_value="package: name='com.example.restored' versionCode='1'\n") as aapt, \
             patch.object(candidate.os, 'chown'), patch.object(candidate.os, 'getxattr', return_value=b'label'), \
             patch.object(candidate.os, 'setxattr'):
            result = candidate.restore_privapp_permissions(self.system, self.pristine, restored, Path('/fake/aapt2'))
        self.assertEqual(aapt.call_count, 1)
        path = self.system / 'etc/permissions/privapp-permissions-s10-restored.xml'
        nodes = ET.parse(path).getroot().findall('privapp-permissions')
        self.assertEqual([node.get('package') for node in nodes], ['com.example.restored'])
        self.assertEqual(nodes[0][0].get('name'), 'android.permission.MODIFY_PHONE_STATE')
        self.assertEqual(result, {'etc/permissions/privapp-permissions-s10-restored.xml': sha(path.read_bytes())})

    def test_privileged_apk_with_unreadable_package_is_rejected(self):
        with patch.object(candidate.subprocess, 'check_output', return_value='bad output'):
            with self.assertRaises(ValueError):
                candidate.restore_privapp_permissions(self.system, self.pristine,
                    {'priv-app/Restored/Restored.apk': 'checksum'}, Path('/fake/aapt2'))

    def test_donor_originals_are_used_without_separate_pristine_input(self):
        donor = self.pristine.parent / 'donor'
        (donor / 'images').mkdir(parents=True)
        (donor / 'images/system.img').write_bytes(b'image')
        (donor / 'input.json').write_text(json.dumps({'kind': 'verified-oneui9-donor',
            'build': 'S942BXXU4ZZH6', 'archive_sha256': candidate.DONOR_ARCHIVE,
            'images': {'system.img': {'sha256': sha(b'image')}}}))
        mount = self.pristine.parent / 'mount'
        mount.mkdir()
        (mount / 'system').symlink_to(self.pristine, target_is_directory=True)
        class FixtureMount:
            def __enter__(self): return str(mount)
            def __exit__(self, *args): pass
        calls = []
        with patch.object(candidate.tempfile, 'TemporaryDirectory', return_value=FixtureMount()), \
             patch.object(candidate.subprocess, 'run', side_effect=lambda cmd, **kw: calls.append(cmd)), \
             patch('s10_ce_storage.configure_tokenless_ce', return_value={'services_sha256': 'fixture'}):
            ce, apps = candidate.restore_donor_content(self.system, donor, None, self.baseline, self.bundle)
        self.assertEqual(ce['services_sha256'], 'fixture')
        self.assertEqual(apps['files'], {})
        self.assertEqual(calls[0][0], 'mount')
        self.assertEqual(calls[-1][0], 'umount')
        self.assertEqual((self.system / self.bundle['inputs']['settings']['path']).read_bytes(), b'original-settings')

    def test_linked_destination_root_is_rejected(self):
        (self.pristine / 'app').mkdir()
        (self.system / 'app').symlink_to(self.pristine / 'app', target_is_directory=True)
        with self.assertRaises(ValueError):
            restore_missing_apps(self.system, self.pristine)


if __name__ == '__main__':
    unittest.main()
