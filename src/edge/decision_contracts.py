"""Small immutable manual-decision contracts; never vendor-data acceptance.

GBP is the reporting currency; GBP and USD cash remain separate. Consumers own
FIFO ledgers, research models, sizing and scheduling (only while EDGE is open).
No I/O, implicit clock, provider lookup, FX inversion or rounding occurs here.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from edge.errors import EdgeError

Currency = Literal["GBP", "USD"]
PriceUnit = Literal["GBP", "GBp", "USD"]
REPORTING_CURRENCY: Currency = "GBP"


class DecisionInputError(EdgeError):
    """A manual decision input violates its explicit contract."""


class DecisionStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DecisionInputError(f"{field} must be a finite Decimal.")
    if positive and value <= 0:
        raise DecisionInputError(f"{field} must be positive.")
    return value


def _currency(value: object) -> None:
    if not isinstance(value, str) or value not in ("GBP", "USD"):
        raise DecisionInputError("currency must be GBP or USD, never GBp.")


def _text(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value)
    ):
        raise DecisionInputError(f"{field} must be bounded nonempty plain text.")


def normalize_utc(value: datetime) -> datetime:
    """Require an aware timestamp; normalize its instant without reading a clock."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DecisionInputError("timestamp must be timezone-aware.")
    try:
        if value.utcoffset() is None:
            raise DecisionInputError("timestamp must be timezone-aware.")
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise DecisionInputError("timestamp cannot be represented in UTC.") from None


@dataclass(frozen=True)
class ManualProvenance:
    """User declaration, not verified identity, freshness, rights or market facts.

    A citation/label does not promote a vendor snapshot. Accepted provider data
    must use its own independently evidence-backed contract, not this wrapper.
    """

    reference: str
    declared_at: datetime
    kind: Literal["user-declared"] = "user-declared"

    def __post_init__(self) -> None:
        _text(self.reference, "reference")
        if self.kind != "user-declared":
            raise DecisionInputError("Manual provenance is user-declared only.")
        object.__setattr__(self, "declared_at", normalize_utc(self.declared_at))


def _provenance(value: object) -> None:
    if not isinstance(value, ManualProvenance):
        raise DecisionInputError("ManualProvenance is required.")


@dataclass(frozen=True)
class FinanceMoney:
    """Signed exact cash/P&L amount; negative money is valid, GBp cash is not."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        _decimal(self.amount, "amount")
        _currency(self.currency)


@dataclass(frozen=True)
class PriceQuote:
    """Manually declared price per share; ``as_of`` is not verified freshness."""

    amount: Decimal
    unit: PriceUnit
    as_of: datetime
    provenance: ManualProvenance

    def __post_init__(self) -> None:
        _decimal(self.amount, "price", positive=True)
        if not isinstance(self.unit, str) or self.unit not in ("GBP", "GBp", "USD"):
            raise DecisionInputError("price unit must be GBP, GBp or USD per share.")
        _provenance(self.provenance)
        object.__setattr__(self, "as_of", normalize_utc(self.as_of))

    def per_share_money(self) -> FinanceMoney:
        """Normalize GBp exactly once; do not round or convert currencies."""
        if self.unit == "GBp":
            return FinanceMoney(self.amount / Decimal(100), "GBP")
        return FinanceMoney(self.amount, self.unit)


@dataclass(frozen=True)
class FxRate:
    """Multiply source currency by rate to obtain target currency.

    Example: from_currency='USD', to_currency='GBP', rate=Decimal('0.8')
    means GBP per USD. Reverse conversion requires a separately explicit rate.
    """

    from_currency: Currency
    to_currency: Currency
    rate: Decimal
    as_of: datetime
    provenance: ManualProvenance

    def __post_init__(self) -> None:
        _currency(self.from_currency)
        _currency(self.to_currency)
        if self.from_currency == self.to_currency:
            raise DecisionInputError("FX currencies must differ.")
        _decimal(self.rate, "rate", positive=True)
        _provenance(self.provenance)
        object.__setattr__(self, "as_of", normalize_utc(self.as_of))

    def convert(self, money: FinanceMoney) -> FinanceMoney:
        if not isinstance(money, FinanceMoney) or money.currency != self.from_currency:
            raise DecisionInputError("Money must match the FX source currency.")
        return FinanceMoney(money.amount * self.rate, self.to_currency)


@dataclass(frozen=True)
class DecisionReadiness:
    """Workflow readiness only; COMPLETE is never provider/release acceptance."""

    status: DecisionStatus
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, DecisionStatus):
            raise DecisionInputError("DecisionStatus is required.")
        if not isinstance(self.reasons, tuple) or len(self.reasons) > 20:
            raise DecisionInputError("reasons must be a bounded tuple.")
        for reason in self.reasons:
            _text(reason, "reason")
        if self.status == DecisionStatus.COMPLETE and self.reasons:
            raise DecisionInputError("Complete readiness cannot have blockers.")
        if self.status != DecisionStatus.COMPLETE and not self.reasons:
            raise DecisionInputError("Incomplete/blocked readiness needs reasons.")


@dataclass(frozen=True)
class RiskPolicy:
    """Disabled until explicitly configured AND confirmed; no default percentage.

    max_risk_fraction is a fraction (0.01 means 1%), not a percent input.
    Consumers may impose additional user-confirmed cash/exposure limits.
    """

    enabled: bool = False
    max_risk_fraction: Decimal | None = None
    confirmed_at: datetime | None = None
    revision_id: str | None = None
    long_only: Literal[True] = True
    whole_shares: Literal[True] = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise DecisionInputError("enabled must be boolean.")
        if self.long_only is not True or self.whole_shares is not True:
            raise DecisionInputError("Only long-only whole-share plans are supported.")
        if self.max_risk_fraction is not None:
            _decimal(self.max_risk_fraction, "max_risk_fraction", positive=True)
            if self.max_risk_fraction > 1:
                raise DecisionInputError("max_risk_fraction cannot exceed one.")
        if (self.confirmed_at is None) != (self.revision_id is None):
            raise DecisionInputError("Confirmation requires timestamp and revision.")
        if self.confirmed_at is not None:
            if self.max_risk_fraction is None:
                raise DecisionInputError("Confirmation requires a configured limit.")
            _text(self.revision_id, "revision_id")
            object.__setattr__(self, "confirmed_at", normalize_utc(self.confirmed_at))
        if self.enabled and self.confirmed_at is None:
            raise DecisionInputError("Risk cannot be enabled before confirmation.")

    def require_enabled(self) -> None:
        """Call before sizing; a valid disabled draft is not permission to size."""
        if not self.enabled:
            raise DecisionInputError("Risk is disabled until explicitly confirmed.")


def validate_whole_shares(quantity: int, *, allow_zero: bool = False) -> int:
    """Validate long-only integer shares without coercion or rounding."""
    if type(allow_zero) is not bool:
        raise DecisionInputError("allow_zero must be boolean.")
    if type(quantity) is not int or quantity < (0 if allow_zero else 1):
        raise DecisionInputError("quantity must be a long-only whole-share integer.")
    return quantity
