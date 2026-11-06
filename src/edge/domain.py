"""Validated, engine-independent simulation contracts."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Quote:
    timestamp_ns: int
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int


@dataclass(frozen=True)
class Dataset:
    instrument_id: str
    company_name: str
    currency: str
    price_unit: str
    source: str
    retrieved_at: str
    synthetic: bool
    corporate_action_status: str
    quotes: tuple[Quote, ...]
    checksum: str


@dataclass(frozen=True)
class SimulationConfig:
    scenario: Literal["round-trip", "risk-rejection"] = "round-trip"
    initial_cash: Decimal = Decimal("10000.00")
    quantity: int = 10
    commission_per_order: Decimal = Decimal("1.00")


@dataclass(frozen=True)
class EngineOutcome:
    """Engine facts only; narrative and persistence belong outside this contract."""

    engine_version: str
    ending_cash: str
    realized_pnl: str
    ending_quantity: str
    orders: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    rejections: tuple[dict[str, object], ...]
    assumptions: tuple[str, ...]
