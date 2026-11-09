#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove a mismatched raw write halts installation before subsequent partitions."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
source = Path(__file__).resolve().parents[1] / "install_oneui9_s10.py"
spec = importlib.util.spec_from_file_location("s10_installer", source)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ReadbackGateTest(unittest.TestCase):
    def test_durable_state_replaces_atomically_and_fsyncs_file_and_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            records = Path(folder)
            calls = []
            real_fsync = installer.os.fsync
            def sync(fd):
                calls.append(fd)
                real_fsync(fd)
            with patch.object(installer.os, "fsync", side_effect=sync):
                installer.save_state(records, {"writes_started": True})
            self.assertEqual(len(calls), 2)
            self.assertIn('"writes_started": true', (records / "state.json").read_text())
            self.assertEqual(list(records.iterdir()), [records / "state.json"])

    def test_failed_fsync_preserves_previous_state_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            records = Path(folder)
            (records / "state.json").write_text('old')
            with patch.object(installer.os, "fsync", side_effect=OSError("sync failure")):
                with self.assertRaises(OSError):
                    installer.save_state(records, {"writes_started": True})
            self.assertEqual((records / "state.json").read_text(), 'old')
            self.assertEqual(list(records.iterdir()), [records / "state.json"])

    def test_mismatch_halts_before_boot_write(self):
        commands, verified = [], []
        installation = [{"partition": "system", "size": 4096, "sha256": "a" * 64},
                        {"partition": "boot", "size": 4096, "sha256": "b" * 64}]

        def shell(command, timeout):
            commands.append(command)
            return "c" * 64 + "  -" if command.startswith("head -c") else "write complete"

        with self.assertRaisesRegex(RuntimeError, "readback checksum mismatch: system"):
            installer.write_verified_images(installation, "/data/install", shell, lambda entry, output: verified.append(entry), lambda: None)
        self.assertFalse(verified)
        self.assertFalse(any("/by-name/boot" in command for command in commands))

    def test_failed_first_write_cannot_resume_as_a_prewrite_attempt(self):
        state = {"status": "failed", "writes_started": False, "writes": [], "data_formatted": False}
        self.assertTrue(installer.can_resume_uploaded(state))
        saved = []
        def begin_write():
            state["writes_started"] = True
            saved.append(state.copy())
        def failed_write(command, timeout):
            self.assertTrue(saved[-1]["writes_started"])
            raise RuntimeError("write interrupted")
        with self.assertRaisesRegex(RuntimeError, "write interrupted"):
            installer.write_verified_images([{"partition": "system"}], "/data/install", failed_write, lambda *_: None, begin_write)
        self.assertFalse(installer.can_resume_uploaded(state))
        del state["writes_started"]
        self.assertFalse(installer.can_resume_uploaded(state))  # Older records are ambiguous.

    def test_marker_persistence_failure_prevents_partition_write(self):
        commands = []
        def begin_write():
            raise OSError("cannot save start marker")
        with self.assertRaisesRegex(OSError, "start marker"):
            installer.write_verified_images([{"partition": "system"}], "/data/install",
                                            lambda command, timeout: commands.append(command),
                                            lambda *_: None, begin_write)
        self.assertEqual(commands, [])

    def test_matching_readback_records_each_completed_write(self):
        verified, commands = [], []
        installation = [{"partition": name, "size": 4096, "sha256": value * 64,
                         "device": "/dev/block/platform/13d60000.ufs/by-name/" + name}
                        for name, value in (("system", "a"), ("boot", "b"))]

        def shell(command, timeout):
            commands.append(command)
            if command.startswith("head -c"):
                return ("a" if "/by-name/system" in command else "b") * 64 + "  -"
            return "write complete"

        installer.write_verified_images(installation, "/data/install", shell, lambda entry, output: verified.append(entry), lambda: None)
        self.assertEqual([entry["partition"] for entry in verified], ["system", "boot"])
        self.assertTrue(all(entry["readback_verified"] for entry in verified))
        self.assertTrue(all("/dev/block/platform/13d60000.ufs/by-name/" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
