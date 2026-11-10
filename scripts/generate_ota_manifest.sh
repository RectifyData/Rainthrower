#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Generate an inspectable, valid OTA JSON manifest only with a real HTTPS URL.
set -euo pipefail
if [[ $# -ne 2 ]]; then
    echo "Usage: scripts/generate_ota_manifest.sh <directory-with-zips> <https-base-url>" >&2
    exit 2
fi
if [[ -z "${SRC_DIR:-}" ]]; then
    SRC_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
fi
python3 - "$1" "$2" "$SRC_DIR/manifest.json" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote, urlsplit
import zipfile


def main(directory, base_url, output):
    directory = Path(directory)
    output = Path(output)
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("An absolute HTTPS download base URL is required")
    if not directory.is_dir():
        raise ValueError("ROM ZIP directory is missing: " + str(directory))
    zips = sorted(directory.glob("*.zip"))
    if not zips:
        raise ValueError("No ROM ZIP archives found")
    items = []
    required = ("timestamp", "device", "version", "security_patch_version", "incremental")
    for archive in zips:
        with zipfile.ZipFile(archive) as packed:
            info = packed.read("build_info.txt").decode("utf-8")
        properties = {}
        for line in info.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key.strip() in properties:
                    raise ValueError("Duplicate build_info property: " + key)
                properties[key.strip()] = value.strip()
        if any(not properties.get(key) for key in required):
            raise ValueError("Missing required build_info property: " + archive.name)
        if not re.fullmatch(r"[0-9]+", properties["timestamp"]) or int(properties["timestamp"]) <= 0:
            raise ValueError("Invalid build timestamp: " + archive.name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", properties["incremental"]):
            raise ValueError("Invalid incremental version: " + archive.name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", properties["version"]):
            raise ValueError("Invalid ROM version: " + archive.name)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", properties["security_patch_version"]):
            raise ValueError("Invalid security patch date: " + archive.name)
        with archive.open("rb") as file:
            sha = hashlib.file_digest(file, "sha256").hexdigest()
        items.append({
            "datetime": int(properties["timestamp"]),
            "device": properties["device"],
            "filename": archive.name,
            "id": sha,
            "patch": properties["security_patch_version"],
            "size": archive.stat().st_size,
            "urls": [base_url.rstrip("/") + "/" + quote(archive.name)],
            "version": properties["version"],
            "incremental": properties["incremental"],
            "sha256": sha,
        })
    descriptor, staged = tempfile.mkstemp(prefix=".rainthrower-ota-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as manifest:
            json.dump({"response": items}, manifest, indent=2)
            manifest.write("\n")
            manifest.flush()
            os.fsync(manifest.fileno())
        os.replace(staged, output)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    print("Validated OTA JSON:", output, "(", len(items), "ROMs )")


try:
    main(*sys.argv[1:])
except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, KeyError) as error:
    sys.exit("OTA manifest: " + str(error))
PY
