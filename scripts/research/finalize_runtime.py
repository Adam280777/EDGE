"""Publish fixed-root consistency attestation after canonical dependency verification."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from provision_support import AREA, CANONICAL, RUNTIME, checkpoint


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def refresh_attestation() -> None:
    """Reissue metadata ONLY after unchanged canonical payload verification.

    Source hashes describe this build input, not a signature or proof of PYZ
    equivalence. All existing execution payload/pin/path evidence is preserved.
    """
    root = AREA.parents[1]
    sys.path.insert(0, str(root / "src"))
    from edge.research_workers import research_runtime_readiness

    target = CANONICAL / "attestation-v1.json"
    original = target.read_bytes()
    data = json.loads(original)
    for framework in ("qlib", "skfolio", "kronos"):
        blockers = research_runtime_readiness(
            framework, project_root=root, timeout_seconds=10,
        )
        if blockers:
            raise RuntimeError(f"Unchanged canonical verification blocked: {framework}: {blockers}")
    sources = [
        "src/edge/research_workers.py", "src/edge/desktop_research_job.py",
        "src/edge/desktop_job.py", "src/edge/desktop.py",
    ]
    data["execution_contract"] = {
        "version": 2,
        "recorded_at": datetime.now(UTC).isoformat(),
        "deadline": "one absolute monotonic deadline from public API entry; trusted desktop supervisor owns verification and nested research launch",
        "cleanup_allowance_seconds": 2.0,
        "bootstrap_limitation": "outermost Windows process creation/job bootstrap OS calls are synchronous, not a universal hard-real-time guarantee",
        "source_hashes": {name: digest(root / name) for name in sources},
        "source_hash_scope": "build-input provenance ONLY, not self-validating, not signed trust, not proof of frozen PYZ equivalence; immutable binary/hash QA remains separate",
    }
    data["reissued_from_sha256"] = hashlib.sha256(original).hexdigest()
    for name in ("schema_version", "base", "engines", "resource_hashes", "model_hashes"):
        if data[name] != json.loads(original)[name]:
            raise RuntimeError("Refresh must not change canonical execution payload evidence.")
    archive = AREA / "attestation-pre-deadline-v2.json"
    if archive.exists():
        raise RuntimeError("Refusing to replace retained previous-attestation evidence.")
    if target.read_bytes() != original:
        raise RuntimeError("Attestation changed during bounded verification; refusing refresh.")
    archive.write_bytes(original)
    temporary = CANONICAL / "attestation-deadline-v2.tmp"
    with temporary.open("xb") as stream:
        stream.write((json.dumps(data, indent=2) + "\n").encode("utf-8"))
    if target.read_bytes() != original:
        temporary.unlink()
        raise RuntimeError("Attestation changed before atomic publication.")
    temporary.replace(target)
    print("Reissued unchanged canonical evidence; metadata source hashes are not PYZ trust.")
    print("attestation_sha256", digest(target))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.refresh:
        refresh_attestation()
        return
    checkpoint(50_000_000)
    manifest = json.loads((AREA / "kronos-artifacts.json").read_text(encoding="utf-8"))
    target_models = CANONICAL / "models"
    for model in manifest["models"]:
        name = model["repository"].rsplit("/", 1)[-1]
        source = AREA / "model-cache" / name
        target = target_models / name
        if not target.exists():
            shutil.copytree(source, target)
        for item in model["files"]:
            if digest(target / item["name"]) != item["sha256"]:
                raise ValueError("Canonical model hash mismatch.")
    expected = {"qlib": ("pyqlib", "0.9.7"), "skfolio": ("skfolio", "1.12.5"),
                "kronos": ("torch", "2.7.1+cpu")}
    engines = {}
    for framework, (package, version) in expected.items():
        root = CANONICAL / "envs" / framework
        interpreter = root / "Scripts" / "python.exe"
        actual = subprocess.check_output([
            str(interpreter), "-I", "-c",
            ("import sys,importlib.metadata,json; "
             f"print(json.dumps([sys.version.split()[0],importlib.metadata.version('{package}')]))"),
        ], text=True).strip()
        python_version, actual_version = json.loads(actual)
        if python_version != "3.12.16" or actual_version != version:
            raise ValueError("Canonical pinned version mismatch.")
        subprocess.run([str(interpreter), "-I", "-m", "pip", "check"], check=True)
        packages = []
        for distribution in importlib.metadata.distributions(
            path=[str(root / "Lib" / "site-packages")]
        ):
            metadata = Path(distribution._path) / "METADATA"
            packages.append({
                "name": distribution.metadata["Name"], "version": distribution.version,
                "metadata_path": str(metadata.relative_to(root)).replace("\\", "/"),
                "metadata_sha256": digest(metadata),
            })
        engines[framework] = {
            "path": str(root), "python_version": python_version,
            "python_sha256": digest(interpreter), "config_sha256": digest(root / "pyvenv.cfg"),
            "primary_package": package, "primary_version": version,
            "packages": sorted(packages, key=lambda item: item["name"].lower()),
        }
    resources = [
        "qlib_worker.py", "skfolio_worker.py", "kronos_worker.py", "worker_protocol.py",
        "kronos-artifacts.json", "kronos-payload-verification.json",
        "vendor/kronos/model/__init__.py", "vendor/kronos/model/kronos.py",
        "vendor/kronos/model/module.py", "vendor/kronos/LICENSE",
    ]
    attestation = {
        "schema_version": 1, "purpose": "local consistency, not signed trust or financial acceptance",
        "base": {
            "path": str(RUNTIME), "version": "3.12.16",
            "python_sha256": digest(RUNTIME / "python.exe"),
            "dll_sha256": digest(RUNTIME / "python312.dll"),
            "artifact_sha256": "1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149",
            "security_note": "Astral third-party prebuilt3.12.16 with recorded provenance; no universal security/production certification.",
        },
        "engines": engines, "resource_hashes": {name: digest(AREA / name) for name in resources},
        "model_hashes": {
            f"{model['repository'].rsplit('/', 1)[-1]}/{item['name']}": item["sha256"]
            for model in manifest["models"] for item in model["files"]
        },
    }
    target = CANONICAL / "attestation-v1.json"
    if target.exists():
        raise RuntimeError("Refusing existing attestation publication.")
    target.write_text(json.dumps(attestation, indent=2) + "\n", encoding="utf-8")
    checkpoint()
    print("Canonical attestation published; actual synthetic checks remain separate required evidence.")


if __name__ == "__main__":
    main()
