from __future__ import annotations

import builtins
import subprocess

from edge.data import research_readiness


def version_map(monkeypatch, values):
    def fake_version(distribution):
        if distribution not in values:
            raise research_readiness.metadata.PackageNotFoundError(distribution)
        return values[distribution]

    monkeypatch.setattr(research_readiness.metadata, "version", fake_version)


def test_readiness_is_explicitly_blocked_and_reports_only_metadata(tmp_path, monkeypatch):
    version_map(
        monkeypatch,
        {
            "nautilus_trader": "2.0.0rc8",
            "openbb-core": "2.0.1",
            "openbb-sec": "2.0.0",
        },
    )
    report = research_readiness.assess_research_readiness(project_root=tmp_path)
    integrations = {item.integration: item for item in report.integrations}
    assert report.production_ready is False
    assert report.python_version
    assert integrations["NautilusTrader"].status == "fixture-only"
    assert integrations["NautilusTrader"].distributions == (
        ("nautilus_trader", "2.0.0rc8"),
    )
    assert integrations["OpenBB SEC"].status == "metadata-only"
    assert "fundamental_fact_ingestion_not_implemented" in (
        integrations["OpenBB SEC"].blockers
    )
    assert integrations["FMP"].status == "ephemeral-display-only"
    assert integrations["Qlib"].status == "not-integrated"
    assert integrations["skfolio"].status == "not-integrated"
    assert integrations["Kronos"].status == "not-integrated"
    assert integrations["Vibe-Trading"].status == "not-integrated"
    assert "repository_local_.venv-qlib_not_present" in (
        integrations["Qlib"].environment_evidence
    )
    assert "source_rights_entitlement_and_market_evidence_not_production_accepted" in (
        report.blockers
    )
    assert "no_data_directory_or_user_dataset_reads" in report.operations_performed


def test_runtime_package_presence_never_promotes_product_integration(tmp_path, monkeypatch):
    version_map(
        monkeypatch,
        {
            "pyqlib": "0.9.7",
            "skfolio": "0.6.1",
            "kronos-forecasting": "1.0.0",
            "vibe_trading": "0.1.0",
        },
    )
    report = research_readiness.assess_research_readiness(project_root=tmp_path)
    integrations = {item.integration: item for item in report.integrations}
    assert integrations["Qlib"].status == "not-integrated"
    assert integrations["Qlib"].distributions == (("pyqlib", "0.9.7"),)
    assert integrations["skfolio"].distributions == (("skfolio", "0.6.1"),)
    assert integrations["Kronos"].distributions == (
        ("kronos_or_kronos-forecasting", "kronos-forecasting=1.0.0"),
    )
    assert integrations["Vibe-Trading"].distributions == (
        ("vibe-trading_or_vibe_trading", "vibe_trading=0.1.0"),
    )
    assert all(item.status == "not-integrated" for item in (
        integrations["Qlib"], integrations["skfolio"],
        integrations["Kronos"], integrations["Vibe-Trading"],
    ))


def test_qlib_worker_inventory_reads_only_named_runtime_metadata(tmp_path, monkeypatch):
    version_map(monkeypatch, {})
    environment = tmp_path / ".venv-qlib"
    site_packages = environment / "Lib" / "site-packages"
    metadata_dir = site_packages / "pyqlib-0.9.7.dist-info"
    metadata_dir.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text(
        "home = C:\\Python312\nversion = 3.12.4\n", encoding="utf-8"
    )
    scripts = environment / "Scripts"
    scripts.mkdir()
    (scripts / "python.exe").write_bytes(b"fixture-not-an-executable")
    (metadata_dir / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: pyqlib\nVersion: 0.9.7\n",
        encoding="utf-8",
    )

    qlib = next(
        item
        for item in research_readiness.assess_research_readiness(
            project_root=tmp_path
        ).integrations
        if item.integration == "Qlib"
    )
    assert qlib.status == "not-integrated"
    assert "pyvenv_cfg_declares_python_3.12.4" in qlib.environment_evidence
    assert "worker_interpreter_file_present_not_executed" in qlib.environment_evidence
    assert "pyqlib_worker_distribution_version=0.9.7" in qlib.environment_evidence
    assert "EDGE_QLib_worker_not_implemented" in qlib.blockers


def test_readiness_does_not_import_optional_packages_or_spawn_processes(
    tmp_path, monkeypatch
):
    version_map(monkeypatch, {})
    original_import = builtins.__import__
    forbidden = {
        "nautilus_trader", "openbb", "openbb_core", "openbb_sec",
        "qlib", "skfolio", "kronos", "vibe_trading",
    }

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in forbidden:
            raise AssertionError("Optional research package was imported.")
        return original_import(name, *args, **kwargs)

    def forbidden_process(*_args, **_kwargs):
        raise AssertionError("Readiness must not start a subprocess.")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    monkeypatch.setattr(subprocess, "run", forbidden_process)
    report = research_readiness.assess_research_readiness(project_root=tmp_path)
    assert report.production_ready is False
    assert "no_provider_imports" in report.operations_performed
    assert "no_subprocesses" in report.operations_performed
    assert "no_model_downloads_or_model_artifact_scan" in report.operations_performed


def test_project_root_must_be_path():
    try:
        research_readiness.assess_research_readiness(project_root="not-a-path")
    except TypeError:
        return
    raise AssertionError("A non-Path project_root must be rejected.")
