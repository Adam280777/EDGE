"""Offline first-party manual templates; no facts, copying or runtime invocation."""

import socket
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta

import pytest
from test_research_workflows import NOW, SOURCE, research

from edge.decision_contracts import DecisionInputError, ManualProvenance
from edge.research_templates import (
    ResearchPrompt,
    assess_research_completeness,
    get_research_template,
)
from edge.research_workflows import DatedCatalyst, ResearchRevision


@pytest.mark.parametrize("engine", ["swing", "longterm"])
def test_immutable_templates_existing_fields_and_concept_disclaimer(engine, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Manual template must not call providers/network.")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    template = get_research_template(engine)
    assert template == get_research_template(engine)
    assert "v0.1.19" in template.concept_reference
    assert "not installed" in template.disclaimers[0]
    assert all(prompt.field in {field.name for field in fields(ResearchRevision)}
               for prompt in template.prompts)
    assert {"bull_case", "bear_case", "mispricing_hypothesis", "risks", "facts",
            "assumptions", "opinions", "invalidation"} <= {
        prompt.field for prompt in template.prompts if prompt.required
    }
    assert len({prompt.key for prompt in template.prompts}) == len(template.prompts)
    assert len(dict(template.field_labels)) == len(template.field_labels)
    with pytest.raises(FrozenInstanceError):
        template.title = "Changed"
    with pytest.raises(FrozenInstanceError):
        template.prompts[0].question = "Changed"


def test_horizon_specific_topics_and_no_financial_values() -> None:
    swing = get_research_template("swing")
    longterm = get_research_template("longterm")
    assert {"momentum", "liquidity", "catalyst", "entry", "swing-invalidation"} <= {
        prompt.key for prompt in swing.prompts
    }
    assert {"growth", "margins-cash", "balance", "valuation", "competition", "capital-allocation"} <= {
        prompt.key for prompt in longterm.prompts
    }
    assert all(not hasattr(prompt, "value") for prompt in (*swing.prompts, *longterm.prompts))
    assert "valuation_scenarios" in dict(longterm.field_labels)
    assert "priced-in" in {prompt.key for prompt in swing.prompts}


@pytest.mark.parametrize("engine", ["both", "long-term", "Swing", "", None, 1])
def test_invalid_engine_never_silently_defaults(engine) -> None:
    with pytest.raises(DecisionInputError):
        get_research_template(engine)


def test_complete_presence_only_and_no_score_or_mutation() -> None:
    original = research()
    report = assess_research_completeness(original)
    assert report.complete
    assert not hasattr(report, "score")
    assert "topic coverage" in report.limitations[0]
    assert original == research()
    assert assess_research_completeness(replace(original, missing_information=("Unknown cash flows",))).complete
    # Known missing information is a limitation, not a fabricated completeness score.
    assert original.readiness.status.value == "complete"


@pytest.mark.parametrize("field,empty", [
    ("bull_case", ""), ("bear_case", "  "), ("mispricing_hypothesis", ""),
    ("invalidation", ""), ("risks", ()), ("facts", ()), ("assumptions", ()),
    ("opinions", ()), ("valuation_scenarios", ()),
])
def test_required_existing_fields_missing_named_not_scored(field, empty) -> None:
    revision = replace(research(), **{field: empty})
    report = assess_research_completeness(revision)
    assert not report.complete
    assert field in report.missing_fields
    assert len(set(report.missing_fields)) == len(report.missing_fields)


def test_swing_requires_documented_catalyst_no_invented_event() -> None:
    revision = replace(research(), horizon="swing", valuation_scenarios=())
    report = assess_research_completeness(revision)
    assert report.missing_fields == ("catalysts",)
    catalyst = DatedCatalyst("User-entered review event", NOW.date(), SOURCE)
    assert assess_research_completeness(replace(revision, catalysts=(catalyst,))).complete


def test_future_source_date_issues_distinct_from_field_presence() -> None:
    revision = research()
    future = ManualProvenance("User source declared later", NOW + timedelta(days=1))
    revised = replace(revision, facts=(replace(revision.facts[0], provenance=future),))
    report = assess_research_completeness(revised)
    assert report.missing_fields == ()
    assert report.source_issues == ("facts[1]: declaration date follows research revision.",)
    assert not report.complete


def test_prompt_validation_no_unknown_fields_or_duplicate_keys() -> None:
    prompt = get_research_template("swing").prompts[0]
    with pytest.raises(DecisionInputError):
        replace(prompt, field="generated_trade_signal")
    with pytest.raises(DecisionInputError):
        ResearchPrompt("key", "facts", "label", "question", 1)
    with pytest.raises(DecisionInputError):
        replace(get_research_template("swing"), prompts=(prompt, prompt))
    with pytest.raises(DecisionInputError):
        assess_research_completeness(None)
