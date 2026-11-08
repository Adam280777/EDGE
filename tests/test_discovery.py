"""Deterministic manual screening: no fake scores or source promotion."""

import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from edge.decision_contracts import DecisionInputError, ManualProvenance
from edge.discovery import (
    CompanyObservations,
    FundamentalObservation,
    RankingRule,
    ScreenFilter,
    screen_companies,
)
from edge.research_workflows import CompanyIdentity

D = Decimal
NOW = datetime(2026, 11, 7, 15, tzinfo=UTC)
SOURCE = ManualProvenance("Synthetic user observations", NOW)
EARLIEST = NOW - timedelta(days=30)


def observation(value: str | None, metric: str = "growth") -> FundamentalObservation:
    return FundamentalObservation(metric, D(value) if value else None, "fraction",
                                  "FY2025", NOW, SOURCE, "claimed-reported")


def company(symbol: str, value: str | None) -> CompanyObservations:
    return CompanyObservations(CompanyIdentity(symbol, "Synthetic " + symbol, "TEST", "GBP"),
                              (observation(value),))


def filters() -> tuple[ScreenFilter, ...]:
    return (ScreenFilter("growth", "fraction", D(".1"), None, "FY2025", EARLIEST),)


def ranks() -> tuple[RankingRule, ...]:
    return (RankingRule("growth", "fraction", "higher", "FY2025", EARLIEST),)


def test_included_rank_trace_and_excluded_never_ranked_no_network(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Discovery must not use network/provider.")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    given = (company("C", ".05"), company("B", ".2"), company("A", ".2"), company("D", None))
    result = screen_companies(given, filters(), ranks(), as_of=NOW)
    assert [r.identity.symbol for r in result] == ["A", "B", "C", "D"]
    assert [r.rank for r in result] == [1, 2, None, None]
    assert "outside-user-bounds" in result[2].exclusions[0]
    assert "missing-data" in result[3].exclusions[0]
    assert "bounds=" in result[0].filter_trace[0]
    assert "source=Synthetic user observations" in result[0].ranking_trace[0]
    assert not hasattr(result[0], "score")
    assert screen_companies(tuple(reversed(given)), filters(), ranks(), as_of=NOW) == result
    with localcontext() as ctx:
        ctx.prec = 3
        assert screen_companies(given, filters(), ranks(), as_of=NOW) == result


@pytest.mark.parametrize("updates,code", [
    ({"value": None}, "missing-data"),
    ({"unit": "percent"}, "unit-mismatch"),
    ({"reporting_period": "FY2024"}, "period-mismatch"),
    ({"as_of": EARLIEST - timedelta(microseconds=1)}, "stale-or-future-date"),
    ({"as_of": NOW + timedelta(microseconds=1)}, "stale-or-future-date"),
    ({"classification": "user-estimate"}, "classification-excluded"),
    ({"provenance": ManualProvenance("Future declaration", NOW + timedelta(seconds=1))},
     "future-declaration"),
])
def test_explicit_data_quality_exclusions(updates: dict, code: str) -> None:
    given = company("A", ".2")
    given = replace(given, observations=(replace(given.observations[0], **updates),))
    result = screen_companies((given,), filters(), ranks(), as_of=NOW)[0]
    assert not result.included and result.rank is None
    assert any(code in reason for reason in result.exclusions)


def test_missing_ranking_metric_excludes_even_if_filters_pass() -> None:
    rank = RankingRule("margin", "fraction", "higher", "FY2025", EARLIEST)
    result = screen_companies((company("A", ".2"),), filters(), (rank,), as_of=NOW)[0]
    assert "pass" in result.filter_trace[0]
    assert not result.included
    assert result.exclusions == ("margin: missing-data",)


def test_lexicographic_priorities_not_weighted_sum() -> None:
    a = replace(company("A", ".2"), observations=(observation(".2"), observation(".1", "margin")))
    b = replace(company("B", ".15"), observations=(observation(".15"), observation(".9", "margin")))
    second = RankingRule("margin", "fraction", "higher", "FY2025", EARLIEST)
    result = screen_companies((b, a), filters(), (*ranks(), second), as_of=NOW)
    assert result[0].identity.symbol == "A"  # first priority wins, no synthetic total
    assert len(result[0].ranking_trace) == 2
    lower = replace(ranks()[0], direction="lower")
    assert screen_companies((b, a), filters(), (lower,), as_of=NOW)[0].identity.symbol == "B"


def test_manual_estimates_opt_in_and_no_ranking_means_no_rank() -> None:
    c = replace(company("A", ".2"), observations=(replace(observation(".2"), classification="user-estimate"),))
    explicit = replace(filters()[0], allowed_classifications=("user-estimate",))
    result = screen_companies((c,), (explicit,), (), as_of=NOW)[0]
    assert result.included
    assert result.rank is None
    assert "user-estimate" in result.filter_trace[0]
    assert screen_companies((), filters(), (), as_of=NOW) == ()


@pytest.mark.parametrize("value", [0.2, "0.2", D("NaN"), D("Infinity")])
def test_observations_reject_coercion(value: object) -> None:
    with pytest.raises(DecisionInputError):
        replace(observation(".2"), value=value)


def test_duplicates_and_rule_validation() -> None:
    c = company("A", ".2")
    for action in (
        lambda: screen_companies((c, c), filters(), ranks(), as_of=NOW),
        lambda: replace(c, observations=(observation(".2"), observation(".3"))),
        lambda: screen_companies((c,), (), ranks(), as_of=NOW),
        lambda: screen_companies((c,), filters(), (*ranks(), *ranks()), as_of=NOW),
        lambda: replace(filters()[0], minimum=None, maximum=None),
        lambda: replace(filters()[0], maximum=D(".01")),
        lambda: replace(ranks()[0], direction="random"),
        lambda: screen_companies((c,), (replace(filters()[0], not_before=NOW + timedelta(days=1)),),
                                ranks(), as_of=NOW),
    ):
        with pytest.raises(DecisionInputError):
            action()
