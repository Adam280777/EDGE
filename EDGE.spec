"""Windows folder build. Never point Tree() at the repository, data or a venv."""

from importlib.metadata import distributions
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

root = Path(SPECPATH)
stage = root / "build" / "desktop-staging"
datas = [
    (str(stage / "licenses"), "licenses"),
]
binaries = []
hiddenimports = ["desktop_dashboard", "edge.desktop_splash", "tkinter", "tkinter.ttk"]
for package in ("streamlit", "nautilus_trader", "openbb_core", "openbb_sec", "webview"):
    datas += collect_data_files(package, include_py_files=False)
    binaries += collect_dynamic_libs(package)
    hiddenimports += collect_submodules(
        package,
        filter=lambda name: ".tests" not in name and ".testing" not in name,
        on_error="warn once",
    )
# Nautilus native code imports Python modules dynamically. Keep metadata for all
# accepted installed dependencies (without direct_url.json / editable path files).
for distribution in distributions():
    if distribution.metadata["Name"] == "edge-research":
        continue
    for item in distribution.files or []:
        normalized = str(item).replace("\\", "/")
        if ".dist-info/" in normalized and Path(normalized).name in {
            "METADATA",
            "WHEEL",
            "entry_points.txt",
            "top_level.txt",
        }:
            source = Path(distribution.locate_file(item))
            if source.is_file():
                datas.append((str(source), str(Path(normalized).parent)))

a = Analysis(
    [str(root / "scripts" / "desktop_entry.py")],
    pathex=[str(root / "src"), str(stage)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
        "matplotlib",
        "scipy",
        "torch",
        "tensorflow",
    ],
    noarchive=False,
    optimize=1,
)
# Runtime hooks can add typing stubs even when include_py_files=False.
a.datas = [item for item in a.datas if Path(item[0]).suffix.lower() != ".pyi"]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EDGE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    uac_admin=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EDGE",
)
