"""Manual, long-only portfolio accounting; no provider ingestion or order execution.

Opening holdings are declarations of existing cost, not fabricated purchases.
Amounts are Decimal in major cash units; only explicit price-unit conversion accepts
GBp. FIFO allocation includes buy fees in cost and deducts sell fees from proceeds.
Corrections void a prior event through a new immutable record, never by deletion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal
from uuid import UUID

from edge.decision_contracts import (
    DecisionReadiness,
    DecisionStatus,
    FinanceMoney,
    FxRate,
    PriceQuote,
)
from edge.errors import EdgeError

Currency = Literal["GBP", "USD"]
PriceUnit = Literal["GBP", "GBp", "USD"]
Market = Literal["UK", "US"]
_CALCULATION_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)


class PortfolioError(EdgeError):
    """A portfolio operation failed safely."""


class PortfolioInputError(PortfolioError):
    """A declaration or operation is invalid."""


class PortfolioStorageError(PortfolioError):
    """Storage is incompatible or corrupt; it has not been reset."""


@dataclass(frozen=True)
class Instrument:
    """User-declared identity, not a provider-verified security."""

    symbol: str
    market: Market
    currency: Currency

    def __post_init__(self) -> None:
        if (
            not isinstance(self.symbol, str)
            or re.fullmatch(r"[A-Z0-9][A-Z0-9.^:/-]{0,31}", self.symbol) is None
            or (self.market, self.currency) not in (("US", "USD"), ("UK", "GBP"))
        ):
            raise PortfolioInputError("Explicit US/USD or UK/GBP instrument required.")


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < 0
        or (positive and value == 0)
        or len(value.as_tuple().digits) > 28
        or not -12 <= value.as_tuple().exponent <= 12
    ):
        raise PortfolioInputError(
            f"{field} must be a finite {'positive' if positive else 'nonnegative'} "
            "Decimal (maximum 28 digits, exponent -12..12); floats are rejected."
        )
    return value


def _quantity(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 10**12:
        raise PortfolioInputError("quantity must be positive whole shares (integer).")
    return value


def _currency(value: object) -> Currency:
    if value not in ("GBP", "USD") or not isinstance(value, str):
        raise PortfolioInputError("Cash currency must be explicitly GBP or USD.")
    return value


def utc_timestamp(value: object) -> datetime:
    """Require an aware timestamp and return UTC; do not assume local time."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PortfolioInputError("Timestamp must be timezone-aware.")
    try:
        if value.utcoffset() is None:
            raise PortfolioInputError("Timestamp must be timezone-aware.")
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise PortfolioInputError("Timestamp cannot be represented as UTC.") from None


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > 2000
        or any(ord(char) < 32 for char in value)
    ):
        raise PortfolioInputError(f"{field} must be nonempty text without controls.")
    return value


def _id(value: object) -> str:
    try:
        if not isinstance(value, str) or UUID(value).hex != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise PortfolioInputError(
            "event_id must be a lowercase UUID hex string."
        ) from None
    return value


def price_in_cash(price: Decimal, unit: PriceUnit, currency: Currency) -> Decimal:
    """Convert explicitly declared GBp exactly once; never infer units from ticker."""
    _decimal(price, "price", positive=True)
    _currency(currency)
    if (unit, currency) not in (("GBp", "GBP"), ("GBP", "GBP"), ("USD", "USD")):
        raise PortfolioInputError("Price unit does not match settlement currency.")
    with localcontext(_CALCULATION_CONTEXT):
        return price / Decimal(100) if unit == "GBp" else price


@dataclass(frozen=True)
class OpeningHolding:
    effective_at: datetime
    instrument: Instrument
    quantity: int
    total_cost: Decimal
    acquired_on: date
    source: str


@dataclass(frozen=True)
class CashOpening:
    effective_at: datetime
    currency: Currency
    amount: Decimal
    source: str


@dataclass(frozen=True)
class Buy:
    effective_at: datetime
    instrument: Instrument
    quantity: int
    price: Decimal
    price_unit: PriceUnit
    fee: Decimal
    fee_currency: Currency
    source: str


