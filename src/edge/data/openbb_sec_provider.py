"""OpenBB v5 (``openbb-sec``) implementation of :class:`SecFilingsProvider`.

All OpenBB imports are lazy and happen only inside :meth:`lookup` /
:meth:`describe`. Behaviour enforced here, after inspecting openbb-sec 2.0.0:

* openbb-sec ships hard-coded placeholder SEC User-Agent headers
  (``openbb_sec.utils.definitions.HEADERS`` / ``SEC_HEADERS``). For the duration
  of a lookup, under a process lock, they are replaced in place with the
  user-configured User-Agent and restored afterwards. If the installed package
  does not expose those shared header objects, the lookup is refused rather
  than sending the placeholder contact.
* Every fetcher is called with ``use_cache=False`` (openbb-sec otherwise uses a
  persistent disk cache).
* Only exact, tested package versions are accepted, because the adapter relies
  on the fetcher/header layout of those versions.
"""

from __future__ import annotations

import asyncio
import threading
import warnings
from importlib import metadata
from typing import Any

from edge.data.sec_filings import (
    SecFilingConfigurationError,
    SecFilingDataError,
    SecFilingError,
    SecFilingNotFoundError,
    SecFilingProviderError,
    SecProviderInfo,
    SecProviderPayload,
    redact,
)

PROVIDER_NAME = "openbb-sec"
TESTED_VERSIONS: dict[str, frozenset[str]] = {
    "openbb-sec": frozenset({"2.0.0"}),
    "openbb-core": frozenset({"2.0.1"}),
}
ENDPOINTS = (
    "https://www.sec.gov/files/company_tickers.json",
    "https://data.sec.gov/submissions/CIK##########.json",
)
_HEADER_LOCK = threading.Lock()
_MISSING = object()


def _installed_version(package: str) -> str:
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError:
        raise SecFilingConfigurationError(
            f"{package} is not installed. Install the optional extra: pip install -e .[sec]"
        ) from None
    if version not in TESTED_VERSIONS[package]:
        raise SecFilingConfigurationError(
            f"{package} {version} is untested with this adapter "
            f"(tested: {', '.join(sorted(TESTED_VERSIONS[package]))}); refusing to run."
        )
    return version


def _load_openbb() -> dict[str, Any]:
    """Import OpenBB modules lazily and verify the header objects are shared."""
    from openbb_core.app.model.abstract.error import OpenBBError
    from openbb_core.provider.utils.errors import EmptyDataError
    from openbb_sec.models import company_filings
    from openbb_sec.models.cik_map import SecCikMapFetcher
    from openbb_sec.models.symbol_map import SecSymbolMapFetcher
    from openbb_sec.utils import definitions, helpers

    header_dicts = (definitions.HEADERS, definitions.SEC_HEADERS)
    if not (
        company_filings.HEADERS is definitions.HEADERS
        and helpers.HEADERS is definitions.HEADERS
        and helpers.SEC_HEADERS is definitions.SEC_HEADERS
        and all(isinstance(d, dict) for d in header_dicts)
    ):
        raise SecFilingConfigurationError(
            "Installed openbb-sec does not use the expected shared SEC header objects; "
            "refusing to send requests that could carry its placeholder User-Agent."
        )
    return {
        "OpenBBError": OpenBBError,
        "EmptyDataError": EmptyDataError,
        "CikMap": SecCikMapFetcher,
        "SymbolMap": SecSymbolMapFetcher,
        "Filings": company_filings.SecCompanyFilingsFetcher,
        "headers": header_dicts,
    }


def _unwrap(result: Any) -> Any:
    """OpenBB fetchers may return an AnnotatedResult wrapper."""
    return getattr(result, "result", result) if type(result).__name__ == "AnnotatedResult" else result


