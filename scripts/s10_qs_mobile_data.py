#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repair the One UI 9 mobile-data tile's missing-active-repository path."""
import copy
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from build_artisan_control import dex_classes, retain_oem_certificate, signing_block
from s10_models import ROOT, digest
from s10_dex_integrity import validate_dex
from s10_personal_data import semantics
from s10_qs_sync_permission import APK

OWNER = 'Lcom/android/systemui/qs/tiles/impl/cell/domain/interactor/MobileDataTileUserActionInteractor;'
REPOSITORY = 'Lcom/android/systemui/statusbar/pipeline/mobile/data/repository/MobileConnectionsRepository;'
METHOD = 'handleClick(Lcom/android/systemui/animation/Expandable;Lkotlin/coroutines/jvm/internal/SuspendLambda;)Ljava/lang/Object;'
# Fingerprint the complete observed method, not merely a short instruction pattern.
# This also guards all OEM carrier, keyguard and policy-dependent control flow.
METHOD_SHA256 = '33d3d6ffed34273706277ef34c05a6d72afde79c11b20031f7ddfdce04852c66'

ORIGINAL_NULL = '''    if-nez v1, :cond_11

    sget-object v0, Lkotlin/Unit;->INSTANCE:Lkotlin/Unit;

    return-object v0

    :cond_11'''
FALLBACK = f'''    if-nez v1, :cond_11

    # Only fall back when Android still reports a valid, active default data SIM.
    # Continue into the original lock-screen, airplane and carrier handling.
    :rain_qs_try_start
    invoke-static {{}}, Landroid/telephony/SubscriptionManager;->getDefaultDataSubscriptionId()I
    move-result v2
    invoke-static {{v2}}, Landroid/telephony/SubscriptionManager;->isValidSubscriptionId(I)Z
    move-result v3
    if-eqz v3, :rain_qs_unavailable
    iget-object v0, p0, {OWNER}->context:Landroid/content/Context;
    invoke-static {{v0}}, Landroid/telephony/SubscriptionManager;->from(Landroid/content/Context;)Landroid/telephony/SubscriptionManager;
    move-result-object v0
    if-eqz v0, :rain_qs_unavailable
    invoke-virtual {{v0, v2}}, Landroid/telephony/SubscriptionManager;->getActiveSubscriptionInfo(I)Landroid/telephony/SubscriptionInfo;
    move-result-object v0
    if-eqz v0, :rain_qs_unavailable
    iget-object v0, p0, {OWNER}->mobileConnectionsRepository:{REPOSITORY}
    invoke-interface {{v0, v2}}, {REPOSITORY}->getRepoForSubId(I)Lcom/android/systemui/statusbar/pipeline/mobile/data/repository/MobileConnectionRepository;
    move-result-object v1
    :rain_qs_try_end
    .catch Ljava/lang/SecurityException; {{:rain_qs_try_start .. :rain_qs_try_end}} :rain_qs_denied
    if-nez v1, :cond_11
    :rain_qs_unavailable
    sget-object v0, Lkotlin/Unit;->INSTANCE:Lkotlin/Unit;
    return-object v0
    :rain_qs_denied
    move-exception v0
    goto :rain_qs_unavailable

    :cond_11'''
ORIGINAL_STATE = '''    invoke-interface {v1}, Lcom/android/systemui/statusbar/pipeline/mobile/data/repository/MobileConnectionRepository;->getDataEnabled()Lkotlinx/coroutines/flow/StateFlow;

    move-result-object v1

    invoke-interface {v1}, Lkotlinx/coroutines/flow/StateFlow;->getValue()Ljava/lang/Object;

    move-result-object v1

    check-cast v1, Ljava/lang/Boolean;

    invoke-virtual {v1}, Ljava/lang/Boolean;->booleanValue()Z

    move-result v1'''
CURRENT_STATE = '''    # An unscoped read uses the default subscription, which can be different
    # from the default-data subscription on dual-SIM devices. Scope both calls.
    invoke-static {}, Landroid/telephony/SubscriptionManager;->getDefaultDataSubscriptionId()I
    move-result v1
    invoke-static {v1}, Landroid/telephony/SubscriptionManager;->isValidSubscriptionId(I)Z
    move-result v2
    if-eqz v2, :cond_2ed
    invoke-virtual {v0, v1}, Landroid/telephony/TelephonyManager;->createForSubscriptionId(I)Landroid/telephony/TelephonyManager;
    move-result-object v0
    invoke-virtual {v0}, Landroid/telephony/TelephonyManager;->isDataEnabled()Z
    move-result v1'''


def patch_smali(text):
    if not text.startswith('.class ') or OWNER not in text.splitlines()[0]:
        raise ValueError('Unexpected mobile-data interactor class')
    pattern = r'^\.method public final ' + re.escape(METHOD) + r'\n.*?^\.end method$'
    matches = list(re.finditer(pattern, text, re.M | re.S))
    if len(matches) != 1:
        raise ValueError('Expected one mobile-data click handler')
    method = matches[0].group()
    fingerprint = hashlib.sha256(semantics(method).encode()).hexdigest()
    if fingerprint != METHOD_SHA256 or method.count(ORIGINAL_NULL) != 1 or method.count(ORIGINAL_STATE) != 1:
        raise ValueError('Unknown mobile-data handler; a separate adapter is required')
    patched = method.replace(ORIGINAL_NULL, FALLBACK).replace(ORIGINAL_STATE, CURRENT_STATE)
    return text[:matches[0].start()] + patched + text[matches[0].end():]



