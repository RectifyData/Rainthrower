#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive model ROM trees from verified stock vendor and the complete native V25 port."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from prepare_oneui9_s10_candidate import set_property
from s10_boot_performance import configure_boot_performance
from s10_models import MODELS, ROOT, digest, verify_family_stock

BASE_SYSTEM = "a8990177b2d5c578561326b71b53252797265262503f8058848eddd437dfacbf"
BASE_VENDOR = "c21d814cd17eadce60085d4381cb8a644d86718749dbca5ad76dcdc69c58772b"


def signature(path):
    if path.is_symlink():
        return ("link", os.readlink(path))
    if path.is_file():
        return ("file", digest(path))
    if path.is_dir():
        return ("directory",)
    return None


def vendor_delta(original, baseline, model_vendor):
    """Only transfer a shared patch when its complete original model input matches."""
    rows = []
    paths = {p.relative_to(root) for root in (original, baseline) for p in root.rglob("*")}
    for relative in sorted(paths):
        before, after = signature(original / relative), signature(baseline / relative)
        if before == after:
            continue
        if signature(model_vendor / relative) != before or after is None:
            raise ValueError("Shared vendor compatibility patch conflicts with model hardware: " + str(relative))
        rows.append(str(relative))
    if len(rows) != 40:
        raise ValueError("The registered V25 vendor compatibility inventory changed")
    return rows


def apply_features(target, stock, model):
    before = target.read_text()
    reference = stock.read_text()
    source = {node.tag: node.text or "" for node in ET.fromstring(reference)}
    selected = {key: value for key, value in source.items() if key.startswith(("SEC_FLOATING_FEATURE_CAMERA_", "SEC_FLOATING_FEATURE_LCD_"))}
    for key in ("SEC_FLOATING_FEATURE_LOCKSCREEN_CONFIG_PUNCHHOLE_VI", "SEC_FLOATING_FEATURE_COMMON_CONFIG_EDGE"):
        if key in source:
            selected[key] = source[key]
    # Disable donor-only variable refresh/fullscreen AOD on the original 60 Hz panels.
    selected.update({"SEC_FLOATING_FEATURE_LCD_CONFIG_AOD_FULLSCREEN": "0", "SEC_FLOATING_FEATURE_LCD_CONFIG_AOD_BRIGHTNESS_ANIMATION": "0",
                     "SEC_FLOATING_FEATURE_BIOAUTH_CONFIG_FINGERPRINT_FEATURES": "capacitive_side" if model == "SM-G970F" else "ultrasonic_display_phone"})
    root = ET.fromstring(before)
    for node in list(root):
        if node.tag.startswith(("SEC_FLOATING_FEATURE_CAMERA_", "SEC_FLOATING_FEATURE_LCD_")) or node.tag in selected:
            root.remove(node)
    for key, value in selected.items():
        ET.SubElement(root, key).text = value
    target.write_text(ET.tostring(root, encoding="unicode") + "\n")
    return selected


def camera_preflight(system, stock, native_manifest):
    old_apk = system / "priv-app/SamsungCamera/SamsungCamera.apk"
    model_apk = stock / "priv-app/SamsungCamera/SamsungCamera.apk"
    with zipfile.ZipFile(old_apk) as old, zipfile.ZipFile(model_apk) as model:
        if old.namelist() != model.namelist():
            raise ValueError("Camera APK member inventory differs")
        for name in old.namelist():
            if not name.startswith(("META-INF/", "SEC-INF/")) and old.read(name) != model.read(name):
                raise ValueError("The model Camera code/resources require a separate adapter: " + name)
        for name, expected in native_manifest["files"].items():
            if not name.startswith("lib/"):
                continue
            leaf = Path(name).name
            patch = native_manifest["elf_changes"].get("libion.so" if leaf == "libs10.so" else leaf)
            source_name = "libion.so" if leaf == "libs10.so" else leaf
            entry = "lib/arm64-v8a/" + source_name
            if entry in model.namelist():
                checksum = hashlib.sha256(model.read(entry)).hexdigest()
            else:
                source = stock / "lib64" / source_name
                if not source.is_file():
                    source = stock.parent.parent / "vendor/lib64" / source_name
                if not source.is_file():
                    raise ValueError("Missing model Camera dependency: " + source_name)
                checksum = digest(source)
            if checksum != (patch["original_sha256"] if patch else expected):
                raise ValueError("The app-private Camera native adapter does not match model input: " + source_name)
            if digest(system / "priv-app/SamsungCamera" / name) != expected:
                raise ValueError("The baseline Camera native library changed: " + name)