class OpenBBSecFilingsProvider:
    """SEC filing metadata via OpenBB's SEC provider fetchers. No caching."""

    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        if not 0 < timeout_seconds <= 300:
            raise SecFilingConfigurationError("timeout_seconds must be in (0, 300].")
        self.timeout_seconds = timeout_seconds

    def describe(self) -> SecProviderInfo:
        return SecProviderInfo(
            name=PROVIDER_NAME,
            version=_installed_version("openbb-sec"),
            framework_version=f"openbb-core {_installed_version('openbb-core')}",
            endpoints=ENDPOINTS,
        )

    async def _run(self, mods: dict[str, Any], sec_ticker: str) -> SecProviderPayload:
        cik_data = _unwrap(await mods["CikMap"].fetch_data({"symbol": sec_ticker, "use_cache": False}))
        raw_cik = getattr(cik_data, "cik", None)
        text = str(raw_cik).strip() if raw_cik is not None else ""
        if not text.isdigit() or not 0 < int(text) < 10**10:
            raise SecFilingDataError("OpenBB SEC CIK map returned a missing or malformed CIK.")
        cik10 = text.zfill(10)
        symbol_data = _unwrap(await mods["SymbolMap"].fetch_data({"query": str(int(text)), "use_cache": False}))
        reverse = getattr(symbol_data, "symbol", None)
        if not isinstance(reverse, str) or not reverse.strip() or reverse.strip().upper().startswith("ERROR"):
            # openbb-sec's ticker->CIK map silently falls back to the fund/ETF list; the
            # reverse lookup only covers operating companies, so stop before fetching filings.
            raise SecFilingDataError(
                f"CIK {cik10} is not in SEC's operating-company ticker map; refusing an "
                "unverified (possibly fund/ETF fallback) identity."
            )
        filings = _unwrap(await mods["Filings"].fetch_data({"cik": cik10, "use_cache": False}))
        if not isinstance(filings, list):
            raise SecFilingDataError("OpenBB SEC filings fetcher returned a non-list result.")
        records = []
        for item in filings:
            if not hasattr(item, "model_dump"):
                raise SecFilingDataError("OpenBB SEC filings fetcher returned a non-model record.")
            records.append(item.model_dump())
        return SecProviderPayload(
            resolved_cik=raw_cik, reverse_ticker=reverse, records=tuple(records),
        )

    def lookup(self, sec_ticker: str, *, user_agent: str) -> SecProviderPayload:
        self.describe()  # version gate before importing provider internals
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise SecFilingConfigurationError("lookup must be called from synchronous code, not a running event loop.")
        try:
            mods = _load_openbb()
        except SecFilingError:
            raise
        except Exception as exc:  # import failures are configuration problems
            raise SecFilingConfigurationError(f"Unable to import OpenBB SEC provider: {type(exc).__name__}.") from None

        with _HEADER_LOCK:
            saved = [d.get("User-Agent", _MISSING) for d in mods["headers"]]
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    for d in mods["headers"]:
                        d["User-Agent"] = user_agent
                    payload = asyncio.run(asyncio.wait_for(self._run(mods, sec_ticker), self.timeout_seconds))
                except SecFilingError:
                    raise
                except TimeoutError:
                    raise SecFilingProviderError(
                        f"SEC lookup timed out after {self.timeout_seconds:g}s; no data returned."
                    ) from None
                except mods["EmptyDataError"] as exc:
                    raise SecFilingNotFoundError(f"OpenBB SEC: {redact(str(exc), user_agent)}") from None
                except mods["OpenBBError"] as exc:
                    message = redact(str(exc), user_agent)
                    error = SecFilingNotFoundError if "not found" in message.lower() else SecFilingProviderError
                    raise error(f"OpenBB SEC: {message}") from None
                except Exception as exc:
                    raise SecFilingProviderError(
                        f"OpenBB SEC request failed ({type(exc).__name__}): {redact(str(exc), user_agent)}"
                    ) from None
                finally:
                    for d, original in zip(mods["headers"], saved, strict=True):
                        if original is _MISSING:
                            d.pop("User-Agent", None)
                        else:
                            d["User-Agent"] = original
            provider_warnings = tuple(
                f"provider warning ({w.category.__name__}): {redact(str(w.message), user_agent)}" for w in caught
            )
        return SecProviderPayload(
            resolved_cik=payload.resolved_cik, reverse_ticker=payload.reverse_ticker,
            records=payload.records, warnings=provider_warnings,
        )