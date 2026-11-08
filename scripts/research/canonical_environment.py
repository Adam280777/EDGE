"""Recreate one approved canonical venv from the verified local wheelhouse."""

from __future__ import annotations

import argparse
import json
import subprocess

from provision_support import CANONICAL, DOWNLOADS, ROOT, RUNTIME, checkpoint


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("framework", choices=["skfolio", "kronos", "qlib"])
    args = parser.parse_args()
    framework = args.framework
    prefix = "research-" + {
        "skfolio": "skfolio-py312",
        "kronos": "kronos-cpu-py312",
        "qlib": "qlib-dependencies-py312",
    }[framework]
    budget = json.loads(
        (ROOT / "requirements" / f"{prefix}.budget.json").read_text(encoding="utf-8")
    )
    checkpoint(budget["exact_compressed_download_bytes"] * 10 + 100_000_000)
    destination = CANONICAL / "envs" / framework
    if destination.exists():
        raise RuntimeError("Refusing an existing canonical environment.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(RUNTIME / "python.exe"), "-I", "-m", "venv", str(destination)], check=True
    )
    interpreter = destination / "Scripts" / "python.exe"
    lock = ROOT / "requirements" / f"{prefix}.local.lock.txt"
    commands = [
        [
            str(interpreter), "-I", "-m", "pip", "install", "--dry-run",
            "--ignore-installed", "--no-cache-dir", "--find-links", str(DOWNLOADS),
            "--report", str(ROOT / "requirements" / f"{prefix}.canonical.target.report.json"),
            "-r", str(lock),
        ],
        [
            str(interpreter), "-I", "-m", "pip", "install", "--no-cache-dir", "--no-compile",
            "--find-links", str(DOWNLOADS),
            "--report", str(ROOT / "requirements" / f"{prefix}.canonical.installed.report.json"),
            "-r", str(lock),
        ],
        [str(interpreter), "-I", "-m", "pip", "check"],
    ]
    for command in commands:
        subprocess.run(command, check=True, cwd=ROOT)
    checkpoint()
    print(f"Canonical {framework} dependencies installed; no financial acceptance.")


if __name__ == "__main__":
    main()
