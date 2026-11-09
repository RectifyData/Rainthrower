#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble immutable per-model public-test payloads, launchers and honest acceptance metadata."""
import argparse
import json
import shutil
from pathlib import Path
import re

from s10_models import ROOT, digest


def assemble(version, output, records, acceptance):
    if (not re.fullmatch(r"v[0-9]+-(?:test|beta)", version) or output.exists() or not output.parent.is_dir()
            or not records.is_dir() or records.resolve().is_relative_to(ROOT) or not acceptance.is_file()):
        raise ValueError("A versioned release output, existing parent and external acceptance record are required")
    tag = version.removesuffix("-test")
    channel = "beta" if version.endswith("-beta") else "public-test"
    device = json.loads(acceptance.read_text())
    required_checks = ("boot", "camera", "fingerprint", "display", "radio", "galaxy_store")
    if (device.get("version") != version or device.get("model") != "SM-G973F"
            or device.get("device_tested") is not True or not isinstance(device.get("checks"), dict)
            or any(device["checks"].get(name) is not True for name in required_checks)):
        raise ValueError("Matching versioned physical S10 acceptance with all required checks is required")
    profiles = json.loads((ROOT / "scripts/oneui9_runtime/display/profiles.json").read_text())["models"]
    rows = []
    for model in ("SM-G970F", "SM-G973F", "SM-G975F"):
        model_records = records / model / tag
        package = json.loads((model_records / "package.json").read_text())
        bootstrap = json.loads((model_records / "bootstrap-package.json").read_text())
        if package["model"] != model or bootstrap["model"] != model or package["version"] != version or bootstrap["version"] != version:
            raise ValueError("Package and launcher model/version do not match")
        if model == "SM-G973F" and (device.get("payload_sha256") != package.get("zip_sha256")
                or device.get("launcher_sha256") != bootstrap.get("launcher_sha256")):
            raise ValueError("Physical acceptance must identify the exact payload and installer")
        chunks = package["packed_image_chunks_verified"]
        if (type(chunks) is not int or chunks < 6 or package["clean_install_required"] is not True
                or package["repartition"] is not False):
            raise ValueError("Image verification or clean-install metadata is invalid")
        if (bootstrap["payload_sha256"] != package["zip_sha256"] or bootstrap["legacy_ufs_alias_supported"] is not True
                or bootstrap["recovery_shell"] != "busybox-sh" or bootstrap["original_installer_checks_preserved"] is not True):
            raise ValueError("Launcher verification metadata is invalid")
        files = []
        for name, size, sha in ((package["zip_file"], package["zip_bytes"], package["zip_sha256"]),
                                (bootstrap["launcher_file"], bootstrap["launcher_bytes"], bootstrap["launcher_sha256"])):
            source = ROOT / "out" / name
            if (not source.is_file() or source.stat().st_size != size
                    or source.with_suffix(source.suffix + ".sha256").read_text().split()[0] != sha
                    or digest(source) != sha):
                raise ValueError("Release payload size or checksum mismatch: " + name)
            files.append({"name": name, "bytes": size, "sha256": sha})
        rows.append({"model": model, "version": version, "rom": package["rom"], "files": files, "display_profile": profiles[model],
                     "host_image_and_chunk_gates": "passed", "physical_acceptance": device if model == "SM-G973F" else {
                         "model": model, "device_tested": False, "status": "pending physical boot/camera/fingerprint/panel/radio testing"}})
    text = (ROOT / "scripts/oneui9_installer/RELEASE_TR.md").read_text().replace("{{VERSION}}", version)
    output.mkdir()
    checksums = []
    for row in rows:
        folder = output / row["model"]
        folder.mkdir()
        for item in row["files"]:
            source = ROOT / "out" / item["name"]
            target = folder / item["name"]
            shutil.copyfile(source, target)
            if target.stat().st_size != item["bytes"] or digest(target) != item["sha256"]:
                raise RuntimeError("Copied release payload failed verification")
            checksum = item["sha256"] + "  " + item["name"] + "\n"
            target.with_suffix(target.suffix + ".sha256").write_text(checksum)
            checksums.append(item["sha256"] + "  " + row["model"] + "/" + item["name"])
        (folder / "SHA256SUMS.txt").write_text("".join(item["sha256"] + "  " + item["name"] + "\n" for item in row["files"]))
        (folder / "VALIDATION.json").write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n")
        launcher = next(item["name"] for item in row["files"] if "_INSTALLER_" in item["name"])
        payload = next(item["name"] for item in row["files"] if item["name"].endswith("_TWRP.zip"))
        preface = "# Bu klasör yalnızca " + row["model"] + " içindir\n\n"
        preface += "Aynı klasörde bulunması gereken dosyalar:\n- `" + payload + "`\n- `" + launcher + "`\n\n"
        preface += "TWRP'de küçük `" + launcher + "` dosyasını seçin.\n\n"
        (folder / "KURULUM_TR.md").write_text(preface + text)
    (output / "KURULUM_TR.md").write_text(text)
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n")
    (output / "RELEASE.json").write_text(json.dumps({"rom": rows[0]["rom"], "version": version, "channel": channel, "models": rows,
         "selinux_enforcing_accepted": False, "unverified_models_not_claimed_tested": True}, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"directory": output.name, "model_count": len(rows), "zip_count": 6, "channel": channel}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    for name in ("output", "records", "acceptance"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    assemble(args.version, args.output, args.records, args.acceptance)