@dataclass(frozen=True)
class Sell:
    effective_at: datetime
    instrument: Instrument
    quantity: int
    price: Decimal
    price_unit: PriceUnit
    fee: Decimal
    fee_currency: Currency
    source: str


@dataclass(frozen=True)
class Fee:
    effective_at: datetime
    currency: Currency
    amount: Decimal
    source: str
    related_event_id: str | None = None


@dataclass(frozen=True)
class CashDeposit:
    effective_at: datetime
    currency: Currency
    amount: Decimal
    source: str


@dataclass(frozen=True)
class CashWithdrawal:
    effective_at: datetime
    currency: Currency
    amount: Decimal
    source: str


@dataclass(frozen=True)
class CashExchange:
    """Explicit cash exchange; shared FX direction is multiplied, never inverted."""

    effective_at: datetime
    amount: Decimal
    fx_rate: FxRate
    fee: Decimal
    fee_currency: Currency
    source: str


@dataclass(frozen=True)
class Correction:
    effective_at: datetime
    target_event_id: str
    reason: str
    source: str


PortfolioEvent = (
    OpeningHolding
    | CashOpening
    | Buy
    | Sell
    | Fee
    | CashDeposit
    | CashWithdrawal
    | CashExchange
    | Correction
)


def validate_event(event: PortfolioEvent) -> None:
    """Validate declarations without rounding, inferred amounts or synthetic trades."""
    if type(event) not in (
        OpeningHolding,
        CashOpening,
        Buy,
        Sell,
        Fee,
        CashDeposit,
        CashWithdrawal,
        CashExchange,
        Correction,
    ):
        raise PortfolioInputError("Unsupported portfolio event.")
    if utc_timestamp(event.effective_at) != event.effective_at:
        raise PortfolioInputError("Invalid event timestamp.")
    _text(event.source, "source")
    if isinstance(event, CashExchange):
        _decimal(event.amount, "amount", positive=True)
        _decimal(event.fee, "fee")
        if not isinstance(event.fx_rate, FxRate):
            raise PortfolioInputError("CashExchange requires shared explicit FxRate.")
        _decimal(event.fx_rate.rate, "FX rate", positive=True)
        if (
            event.fee_currency
            not in (
                event.fx_rate.from_currency,
                event.fx_rate.to_currency,
            )
            or event.fx_rate.as_of > utc_timestamp(event.effective_at)
            or event.fx_rate.as_of > event.fx_rate.provenance.declared_at
        ):
            raise PortfolioInputError("Exchange rate time or fee currency is invalid.")
        return
    if isinstance(event, Correction):
        _id(event.target_event_id)
        _text(event.reason, "reason")
        return
    if isinstance(event, (OpeningHolding, Buy, Sell)):
        if not isinstance(event.instrument, Instrument):
            raise PortfolioInputError("Explicit Instrument required.")
        _quantity(event.quantity)
        if isinstance(event, OpeningHolding):
            _decimal(event.total_cost, "total_cost")
            if (
                type(event.acquired_on) is not date
                or event.acquired_on > utc_timestamp(event.effective_at).date()
            ):
                raise PortfolioInputError("acquired_on must be a declared past date.")
        else:
            price_in_cash(event.price, event.price_unit, event.instrument.currency)
            _decimal(event.fee, "fee")
            if event.fee_currency != event.instrument.currency:
                raise PortfolioInputError(
                    "Fees must use the same settlement cash currency."
                )
        return
    _currency(event.currency)
    _decimal(event.amount, "amount", positive=not isinstance(event, CashOpening))
    if isinstance(event, Fee) and event.related_event_id is not None:
        _id(event.related_event_id)


@dataclass(frozen=True)
class LedgerEntry:
    event_id: str
    recorded_at: datetime
    event: PortfolioEvent
    provenance: Literal["user-declared"] = "user-declared"


