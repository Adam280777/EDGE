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
    (str(stage / "research"), "research"),
]
binaries = []
hiddenimports = [
    "desktop_dashboard", "edge.desktop_splash", "tkinter", "tkinter.ttk",
    "edge.portfolio", "edge.portfolio_store", "edge.decision_contracts",
    "edge.decision_store", "edge.trade_planning", "edge.research_workflows",
    "edge.discovery", "edge.monitoring", "edge.monitoring_store",
    "edge.application_backup", "edge.application_storage_lock",
    "edge.dashboard_portfolio", "edge.dashboard_decisions",
    "edge.dashboard_discovery", "edge.dashboard_monitoring",
    "edge.dashboard_application_backup", "edge.dashboard_manual_csv",
    "edge.native_dialogs_manual", "edge.data.manual_observations",
    "edge.data.research_readiness",
    "edge.dashboard_research_lab", "edge.research_templates",
    "edge.research_workers", "edge.desktop_research_job",
    "edge.portfolio_optimization", "edge.desktop_research_check",
    "edge.ui_kit", "edge.ui_theme", "edge.ui_charts", "edge.dashboard_home",
    "edge.layout", "edge.layout_store", "edge.ui_grid",
]
# Altair ships the Vega-Lite schema used to validate every terminal chart.
for package in ("streamlit", "altair", "nautilus_trader", "openbb_core", "openbb_sec", "webview"):
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
