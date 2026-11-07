import json
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from edge.provider_settings import (
    ProviderSettings,
    SettingsError,
    load_settings,
    save_settings,
    settings_path,
)


def test_first_run_is_inert_and_defaults_are_immutable(tmp_path):
    assert load_settings(tmp_path / "data") == ProviderSettings()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(FrozenInstanceError):
        ProviderSettings().provider = "other"


def test_explicit_atomic_nonsecret_save(tmp_path):
    root = tmp_path / "data"
    settings = replace(ProviderSettings(), timeout_seconds=3)
    save_settings(root, settings)
    path = settings_path(root)
    assert path == tmp_path / "settings" / "provider-v1.json"
    assert load_settings(root) == settings
    assert not root.exists()
    assert list(path.parent.iterdir()) == [path]
    assert json.loads(path.read_text()) == asdict(settings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("provider", "yahoo"),
        ("plan_label", "pasted-secret"),
        ("max_history_days", 367),
        ("max_calls_per_snapshot", 3),
        ("timeout_seconds", 11),
        ("timeout_seconds", True),
        ("max_response_bytes", 4194305),
        ("cooldown_seconds", 0),
        ("max_snapshots_per_session", 11),
    ],
)
def test_invalid_settings_never_write(tmp_path, field, value):
    with pytest.raises(SettingsError):
        save_settings(tmp_path / "data", replace(ProviderSettings(), **{field: value}))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    [
        '{"version":2}',
        '{"provider":"other"}',
        '{"api_key":"DO-NOT-LEAK"}',
        '{"version":1,"version":1}',
        "null",
        "{broken",
        "x" * 4097,
    ],
)
def test_corrupt_unsupported_or_secret_config_never_falls_back(tmp_path, payload):
    path = settings_path(tmp_path / "data")
    path.parent.mkdir()
    path.write_text(payload)
    with pytest.raises(SettingsError) as error:
        load_settings(tmp_path / "data")
    assert "DO-NOT-LEAK" not in str(error.value)
    assert path.read_text() == payload


def test_failed_replace_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    root = tmp_path / "data"
    save_settings(root, ProviderSettings())
    before = settings_path(root).read_bytes()

    def fail(*args):
        raise OSError("SENSITIVE")

    monkeypatch.setattr("edge.provider_settings.os.replace", fail)
    with pytest.raises(SettingsError) as error:
        save_settings(root, replace(ProviderSettings(), timeout_seconds=2))
    assert "SENSITIVE" not in str(error.value)
    assert settings_path(root).read_bytes() == before
    assert len(list(settings_path(root).parent.iterdir())) == 1
