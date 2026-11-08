"""Hash-verify and build only approved Gym base source in isolated Qlib env."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

from provision_support import AREA, CANONICAL, DOWNLOADS, ROOT, checkpoint, download


def main() -> None:
    source = json.loads((AREA / "qlib-source-build.json").read_text(encoding="utf-8"))["gym"]
    checkpoint(100_000_000)
    archive = download(
        source["source_url"], "gym-0.26.2.tar.gz",
        source["source_bytes"], source["source_sha256"],
    )
    # Current approved staging build env only; never use existing main envs.
    interpreter = ROOT / ".venv-qlib" / "Scripts" / "python.exe"
    previous_path = AREA / "gym-build-verification.json"
    if previous_path.is_file():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))["built_wheel"]
        existing = DOWNLOADS / previous["filename"]
        with existing.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != previous["sha256"]:
                raise RuntimeError("Existing source-built Gym wheel hash mismatch.")
    else:
        subprocess.run([
            str(interpreter), "-I", "-m", "pip", "wheel", "--no-index",
            "--no-cache-dir", "--no-build-isolation", "--no-deps",
            "--wheel-dir", str(DOWNLOADS), str(archive),
        ], check=True)
    wheels = list(DOWNLOADS.glob("gym-0.26.2-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one newly built Gym base wheel.")
    with wheels[0].open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    source["actual_source_hash_verified"] = True
    source["built_wheel"] = {
        "filename": wheels[0].name, "bytes": wheels[0].stat().st_size, "sha256": digest
    }
    (AREA / "gym-build-verification.json").write_text(
        json.dumps(source, indent=2) + "\n", encoding="utf-8"
    )
    # Joint dependency resolution includes Gym, pyqlib and the full locked
    # dependency set. No missing dependency is suppressed at installation.
    lines = (ROOT / "requirements" / "research-qlib-dependencies-py312.local.lock.txt").read_text()
    qlib = json.loads(
        (ROOT / "requirements" / "research-qlib-package-only-py312.budget.json").read_text()
    )["wheels"][0]
    lines += (
        f"\ngym==0.26.2 --hash=sha256:{digest}\n"
        f"pyqlib==0.9.7 --hash=sha256:{qlib['sha256']}\n"
    )
    final = ROOT / "requirements" / "research-qlib-complete-py312.local.lock.txt"
    final.write_text(lines, encoding="utf-8")
    for interpreter in [
        ROOT / ".venv-qlib" / "Scripts" / "python.exe",
        CANONICAL / "envs" / "qlib" / "Scripts" / "python.exe",
    ]:
        if not interpreter.is_file():
            continue
        suffix = "canonical" if CANONICAL in interpreter.parents else "staging"
        for dry_run in (True, False):
            report = ROOT / "requirements" / (
                f"research-qlib-complete-py312.{suffix}."
                f"{'target' if dry_run else 'installed'}.report.json"
            )
            command = [
                str(interpreter), "-I", "-m", "pip", "install", "--no-cache-dir",
                "--find-links", str(DOWNLOADS), "--report", str(report), "-r", str(final),
            ]
            if dry_run:
                command += ["--dry-run", "--ignore-installed"]
            subprocess.run(command, check=True, cwd=ROOT, env=dict(os.environ))
        subprocess.run([str(interpreter), "-I", "-m", "pip", "check"], check=True)
    checkpoint()


if __name__ == "__main__":
    main()