def validate_clean_control_inputs(system, pristine, baseline_control, bundle):
    """Require both sides of the exact native-control transformation before rollback."""
    inputs = bundle.get("inputs", {})
    if set(inputs) != {"framework", "services", "systemui", "home", "settings"}:
        raise ValueError("The complete control adapter input inventory is required")
    for label, spec in inputs.items():
        relative = Path(spec["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid original component path")
        old, current = pristine / relative, system / relative
        if (not old.is_file() or old.is_symlink() or not current.is_file() or current.is_symlink()
                or digest(old) != spec["sha256"]
                or digest(current) != baseline_control["outputs"][label + current.suffix]):
            raise ValueError("Cannot safely restore control component: " + label)
    for relative in ("framework/artisan-control-broker.jar", "etc/init/artisan-control.rc"):
        if not (system / relative).is_file() or (system / relative).is_symlink():
            raise ValueError("Missing baseline control component: " + relative)


def restore_clean_control_inputs(system, pristine, bundle):
    for spec in bundle["inputs"].values():
        relative = Path(spec["path"])
        # Write in place so Android ownership, mode and SELinux labels are retained.
        with (system / relative).open("wb") as output, (pristine / relative).open("rb") as source:
            shutil.copyfileobj(source, output)
    for relative in ("framework/artisan-control-broker.jar", "etc/init/artisan-control.rc"):
        (system / relative).unlink()


APP_ROOTS = ("app", "priv-app", "system_ext/app", "system_ext/priv-app", "product/app", "product/priv-app")
DONOR_ARCHIVE = "54c851a0db5a2c810f441ab6336aa49766848a6279b58f353b4cc835fbe3ddcd"


def restore_missing_apps(system, donor_system):
    """Restore absent donor APKs without mixing adapted or unknown split sets."""
    restored = {}
    for root in APP_ROOTS:
        source_root, dest_root = donor_system / root, system / root
        if not source_root.is_dir():
            continue
        if source_root.is_symlink() or not dest_root.is_dir() or dest_root.is_symlink():
            raise ValueError("Unexpected application directory: " + root)
        for source in sorted(source_root.iterdir()):
            if source.is_symlink():
                continue
            apks = ([source] if source.is_file() and source.suffix == ".apk" else
                    sorted(p for p in source.rglob("*.apk") if p.is_file() and not p.is_symlink())
                    if source.is_dir() else [])
            if not apks:
                continue
            target = dest_root / source.name
            if target.exists() or target.is_symlink():
                if target.is_symlink():
                    raise ValueError("Unexpected application symlink: " + str(target))
                if not source.is_dir() or not target.is_dir():
                    continue
                donor_apks = {p.relative_to(source): p for p in apks}
                target_apks = {p.relative_to(target): p for p in target.rglob("*.apk")}
                missing = set(donor_apks) - set(target_apks)
                if not missing:
                    continue
                # Restore splits only when every existing APK is byte-for-byte
                # the donor's and there are no extra splits. Never mix unknown
                # adaptations/signers or follow a symlink into another tree.
                if (not target_apks or set(target_apks) - set(donor_apks)
                        or any(p.is_symlink() or not p.is_file() or digest(p) != digest(donor_apks[name])
                               for name, p in target_apks.items())):
                    raise ValueError("Existing application is missing donor APKs; review the adapted split set: " + str(source.name))
                for name in sorted(missing):
                    destination = target / name
                    parent = target
                    for part in name.parts[:-1]:
                        parent = parent / part
                        if parent.is_symlink() or not parent.is_dir():
                            raise ValueError("Missing split parent requires manual review: " + str(destination))
                    if destination.is_symlink() or destination.exists():
                        raise ValueError("Unexpected split destination: " + str(destination))
                    subprocess.run(["cp", "-a", str(donor_apks[name]), str(destination)], check=True)
                    if digest(destination) != digest(donor_apks[name]):
                        raise ValueError("Restored APK differs from donor: " + str(name))
                    restored[str(destination.relative_to(system))] = digest(donor_apks[name])
                continue
            subprocess.run(["cp", "-a", str(source), str(target)], check=True)
            for apk in apks:
                relative = apk.relative_to(donor_system)
                if digest(system / relative) != digest(apk):
                    raise ValueError("Restored APK differs from donor: " + str(relative))
                restored[str(relative)] = digest(apk)
    return {"files": restored, "existing_app_directories_preserved": True,
            "compatibility_device_tested": False}


def restore_privapp_permissions(system, donor_system, restored, aapt2):
    """Restore only donor permission declarations for APKs we actually restored."""
    packages = {}
    for relative in restored:
        path = Path(relative)
        parts = path.parts
        if parts[0] == "priv-app":
            partition = ""
        elif len(parts) > 1 and parts[0] in ("system_ext", "product") and parts[1] == "priv-app":
            partition = parts[0]
        else:
            continue
        output = subprocess.check_output([str(aapt2), "dump", "badging", str(donor_system / path)], text=True)
        match = re.search(r"^package: name='([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)'", output, re.M)
        if not match:
            raise ValueError("Cannot identify restored privileged APK: " + relative)
        packages.setdefault(partition, set()).add(match.group(1))
    written = {}
    for partition, names in packages.items():
        donor_permissions = donor_system / partition / "etc/permissions"
        destination = system / partition / "etc/permissions/privapp-permissions-s10-restored.xml"
        nodes, reference = [], None
        if not donor_permissions.is_dir() or not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
            raise ValueError("Unexpected privileged-permission directory")
        for source in sorted(donor_permissions.glob("*.xml")):
            root = ET.parse(source).getroot()
            if root.tag != "permissions":
                continue
            selected = [node for node in root.findall("privapp-permissions") if node.get("package") in names]
            if selected:
                reference = reference or source
                nodes.extend(copy.deepcopy(node) for node in selected)
        if not nodes:
            continue
        root = ET.Element("permissions")
        root.extend(nodes)
        with destination.open("xb") as output:
            output.write(ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n")
        os.chown(destination, 0, 0)
        os.chmod(destination, 0o644)
        os.setxattr(destination, "security.selinux", os.getxattr(reference, "security.selinux"))
        written[str(destination.relative_to(system))] = digest(destination)
    return written


def restore_donor_content(system, donor, pristine, baseline_control, bundle):
    """Restore the original control inputs and absent apps from a pinned donor."""
    info = json.loads((donor / "input.json").read_text())
    image = donor / "images/system.img"
    if (info.get("kind") != "verified-oneui9-donor" or info.get("build") != "S942BXXU4ZZH6"
            or info.get("archive_sha256") != DONOR_ARCHIVE or digest(image) != info["images"]["system.img"]["sha256"]):
        raise ValueError("The verified pinned One UI 9 donor image is required")
    with tempfile.TemporaryDirectory(prefix="s10-donor-apps-") as mount:
        subprocess.run(["mount", "-t", "ext4", "-o", "loop,ro,noload", str(image), mount], check=True)
        try:
            donor_system = Path(mount) / "system"
            originals = pristine if pristine is not None else donor_system
            validate_clean_control_inputs(system, originals, baseline_control, bundle)
            restore_clean_control_inputs(system, originals, bundle)
            from s10_ce_storage import configure_tokenless_ce
            ce = configure_tokenless_ce(system)
            apps = restore_missing_apps(system, donor_system)
            aapt2 = ROOT / "out/host-tools/android-sdk-36/build-tools/android-16/aapt2"
            permissions = restore_privapp_permissions(system, donor_system, apps["files"], aapt2)
            apps["restored_privileged_permissions"] = permissions
            apps["files"].update(permissions)
            return ce, apps
        finally:
            subprocess.run(["umount", mount], check=True)


def prepare(args):
    resume = args.resume_copy and args.output.is_dir() and {p.name for p in args.output.iterdir()} == {"tree"}
    if (os.geteuid() != 0 or (args.output.exists() and not resume) or not args.output.parent.is_dir()
            or not args.records.is_dir() or args.records.resolve().is_relative_to(ROOT)):
        raise ValueError("Linux root, new candidate output and existing external records are required")
    metadata = json.loads((args.baseline / "input.json").read_text())
    if metadata.get("model") != "SM-G973F" or digest(args.baseline / "system.img") != BASE_SYSTEM or digest(args.baseline / "vendor.img") != BASE_VENDOR:
        raise ValueError("The complete original-layout V25 baseline is required")
    kernel = json.loads((args.kernel / "input.json").read_text())
    if args.model == "SM-G973F":
        stock = None
        if digest(args.boot) != metadata["s10_boot_performance"]["kernel_sha256"]:
            raise ValueError("The working G973F full boot pin must be retained")
        patches = []
    else:
        stock = verify_family_stock(args.stock, args.model)
        if kernel.get("model") != args.model or kernel.get("device") != MODELS[args.model]["device"]:
            raise ValueError("A separately built kernel and DTBO for this model are required")
        for name, spec in kernel["images"].items():
            if digest(args.kernel / name) != spec["sha256"]:
                raise ValueError("The model kernel image changed: " + name)
        if args.boot.resolve() != (args.kernel / "boot.img").resolve():
            raise ValueError("Use the model's own full boot image")
        patches = vendor_delta(args.reference_stock / "vendor", args.baseline / "vendor", args.stock / "vendor")
        native = json.loads((args.camera_native / "input.json").read_text())
        camera_preflight(args.baseline / "tree/system", args.stock / "tree/system", native)
    bundle = json.loads((args.bundle / "manifest.json").read_text())
    if args.model == "SM-G970F" and bundle.get("model_adaptation", {}).get("model") != args.model:
        raise ValueError("The S10e side-fingerprint native adapter is required")
    if args.model != "SM-G970F" and bundle.get("model_adaptation"):
        raise ValueError("Side-sensor firmware cannot be installed on an in-display sensor model")
    for name, expected in bundle["outputs"].items():
        if Path(name).name != name or digest(args.bundle / name) != expected:
            raise ValueError("Artisan bundle hash mismatch")
    if args.model == "SM-G975F":
        if not args.donor:
            raise ValueError("S10+ requires the pinned donor to restore apps and remove YuROM Settings")
        if args.pristine_system:
            validate_clean_control_inputs(args.baseline / "tree/system", args.pristine_system,
                                          metadata["artisan_control"], bundle)
    args.output.mkdir(exist_ok=resume)
    for name, source in (("tree", args.baseline / "tree"), ("vendor", (args.stock / "vendor") if stock else args.baseline / "vendor")):
        destination = args.output / name
        destination.mkdir(exist_ok=resume)
        subprocess.run(["cp", "-a", "--reflink=auto", str(source) + "/.", str(destination)], check=True)
    system, vendor = args.output / "tree/system", args.output / "vendor"
    if stock:
        for name in patches:
            source, target = args.baseline / "vendor" / name, vendor / name
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copystat(source, target)
                os.chown(target, source.stat().st_uid, source.stat().st_gid)
            else:
                subprocess.run(["cp", "-a", str(source), str(target)], check=True)
        for name in ("etc/init/02-s10-performance.rc", "etc/s10-performance-profile.sh"):
            (vendor / name).unlink()
        metadata["s10_boot_performance"] = configure_boot_performance(vendor, model=args.model, kernel=kernel)
        model_system = args.stock / "tree/system"
        for partition, file in (("system", system / "build.prop"), ("system_ext", system / "system_ext/etc/build.prop"), ("product", system / "product/etc/build.prop")):
            for field, value, expected in (("model", args.model, "SM-G973F"), ("device", MODELS[args.model]["device"].removesuffix("lte"), "beyond1"),
                                           ("name", MODELS[args.model]["device"] + "xx", "beyond1ltexx")):
                set_property(file, "ro.product." + partition + "." + field, value, expected)
        metadata["model_hardware_features"] = apply_features(system / "etc/floating_feature.xml", model_system / "etc/floating_feature.xml", args.model)
        for name in metadata["copied_stock_inputs"]:
            source, target = model_system / name, system / name
            target.write_bytes(source.read_bytes())
            metadata["copied_stock_inputs"][name] = digest(target)
        # Keep the original model APK signed and use the preflight-proven common native adapter.
        camera = system / "priv-app/SamsungCamera/SamsungCamera.apk"
        camera.write_bytes((model_system / "priv-app/SamsungCamera/SamsungCamera.apk").read_bytes())
        shutil.rmtree(system / "cameradata")
        subprocess.run(["cp", "-a", str(model_system / "cameradata"), str(system / "cameradata")], check=True)
        metadata["samsung_camera_integration"].update({"source_build": stock["build"], "apk_sha256": digest(camera)})
        metadata["samsung_camera_integration"]["camera_features"] = {key: value for key, value in metadata["model_hardware_features"].items() if key.startswith("SEC_FLOATING_FEATURE_CAMERA_")}
        metadata["samsung_camera_integration"]["files"] = {str(p.relative_to(system)): digest(p) for directory in (system / "cameradata", system / "priv-app/SamsungCamera") for p in directory.rglob("*") if p.is_file()}
        metadata["samsung_camera_integration"]["files"]["etc/floating_feature.xml"] = digest(system / "etc/floating_feature.xml")
        metadata["model_vendor_patch_files"] = patches
        metadata["stock_build"] = stock["build"]
        metadata["stock_input_archive_sha256"] = stock["archive_sha256"]
        metadata["kernel"] = kernel
    else:
        for name in ("etc/init/02-s10-performance.rc", "etc/s10-performance-profile.sh"):
            (vendor / name).unlink()
        metadata["s10_boot_performance"] = configure_boot_performance(vendor)
    if args.model == "SM-G975F":
        ce, apps = restore_donor_content(system, args.donor, args.pristine_system,
                                         metadata["artisan_control"], bundle)
        metadata["s10_ce_storage_compatibility"] = ce
        metadata["ce_vold_sha256"] = metadata["s10_ce_storage_compatibility"]["vold_sha256"]
        metadata["artisan_control"] = None
        metadata["yurom_settings_removed"] = True
        metadata["s10_settings_branding"].update({"settings_sha256": digest(system / "priv-app/SecSettings/SecSettings.apk"),
                                                   "settings_apk_unmodified": True})
        metadata["donor_app_restoration"] = apps
        from s10_qs_sync_permission import configure as configure_qs_sync
        metadata["s10_qs_sync_permission"] = configure_qs_sync(system, bundle["inputs"]["systemui"]["sha256"])
        from s10_qs_mobile_data import configure as configure_qs_mobile
        sync_patch = metadata["s10_qs_sync_permission"]
        metadata["s10_qs_mobile_data"] = configure_qs_mobile(system, sync_patch["files"][bundle["inputs"]["systemui"]["path"]])
        # Both integrations refer to the final APK after the second patch.
        sync_patch["files"] = dict(metadata["s10_qs_mobile_data"]["files"])
    else:
        for label, spec in bundle["inputs"].items():
            target = system / spec["path"]
            if digest(target) != metadata["artisan_control"]["outputs"][label + target.suffix]:
                raise ValueError("The baseline native Artisan target changed: " + label)
            target.write_bytes((args.bundle / (label + target.suffix)).read_bytes())
        (system / "framework/artisan-control-broker.jar").write_bytes((args.bundle / "ArtisanControlBroker.jar").read_bytes())
        metadata["artisan_control"].update({"outputs": bundle["outputs"], "features": bundle["features"]})
        metadata["s10_settings_branding"]["settings_sha256"] = bundle["outputs"]["settings.apk"]
    if args.pde_bundle:
        from s10_personal_data import configure as configure_pde
        metadata["s10_personal_data_compatibility"] = configure_pde(system, args.pde_bundle)
        metadata["samsung_camera_integration"]["files"]["etc/floating_feature.xml"] = digest(system / "etc/floating_feature.xml")
    if args.model != "SM-G975F":
        from s10_telemetry import configure as configure_telemetry
        metadata["telemetry"] = configure_telemetry(system, args.version, args.telemetry_endpoint)
    else:
        metadata.pop("telemetry", None)
    from s10_display_defaults import configure as configure_display_defaults
    display_defaults = configure_display_defaults(ROOT, system, args.model)
    if display_defaults is not None:
        metadata["s10_display_defaults"] = display_defaults
    if args.model != "SM-G975F" and "brand_derivation" in bundle:
        from s10_yurom_branding import configure as configure_yurom_branding
        metadata["yurom_branding"] = configure_yurom_branding(system, args.model, ROOT / "out/host-tools/yurom-settings-signature-v35.apk")
        metadata["s10_settings_branding"]["apk_sha256"] = metadata["yurom_branding"]["signature_sha256"]
        metadata["s10_settings_branding"]["settings_sha256"] = bundle["outputs"]["settings.apk"]
    metadata.update({"model": args.model, "parent_candidate": args.baseline.name, "layout": MODELS[args.model]["device"],
                     "adaptive_performance_control": ({"surface": "native-settings", "local_learning": True, "device_tested": False}
                                                      if args.model != "SM-G975F" else None),
                     "device_boot_tested": False, "ready_for_installation": False, "repartition": False})
    metadata["distribution"].update({"supported_model": args.model, "version": args.version, "final_zip_device_install_tested": False})
    (args.output / "input.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (args.records / "candidate-preparation.json").write_text(json.dumps({"model": args.model, "shared_vendor_patches": patches, "kernel": kernel["images"],
                                                                       "device_writes": False, "physical_acceptance": "pending"}, indent=2) + "\n")
    print("Prepared native " + args.version + " candidate: " + args.model)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--version", default="v28-test")
    parser.add_argument("--telemetry-endpoint", default=None)
    parser.add_argument("--pde-bundle", type=Path)
    parser.add_argument("--pristine-system", type=Path, help="Optional original checksum-matched adapter inputs; otherwise use the donor image")
    parser.add_argument("--donor", type=Path, help="Verified One UI 9 donor image cache; required for SM-G975F")
    parser.add_argument("--resume-copy", action="store_true", help="Resume only an interrupted initial tree copy before vendor creation")
    for name in ("baseline", "reference-stock", "stock", "kernel", "boot", "camera-native", "bundle", "output", "records"):
        parser.add_argument("--" + name, type=Path, required=True)
    prepare(parser.parse_args())
