#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the CE hand-off: legacy HAT removed, user/secret/mode and failure path retained."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import s10_ce_storage as ce
from s10_ce_storage import CALL,OWNER,REPLACEMENT,patch_smali
from s10_dex_integrity import validate_dex

FIXTURE=f'''.class public final {OWNER}
.super Ljava/lang/Object;
.method public final unlockCeStorage(I[B[B)V
    .locals 2
    const/4 v0, 0x0
    const/4 v1, 0x0
    const-string p2, "legacy-hat"
{CALL}
    return-void
.end method
'''


class TokenlessCE(unittest.TestCase):
    def test_only_hat_argument_changes_and_result_is_valid_dex(self):
        changed=patch_smali(FIXTURE)
        self.assertEqual(changed.replace(REPLACEMENT,CALL),FIXTURE)
        self.assertIn('const-string p2, "!"\n\n'+CALL,changed)
        self.assertNotIn("move-object p3",changed)
        self.assertNotIn(".catch",changed)
        with tempfile.TemporaryDirectory(prefix="s10-ce-smali-") as temporary:
            folder=Path(temporary);(folder/"Storage.smali").write_text(changed)
            subprocess.run(["smali","a","-a","28","-o",str(folder/"classes.dex"),str(folder)],check=True,capture_output=True)
            self.assertEqual(validate_dex((folder/"classes.dex").read_bytes()),b"039")

    def test_real_rewrite_preserves_valid_dex_and_other_entries(self):
        with tempfile.TemporaryDirectory(prefix="s10-ce-repack-") as temporary:
            root = Path(temporary)
            smali = root / "smali"; smali.mkdir()
            (smali / "Storage.smali").write_text(FIXTURE)
            dex = root / "classes.dex"
            subprocess.run(["smali", "a", "-a", "28", "-o", str(dex), str(smali)], check=True)
            system = root / "system"; (system / "framework").mkdir(parents=True); (system / "bin").mkdir()
            services = system / "framework/services.jar"; vold = system / "bin/vold"
            vold.write_bytes(b"fixture")
            with zipfile.ZipFile(services, "w") as archive:
                archive.writestr("classes.dex", dex.read_bytes())
                archive.writestr("unrelated", b"unchanged")
            with patch.object(ce, "SERVICES_SHA256", ce.digest(services)), patch.object(ce, "VOLD_SHA256", ce.digest(vold)):
                ce.configure_tokenless_ce(system)
            with zipfile.ZipFile(services) as archive:
                self.assertEqual(validate_dex(archive.read("classes.dex")), b"039")
                self.assertEqual(archive.read("unrelated"), b"unchanged")
                dex.write_bytes(archive.read("classes.dex"))
            rebuilt = root / "rebuilt"
            subprocess.run(["baksmali", "d", str(dex), "-o", str(rebuilt)], check=True)
            text = (rebuilt / (OWNER[1:-1] + ".smali")).read_text()
            self.assertIn('const-string p2, "!"', text)
            self.assertIn(CALL.strip(), text)

    def test_duplicate_unknown_and_relocated_calls_are_rejected(self):
        for source in [patch_smali(FIXTURE),FIXTURE.replace(CALL,CALL+"\n"+CALL),FIXTURE.replace("unlockCeStorage(I[B[B)V","other(I[B[B)V"),FIXTURE.replace(OWNER,"Lother/Storage;"),FIXTURE.replace(CALL,"")]:
            with self.subTest(source=source),self.assertRaises(ValueError):patch_smali(source)


if __name__=="__main__":unittest.main()
