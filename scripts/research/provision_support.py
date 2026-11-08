"""Explicit bounded public artifact download and owned-path footprint helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / "scripts" / "research"
DOWNLOADS = AREA / "downloads"
RUNTIME = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "EDGE-Research-Python312"
CANONICAL = Path(os.environ["LOCALAPPDATA"]) / "EDGE" / "research"
OWNED = [
    ROOT / ".venv-qlib", ROOT / ".venv-skfolio", ROOT / ".venv-kronos",
    AREA, ROOT / "requirements", RUNTIME, CANONICAL,
    Path(os.environ["LOCALAPPDATA"]) / "Package Cache" / "{b6ce88eb-2ce3-4d91-8efc-425ae1f48caf}",
]
cache_inventory = AREA / "python-installer-cache-paths.json"
if cache_inventory.is_file():
    OWNED.extend(
        Path(os.environ["LOCALAPPDATA"]) / "Package Cache" / identifier
        for identifier in json.loads(cache_inventory.read_text(encoding="utf-8"))
        if re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}(?:v3\.12\.10150\.0)?", identifier)
        and identifier != "{b6ce88eb-2ce3-4d91-8efc-425ae1f48caf}"
    )
LIMIT = 8_000_000_000


def footprint() -> int:
    total = 0
    for root in OWNED:
        if root.exists():
            for directory, _, files in os.walk(root, followlinks=False):
                for name in files:
                    path = Path(directory) / name
                    if not path.is_symlink():
                        try:
                            total += path.stat().st_size
                        except FileNotFoundError:
                            # pip atomically publishes temporary bytecode; only
                            # files already absent at measurement contribute zero.
                            continue
    return total


def checkpoint(reserve: int = 0) -> int:
    measured = footprint()
    print(f"owned_bytes={measured}; reserved_next_step={reserve}; hard_limit={LIMIT}")
    if measured + reserve >= LIMIT:
        raise RuntimeError("STOP: additional research disk budget would be exceeded.")
    return measured


def download(
    url: str, name: str, size: int, sha256: str, *, check_space: bool = True
) -> Path:
    if urlsplit(url).hostname not in {
        "www.python.org", "files.pythonhosted.org", "huggingface.co",
        "raw.githubusercontent.com", "download.pytorch.org", "download-r2.pytorch.org",
        "github.com",
    }:
        raise ValueError("Unapproved public artifact host.")
    if Path(name).name != name:
        raise ValueError("Artifact filename must not contain a path.")
    if check_space:
        checkpoint(size)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS / name
    if target.exists():
        data_hash = hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
        if target.stat().st_size == size and data_hash == sha256:
            print(f"verified existing {name} sha256={sha256}")
            return target
        raise RuntimeError("Existing owned artifact does not match approved hash.")
    digest = hashlib.sha256()
    received = 0
    try:
        with urlopen(url, timeout=60) as response, target.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > size:
                    raise RuntimeError("Download exceeds approved published size.")
                output.write(chunk)
                digest.update(chunk)
        if received != size or digest.hexdigest() != sha256:
            raise RuntimeError("Artifact byte count or SHA256 verification failed.")
    except BaseException:
        target.unlink(missing_ok=True)  # Only this helper's named incomplete file.
        raise
    print(f"verified {name} bytes={received} sha256={digest.hexdigest()}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["checkpoint", "python-installer", "wheels"])
    parser.add_argument("--reserve", type=int, default=0)
    parser.add_argument("--budget", type=Path)
    args = parser.parse_args()
    if args.command == "checkpoint":
        checkpoint(args.reserve)
        return
    if args.command == "wheels":
        if args.budget is None:
            raise ValueError("Explicit budget JSON required.")
        budget = json.loads(args.budget.read_text(encoding="utf-8"))
        checkpoint(budget["exact_compressed_download_bytes"])
        lines = ["--only-binary=:all:", "--require-hashes", "--no-index"]
        expanded = 0
        for wheel in budget["wheels"]:
            path = download(
                wheel["url"], wheel["filename"], wheel["download_bytes"], wheel["sha256"],
                # Whole batch was measured and reserved above. Rewalking every
                # installed framework for every tiny wheel is unnecessary;
                # each download is strictly capped by its published byte count.
                check_space=False,
            )
            with zipfile.ZipFile(path) as archive:
                expanded += sum(entry.file_size for entry in archive.infolist())
            lines.append(
                f"{wheel['name']}=={wheel['version']} --hash=sha256:{wheel['sha256']}"
            )
        lock = args.budget.with_name(
            args.budget.name.removesuffix(".budget.json") + ".local.lock.txt"
        )
        lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"verified_wheel_expanded_bytes={expanded}; local_lock={lock}")
        # Bytecode/install bookkeeping reserve beyond exact ZIP expansion.
        checkpoint(expanded * 2)
        return
    url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    with urlopen(url + ".sigstore", timeout=30) as response:
        bundle = json.load(response)
    import base64

    digest = base64.b64decode(bundle["messageSignature"]["messageDigest"]["digest"]).hex()
    with urlopen(url + ".spdx.json", timeout=30) as response:
        spdx = json.load(response)
    # Digest sourced from official Sigstore metadata; Authenticode publisher is
    # separately verified by PowerShell before executing the installer.
    print("official_sigstore_digest", digest)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    (DOWNLOADS / "python-installer.spdx.json").write_text(
        json.dumps(spdx), encoding="utf-8"
    )
    from urllib.request import Request

    with urlopen(Request(url, method="HEAD"), timeout=30) as response:
        size = int(response.headers["Content-Length"])
    download(url, "python-3.12.10-amd64.exe", size, digest)


if __name__ == "__main__":
    main()
