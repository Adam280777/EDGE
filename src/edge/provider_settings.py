"""Versioned non-secret desktop settings, outside all data databases."""

# ruff: noqa: BLE001
# Fail closed for unknown parsing/OS failures; never echo corrupt config text.

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from edge.application_storage_lock import storage_write_lock
from edge.data.market_data import MarketDataConfig, validate_config

PROVIDERS = ("fmp",)  # Implemented registry only; not a promise of other adapters.
PLAN_LABELS = ("unspecified", "starter", "premium", "ultimate", "other")


class SettingsError(Exception):
    def __init__(self) -> None:
        super().__init__("Provider settings are invalid or unavailable.")


@dataclass(frozen=True)
class ProviderSettings:
    version: int = 1
    provider: str = "fmp"
    plan_label: str = "unspecified"
    max_history_days: int = 366
    max_calls_per_snapshot: int = 4
    timeout_seconds: int = 10
    max_response_bytes: int = 4 * 1024 * 1024
    cooldown_seconds: int = 60
    max_snapshots_per_session: int = 3

    def validate(self) -> None:
        bounds = {
            "version": (1, 1),
            "max_history_days": (1, 366),
            "max_calls_per_snapshot": (4, 4),
            "timeout_seconds": (1, 10),
            "max_response_bytes": (1024, 4 * 1024 * 1024),
            "cooldown_seconds": (30, 3600),
            "max_snapshots_per_session": (1, 10),
        }
        if self.provider not in PROVIDERS or self.plan_label not in PLAN_LABELS:
            raise SettingsError()
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise SettingsError()
        try:
            validate_config(self.config())
        except Exception:
            raise SettingsError() from None

    def config(self) -> MarketDataConfig:
        return MarketDataConfig(
            provider=self.provider,
            plan_label=self.plan_label,
            max_history_days=self.max_history_days,
            max_calls_per_snapshot=self.max_calls_per_snapshot,
            timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
        )


def settings_path(data_root: Path) -> Path:
    return data_root.parent / "settings" / "provider-v1.json"


def load_settings(data_root: Path) -> ProviderSettings:
    """Missing file has explicit first-run defaults; corrupt files never fallback."""
    path = settings_path(data_root)
    try:
        with path.open("rb") as stream:
            contents = stream.read(4097)
    except FileNotFoundError:
        return ProviderSettings()
    except OSError:
        raise SettingsError() from None
    try:
        if len(contents) > 4096:
            raise SettingsError()

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise SettingsError()
                result[key] = value
            return result

        values = json.loads(contents, object_pairs_hook=unique)
        if not isinstance(values, dict) or set(values) != set(
            asdict(ProviderSettings())
        ):
            raise SettingsError()
        settings = ProviderSettings(**values)
        settings.validate()
        return settings
    except Exception:
        raise SettingsError() from None


def save_settings(data_root: Path, settings: ProviderSettings) -> None:
    """Explicit submit only: validated atomic replace, no credential-bearing fields."""
    if not isinstance(settings, ProviderSettings):
        raise SettingsError()
    settings.validate()
    path = settings_path(data_root)
    temporary = None
    try:
        with storage_write_lock(path.parent.parent):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    delete=False,
                    prefix=".provider-",
                    suffix=".tmp",
                ) as stream:
                    temporary = Path(stream.name)
                    json.dump(asdict(settings), stream, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except Exception:
        raise SettingsError() from None
