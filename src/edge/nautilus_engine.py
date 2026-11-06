"""NautilusTrader v2.0.0rc8 adapter for the deterministic EDGE simulation fixture.

This module intentionally supports only the local, synthetic ``EDGE_TEST.SIM``
instrument and never configures a live execution client.
"""

from __future__ import annotations

from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version

from edge.domain import Dataset, EngineOutcome, SimulationConfig
from edge.validation import dataset_document, validate_config, validate_dataset

NAUTILUS_VERSION = "2.0.0rc8"
_VENUE = "SIM"
_STRATEGY_ID = "EDGE-QUOTE-ACCEPTANCE"


def _require_supported_version() -> str:
    """Fail closed unless the exact release-candidate API has been installed."""
    try:
        installed = version("nautilus_trader")
    except PackageNotFoundError as error:
        raise RuntimeError(
            "NautilusTrader is not installed; use the isolated simulation environment."
        ) from error
    if installed != NAUTILUS_VERSION:
        raise RuntimeError(
            f"Nautilus version acceptance failed: expected {NAUTILUS_VERSION}, found {installed}."
        )
    return installed


def _decimal_text(value: Decimal) -> str:
    """Convert native Decimal-like values without introducing float arithmetic."""
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def run_simulation(dataset: Dataset, config: SimulationConfig) -> EngineOutcome:
    """Run a quote-driven round trip or native risk-engine rejection.

    The strategy observes only the quote currently delivered to its callback.
    In the round-trip scenario it submits a buy on the first quote and a sell
    on the second; the simulator's quote-aware matching model determines fills.
    """
    engine_version = _require_supported_version()
    dataset = validate_dataset(dataset_document(dataset))
    validate_config(config, dataset)

    # Imports are deferred so importing the application package does not
    # require the optional simulation wheel.
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LogLevel, LoggerConfig
    from nautilus_trader.execution import FixedFeeModel
    from nautilus_trader.model import (
        AccountType,
        BookType,
        Currency,
        Equity,
        InstrumentId,
        Money,
        OmsType,
        OrderDenied,
        OrderFilled,
        OrderRejected,
        OrderSide,
        Price,
        PositionClosed,
        PositionOpened,
        Quantity,
        QuoteTick,
        StrategyId,
        Symbol,
        TimeInForce,
        Venue,
    )
    from nautilus_trader.risk import RiskEngineConfig
    from nautilus_trader.trading import Strategy, StrategyConfig

    instrument_id = InstrumentId.from_str(dataset.instrument_id)
    currency = Currency.from_str(dataset.currency)
    first_timestamp = dataset.quotes[0].timestamp_ns
    instrument = Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol("EDGE_TEST"),
        currency=currency,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        ts_event=first_timestamp,
        ts_init=first_timestamp,
    )
    ticks = [
        QuoteTick(
            instrument_id=instrument_id,
            bid_price=Price.from_str(str(quote.bid)),
            ask_price=Price.from_str(str(quote.ask)),
            bid_size=Quantity.from_int(quote.bid_size),
            ask_size=Quantity.from_int(quote.ask_size),
            ts_event=quote.timestamp_ns,
            ts_init=quote.timestamp_ns,
        )
        for quote in dataset.quotes
    ]

    commission = Money.from_decimal_exact(config.commission_per_order, currency)
    fee_model = FixedFeeModel(commission, charge_commission_once=True)
    # The cap deliberately sits below the first quote notional; this is a
    # Nautilus risk-engine policy, not an EDGE-side synthetic rejection.
    risk_config = (
        RiskEngineConfig(max_notional_per_order={dataset.instrument_id: "1"})
        if config.scenario == "risk-rejection"
        else None
    )
    engine = BacktestEngine(
        BacktestEngineConfig(
            risk_engine=risk_config,
            bypass_logging=True,
            logging=LoggerConfig(
                stdout_level=LogLevel.OFF,
                fileout_level=LogLevel.OFF,
                bypass_logging=True,
                print_config=False,
            ),
        )
    )

    class QuoteStrategy(Strategy):
        """Submit fixture orders from quote callbacks and capture native events."""

        def __init__(self) -> None:
            super().__init__(StrategyConfig(strategy_id=StrategyId(_STRATEGY_ID)))
            self.quote_count = 0
            self.orders: list[dict[str, object]] = []
            self.fills: list[dict[str, object]] = []
            self.rejections: list[dict[str, object]] = []
            self.positions: dict[str, dict[str, object]] = {}
            self._filled_quantity_by_order: dict[str, Decimal] = {}

        def on_start(self) -> None:
            self.subscribe_quotes(instrument_id)

        def on_quote(self, quote: QuoteTick) -> None:
            self.quote_count += 1
            if self.quote_count == 1:
                self._submit(quote, OrderSide.BUY)
            elif config.scenario == "round-trip" and self.quote_count == 2:
                self._submit(quote, OrderSide.SELL)

        def _submit(self, quote: QuoteTick, side: OrderSide) -> None:
            order = self.order_factory.market(
                instrument_id=instrument_id,
                order_side=side,
                quantity=Quantity.from_int(config.quantity),
                time_in_force=TimeInForce.IOC,
            )
            order_id = str(order.client_order_id)
            self.orders.append(
                {
                    "order_id": order_id,
                    "instrument_id": dataset.instrument_id,
                    "side": side.name.lower(),
                    "quantity": str(config.quantity),
                    "submitted_at_ns": int(quote.ts_event),
                    "status": "submitted",
                }
            )
            self.submit_order(order)

        def on_order_filled(self, event: OrderFilled) -> None:
            order_id = str(event.client_order_id)
            filled_quantity = self._filled_quantity_by_order.get(order_id, Decimal("0"))
            filled_quantity += Decimal(str(event.last_qty))
            self._filled_quantity_by_order[order_id] = filled_quantity
            for order in self.orders:
                if order["order_id"] == order_id:
                    order["filled_quantity"] = format(filled_quantity, "f")
                    if filled_quantity >= Decimal(str(order["quantity"])):
                        order["status"] = "filled"
                        order["filled_at_ns"] = int(event.ts_event)
                    else:
                        order["status"] = "partially_filled"
            self.fills.append(
                {
                    "order_id": order_id,
                    "trade_id": str(event.trade_id),
                    "instrument_id": str(event.instrument_id),
                    "side": event.order_side.name.lower(),
                    "quantity": str(event.last_qty),
                    "price": str(event.last_px),
                    "fee": _decimal_text(event.commission.as_decimal())
                    if event.commission is not None
                    else "0",
                    "currency": event.currency.code,
                    "filled_at_ns": int(event.ts_event),
                    "position_id": str(event.position_id)
                    if event.position_id is not None
                    else None,
                }
            )

        def on_order_denied(self, event: OrderDenied) -> None:
            self._record_rejection(event)

        def on_order_rejected(self, event: OrderRejected) -> None:
            self._record_rejection(event)

        def _record_rejection(self, event: OrderDenied | OrderRejected) -> None:
            order_id = str(event.client_order_id)
            self.rejections.append(
                {
                    "order_id": order_id,
                    "instrument_id": str(event.instrument_id),
                    "reason": str(event.reason),
                    "rejected_at_ns": int(event.ts_event),
                    "native_event": type(event).__name__,
                }
            )
            for order in self.orders:
                if order["order_id"] == order_id:
                    order["status"] = "denied" if type(event).__name__ == "OrderDenied" else "rejected"

        def on_position_opened(self, event: PositionOpened) -> None:
            self.positions[str(event.position_id)] = {
                "position_id": str(event.position_id),
                "instrument_id": str(event.instrument_id),
                "quantity": str(event.quantity),
                "side": event.side.name.lower(),
                "opened_at_ns": int(event.ts_event),
                "status": "open",
            }

        def on_position_closed(self, event: PositionClosed) -> None:
            position = self.positions.get(str(event.position_id))
            if position is not None:
                position["status"] = "closed"
                position["closed_at_ns"] = int(event.ts_event)
                position["opening_quantity"] = position["quantity"]
                position["quantity"] = str(event.quantity)
                position["signed_quantity"] = _decimal_text(event.signed_qty)

    strategy = QuoteStrategy()

    try:
        engine.add_venue(
            venue=Venue(_VENUE),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money.from_decimal_exact(config.initial_cash, currency)],
            base_currency=currency,
            fee_model=fee_model,
            book_type=BookType.L1_MBP,
            allow_cash_borrowing=False,
            use_market_order_acks=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(ticks)
        engine.add_strategy(strategy)
        engine.run()

        account = engine.cache.account_for_venue(Venue(_VENUE))
        if account is None:
            raise RuntimeError("Nautilus completed without a simulated venue account.")
        balance = account.balance_total(currency)
        if balance is None:
            raise RuntimeError("Nautilus simulated account has no USD balance.")
        ending_cash = _decimal_text(balance.as_decimal())

        realized = engine.portfolio.realized_pnl(
            instrument_id, account.id, currency
        )
        if realized is None:
            if config.scenario != "risk-rejection" or strategy.fills:
                raise RuntimeError(
                    "Nautilus returned no realized P&L for a simulation that executed fills."
                )
            # A risk-rejected scenario has no positions or fills; zero is the
            # documented no-trade result rather than an absent-value fallback.
            realized_pnl = "0.00"
        else:
            realized_pnl = _decimal_text(realized.as_decimal())
        ending_quantity = _decimal_text(
            engine.portfolio.net_position(instrument_id, account.id)
        )

        if config.scenario == "round-trip" and (
            len(strategy.fills) != 2
            or any(order["status"] != "filled" for order in strategy.orders)
        ):
            raise RuntimeError(
                "The quote round trip did not produce two complete native fills for "
                f"quantity {config.quantity}; got {len(strategy.fills)} fill events."
            )
        if config.scenario == "risk-rejection" and (
            strategy.fills or not strategy.rejections
        ):
            raise RuntimeError(
                "Expected a native Nautilus risk rejection and zero fills for risk-rejection."
            )

        assumptions = (
            "All input quotes are explicitly synthetic and describe fictional EDGE_TEST.SIM; "
            "they are not real, delayed, or live market data.",
            "The strategy submits only from quote callbacks and never reads a later quote; "
            "market orders use the simulator's contemporaneous quote-aware execution.",
            "Native Nautilus simulated-exchange matching is used; there is no second fill engine.",
            "Native FixedFeeModel charges the configured fixed commission once per completed order.",
            "No latency, queue position, market impact, slippage, or corporate actions are modeled.",
            "The risk-rejection scenario uses Nautilus RiskEngineConfig max_notional_per_order "
            "and requires no fills.",
        )
        return EngineOutcome(
            engine_version=engine_version,
            ending_cash=ending_cash,
            realized_pnl=realized_pnl,
            ending_quantity=ending_quantity,
            orders=tuple(strategy.orders),
            fills=tuple(strategy.fills),
            positions=tuple(strategy.positions.values()),
            rejections=tuple(strategy.rejections),
            assumptions=assumptions,
        )
    finally:
        engine.dispose()
