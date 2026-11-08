"""Read-only inventory of EDGE research integration capability and blockers.

Only installed distribution metadata and the explicitly named repository-local
``.venv-qlib`` configuration are inspected. No provider/model module is imported,
no subprocess or network request is started, no model weights are scanned or
downloaded, and no ``data`` directory or user dataset is opened.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]{0,63}$")
_PYTHON_VERSION_RE = re.compile(
    r"^\s*version\s*=\s*(3\.\d+(?:\.\d+)?)\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class IntegrationReadiness:
    """Observed package/runtime facts, EDGE capability, and explicit blockers."""

    integration: str
    status: Literal[
        "fixture-only",
        "metadata-only",
        "ephemeral-display-only",
        "not-integrated",
        "unavailable",
    ]
    implementation_scope: str
    distributions: tuple[tuple[str, str | None], ...]
    environment_evidence: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class ResearchReadinessReport:
    """Read-only readiness inventory; never an acceptance/production certificate."""

    python_version: str
    integrations: tuple[IntegrationReadiness, ...]
    blockers: tuple[str, ...]
    production_ready: Literal[False] = False
    operations_performed: tuple[str, ...] = (
        "installed_distribution_metadata_read_only",
        "repository_local_qlib_venv_metadata_read_only",
        "no_provider_imports",
        "no_subprocesses",
        "no_network_requests",
        "no_model_downloads_or_model_artifact_scan",
        "no_data_directory_or_user_dataset_reads",
    )


def assess_research_readiness(
    *, project_root: Path | None = None
) -> ResearchReadinessReport:
    """Report known integration capability without importing heavy packages.

    Package presence is not integration acceptance. ``project_root`` is used
    only to inspect ``.venv-qlib/pyvenv.cfg`` and allowlisted ``pyqlib``
    distribution metadata, never arbitrary project or user data.
    """
    root = (
        Path(__file__).resolve().parents[3]
        if project_root is None
        else project_root
    )
    if not isinstance(root, Path):
        raise TypeError("project_root must be pathlib.Path or None.")

    nautilus = _version("nautilus_trader")
    openbb_core = _version("openbb-core")
    openbb_sec = _version("openbb-sec")
    qlib_runtime = _version("pyqlib")
    skfolio = _version("skfolio")
    kronos = _first_version(("kronos", "kronos-forecasting"))
    vibe = _first_version(("vibe-trading", "vibe_trading"))
    qlib_worker = _qlib_worker_evidence(root)

    integrations = (
        IntegrationReadiness(
            integration="NautilusTrader",
            status="fixture-only" if nautilus is not None else "unavailable",
            implementation_scope=(
                "Existing EDGE adapter runs the synthetic EDGE_TEST.SIM acceptance "
                "fixture only; real market histories are not connected."
            ),
            distributions=(("nautilus_trader", nautilus),),
            environment_evidence=(f"current_python={_python_version()}",),
            blockers=(
                "real_market_history_not_accepted",
                "real_corporate_action_accounting_unaccepted",
                "simulation_fixture_is_not_a_production_backtest",
            ),
        ),
        IntegrationReadiness(
            integration="OpenBB SEC",
            status=(
                "metadata-only"
                if openbb_core is not None and openbb_sec is not None
                else "unavailable"
            ),
            implementation_scope=(
                "OpenBB SEC adapter covers filing metadata only, not prices or "
                "financial-statement/fundamental facts; no live response is certified."
            ),
            distributions=(
                ("openbb-core", openbb_core),
                ("openbb-sec", openbb_sec),
            ),
            environment_evidence=("lazy_adapter_import_not_attempted",),
            blockers=(
                "live_SEC_response_not_verified",
                "SEC_recent_submissions_coverage_may_be_incomplete",
                "fundamental_fact_ingestion_not_implemented",
            ),
        ),
        IntegrationReadiness(
            integration="FMP",
            status="ephemeral-display-only",
            implementation_scope=(
                "Existing explicit FMP path is private ephemeral historical display; "
                "it is not a storage, calculation, backtest or Nautilus input."
            ),
            distributions=(),
            environment_evidence=("no_FMP_request_or_credential_access_performed",),
            blockers=(
                "account_endpoint_entitlement_not_independently_verified",
                "data_rights_and_retention_license_not_independently_established",
                "whole_OHLC_adjustment_basis_unresolved",
                "corporate_action_completeness_and_accounting_unresolved",
                "publication_revision_history_and_session_calendar_unresolved",
            ),
        ),
        IntegrationReadiness(
            integration="Qlib",
            status="not-integrated",
            implementation_scope=(
                "No EDGE Qlib worker/adapter capability is enabled; target is an "
                "isolated Python 3.12 research worker, not a simulator."
            ),
            distributions=(("pyqlib", qlib_runtime),),
            environment_evidence=qlib_worker,
            blockers=(
                "EDGE_QLib_worker_not_implemented",
                "isolated_Python_3_12_worker_not_verified",
                "no_approved_research_dataset_or_license_evidence",
                "model_artifacts_not_inspected_or_downloaded",
            ),
        ),
        IntegrationReadiness(
            integration="skfolio",
            status="not-integrated",
            implementation_scope="No EDGE skfolio allocation adapter or accepted workflow.",
            distributions=(("skfolio", skfolio),),
            environment_evidence=("distribution_metadata_only",),
            blockers=("EDGE_skfolio_integration_not_implemented",),
        ),
        IntegrationReadiness(
            integration="Kronos",
            status="not-integrated",
            implementation_scope=(
                "No EDGE Kronos adapter; model artifacts are deliberately not scanned."
            ),
            distributions=(
                ("kronos_or_kronos-forecasting", kronos),
            ),
            environment_evidence=("candidate_distribution_metadata_only",),
            blockers=(
                "EDGE_Kronos_integration_not_implemented",
                "model_artifacts_not_inspected_or_downloaded",
            ),
        ),
        IntegrationReadiness(
            integration="Vibe-Trading",
            status="not-integrated",
            implementation_scope=(
                "No EDGE Vibe-Trading workflow adapter; no upstream code is imported."
            ),
            distributions=(("vibe-trading_or_vibe_trading", vibe),),
            environment_evidence=("candidate_distribution_metadata_only",),
            blockers=(
                "EDGE_Vibe_Trading_integration_not_implemented",
                "model_artifacts_not_inspected_or_downloaded",
            ),
        ),
    )
    blockers = tuple(
        f"{item.integration}:{reason}"
        for item in integrations
        for reason in item.blockers
    ) + (
        "source_rights_entitlement_and_market_evidence_not_production_accepted",
        "manual_observations_are_user_declarations_not_verified_market_history",
        "no_freshness_or_exchange_calendar_default_is_authorized",
    )
    return ResearchReadinessReport(
        python_version=_python_version(),
        integrations=integrations,
        blockers=blockers,
    )


def _python_version() -> str:
    version = sys.version_info
    return f"{version.major}.{version.minor}.{version.micro}"


def _version(distribution: str) -> str | None:
    """Read distribution metadata only; never import its top-level module."""
    try:
        value = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None
    except (OSError, ValueError, metadata.MetadataError):
        return None
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        return "present_version_unreadable"
    return value


def _first_version(distributions: tuple[str, ...]) -> str | None:
    for distribution in distributions:
        value = _version(distribution)
        if value is not None:
            return f"{distribution}={value}"
    return None


def _qlib_worker_evidence(root: Path) -> tuple[str, ...]:
    environment = root / ".venv-qlib"
    config = environment / "pyvenv.cfg"
    if not config.is_file():
        return ("repository_local_.venv-qlib_not_present",)
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ("repository_local_.venv-qlib_config_unreadable",)
    version_text = None
    for line in lines:
        match = _PYTHON_VERSION_RE.fullmatch(line)
        if match:
            version_text = match.group(1)
            break
    interpreter_paths = (
        environment / "Scripts" / "python.exe",
        environment / "bin" / "python",
        environment / "bin" / "python3",
    )
    interpreter_present = any(path.is_file() for path in interpreter_paths)
    evidence = [
        "repository_local_.venv-qlib_config_present",
        (
            f"pyvenv_cfg_declares_python_{version_text}"
            if version_text is not None
            else "pyvenv_cfg_python_version_unreadable"
        ),
        (
            "worker_interpreter_file_present_not_executed"
            if interpreter_present
            else "worker_interpreter_file_not_found"
        ),
    ]
    site_packages = (
        environment / "Lib" / "site-packages",
        environment / "lib" / "python3.12" / "site-packages",
    )
    qlib_metadata = None
    for directory in site_packages:
        if directory.is_dir():
            candidates = sorted(directory.glob("pyqlib-*.dist-info/METADATA"))
            if candidates:
                try:
                    for line in candidates[0].read_text(encoding="utf-8").splitlines():
                        if line.lower().startswith("version:"):
                            qlib_metadata = line.partition(":")[2].strip()
                            break
                except (OSError, UnicodeError):
                    qlib_metadata = "present_version_unreadable"
                break
    if qlib_metadata is None:
        evidence.append("pyqlib_distribution_metadata_not_found_in_worker")
    elif _VERSION_RE.fullmatch(qlib_metadata) is None:
        evidence.append("pyqlib_distribution_metadata_version_unreadable")
    else:
        evidence.append(f"pyqlib_worker_distribution_version={qlib_metadata}")
    return tuple(evidence)
