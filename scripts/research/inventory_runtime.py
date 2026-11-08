"""Read only approved owned software roots and publish measured verification inventory."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from provision_support import AREA, CANONICAL, DOWNLOADS, OWNED


def file_bytes(root: Path) -> int:
    return sum(
        path.stat().st_size for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ) if root.exists() else 0


def main() -> None:
    prefix = str(Path(os.environ["LOCALAPPDATA"]) / "Package Cache")
    pattern = re.compile(
        re.escape(prefix) + r"\\(\{[0-9a-f-]{36}\}(?:v3\.12\.10150\.0)?)",
        re.IGNORECASE,
    )
    identifiers = set()
    # These are solely this provisioning's named Python installer logs.
    for log in (AREA / "temp").glob("Python 3.12.10 (64-bit)_*.log"):
        text = log.read_text(encoding="utf-8", errors="replace").replace("/", "\\")
        identifiers.update(pattern.findall(text))
    (AREA / "python-installer-cache-paths.json").write_text(
        json.dumps(sorted(identifiers), indent=2) + "\n", encoding="utf-8"
    )
    roots = set(OWNED) | {Path(prefix) / name for name in identifiers}
    wheel_files = {}
    for budget_path in (AREA.parents[1] / "requirements").glob("research-*.budget.json"):
        for wheel in json.loads(budget_path.read_text(encoding="utf-8"))["wheels"]:
            actual = DOWNLOADS / wheel["filename"]
            with actual.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual.stat().st_size != wheel["download_bytes"] or digest != wheel["sha256"]:
                raise RuntimeError("Retained wheel hash/size verification failed.")
            wheel_files[wheel["filename"]] = actual.stat().st_size
    unique_wheel_bytes = sum(wheel_files.values())
    # Known payload transfers, not HTTP/TLS wire bytes. Metadata is not metered.
    accounted = (
        unique_wheel_bytes + 26_964_224 + 721_689 + 32_283_678
        + 53_971 + 1062 + 17_702 + 84_136_104 + 25_335
    )
    patch_record = AREA / "python31216-provenance.json"
    if patch_record.exists():
        patch = json.loads(patch_record.read_text(encoding="utf-8"))
        patched_archive = DOWNLOADS / "cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
        if hashlib.sha256(patched_archive.read_bytes()).hexdigest() != patch["artifact_sha256"]:
            raise RuntimeError("Patched runtime archive provenance mismatch.")
        accounted += patch["artifact_bytes"]
    attestation_path = CANONICAL / "attestation-v1.json"
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    smoke = AREA / "canonical-final-smoke-results.json"
    report = {
        "schema_version": 1, "recorded_at": datetime.now(UTC).isoformat(),
        "scope": "software provisioning and actual fixed synthetic tests only",
        "production_ready": False,
        "hard_owned_file_bytes_limit": 8_000_000_000,
        "largest_observed_owned_file_bytes_before_final_cleanup": 4_997_104_741,
        "disk_measurement": (
            "logical file-byte sums of owned base/env/model/wheel/temp/report roots plus "
            "installer cache roots identified from this install's logs; not a continuous "
            "peak monitor or filesystem allocation/metadata measurement"
        ),
        "unique_downloaded_wheel_count": len(wheel_files),
        "unique_downloaded_wheel_bytes": unique_wheel_bytes,
        "accounted_download_payload_bytes": accounted,
        "download_measurement": (
            "verified retained wheels, installer, Gym source, model/config and selected "
            "source/card/license payloads plus known repeated skfolio 84136104-byte "
            "dry-run and early Colorama25335-byte fetch; public metadata/HTTP/TLS "
            "overhead and earlier research reads not metered. Not a total wire-byte claim."
        ),
        "base": attestation["base"],
        "engines": {
            framework: {
                key: value for key, value in engine.items() if key != "packages"
            } for framework, engine in attestation["engines"].items()
        },
        "attestation_sha256": hashlib.sha256(attestation_path.read_bytes()).hexdigest(),
        "execution_contract_metadata": attestation.get("execution_contract"),
        "reissued_from_sha256": attestation.get("reissued_from_sha256"),
        "resource_hashes": attestation["resource_hashes"],
        "model_hashes": attestation["model_hashes"],
        "final_smoke_report_sha256": hashlib.sha256(smoke.read_bytes()).hexdigest(),
        "prior_kronos_timeout_preserved": "canonical-smoke-results.json",
        "kronos_bounded_retry_evidence": "canonical-kronos-retry.json",
        "limitations": [
            "All three latest canonical fixed synthetic checks passed; real financial panels remain blocked.",
            "First canonical Kronos attempt timed out; later bounded retry and full rerun passed. Cold-start timing cause/guarantee unverified.",
            "Current runtime provenance/version is in base metadata; no universal CVE/security or production certification.",
            "Frozen12-route application/native UI acceptance and provider data rights/quality gates remain separate.",
        ],
    }
    repaired_smoke = AREA / "canonical-deadline-v2-smoke-results.json"
    if repaired_smoke.exists():
        repaired = json.loads(repaired_smoke.read_text(encoding="utf-8"))
        report["deadline_v2_smoke_report_sha256"] = hashlib.sha256(
            repaired_smoke.read_bytes()
        ).hexdigest()
        report["deadline_v2_smokes"] = {
            name: {
                "status": item["status"], "elapsed_seconds": item["elapsed_seconds"],
            } for name, item in repaired["results"].items()
        }
        report["limitations"].append(
            "One supervised entry deadline includes chunked verification and nested launch; "
            "outermost synchronous Windows bootstrap is not a universal hard-real-time guarantee. "
            "A shared 2-second cleanup allowance is separate from the requested deadline."
        )
    destination = AREA / "runtime-inventory.json"
    for _ in range(3):  # Include this report's own stable byte length in the sum.
        report["roots"] = {str(root): file_bytes(root) for root in sorted(roots)}
        report["total_owned_file_bytes"] = sum(report["roots"].values())
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["total_owned_file_bytes"] >= 8_000_000_000:
        raise RuntimeError("Hard disk cap exceeded.")
    print("owned_file_bytes", report["total_owned_file_bytes"])
    print("unique_wheels", len(wheel_files), "bytes", unique_wheel_bytes)
    print("accounted_payload_transfer_bytes", accounted, "metadata/overheads excluded")
    print("attestation_sha256", report["attestation_sha256"])
    print("final_smoke_sha256", report["final_smoke_report_sha256"])


if __name__ == "__main__":
    main()
