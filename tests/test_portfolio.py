"""Offline deterministic financial oracles; no provider data or live user files."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

import pytest

from edge.decision_contracts import (
    DecisionStatus,
    FxRate,
    ManualProvenance,
    PriceQuote,
)
from edge.portfolio import (
    Buy,
    CashDeposit,
    CashExchange,
    CashOpening,
    CashWithdrawal,
    Correction,
    Fee,
    Instrument,
    LedgerEntry,
    OpeningHolding,
    PortfolioInputError,
    Sell,
    price_in_cash,
    replay,
    snapshot,
    validate_event,
)

D = Decimal
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
US = Instrument("ACME", "US", "USD")
UK = Instrument("TEST.L", "UK", "GBP")
SOURCE = "explicit test declaration"
PROVENANCE = ManualProvenance(SOURCE, NOW)


def entries(*events):
    return tuple(LedgerEntry(uuid4().hex, NOW, event) for event in events)


def openings(gbp="10000", usd="10000"):
    return (
        CashOpening(NOW, "GBP", D(gbp), SOURCE),
        CashOpening(NOW, "USD", D(usd), SOURCE),
    )


def buy(quantity=10, price="100", fee="5", instrument=US, unit="USD"):
    return Buy(
        NOW, instrument, quantity, D(price), unit, D(fee), instrument.currency, SOURCE
    )


def sell(quantity=12, price="130", fee="6", instrument=US, unit="USD"):
    return Sell(
        NOW, instrument, quantity, D(price), unit, D(fee), instrument.currency, SOURCE
    )


def fx(rate="0.8", source="USD", target="GBP", as_of=NOW):
    return FxRate(source, target, D(rate), as_of, PROVENANCE)


def quote(amount="150", unit="USD", as_of=NOW):
    return PriceQuote(D(amount), unit, as_of, PROVENANCE)


def value(records, quotes=None, rates=()):
    return snapshot(
        records,
        quotes={} if quotes is None else quotes,
        fx_rates=rates,
        as_of=NOW,
        max_age=timedelta(days=1),
    )


def test_fifo_repeated_buy_fees_partial_close_and_exact_totals():
    records = entries(*openings(), buy(), buy(5, "120"), sell())
    state = replay(records)
    assert state.cash == {"GBP": D("10000"), "USD": D("9944")}
    sale = state.sales[0]
    assert sale.proceeds == D("1554")
    assert sale.cost == D("1247")
    assert sale.realized == D("307")
    assert [part.cost for part in sale.allocations] == [D("1005"), D("242")]
    assert state.lots[0].quantity == 3
    assert state.lots[0].remaining_cost == D("363")
    result = value(records, {US: quote()}, (fx(),))
    assert result.readiness.status == DecisionStatus.COMPLETE
    assert result.holdings[0].unrealized == D("87")
    assert result.holdings[0].unrealized_gbp == D("69.6")
    assert result.cash_total_gbp == D("17955.2")
    assert result.holdings_total_gbp == D("360")
    assert result.equity_total_gbp == D("18315.2")
    assert result.realized_total_gbp == D("245.6")
    assert result.holdings[0].quote.provenance.reference == SOURCE
    assert replay(records) == replay(records)
    with pytest.raises(TypeError):
        state.cash["GBP"] = D("1")


def test_same_price_repeated_buys_are_separate_lots():
    records = entries(*openings(), buy(), buy())
    state = replay(records)
    assert len(state.lots) == 2
    assert state.lots[0].event_id != state.lots[1].event_id
    assert state.cash["USD"] == D("7990")


def test_opening_holding_declared_basis_no_fabricated_purchase_or_cash_flow():
    opening = OpeningHolding(NOW, US, 10, D("900"), date(2025, 1, 1), SOURCE)
    state = replay(entries(*openings(), opening, buy(5, "120"), sell()))
    assert state.cash["USD"] == D("10949")
    assert state.sales[0].cost == D("1142")
    assert state.sales[0].realized == D("412")
    assert state.lots[0].remaining_cost == D("363")
    assert state.lots[0].quantity == 3


def test_opening_zero_basis_is_explicit_not_assumed():
    opening = OpeningHolding(NOW, UK, 2, D("0"), date(2026, 1, 1), SOURCE)
    state = replay(entries(*openings(), opening))
    assert state.lots[0].remaining_cost == 0
    with pytest.raises(PortfolioInputError):
        validate_event(replace(opening, total_cost=None))


def test_acquisition_date_fifo_not_symbol_or_uuid_sorting():
    opening = OpeningHolding(NOW, US, 2, D("30"), date(2020, 1, 1), SOURCE)
    state = replay(
        entries(*openings(), opening, buy(2, "100", "0"), sell(3, "110", "0"))
    )
    assert state.sales[0].cost == D("130")
    assert state.lots[0].remaining_cost == D("100")


def test_partial_fifo_division_consumes_entire_residual_deterministically():
    records = entries(
        *openings(),
        OpeningHolding(NOW, US, 3, D("1"), date(2025, 1, 1), SOURCE),
        sell(1, "1", "0"),
        sell(1, "1", "0"),
        sell(1, "1", "0"),
    )
    with localcontext() as context:
        context.prec = 8
        first = replay(records)
        context.prec = 35
        second = replay(records)
    assert first == second
    assert first.lots == ()
    with localcontext() as context:
        context.prec = 80
        assert sum((sale.cost for sale in first.sales), D(0)) == D(1)
        assert sum((sale.realized for sale in first.sales), D(0)) == D(2)


@pytest.mark.parametrize("quantity", [0, -1, 1.0, D("1"), True, "1", 10**13])
def test_quantities_require_positive_integer_whole_shares(quantity):
    with pytest.raises(PortfolioInputError):
        validate_event(replace(buy(), quantity=quantity))


@pytest.mark.parametrize(
    "price", [1.0, 1, "1", D("NaN"), D("Infinity"), D("0"), D("-1")]
)
def test_prices_never_coerce_float_or_nondecimal(price):
    with pytest.raises(PortfolioInputError):
        validate_event(replace(buy(), price=price))


@pytest.mark.parametrize("fee", [0.0, 0, "0", D("NaN"), D("-1")])
def test_fees_explicit_decimal_nonnegative(fee):
    with pytest.raises(PortfolioInputError):
        validate_event(replace(buy(), fee=fee))


def test_gbp_units_convert_once_not_ticker_inference():
    assert price_in_cash(D("1234"), "GBp", "GBP") == D("12.34")
    assert price_in_cash(D("1234"), "GBP", "GBP") == D("1234")
    records = entries(*openings(), buy(2, "1234", "1", UK, "GBp"))
    state = replay(records)
    assert state.lots[0].remaining_cost == D("25.68")
    result = value(records, {UK: quote("1300", "GBp")})
    assert result.holdings[0].market_value == D("26")
    assert result.holdings[0].unrealized == D("0.32")


def test_wrong_currency_fee_and_price_units_rejected():
    with pytest.raises(PortfolioInputError, match="Fees"):
        validate_event(replace(buy(), fee_currency="GBP"))
    with pytest.raises(PortfolioInputError, match="Price unit"):
        validate_event(replace(buy(), price_unit="GBp"))
    with pytest.raises(PortfolioInputError):
        Instrument("TEST.L", "UK", "USD")


def test_oversell_shorting_cash_deficit_and_missing_opening_guarded():
    with pytest.raises(PortfolioInputError, match="Oversell"):
        replay(entries(*openings(), sell()))
    with pytest.raises(PortfolioInputError, match="Oversell"):
        replay(entries(*openings(), buy(), sell()))
    with pytest.raises(PortfolioInputError, match="Insufficient USD"):
        replay(entries(*openings(usd="1004"), buy()))
    with pytest.raises(PortfolioInputError, match="CashOpening"):
        replay(entries(CashOpening(NOW, "GBP", D("10000"), SOURCE), buy()))


def test_cash_moves_no_currency_conversion_fee_or_assumed_deposit():
    state = replay(
        entries(
            *openings(gbp="100", usd="0"),
            CashDeposit(NOW, "GBP", D("20"), SOURCE),
            CashWithdrawal(NOW, "GBP", D("10"), SOURCE),
            Fee(NOW, "GBP", D("2"), SOURCE),
        )
    )
    assert state.cash == {"GBP": D("108"), "USD": D("0")}
    assert state.standalone_fees == {"GBP": D("2"), "USD": D("0")}
    with pytest.raises(PortfolioInputError, match="Insufficient"):
        replay(entries(*openings(gbp="0"), CashWithdrawal(NOW, "GBP", D("1"), SOURCE)))


def test_linked_buy_and_sell_fees_allocate_fifo_cost_and_net_proceeds():
    records = entries(*openings(), buy(10, "100", "0"))
    records += entries(Fee(NOW, "USD", D("5"), SOURCE, records[-1].event_id))
    records += entries(sell(5, "120", "0"))
    records += entries(Fee(NOW, "USD", D("2"), SOURCE, records[-1].event_id))
    state = replay(records)
    assert state.lots[0].remaining_cost == D("502.5")
    assert state.sales[0].cost == D("502.5")
    assert state.sales[0].proceeds == D("598")
    assert state.sales[0].realized == D("95.5")
    assert state.standalone_fees["USD"] == 0


def test_late_linked_fee_rejected_not_silently_misallocated():
    records = entries(*openings(), buy())
    linked = Fee(NOW, "USD", D("1"), SOURCE, records[-1].event_id)
    records += entries(sell(1, "100", "0"))
    with pytest.raises(PortfolioInputError, match="latest"):
        replay(records + entries(linked))
    with pytest.raises(PortfolioInputError, match="latest"):
        replay(entries(*openings(), replace(linked, related_event_id=uuid4().hex)))


def test_explicit_cash_exchange_multiply_rate_and_keep_fee_separate():
    records = entries(
        *openings(gbp="0", usd="1000"),
        CashExchange(NOW, D("100"), fx(), D("2"), "GBP", SOURCE),
    )
    state = replay(records)
    assert state.cash == {"GBP": D("78"), "USD": D("900")}
    assert state.standalone_fees["GBP"] == D("2")
    with pytest.raises(PortfolioInputError, match="Insufficient"):
        replay(
            entries(
                *openings(gbp="0", usd="100"),
                CashExchange(NOW, D("100"), fx(), D("1"), "USD", SOURCE),
            )
        )
    reverse = replay(
        entries(
            *openings(gbp="100", usd="0"),
            CashExchange(NOW, D("80"), fx("1.25", "GBP", "USD"), D("0"), "GBP", SOURCE),
        )
    )
    assert reverse.cash == {"GBP": D("20"), "USD": D("100")}


def test_missing_quote_fx_never_zero_partial_overall_totals():
    records = entries(*openings(), buy())
    result = value(records)
    assert result.readiness.status == DecisionStatus.INCOMPLETE
    assert result.holdings[0].market_value is None
    assert result.cash_gbp["USD"] is None
    assert result.equity_total_gbp is None
    assert result.holdings_total_gbp is None
    assert result.unrealized_total_gbp is None
    assert result.holdings[0].cost == D("1005")


def test_cash_openings_missing_not_silently_zero():
    result = value(entries(CashOpening(NOW, "GBP", D("1"), SOURCE)))
    assert result.cash == {"GBP": D("1")}
    assert result.cash_total_gbp is None
    assert result.equity_total_gbp is None
    assert result.readiness.status == DecisionStatus.INCOMPLETE
    empty = value(())
    assert empty.equity_total_gbp is None
    assert len(empty.readiness.reasons) == 2
    zero = value(entries(*openings("0", "0")))
    assert zero.readiness.status == DecisionStatus.COMPLETE
    assert zero.equity_total_gbp == 0


def test_wrong_fx_direction_not_inverted_and_quote_currency_incomplete():
    records = entries(*openings(), buy())
    result = value(records, {US: quote()}, (fx("1.25", "GBP", "USD"),))
    assert result.readiness.status == DecisionStatus.INCOMPLETE
    assert result.cash_gbp["USD"] is None
    assert result.holdings[0].market_value == D("1500")
    assert result.holdings[0].market_value_gbp is None
    wrong_quote = value(records, {US: quote("150", "GBP")}, (fx(),))
    assert wrong_quote.holdings[0].market_value is None
    assert wrong_quote.readiness.status == DecisionStatus.INCOMPLETE


def test_stale_future_and_missing_provenance_are_not_valid_observations():
    records = entries(*openings(), buy())
    stale = value(records, {US: quote(as_of=NOW - timedelta(days=2))}, (fx(),))
    assert stale.holdings[0].market_value is None
    future = value(records, {US: quote(as_of=NOW + timedelta(seconds=1))}, (fx(),))
    assert future.holdings[0].market_value is None
    stale_fx = value(records, {US: quote()}, (fx(as_of=NOW - timedelta(days=2)),))
    assert stale_fx.cash_gbp["USD"] is None
    with pytest.raises(PortfolioInputError, match="Ambiguous"):
        value(records, {US: quote()}, (fx(), fx("0.9")))
    with pytest.raises(PortfolioInputError):
        snapshot(records, quotes={}, fx_rates=(), as_of=NOW, max_age=None)


def test_event_chronology_and_recording_time_and_naive_time_rejected():
    earlier = NOW - timedelta(days=1)
    with pytest.raises(PortfolioInputError, match="chronology"):
        replay(entries(*openings(), replace(buy(), effective_at=earlier)))
    with pytest.raises(PortfolioInputError, match="before"):
        replay(entries(replace(openings()[0], effective_at=NOW + timedelta(days=1))))
    with pytest.raises(PortfolioInputError, match="timezone"):
        validate_event(replace(buy(), effective_at=NOW.replace(tzinfo=None)))
    with pytest.raises(PortfolioInputError, match="precede"):
        snapshot(
            entries(*openings()),
            quotes={},
            fx_rates=(),
            as_of=earlier,
            max_age=timedelta(1),
        )


def test_immutable_correction_replay_safe_and_dangerous_correction_rejected():
    records = entries(*openings(), buy())
    corrected = records + entries(
        Correction(NOW, records[-1].event_id, "wrong entry", SOURCE)
    )
    state = replay(corrected)
    assert state.lots == ()
    assert state.cash["USD"] == D("10000")
    assert len(corrected) == 4
    sold = records + entries(sell(1, "110", "0"))
    with pytest.raises(PortfolioInputError, match="Oversell"):
        replay(
            sold + entries(Correction(NOW, records[-1].event_id, "remove buy", SOURCE))
        )
    with pytest.raises(PortfolioInputError, match="active"):
        replay(
            corrected + entries(Correction(NOW, records[-1].event_id, "again", SOURCE))
        )
    with pytest.raises(PortfolioInputError, match="active"):
        replay(
            corrected
            + entries(
                Correction(NOW, corrected[-1].event_id, "remove correction", SOURCE)
            )
        )


def test_declared_opening_dates_duplicates_and_order_not_defaulted():
    opening = OpeningHolding(NOW, US, 10, D("900"), date(2025, 1, 1), SOURCE)
    with pytest.raises(PortfolioInputError, match="acquired_on"):
        validate_event(replace(opening, acquired_on=date(2027, 1, 1)))
    with pytest.raises(PortfolioInputError, match="once"):
        replay(entries(*openings(), opening, opening))
    with pytest.raises(PortfolioInputError, match="before trades"):
        replay(entries(*openings(), buy(), opening))
    with pytest.raises(PortfolioInputError, match="one active"):
        replay(entries(*openings(), openings()[0]))


def test_provenance_declared_before_observation_is_incomplete_not_silently_accepted():
    provenance = ManualProvenance(SOURCE, NOW - timedelta(seconds=1))
    observed = PriceQuote(D("100"), "USD", NOW, provenance)
    result = value(entries(*openings(), buy()), {US: observed}, (fx(),))
    assert result.holdings[0].market_value is None
    assert result.readiness.status == DecisionStatus.INCOMPLETE


def test_recording_chronology_duplicate_uuid_and_provenance_rejected():
    records = entries(*openings())
    changed_recording = replace(records[-1], recorded_at=NOW - timedelta(seconds=1))
    with pytest.raises(PortfolioInputError):
        replay((records[0], changed_recording))
    with pytest.raises(PortfolioInputError, match="Duplicate"):
        replay((records[0], replace(records[1], event_id=records[0].event_id)))
    with pytest.raises(PortfolioInputError, match="provenance"):
        replay((replace(records[0], provenance="provider"),))


def test_wrong_linked_fee_currency_and_voided_trade_link_are_rejected():
    records = entries(*openings(), buy())
    linked = Fee(NOW, "GBP", D("1"), SOURCE, records[-1].event_id)
    with pytest.raises(PortfolioInputError, match="same-currency"):
        replay(records + entries(linked))
    linked = replace(linked, currency="USD")
    with pytest.raises(PortfolioInputError, match="latest"):
        replay(
            records
            + entries(
                linked,
                Correction(NOW, records[-1].event_id, "void linked trade", SOURCE),
            )
        )


def test_signed_gbp_unrealized_loss_and_snapshot_fx_never_move_cash():
    records = entries(*openings(), buy())
    first = value(records, {US: quote("80")}, (fx("0.8"),))
    second = value(records, {US: quote("80")}, (fx("0.7"),))
    assert first.holdings[0].unrealized == D("-205")
    assert first.holdings[0].unrealized_gbp == D("-164")
    assert second.holdings[0].unrealized_gbp == D("-143.5")
    assert first.cash == second.cash == replay(records).cash


def test_readiness_many_missing_holdings_remains_bounded():
    records = entries(
        *openings(),
        *(
            OpeningHolding(
                NOW,
                Instrument(f"TEST{index}", "UK", "GBP"),
                1,
                D("1"),
                date(2025, 1, 1),
                SOURCE,
            )
            for index in range(25)
        ),
    )
    result = value(records)
    assert result.readiness.status == DecisionStatus.INCOMPLETE
    assert len(result.readiness.reasons) == 20
    assert result.equity_total_gbp is None


def test_snapshot_cannot_look_ahead_to_later_recorded_historical_declarations():
    records = entries(
        CashOpening(NOW - timedelta(days=1), "GBP", D("1"), SOURCE),
        CashOpening(NOW - timedelta(days=1), "USD", D("0"), SOURCE),
    )
    with pytest.raises(PortfolioInputError, match="recording"):
        snapshot(
            records,
            quotes={},
            fx_rates=(),
            as_of=NOW - timedelta(hours=1),
            max_age=timedelta(days=1),
        )


def test_accounting_independent_of_ambient_rounding_precision_and_inexact_traps():
    records = entries(
        *openings(),
        OpeningHolding(NOW, US, 3, D("1"), date(2025, 1, 1), SOURCE),
        sell(1, "1", "0"),
    )
    expected_state = replay(records)
    expected_snapshot = value(records, {US: quote("1")}, (fx(),))
    for rounding in (ROUND_UP, ROUND_DOWN):
        with localcontext() as context:
            context.prec = 2
            context.rounding = rounding
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            assert replay(records) == expected_state
            assert value(records, {US: quote("1")}, (fx(),)) == expected_snapshot
            assert price_in_cash(D("1234"), "GBp", "GBP") == D("12.34")
