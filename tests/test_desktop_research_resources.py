"""Freeze only explicit worker resources, never downloads, weights or environments."""

import importlib.util
from pathlib import Path

import pytest


def builder():
    path = Path("scripts/build_desktop.py").resolve()
    spec = importlib.util.spec_from_file_location("edge_build_resource_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allowlisted_worker_resources_only_and_stale_files_removed(tmp_path):
    module = builder()
    source = tmp_path / "source"
    destination = tmp_path / "staging"
    for relative in (*module.RESEARCH_RESOURCES, *module.RESEARCH_NOTICES):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic resource")
    for relative in ("downloads/package.whl", "model-cache/weights.safetensors", "temp/log.txt"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"must not bundle")
    destination.mkdir()
    (destination / "stale.py").write_bytes(b"stale")
    module.stage_research_resources(source, destination)
    assert {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*") if path.is_file()
    } == set(module.RESEARCH_RESOURCES) | {
        f"notices/{Path(relative).parent.name}/{Path(relative).name}"
        for relative in module.RESEARCH_NOTICES
    }
    assert all(path.read_bytes() == b"synthetic resource" for path in destination.rglob("*") if path.is_file())


def test_missing_resource_fails_closed(tmp_path):
    module = builder()
    source = tmp_path / "absent"
    with pytest.raises(RuntimeError, match="Missing"):
        module.stage_research_resources(source, tmp_path / "staging")