@dataclass(frozen=True)
class Lot:
    event_id: str
    instrument: Instrument
    acquired_on: date
    quantity: int
    remaining_cost: Decimal


@dataclass(frozen=True)
class FIFOAllocation:
    opening_event_id: str
    quantity: int
    cost: Decimal


@dataclass(frozen=True)
class RealizedSale:
    event_id: str
    instrument: Instrument
    quantity: int
    proceeds: Decimal
    cost: Decimal
    realized: Decimal
    allocations: tuple[FIFOAllocation, ...]


@dataclass(frozen=True)
class LedgerState:
    """Native cash and cost remain independently available when valuation is missing."""

    cash: Mapping[Currency, Decimal]
    lots: tuple[Lot, ...]
    sales: tuple[RealizedSale, ...]
    realized: Mapping[Currency, Decimal]
    standalone_fees: Mapping[Currency, Decimal]
    last_effective_at: datetime | None


def replay(entries: Iterable[LedgerEntry]) -> LedgerState:
    """Replay append order, rejecting oversells, cash deficits and unsafe corrections.

    FIFO is acquisition-date order, with append order breaking same-date ties.
    A linked Fee must follow its trade before any further trade in that instrument:
    buy fees enter the remaining lot cost; sell fees reduce that sale's proceeds.
    """
    with localcontext(_CALCULATION_CONTEXT):
        return _replay(tuple(entries))


