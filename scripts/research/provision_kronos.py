"""Explicit approved immutable Kronos source/model provisioning; no startup hooks."""

from __future__ import annotations

import hashlib
import json
import shutil
from urllib.request import urlopen

from provision_support import AREA, checkpoint, download


def main() -> None:
    manifest = json.loads((AREA / "kronos-artifacts.json").read_text(encoding="utf-8"))
    commit = manifest["source"]["commit"]
    checkpoint(70_000_000)
    vendor = AREA / "vendor" / "kronos"
    if vendor.exists():
        raise RuntimeError("Refusing to overwrite existing vendor source.")
    expected_blobs = {
        "model/__init__.py": "718d07a21b53b7eff4a6564e6dfcae8ee7e8c6b1",
        "model/kronos.py": "ce4494ee0b3ec8751b09d5488c93bde995e008e0",
        "model/module.py": "f2a05158b48a56e9235426f6583e384360d761f9",
    }
    evidence: dict[str, object] = {"commit": commit, "files": []}
    vendor.mkdir(parents=True)
    for filename, blob in expected_blobs.items():
        url = f"https://raw.githubusercontent.com/shiyu-coder/Kronos/{commit}/{filename}"
        with urlopen(url, timeout=30) as response:
            text = response.read(100_000)
        actual_blob = hashlib.sha1(
            f"blob {len(text)}\0".encode() + text, usedforsecurity=False
        ).hexdigest()
        if actual_blob != blob:
            raise RuntimeError("Immutable Git blob verification failed.")
        target = vendor / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text)
        evidence["files"].append({
            "path": filename, "git_blob_sha1": actual_blob,
            "sha256": hashlib.sha256(text).hexdigest(), "bytes": len(text),
        })
    with urlopen(manifest["source"]["license_url"], timeout=30) as response:
        license_text = response.read(100_000)
    if hashlib.sha256(license_text).hexdigest() != manifest["source"]["license_text_sha256"]:
        raise RuntimeError("Pinned MIT license verification failed.")
    (vendor / "LICENSE").write_bytes(license_text)
    (vendor / "NOTICE.txt").write_text(
        f"Kronos source from https://github.com/shiyu-coder/Kronos\nCommit {commit}\n"
        "Copyright upstream authors. Distributed under included MIT LICENSE.\n"
        "Unmodified selected model package only; no example scripts or data copied.\n",
        encoding="utf-8",
    )
    for model in manifest["models"]:
        short_name = model["repository"].rsplit("/", 1)[-1]
        destination = AREA / "model-cache" / short_name
        destination.mkdir(parents=True, exist_ok=False)
        for item in model["files"]:
            url = (
                f"https://huggingface.co/{model['repository']}/resolve/"
                f"{model['revision']}/{item['name']}"
            )
            cached = download(
                url, f"{short_name}-{item['name']}", item["bytes"], item["sha256"]
            )
            shutil.copyfile(cached, destination / item["name"])
        with urlopen(
            f"https://huggingface.co/{model['repository']}/resolve/"
            f"{model['revision']}/README.md", timeout=30
        ) as response:
            card = response.read(100_000)
        if hashlib.sha256(card).hexdigest() != model["readme_sha256"]:
            raise RuntimeError("Pinned MIT model card verification failed.")
        (destination / "README.md").write_bytes(card)
        shutil.copyfile(vendor / "LICENSE", destination / "UPSTREAM_SOURCE_LICENSE")
    (AREA / "kronos-payload-verification.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    checkpoint()
    print("Pinned source, MIT notices, model payloads/configs verified; not financial acceptance.")


if __name__ == "__main__":
    main()