def configure(system, expected_sha256, tools=None):
    apk = Path(system) / APK
    if apk.is_symlink() or not apk.is_file() or digest(apk) != expected_sha256:
        raise ValueError('SystemUI does not match the checked sync-permission output')
    tools = tools or ROOT / 'out/host-tools/android-sdk-36/build-tools/android-16'
    zipalign = tools / 'zipalign'
    if not zipalign.is_file():
        raise ValueError('Android build-tools zipalign is required for the mobile-data patch')
    with tempfile.TemporaryDirectory(prefix='s10-qs-mobile-') as temporary:
        tmp = Path(temporary)
        with zipfile.ZipFile(apk) as old:
            names = old.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Unexpected SystemUI APK inventory')
            matches = [name for name in names if re.fullmatch(r'classes\d*\.dex', name)
                       and OWNER in dex_classes(old.read(name))]
            if len(matches) != 1:
                raise ValueError('Mobile-data interactor must occur in exactly one DEX')
            entry = matches[0]
            validate_dex(old.read(entry), b'039')
            folder, canonical, rebuilt = tmp / 'smali', tmp / 'canonical', tmp / 'rebuilt'
            subprocess.run(['baksmali', 'd', '--debug-info', 'false', '-l', '-j', '8', '-o', str(folder),
                            str(apk) + '/' + entry], check=True, capture_output=True)
            target = folder / (OWNER[1:-1] + '.smali')
            patched_text = patch_smali(target.read_text())
            # Canonicalise before comparing: reassembly can change DEX indices,
            # jumbo strings and branch offsets even in unchanged classes.
            baseline_dex = tmp / 'baseline.dex'
            subprocess.run(['smali', 'a', '-a', '28', '-j', '8', '-o', str(baseline_dex), str(folder)],
                           check=True, capture_output=True)
            validate_dex(baseline_dex.read_bytes(), b'039')
            subprocess.run(['baksmali', 'd', '--debug-info', 'false', '-l', '-j', '8', '-o', str(canonical),
                            str(baseline_dex)], check=True, capture_output=True)
            target.write_text(patched_text)
            dex = tmp / 'patched.dex'
            subprocess.run(['smali', 'a', '-a', '28', '-j', '8', '-o', str(dex), str(folder)],
                           check=True, capture_output=True)
            validate_dex(dex.read_bytes(), b'039')
            if set(dex_classes(dex.read_bytes())) != set(dex_classes(old.read(entry))):
                raise ValueError('Mobile-data patch changed the class inventory')
            subprocess.run(['baksmali', 'd', '--debug-info', 'false', '-l', '-j', '8', '-o', str(rebuilt),
                            str(dex)], check=True, capture_output=True)
            before = {p.relative_to(canonical) for p in canonical.rglob('*.smali')}
            after = {p.relative_to(rebuilt) for p in rebuilt.rglob('*.smali')}
            if before != after:
                raise ValueError('Rebuilt mobile-data DEX changed the smali inventory')
            owner_path = Path(OWNER[1:-1] + '.smali')
            for path in before - {owner_path}:
                if semantics((canonical / path).read_text()) != semantics((rebuilt / path).read_text()):
                    raise ValueError('Unrelated SystemUI class changed: ' + str(path))
            output_owner = (rebuilt / owner_path).read_text()
            method_pattern = r'^\.method public final ' + re.escape(METHOD) + r'\n.*?^\.end method$'
            original_owner = (canonical / owner_path).read_text()
            if (len(re.findall(method_pattern, original_owner, re.M | re.S)) != 1
                    or len(re.findall(method_pattern, output_owner, re.M | re.S)) != 1
                    or semantics(re.sub(method_pattern, '', original_owner, flags=re.M | re.S))
                    != semantics(re.sub(method_pattern, '', output_owner, flags=re.M | re.S))):
                raise ValueError('Another method in the mobile-data interactor changed')
            output_method = re.search(method_pattern, output_owner, re.M | re.S).group()
            for fragment in ('->getActiveSubscriptionInfo(I)Landroid/telephony/SubscriptionInfo;',
                             '->getRepoForSubId(I)Lcom/android/systemui/statusbar/pipeline/mobile/data/repository/MobileConnectionRepository;',
                             '->createForSubscriptionId(I)Landroid/telephony/TelephonyManager;',
                             '->isDataEnabled()Z', '->setDataEnabled(Z)V'):
                if fragment not in output_method:
                    raise ValueError('Rebuilt mobile-data handler lost required instructions')
            raw = tmp / 'raw.apk'
            with zipfile.ZipFile(raw, 'w') as packed:
                for info in old.infolist():
                    packed.writestr(copy.copy(info), dex.read_bytes() if info.filename == entry else old.read(info.filename))
        aligned = tmp / 'aligned.apk'
        subprocess.run([str(zipalign), '-f', '-p', '4', str(raw), str(aligned)], check=True)
        retain_oem_certificate(apk, aligned)
        subprocess.run([str(zipalign), '-c', '-p', '4', str(aligned)], check=True)
        if not signing_block(apk.read_bytes()) or signing_block(apk.read_bytes()) != signing_block(aligned.read_bytes()):
            raise ValueError('SystemUI OEM signing block was not retained')
        with zipfile.ZipFile(apk) as old, zipfile.ZipFile(aligned) as new:
            if old.namelist() != new.namelist():
                raise ValueError('SystemUI APK inventory changed')
            for name in old.namelist():
                if name != entry and old.read(name) != new.read(name):
                    raise ValueError('Unexpected SystemUI payload change: ' + name)
        apk.write_bytes(aligned.read_bytes())
    return {'files': {APK: digest(apk)}, 'input_sha256': expected_sha256,
            'missing_active_repo_fallback': 'valid active default data subscription',
            'reads_same_telephony_manager_as_setter': True, 'scoped_to_default_data_subscription': True,
            'original_signature_valid': False, 'device_tested': False}