def _replay(entries: tuple[LedgerEntry, ...]) -> LedgerState:
    known: dict[str, LedgerEntry] = {}
    voided: set[str] = set()
    last_time: datetime | None = None
    last_recorded: datetime | None = None
    for entry in entries:
        _id(entry.event_id)
        validate_event(entry.event)
        recorded = utc_timestamp(entry.recorded_at)
        effective = utc_timestamp(entry.event.effective_at)
        if entry.provenance != "user-declared" or entry.event_id in known:
            raise PortfolioInputError("Duplicate UUID or invalid provenance.")
        if recorded < effective:
            raise PortfolioInputError(
                "Events cannot be declared before their effective time."
            )
        if (last_time is not None and effective < last_time) or (
            last_recorded is not None and recorded < last_recorded
        ):
            raise PortfolioInputError(
                "Event and recording chronology must be nondecreasing."
            )
        if isinstance(entry.event, Correction):
            target = known.get(entry.event.target_event_id)
            if (
                target is None
                or isinstance(target.event, Correction)
                or target.event_id in voided
            ):
                raise PortfolioInputError(
                    "Correction needs an active earlier non-correction event."
                )
            voided.add(target.event_id)
        known[entry.event_id] = entry
        last_time, last_recorded = effective, recorded

    cash: dict[Currency, Decimal] = {}
    lots: list[Lot] = []
    sales: list[RealizedSale] = []
    fees: dict[Currency, Decimal] = {"GBP": Decimal(0), "USD": Decimal(0)}
    opened: set[Instrument] = set()
    traded: set[Instrument] = set()
    active: dict[str, PortfolioEvent] = {}
    last_trade: dict[Instrument, str] = {}

    def move(currency: Currency, amount: Decimal) -> None:
        if currency not in cash:
            raise PortfolioInputError(
                f"Explicit CashOpening for {currency} is required."
            )
        balance = cash[currency] + amount
        if balance < 0:
            raise PortfolioInputError(f"Insufficient {currency} cash.")
        cash[currency] = balance

    for entry in entries:
        event = entry.event
        if entry.event_id in voided or isinstance(event, Correction):
            continue
        active[entry.event_id] = event
        if isinstance(event, CashOpening):
            if event.currency in cash:
                raise PortfolioInputError(
                    "Only one active CashOpening per currency is allowed."
                )
            cash[event.currency] = event.amount
        elif isinstance(event, OpeningHolding):
            if event.instrument in opened or event.instrument in traded:
                raise PortfolioInputError(
                    "Opening holding must be declared once before trades."
                )
            opened.add(event.instrument)
            lots.append(
                Lot(
                    entry.event_id,
                    event.instrument,
                    event.acquired_on,
                    event.quantity,
                    event.total_cost,
                )
            )
        elif isinstance(event, CashDeposit):
            move(event.currency, event.amount)
        elif isinstance(event, CashWithdrawal):
            move(event.currency, -event.amount)
        elif isinstance(event, CashExchange):
            if event.fx_rate.provenance.declared_at > utc_timestamp(entry.recorded_at):
                raise PortfolioInputError(
                    "FX cannot be declared after event recording."
                )
            move(event.fx_rate.from_currency, -event.amount)
            converted = event.fx_rate.convert(
                FinanceMoney(event.amount, event.fx_rate.from_currency)
            )
            move(converted.currency, converted.amount)
            move(event.fee_currency, -event.fee)
            fees[event.fee_currency] += event.fee
        elif isinstance(event, (Buy, Sell)):
            traded.add(event.instrument)
            last_trade[event.instrument] = entry.event_id
            gross = (
                price_in_cash(event.price, event.price_unit, event.instrument.currency)
                * event.quantity
            )
            if isinstance(event, Buy):
                cost = gross + event.fee
                move(event.instrument.currency, -cost)
                lots.append(
                    Lot(
                        entry.event_id,
                        event.instrument,
                        utc_timestamp(event.effective_at).date(),
                        event.quantity,
                        cost,
                    )
                )
            else:
                available = sum(
                    lot.quantity for lot in lots if lot.instrument == event.instrument
                )
                if event.quantity > available:
                    raise PortfolioInputError(
                        "Oversell rejected; short positions are forbidden."
                    )
                remaining = event.quantity
                allocations: list[FIFOAllocation] = []
                order = sorted(
                    (
                        index
                        for index, lot in enumerate(lots)
                        if lot.instrument == event.instrument
                    ),
                    key=lambda index: (lots[index].acquired_on, index),
                )
                for index in order:
                    if remaining == 0:
                        break
                    lot = lots[index]
                    take = min(remaining, lot.quantity)
                    # Consume the exact residual on final closure (no stranded penny).
                    allocated = (
                        lot.remaining_cost
                        if take == lot.quantity
                        else lot.remaining_cost * take / lot.quantity
                    )
                    allocations.append(FIFOAllocation(lot.event_id, take, allocated))
                    lots[index] = Lot(
                        lot.event_id,
                        lot.instrument,
                        lot.acquired_on,
                        lot.quantity - take,
                        lot.remaining_cost - allocated,
                    )
                    remaining -= take
                cost = sum((part.cost for part in allocations), Decimal(0))
                proceeds = gross - event.fee
                move(event.instrument.currency, proceeds)
                sales.append(
                    RealizedSale(
                        entry.event_id,
                        event.instrument,
                        event.quantity,
                        proceeds,
                        cost,
                        proceeds - cost,
                        tuple(allocations),
                    )
                )
        elif isinstance(event, Fee):
            if event.related_event_id is None:
                move(event.currency, -event.amount)
                fees[event.currency] += event.amount
                continue
            trade = active.get(event.related_event_id)
            if (
                not isinstance(trade, (Buy, Sell))
                or trade.instrument.currency != event.currency
                or last_trade.get(trade.instrument) != event.related_event_id
            ):
                raise PortfolioInputError(
                    "Linked fee requires latest active same-currency trade."
                )
            move(event.currency, -event.amount)
            if isinstance(trade, Buy):
                for index, lot in enumerate(lots):
                    if lot.event_id == event.related_event_id:
                        lots[index] = Lot(
                            lot.event_id,
                            lot.instrument,
                            lot.acquired_on,
                            lot.quantity,
                            lot.remaining_cost + event.amount,
                        )
                        break
            else:
                for index, sale in enumerate(sales):
                    if sale.event_id == event.related_event_id:
                        sales[index] = RealizedSale(
                            sale.event_id,
                            sale.instrument,
                            sale.quantity,
                            sale.proceeds - event.amount,
                            sale.cost,
                            sale.realized - event.amount,
                            sale.allocations,
                        )
                        break

    from types import MappingProxyType

    realized: dict[Currency, Decimal] = {"GBP": Decimal(0), "USD": Decimal(0)}
    for sale in sales:
        realized[sale.instrument.currency] += sale.realized
    return LedgerState(
        MappingProxyType(cash),
        tuple(lot for lot in lots if lot.quantity),
        tuple(sales),
        MappingProxyType(realized),
        MappingProxyType(fees),
        last_time,
    )


