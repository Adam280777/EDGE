"""Build an isolated Windows onedir distribution and preserve dependency notices.

Run with .venv-desktop\\Scripts\\python.exe. Never installs packages, copies data,
searches for credentials, or invokes providers. Only allowlisted source inputs
are staged; PyInstaller compiles them, rather than bundling the repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_RESOURCES = (
    "qlib_worker.py", "skfolio_worker.py", "kronos_worker.py", "worker_protocol.py",
    "kronos-artifacts.json", "kronos-payload-verification.json",
    "vendor/kronos/model/__init__.py", "vendor/kronos/model/kronos.py",
    "vendor/kronos/model/module.py", "vendor/kronos/LICENSE", "vendor/kronos/NOTICE.txt",
)
RESEARCH_NOTICES = (
    "model-cache/Kronos-mini/README.md",
    "model-cache/Kronos-Tokenizer-2k/README.md",
)


def stage_research_resources(source: Path, destination: Path) -> None:
    """Only fixed isolated-worker resources; never wheels, envs, weights or logs.

    Python source is intentional here: external attested CPython 3.12 executes
    these fixed bootstrap files in isolated mode. It cannot use the 3.13 PYZ.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative in RESEARCH_RESOURCES:
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or redirected fixed research resource.")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for relative in RESEARCH_NOTICES:
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or redirected research model notice.")
        target = destination / "notices" / Path(relative).parent.name / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def prepare() -> None:
    if os.name != "nt" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("The accepted desktop build requires Windows x64 CPython 3.13.")
    if Path(sys.prefix).resolve() != (ROOT / ".venv-desktop").resolve():
        raise RuntimeError("Use the isolated .venv-desktop interpreter; original .venv is not a build env.")
    for package, version in {
        "pywebview": "6.2.1", "pyinstaller": "6.22.3", "streamlit": "1.66.1",
        "nautilus_trader": "2.0.0rc8", "openbb-core": "2.0.1", "openbb-sec": "2.0.0",
    }.items():
        if metadata.version(package) != version:
            raise RuntimeError(f"Unaccepted build dependency: {package}; reinstall the documented lock/extras.")
    staging = ROOT / "build" / "desktop-staging"
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "app.py", staging / "desktop_dashboard.py")
    stage_research_resources(ROOT / "scripts" / "research", staging / "research")
    notices = staging / "licenses"
    if notices.exists():
        shutil.rmtree(notices)
    notices.mkdir()
    inventory = []
    for dist in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        if name == "edge-research":
            continue
        inventory.append({"name": name, "version": version})
        # Preserve distribution licence/notice files including vendored components,
        # but never copy source trees, editable-install paths, .env, or user files.
        for item in dist.files or []:
            leaf = Path(str(item)).name.lower()
            # Models called license.py are program source, not licence texts.
            if Path(leaf).suffix in (".py", ".pyc", ".pyo", ".pyi", ".pyd", ".dll"):
                continue
            if not (leaf.startswith(("license", "licence", "copying", "notice", "authors"))
                    or ".dist-info/licenses/" in str(item).replace("\\", "/")):
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                continue
            relative = Path(*[part for part in Path(str(item)).parts if part not in ("..", ".")])
            destination = notices / f"{name}-{version}" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("CPython licence was not found; do not ship without it.")
    shutil.copy2(python_license, notices / "PYTHON-LICENSE.txt")
    shutil.copytree(
        ROOT / "scripts" / "desktop_licenses", notices / "Desktop-native",
    )
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", notices / "EDGE-THIRD-PARTY-NOTICES.md")
    (notices / "BUILD-DEPENDENCIES.json").write_text(
        json.dumps(inventory, indent=2), encoding="utf-8"
    )
    (notices / "README.txt").write_text(
        "Installed build-environment inventory is a conservative superset of runtime components.\n"
        "Licences/notices are preserved from installed distributions, including native dependencies.\n"
        "NautilusTrader: LGPL-3.0; unmodified wheel version 2.0.0rc8.\n"
        "Corresponding upstream source: https://github.com/nautechsystems/nautilus_trader/tree/v2.0.0rc8\n"
        "This is an unsigned local build, not a cleared external redistribution release.\n"
        "Before external delivery review all source/relinking obligations; retain the complete folder.\n",
        encoding="utf-8",
    )


def synchronize_notices() -> None:
    """Keep the generated legal sidecars exact, including removed false matches."""
    destination = ROOT / "dist" / "EDGE" / "_internal" / "licenses"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(ROOT / "build" / "desktop-staging" / "licenses", destination)
    # Typing-only sidecars are not runtime inputs and need not be distributed.
    for stub in (ROOT / "dist" / "EDGE" / "_internal").rglob("*.pyi"):
        stub.unlink()


def main() -> int:
    prepare()
    result = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "EDGE.spec")],
        cwd=ROOT,
    )
    if result == 0:
        synchronize_notices()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
