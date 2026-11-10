# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'target/beyond1lte/patches/user_defaults'
KEYS = ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale')
MARKER = 'artisanrom_s10_animation_defaults_version_2'


class UserDefaultsTests(unittest.TestCase):
    def test_animation_migration_and_user_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / 'settings.json'
            db.write_text(json.dumps({key: '1.0' for key in KEYS}))
            command = folder / 'settings'
            command.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ['TEST_SETTINGS_DB']); d=json.loads(p.read_text())
action, namespace, key=sys.argv[1:4]
assert namespace == 'global'
if action == 'get': print(d.get(key, 'null'))
elif key == os.environ.get('TEST_FAIL_KEY'): sys.exit(1)
else:
    d[key]=sys.argv[4]; p.write_text(json.dumps(d))
''')
            command.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ['PATH'],
                       TEST_SETTINGS_DB=str(db))
            script = MODULE / 'system/system/etc/s10-user-defaults.sh'
            # A partial failure must not mark the migration complete.
            failed = subprocess.run(['sh', str(script)], env=dict(env, TEST_FAIL_KEY=KEYS[1]))
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn(MARKER, json.loads(db.read_text()))
            subprocess.run(['sh', str(script)], env=env, check=True)
            data = json.loads(db.read_text())
            self.assertEqual([data[key] for key in KEYS], ['1.0'] * 3)
            self.assertEqual(data[MARKER], '1')
            self.assertNotIn('development_settings_enabled', data)
            data[KEYS[0]] = '1.5'
            db.write_text(json.dumps(data))
            subprocess.run(['sh', str(script)], env=env, check=True)
            self.assertEqual(json.loads(db.read_text()), data)

    def test_compatibility_default_never_overwrites_persisted_bluetooth_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = Path(tmp) / 'system/system/etc/init/hw/init.rc'
            rc.parent.mkdir(parents=True)
            rc.write_text('on post-fs-data\n    load_persist_props\n    start apexd\n')
            env = dict(os.environ, WORK_DIR=tmp)
            command = ['bash', '-c', 'SET_METADATA() { return 0; }; source "$1"',
                       'test', str(MODULE / 'customize.sh')]
            for _ in range(2):
                subprocess.run(command, env=env, check=True)
            self.assertEqual(rc.read_text(), 'on post-fs-data\n    load_persist_props\n    start apexd\n')
            self.assertNotIn('setprop persist.bluetooth', (MODULE / 'system/system/etc/init/s10-user-defaults.rc').read_text())
            self.assertNotIn('load_persist_props', (MODULE / 'customize.sh').read_text())