@dataclass(frozen=True)
class HoldingSnapshot:
    instrument: Instrument
    quantity: int
    cost: Decimal
    market_value: Decimal | None
    unrealized: Decimal | None
    cost_gbp: Decimal | None
    market_value_gbp: Decimal | None
    unrealized_gbp: Decimal | None
    quote: PriceQuote | None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """GBP figures use the declared snapshot FX, NOT historical transaction FX.

    This is portfolio accounting, not tax-basis reporting or FX return attribution.
    Missing/stale observations yield None, never zero or a partial overall total.
    Standalone fees are reported separately from FIFO realized sale P&L.
    """

    as_of: datetime
    readiness: DecisionReadiness
    holdings: tuple[HoldingSnapshot, ...]
    cash: Mapping[Currency, Decimal]
    cash_gbp: Mapping[Currency, Decimal | None]
    realized: Mapping[Currency, Decimal]
    standalone_fees: Mapping[Currency, Decimal]
    cash_total_gbp: Decimal | None
    holdings_total_gbp: Decimal | None
    equity_total_gbp: Decimal | None
    cost_total_gbp: Decimal | None
    unrealized_total_gbp: Decimal | None
    realized_total_gbp: Decimal | None
    standalone_fees_total_gbp: Decimal | None
    fx_rates: tuple[FxRate, ...]


