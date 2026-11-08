"""Metadata reissue rejects payload changes; no runtime install/execution here."""

from __future__ import annotations

import json
from pathlib import Path

import finalize_runtime as finalize
import pytest

from edge import research_workers


@pytest.fixture
def refresh(tmp_path: Path, monkeypatch):
    area = tmp_path / "scripts" / "research"
    area.mkdir(parents=True)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    for name in (
        "research_workers.py", "desktop_research_job.py", "desktop_job.py", "desktop.py",
    ):
        path = tmp_path / "src" / "edge" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test build provenance\n")
    target = canonical / "attestation-v1.json"
    original = {
        "schema_version": 1, "base": {"fixture": "test"},
        "engines": {"fixture": "test"}, "resource_hashes": {"fixture": "test"},
        "model_hashes": {"fixture": "test"},
    }
    target.write_text(json.dumps(original))
    monkeypatch.setattr(finalize, "AREA", area)
    monkeypatch.setattr(finalize, "CANONICAL", canonical)
    monkeypatch.setattr(finalize.sys, "path", list(finalize.sys.path))
    monkeypatch.setattr(research_workers, "research_runtime_readiness", lambda *a, **k: ())
    return target, area, original


def test_refresh_preserves_all_payload_evidence_and_archives_prior(refresh):
    target, area, original = refresh
    before = target.read_bytes()
    finalize.refresh_attestation()
    data = json.loads(target.read_bytes())
    for name in original:
        assert data[name] == original[name]
    assert data["execution_contract"]["version"] == 2
    assert data["execution_contract"]["cleanup_allowance_seconds"] == 2
    assert "not proof" in data["execution_contract"]["source_hash_scope"]
    assert (area / "attestation-pre-deadline-v2.json").read_bytes() == before
    assert not (target.parent / "attestation-deadline-v2.tmp").exists()


def test_refresh_blocked_verification_never_replaces_attestation(refresh, monkeypatch):
    target, area, _ = refresh
    before = target.read_bytes()
    monkeypatch.setattr(
        research_workers, "research_runtime_readiness", lambda *a, **k: ("hash mismatch",),
    )
    with pytest.raises(RuntimeError, match="verification blocked"):
        finalize.refresh_attestation()
    assert target.read_bytes() == before
    assert not (area / "attestation-pre-deadline-v2.json").exists()


def test_refresh_refuses_concurrent_change(refresh, monkeypatch):
    target, area, _ = refresh

    def changed(*args, **kwargs):
        target.write_text('{"changed":true}')
        return ()

    monkeypatch.setattr(research_workers, "research_runtime_readiness", changed)
    with pytest.raises(RuntimeError, match="changed during"):
        finalize.refresh_attestation()
    assert not (area / "attestation-pre-deadline-v2.json").exists()
