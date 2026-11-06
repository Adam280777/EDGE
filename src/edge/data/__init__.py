"""Opt-in external data adapters.

Importing this package performs no network access and does not import any
provider SDK. Provider packages (for example OpenBB) are imported lazily only
when an explicit lookup function is called.
"""

from edge.data.sec_filings import (
    ALLOWED_FORM_TYPES,
    DEFAULT_FORM_TYPES,
    USER_AGENT_ENV_VAR,
    SecCompanyIdentity,
    SecFilingConfigurationError,
    SecFilingDataError,
    SecFilingError,
    SecFilingInputError,
    SecFilingLookupResult,
    SecFilingNotFoundError,
    SecFilingProvenance,
    SecFilingProviderError,
    SecFilingRecord,
    lookup_sec_filings,
)

__all__ = [
    "ALLOWED_FORM_TYPES",
    "DEFAULT_FORM_TYPES",
    "USER_AGENT_ENV_VAR",
    "SecCompanyIdentity",
    "SecFilingConfigurationError",
    "SecFilingDataError",
    "SecFilingError",
    "SecFilingInputError",
    "SecFilingLookupResult",
    "SecFilingNotFoundError",
    "SecFilingProvenance",
    "SecFilingProviderError",
    "SecFilingRecord",
    "lookup_sec_filings",
]
