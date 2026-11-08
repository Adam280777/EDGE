"""Audit pip dry-run reports using public metadata, not wheel/model downloads.

Explicit command only. Writes proposed hash-pinned wheel manifests and a budget
report beside the caller-selected pip reports. Never installs or loads packages.
Installed-size ranges are planning heuristics, NOT verified upper bounds.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


def public_json(url: str) -> dict:
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=30) as response:
        return json.load(response)


def wheel_size(item: dict) -> dict:
    info = item["metadata"]
    download = item["download_info"]
    url = download["url"]
    parsed = urlsplit(url)
    name = unquote(parsed.path.rsplit("/", 1)[-1])
    digest = download["archive_info"].get("hashes", {}).get("sha256")
    substituted = False
    if not name.endswith(".whl"):
        raise ValueError("A non-wheel artifact cannot be budgeted as a wheel.")
    if parsed.scheme != "https" or parsed.hostname not in {
        "files.pythonhosted.org", "download.pytorch.org", "download-r2.pytorch.org",
    }:
        raise ValueError("Unapproved wheel metadata host.")
    if parsed.hostname == "files.pythonhosted.org" or digest is None:
        release = public_json(f"https://pypi.org/pypi/{info['name']}/{info['version']}/json")
        matched = next(
            candidate for candidate in release["urls"]
            if candidate["filename"] == name
            and (digest is None or candidate["digests"]["sha256"] == digest)
        )
        if digest is None:
            # Select a hashed official PyPI artifact instead of trusting an
            # unhashed index mirror. This is a proposed replacement, not proof
            # the mirror's bytes match. The lock needs another target dry-run.
            url = matched["url"]
            digest = matched["digests"]["sha256"]
            substituted = True
        size = matched["size"]
        evidence = "PyPI_release_filename_sha256_and_size_match"
    else:
        with urlopen(Request(url, method="HEAD"), timeout=30) as response:
            size_text = response.headers.get("Content-Length")
        if size_text is None:
            raise ValueError("Official CPU wheel HEAD did not report Content-Length.")
        size = int(size_text)
        evidence = "official_PyTorch_HEAD_Content_Length_and_pip_index_sha256"
    return {
        "name": info["name"],
        "version": info["version"],
        "filename": name,
        "url": url,
        "sha256": digest,
        "download_bytes": size,
        "size_evidence": evidence,
        "substituted_unhashed_mirror_requires_target_reresolution": substituted,
        "declared_license": info.get("license_expression") or info.get("license"),
        "requires_python": info.get("requires_python"),
    }


def audit(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        wheels = list(executor.map(wheel_size, report["install"]))
    total = sum(item["download_bytes"] for item in wheels)
    result = {
        "pip_report": report_path.name,
        "target": "Windows_x64_CP312_cross_resolution_not_runtime_verification",
        "marker_evaluation_caveat": (
            "pip ran under CPython 3.13; some environment markers may follow the "
            "resolver host. Re-resolve under actual CPython 3.12 before installation."
        ),
        "wheel_count": len(wheels),
        "exact_compressed_download_bytes": total,
        "planning_installed_size_range_bytes": [total * 3, total * 8],
        "installed_size_confidence": (
            "low: compression heuristic, not measured installed size or a hard bound; "
            "excludes Python runtime, wheel cache, source checkout, datasets and outputs"
        ),
        "wheels": wheels,
    }
    prefix = report_path.name.removesuffix(".report.json")
    report_path.with_name(prefix + ".budget.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    manifest = [
        "# Proposed CPython 3.12 Windows x64 wheel lock. NOT installed or runtime-accepted.",
        "# Re-resolve target interpreter markers before approval/install.",
        "# Direct public URLs and hashes avoid index substitutions.",
        "--only-binary=:all:",
        "--require-hashes",
    ] + [
        f"{item['name']} @ {item['url']} --hash=sha256:{item['sha256']}"
        for item in wheels
    ]
    report_path.with_name(prefix + ".lock.txt").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    for report in args.reports:
        result = audit(report)
        print(
            f"{report.name}: wheels={result['wheel_count']}; "
            f"compressed_bytes={result['exact_compressed_download_bytes']}; "
            f"installed_planning_range_bytes="
            f"{result['planning_installed_size_range_bytes']}; NOT installed"
        )
        largest = sorted(
            result["wheels"], key=lambda item: item["download_bytes"], reverse=True
        )[:5]
        for item in largest:
            print(f"  {item['name']}=={item['version']}: {item['download_bytes']} bytes")


if __name__ == "__main__":
    main()