def snapshot(
    entries: Iterable[LedgerEntry],
    *,
    quotes: Mapping[Instrument, PriceQuote],
    fx_rates: Iterable[FxRate],
    as_of: datetime,
    max_age: timedelta,
) -> PortfolioSnapshot:
    """Value manually supplied observations with an explicit freshness tolerance.

    Quote keys are user-declared instruments. Source/as-of data are retained; a
    source label never verifies a company, split history or provider acceptance.
    Only an explicit USD->GBP rate is usable; reverse rates are not inverted.
    """
    from types import MappingProxyType

    as_of = utc_timestamp(as_of)
    if not isinstance(max_age, timedelta) or max_age < timedelta(0):
        raise PortfolioInputError("Explicit nonnegative max_age timedelta required.")
    records = tuple(entries)
    state = replay(records)
    if state.last_effective_at is not None and as_of < state.last_effective_at:
        raise PortfolioInputError("Snapshot as_of cannot precede ledger events.")
    if any(as_of < utc_timestamp(entry.recorded_at) for entry in records):
        raise PortfolioInputError(
            "Snapshot as_of cannot precede declaration recording."
        )
    rates = tuple(fx_rates)
    reasons: list[str] = []
    rate: FxRate | None = None

    def fresh(observed: datetime, declared: datetime) -> bool:
        return (
            observed <= declared <= as_of
            and observed <= as_of
            and as_of - observed <= max_age
        )

    for candidate in rates:
        if not isinstance(candidate, FxRate):
            raise PortfolioInputError("fx_rates must contain shared FxRate objects.")
        _decimal(candidate.rate, "FX rate", positive=True)
        if (candidate.from_currency, candidate.to_currency) == ("USD", "GBP"):
            if rate is not None:
                raise PortfolioInputError(
                    "Ambiguous duplicate USD->GBP FX observations."
                )
            rate = candidate
    if rate is not None and not fresh(rate.as_of, rate.provenance.declared_at):
        reasons.append("USD->GBP FX is stale or future-dated.")
        rate = None
    usd_needed = (
        state.cash.get("USD", Decimal(0)) != 0
        or any(lot.instrument.currency == "USD" for lot in state.lots)
        or any(sale.instrument.currency == "USD" for sale in state.sales)
        or state.standalone_fees["USD"] != 0
    )
    if usd_needed and rate is None:
        reasons.append("Explicit current USD->GBP FX is missing.")
    for currency in ("GBP", "USD"):
        if currency not in state.cash:
            reasons.append(f"Explicit {currency} cash opening is missing.")

    with localcontext(_CALCULATION_CONTEXT):

        def gbp(amount: Decimal, currency: Currency) -> Decimal | None:
            if currency == "GBP":
                return amount
            if amount == 0:
                return Decimal(
                    0
                )  # A known zero needs no FX; unknown values never enter here.
            if rate is None:
                return None
            return rate.convert(FinanceMoney(amount, currency)).amount

        grouped: dict[Instrument, tuple[int, Decimal]] = {}
        for lot in state.lots:
            quantity, cost = grouped.get(lot.instrument, (0, Decimal(0)))
            grouped[lot.instrument] = (
                quantity + lot.quantity,
                cost + lot.remaining_cost,
            )
        holdings: list[HoldingSnapshot] = []
        for instrument, (quantity, cost) in grouped.items():
            quote = quotes.get(instrument)
            value: Decimal | None = None
            if quote is None:
                reasons.append(
                    f"{instrument.market}:{instrument.symbol} price is missing."
                )
            elif not isinstance(quote, PriceQuote):
                raise PortfolioInputError(
                    "quotes must contain shared PriceQuote objects."
                )
            elif not fresh(quote.as_of, quote.provenance.declared_at):
                reasons.append(
                    f"{instrument.market}:{instrument.symbol} price is stale/future."
                )
            else:
                _decimal(quote.amount, "quote price", positive=True)
                money = quote.per_share_money()
                if money.currency != instrument.currency:
                    reasons.append(
                        f"{instrument.market}:{instrument.symbol} price currency mismatch."
                    )
                else:
                    value = money.amount * quantity
            unrealized = None if value is None else value - cost
            holdings.append(
                HoldingSnapshot(
                    instrument,
                    quantity,
                    cost,
                    value,
                    unrealized,
                    gbp(cost, instrument.currency),
                    None if value is None else gbp(value, instrument.currency),
                    None
                    if unrealized is None
                    else gbp(unrealized, instrument.currency),
                    quote,
                )
            )

        def total(values: Iterable[Decimal | None]) -> Decimal | None:
            parts = tuple(values)
            if any(part is None for part in parts):
                return None
            return sum((part for part in parts if part is not None), Decimal(0))

        cash_gbp: dict[Currency, Decimal | None] = {
            currency: gbp(amount, currency) for currency, amount in state.cash.items()
        }
        cash_total = total(cash_gbp.values()) if len(state.cash) == 2 else None
        holdings_total = total(holding.market_value_gbp for holding in holdings)
        equity = (
            None
            if cash_total is None or holdings_total is None
            else cash_total + holdings_total
        )
        cost_total = total(holding.cost_gbp for holding in holdings)
        unrealized_total = total(holding.unrealized_gbp for holding in holdings)
        realized_total = total(
            gbp(amount, currency) for currency, amount in state.realized.items()
        )
        fee_total = total(
            gbp(amount, currency) for currency, amount in state.standalone_fees.items()
        )
        # Keep shared readiness bounded without discarding the overall gap signal.
        if len(reasons) > 20:
            reasons = reasons[:19] + [
                "Additional portfolio observations are incomplete."
            ]
        readiness = DecisionReadiness(
            DecisionStatus.INCOMPLETE if reasons else DecisionStatus.COMPLETE,
            tuple(reasons),
        )
        return PortfolioSnapshot(
            as_of,
            readiness,
            tuple(holdings),
            state.cash,
            MappingProxyType(cash_gbp),
            state.realized,
            state.standalone_fees,
            cash_total,
            holdings_total,
            equity,
            cost_total,
            unrealized_total,
            realized_total,
            fee_total,
            rates,
        )
