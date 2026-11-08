"""Explicit approved Astral 3.12.16 upgrade; no provider/download fallback."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

from provision_support import (
    AREA,
    CANONICAL,
    DOWNLOADS,
    ROOT,
    RUNTIME,
    checkpoint,
    download,
)

NAME = "cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
SHA = "1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149"
SIZE = 22_014_416
URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/" + NAME.replace("+", "%2B")
COMMIT = "fa506e0858ac9f2cc5b289c137b888a2aa24d1ee"
RECORD = AREA / "python31216-provenance.json"
ROLLBACK = CANONICAL / "rollback-python31210"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url: str, cap: int = 2_000_000) -> bytes:
    with urlopen(url, timeout=30) as response:
        data = response.read(cap + 1)
    if len(data) > cap:
        raise RuntimeError("Public provenance metadata exceeds cap.")
    return data


def verify_provenance() -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    release = json.loads(fetch("https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/20261026"))
    asset = next(a for a in release["assets"] if a["name"] == NAME)
    if (
        release.get("immutable") is not True or release["target_commitish"] != COMMIT
        or asset["size"] != SIZE or asset["digest"] != "sha256:" + SHA
    ):
        raise RuntimeError("Immutable published asset metadata mismatch.")
    sums = fetch("https://github.com/astral-sh/python-build-standalone/releases/download/20261026/SHA256SUMS")
    if not any(line.split() == [SHA, NAME] for line in sums.decode().splitlines()):
        raise RuntimeError("Publisher checksum list mismatch.")
    bundles = json.loads(fetch(
        "https://api.github.com/repos/astral-sh/python-build-standalone/attestations/sha256:" + SHA,
    ))
    bundle = bundles["attestations"][0]["bundle"]
    payload = base64.b64decode(bundle["dsseEnvelope"]["payload"])
    statement = json.loads(payload)
    subjects = [s for s in statement["subject"] if s["digest"].get("sha256") == SHA]
    if len(subjects) != 1:
        raise RuntimeError("Exact artifact digest not attested.")
    workflow = statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"]
    dependency = statement["predicate"]["buildDefinition"]["resolvedDependencies"][0]
    identity = "https://github.com/astral-sh/python-build-standalone/.github/workflows/release.yml@refs/heads/main"
    if (
        workflow != {"ref": "refs/heads/main",
                     "repository": "https://github.com/astral-sh/python-build-standalone",
                     "path": ".github/workflows/release.yml"}
        or dependency["digest"]["gitCommit"] != COMMIT
    ):
        raise RuntimeError("Release workflow provenance mismatch.")
    trust_url = "https://raw.githubusercontent.com/sigstore/root-signing/main/targets/"
    root_pem = fetch(trust_url + "fulcio_v1.crt.pem")
    intermediate_pem = fetch(trust_url + "fulcio_intermediate_v1.crt.pem")
    rekor_pem = fetch(trust_url + "rekor.pub")
    root = x509.load_pem_x509_certificate(root_pem)
    intermediate = x509.load_pem_x509_certificate(intermediate_pem)
    certificate = x509.load_der_x509_certificate(
        base64.b64decode(bundle["verificationMaterial"]["certificate"]["rawBytes"]),
    )
    root.verify_directly_issued_by(root)
    intermediate.verify_directly_issued_by(root)
    certificate.verify_directly_issued_by(intermediate)
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if san.get_values_for_type(x509.UniformResourceIdentifier) != [identity]:
        raise RuntimeError("Certificate workflow identity mismatch.")
    issuer = certificate.extensions.get_extension_for_oid(x509.ObjectIdentifier("1.3.6.1.4.1.57264.1.1")).value.value
    if issuer != b"https://token.actions.githubusercontent.com":
        raise RuntimeError("Certificate OIDC issuer mismatch.")
    kind = bundle["dsseEnvelope"]["payloadType"].encode()
    pae = b"DSSEv1 " + str(len(kind)).encode() + b" " + kind + b" " + str(len(payload)).encode() + b" " + payload
    signature = base64.b64decode(bundle["dsseEnvelope"]["signatures"][0]["sig"])
    certificate.public_key().verify(signature, pae, ec.ECDSA(hashes.SHA256()))
    entry = bundle["verificationMaterial"]["tlogEntries"][0]
    key = serialization.load_pem_public_key(rekor_pem)
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(der).digest() != base64.b64decode(entry["logId"]["keyId"]):
        raise RuntimeError("Published Rekor key mismatch.")
    body = json.loads(base64.b64decode(entry["canonicalizedBody"]))
    if (
        body["spec"]["payloadHash"] != {"algorithm": "sha256", "value": hashlib.sha256(payload).hexdigest()}
        or base64.b64decode(body["spec"]["signatures"][0]["signature"]) != signature
        or x509.load_pem_x509_certificate(base64.b64decode(body["spec"]["signatures"][0]["verifier"])).fingerprint(hashes.SHA256())
        != certificate.fingerprint(hashes.SHA256())
    ):
        raise RuntimeError("Signed log entry does not bind this DSSE/certificate.")
    signed = {"body": entry["canonicalizedBody"],
              "integratedTime": int(entry["integratedTime"]),
              "logID": hashlib.sha256(der).hexdigest(), "logIndex": int(entry["logIndex"])}
    key.verify(
        base64.b64decode(entry["inclusionPromise"]["signedEntryTimestamp"]),
        json.dumps(signed, sort_keys=True, separators=(",", ":")).encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    logged = datetime.fromtimestamp(int(entry["integratedTime"]), UTC)
    if not all(c.not_valid_before_utc <= logged <= c.not_valid_after_utc
               for c in (certificate, intermediate, root)):
        raise RuntimeError("Certificate chain invalid at signed log time.")
    for name, data in (
        ("python31216-attestation-bundle.json", json.dumps(bundle).encode()),
        ("python31216-SHA256SUMS", sums), ("fulcio_v1.crt.pem", root_pem),
        ("fulcio_intermediate_v1.crt.pem", intermediate_pem), ("rekor.pub", rekor_pem),
    ):
        (AREA / name).write_bytes(data)
    return {
        "version": "3.12.16", "artifact_url": URL, "artifact_bytes": SIZE,
        "artifact_sha256": SHA, "release_commit": COMMIT,
        "release_immutable": True, "attested_subject": subjects[0]["name"],
        "workflow_identity": identity, "oidc_issuer": issuer.decode(),
        "signed_log_time": logged.isoformat(),
        "certificate_chain_verified": True, "dsse_signature_verified": True,
        "rekor_signed_entry_timestamp_verified": True,
        "trust_source": trust_url,
        "trust_file_sha256": {
            "fulcio_v1.crt.pem": hashlib.sha256(root_pem).hexdigest(),
            "fulcio_intermediate_v1.crt.pem": hashlib.sha256(intermediate_pem).hexdigest(),
            "rekor.pub": hashlib.sha256(rekor_pem).hexdigest(),
        },
        "limitations": [
            "Astral third-party prebuilt, not a Python Software Foundation Windows installer.",
            "Trust roots retrieved from official Sigstore repository over HTTPS; no full TUF role verification or independent Merkle inclusion verification claimed.",
            "Release identity/digest/signatures/log timestamp verified; not a universal security/CVE or performance certification.",
        ],
    }


def execute(argv: tuple[str, ...], seconds: int = 300) -> bytes:
    sys.path.insert(0, str(ROOT / "src"))
    from edge.desktop_research_job import run_job_worker
    from edge.research_workers import _minimal_environment

    environment = _minimal_environment()
    environment.update({
        "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(CANONICAL / "temp"), "TMP": str(CANONICAL / "temp"),
    })
    (CANONICAL / "temp").mkdir(parents=True, exist_ok=True)
    result = run_job_worker(
        argv, cwd=ROOT, env=environment, payload=b"", timeout_seconds=seconds,
        deadline_monotonic=time.monotonic() + seconds, max_response_bytes=1_048_576,
    )
    if result.status != "succeeded":
        # Retain bounded diagnostics locally only, never secrets/environment.
        (AREA / "python31216-owned-command-error.txt").write_bytes(result.stdout + result.stderr)
        raise RuntimeError(f"Owned offline command {result.status}; diagnostics python31216-owned-command-error.txt")
    print(result.stdout.decode("utf-8", errors="replace")[-1500:])
    return result.stdout


def prepare() -> None:
    if RECORD.exists() or ROLLBACK.exists():
        raise RuntimeError("Refusing an existing upgrade/rollback record.")
    checkpoint(300_000_000)
    record = verify_provenance()
    archive = download(URL, NAME, SIZE, SHA, check_space=False)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        expanded = sum(m.size for m in members if m.isfile())
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not path.parts or path.parts[0] != "python"
                or path.is_absolute() or ".." in path.parts
                or "\\" in member.name or ":" in member.name
                or not (member.isfile() or member.isdir())
            ):
                raise RuntimeError("Archive contains unsafe/nonregular path.")
        if expanded > 200_000_000:
            raise RuntimeError("Expanded base exceeds explicit reservation.")
        checkpoint(expanded)
        stage = CANONICAL / "python31216-staging"
        stage.mkdir()
        for member in members:
            target = stage.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                if target.stat().st_size != member.size:
                    raise RuntimeError("Extracted file size mismatch.")
    base = stage / "python"
    record.update({
        "expanded_base_bytes": expanded,
        "python_sha256": digest(base / "python.exe"),
        "dll_sha256": digest(base / "python312.dll"),
        "tree_hashes": {
            str(p.relative_to(base)).replace("\\", "/"): digest(p)
            for p in base.rglob("*") if p.is_file()
        },
        "user_path_before": os.environ.get("PATH"),
    })
    # Executable bytes are bound by the verified archive before first execution.
    version = json.loads(execute((
        str(base / "python.exe"), "-I", "-B", "-c",
        "import sys,platform,json;print(json.dumps([sys.version.split()[0],platform.machine()]))",
    ), 30))
    if version != ["3.12.16", "AMD64"]:
        raise RuntimeError("Verified staged runtime version/platform mismatch.")
    old = (CANONICAL / "attestation-v1.json").read_bytes()
    prior = json.loads(old)
    if (
        Path(prior["base"]["path"]) != RUNTIME or prior["base"]["version"] != "3.12.10"
        or digest(RUNTIME / "python.exe") != prior["base"]["python_sha256"]
        or digest(RUNTIME / "python312.dll") != prior["base"]["dll_sha256"]
    ):
        raise RuntimeError("Owned original base differs; refusing replacement.")
    ROLLBACK.mkdir()
    (ROLLBACK / "attestation-v1.json").write_bytes(old)
    RUNTIME.rename(ROLLBACK / "base")
    try:
        base.rename(RUNTIME)
    except BaseException:
        (ROLLBACK / "base").rename(RUNTIME)
        raise
    stage.rmdir()
    # Do not retain/print PATH contents; only equality is relevant evidence.
    record.pop("user_path_before")
    record["recorded_at"] = datetime.now(UTC).isoformat()
    record["environments"] = {}
    RECORD.write_text(json.dumps(record, indent=2) + "\n")
    checkpoint()
    print("Verified patched base promoted; old owned base retained; existing313 environments untouched.")


def verified_base() -> dict:
    record = json.loads(RECORD.read_text())
    if (
        record["artifact_sha256"] != SHA or record["version"] != "3.12.16"
        or digest(RUNTIME / "python.exe") != record["python_sha256"]
        or digest(RUNTIME / "python312.dll") != record["dll_sha256"]
    ):
        raise RuntimeError("Patched executable provenance mismatch.")
    return record


def environment(framework: str) -> None:
    record = verified_base()
    destination = CANONICAL / "envs" / framework
    backup = ROLLBACK / framework
    if backup.exists() or framework in record["environments"]:
        raise RuntimeError("Refusing repeated environment recreation.")
    existing_bytes = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
    checkpoint(math.ceil(existing_bytes * 1.3) + 100_000_000)
    destination.rename(backup)
    execute((str(RUNTIME / "python.exe"), "-I", "-B", "-m", "venv", str(destination)))
    executable = destination / "Scripts" / "python.exe"
    lock_name = {
        "qlib": "research-qlib-complete-py312.local.lock.txt",
        "skfolio": "research-skfolio-py312.local.lock.txt",
        "kronos": "research-kronos-cpu-py312.local.lock.txt",
    }[framework]
    lock = ROOT / "requirements" / lock_name
    execute((
        str(executable), "-I", "-B", "-m", "pip", "install",
        "--no-index", "--no-cache-dir", "--no-compile", "--require-hashes",
        "--find-links", str(DOWNLOADS), "-r", str(lock),
    ))
    execute((str(executable), "-I", "-B", "-m", "pip", "check"))
    package = {"qlib": "pyqlib", "skfolio": "skfolio", "kronos": "torch"}[framework]
    expected = {"qlib": "0.9.7", "skfolio": "1.12.5", "kronos": "2.7.1+cpu"}[framework]
    actual = json.loads(execute((
        str(executable), "-I", "-B", "-c",
        f"import sys,importlib.metadata,json;print(json.dumps([sys.version.split()[0],importlib.metadata.version('{package}')]))",
    ), 30))
    if actual != ["3.12.16", expected]:
        raise RuntimeError("Patched environment pin mismatch.")
    record["environments"][framework] = {
        "python_version": actual[0], "primary_package": package, "primary_version": expected,
        "python_sha256": digest(executable), "config_sha256": digest(destination / "pyvenv.cfg"),
        "path": str(destination), "local_lock_sha256": digest(lock), "pip_check": "passed",
    }
    RECORD.write_text(json.dumps(record, indent=2) + "\n")
    checkpoint()


def publish() -> None:
    record = verified_base()
    if set(record["environments"]) != {"qlib", "skfolio", "kronos"}:
        raise RuntimeError("All recreated verified environments required.")
    old = json.loads((ROLLBACK / "attestation-v1.json").read_bytes())
    for relative, expected in old["resource_hashes"].items():
        if digest(AREA / relative) != expected:
            raise RuntimeError("Worker resource changed during upgrade.")
    for relative, expected in old["model_hashes"].items():
        if digest(CANONICAL / "models" / relative) != expected:
            raise RuntimeError("Model changed during upgrade.")
    engines = {}
    for framework, details in record["environments"].items():
        root = CANONICAL / "envs" / framework
        if digest(root / "Scripts" / "python.exe") != details["python_sha256"] or digest(root / "pyvenv.cfg") != details["config_sha256"]:
            raise RuntimeError("Recreated runtime changed before publication.")
        packages = []
        for distribution in importlib.metadata.distributions(path=[str(root / "Lib" / "site-packages")]):
            metadata = Path(distribution._path) / "METADATA"
            packages.append({
                "name": distribution.metadata["Name"], "version": distribution.version,
                "metadata_path": str(metadata.relative_to(root)).replace("\\", "/"),
                "metadata_sha256": digest(metadata),
            })
        engines[framework] = {
            k: v for k, v in details.items() if k not in {"local_lock_sha256", "pip_check"}
        }
        engines[framework]["packages"] = sorted(packages, key=lambda p: p["name"].lower())
    old["base"] = {
        "path": str(RUNTIME), "version": "3.12.16",
        "python_sha256": record["python_sha256"], "dll_sha256": record["dll_sha256"],
        "artifact_sha256": SHA, "artifact_url": URL,
        "security_note": "Astral third-party prebuilt3.12.16; published provenance verified, no universal security certification.",
    }
    old["engines"] = engines
    old["reissued_from_sha256"] = digest(ROLLBACK / "attestation-v1.json")
    old["execution_contract"] = {
        "version": 2, "cleanup_allowance_seconds": 2,
        "deadline": "one absolute monotonic public API entry deadline, trusted desktop supervisor owns hashes/nested research launch",
        "source_hashes": {
            name: digest(ROOT / name) for name in (
                "src/edge/research_workers.py", "src/edge/desktop_research_job.py",
                "src/edge/desktop_job.py", "src/edge/desktop.py",
            )
        },
        "source_hash_scope": "build-input provenance only, not self-validating/signature/PYZ proof; immutable binary QA separate",
        "bootstrap_limitation": "outermost synchronous Windows OS bootstrap is not a universal hard-real-time guarantee",
    }
    target = CANONICAL / "attestation-v1.json"
    if digest(target) != digest(ROLLBACK / "attestation-v1.json"):
        raise RuntimeError("Existing attestation changed; refusing overwrite.")
    temporary = CANONICAL / "attestation-patched.tmp"
    with temporary.open("x") as stream:
        stream.write(json.dumps(old, indent=2) + "\n")
    temporary.replace(target)
    print("patched_attestation_sha256", digest(target))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "qlib", "skfolio", "kronos", "publish"])
    action = parser.parse_args().action
    if action == "prepare":
        prepare()
    elif action == "publish":
        publish()
    else:
        environment(action)


if __name__ == "__main__":
    main()
