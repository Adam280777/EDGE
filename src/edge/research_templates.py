"""Original first-party manual checklists; concept inspiration, not Vibe runtime.

No upstream YAML, prompts, presets or source are copied. No LLM, tool execution,
provider request, scoring or financial fact generation occurs in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from edge.decision_contracts import DecisionInputError, ManualProvenance
from edge.research_workflows import ResearchRevision, immutable_items, text

ResearchEngine = Literal["swing", "longterm"]
_REFERENCE = "Vibe-Trading v0.1.19 — conceptual inspiration for structured research workflows"
_DISCLAIMER = (
    "Original first-party manual workflow; Vibe-Trading runtime is not installed or invoked.",
    "User statements and citations are unverified; checklist completeness is not financial truth or advice.",
    "No copied upstream presets/source, dependencies, LLM providers, tools or paid/network calls.",
)
_FIELDS = {
    "identity", "facts", "assumptions", "opinions", "valuation_scenarios", "catalysts",
    "bull_case", "bear_case", "mispricing_hypothesis", "invalidation", "risks",
    "missing_information", "conclusion",
}


@dataclass(frozen=True)
class ResearchPrompt:
    key: str
    field: str
    label: str
    question: str
    required: bool

    def __post_init__(self) -> None:
        for name in ("key", "field", "label", "question"):
            text(getattr(self, name), name)
        if self.field not in _FIELDS or type(self.required) is not bool:
            raise DecisionInputError("Prompt must map to an existing research field with explicit requirement.")


@dataclass(frozen=True)
class ResearchTemplate:
    engine: ResearchEngine
    title: str
    prompts: tuple[ResearchPrompt, ...]
    concept_reference: str
    disclaimers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.engine not in ("swing", "longterm"):
            raise DecisionInputError("Research engine must be swing or longterm.")
        for name in ("title", "concept_reference"):
            text(getattr(self, name), name)
        immutable_items(self.prompts, ResearchPrompt, "prompts")
        immutable_items(self.disclaimers, str, "disclaimers")
        if not self.prompts or len({prompt.key for prompt in self.prompts}) != len(self.prompts):
            raise DecisionInputError("Template prompts need distinct keys.")
        if not self.disclaimers:
            raise DecisionInputError("Manual workflow disclaimers required.")
        for disclaimer in self.disclaimers:
            text(disclaimer, "disclaimer")

    @property
    def field_labels(self) -> tuple[tuple[str, str], ...]:
        """Canonical existing field labels; no new draft fields or generated values."""
        labels = {}
        for prompt in self.prompts:
            labels.setdefault(prompt.field, prompt.label)
        return tuple(labels.items())


@dataclass(frozen=True)
class ResearchCompleteness:
    engine: ResearchEngine
    missing_fields: tuple[str, ...]
    source_issues: tuple[str, ...]
    limitations: tuple[str, ...] = (
        "Presence/date checks only; no assessment of investment quality, truth or topic coverage.",
        "No numeric score, ranking, trade signal, financial calculation or source acceptance.",
    )

    @property
    def complete(self) -> bool:
        return not self.missing_fields and not self.source_issues


def _prompt(key: str, field: str, label: str, question: str,
            required: bool = True) -> ResearchPrompt:
    return ResearchPrompt(key, field, label, question, required)


def get_research_template(engine: ResearchEngine) -> ResearchTemplate:
    """Return immutable prompts before a manual draft; never fill facts/claims."""
    if engine not in ("swing", "longterm"):
        raise DecisionInputError("Research engine must be swing or longterm, not both.")
    common = (
        _prompt("identity", "identity", "User-declared company identity",
                "State symbol, company, exchange and reporting currency; explain unresolved identity questions."),
        _prompt("evidence", "facts", "Claimed facts and source dates",
                "For every factual claim enter its manual citation, declaration/as-of date, reporting period "
                "and units. Distinguish reported claims from estimates and derived values; do not invent missing data."),
        _prompt("assumptions", "assumptions", "Explicit assumptions",
                "Which inputs are assumptions rather than reported facts? Give source/as-of, units and period; "
                "state conditions that would make each assumption fail."),
        _prompt("opinions", "opinions", "Interpretation, not facts",
                "Record your interpretation separately from evidence and assumptions. Cite what it relies on."),
        _prompt("bull", "bull_case", "Bull evidence",
                "What evidence supports a favorable outcome? Explain the causal link and weakest supporting claim."),
        _prompt("bear", "bear_case", "Bear evidence",
                "What evidence supports an unfavorable outcome? Describe downside mechanisms and challenge the bull case."),
        _prompt("priced-in", "mispricing_hypothesis", "Already priced in / possible mispricing",
                "What expectations may already be in the price? What specifically could the market be "
                "underestimating or overestimating? Separate the hypothesis from verified facts."),
        _prompt("invalidation", "invalidation", "Thesis invalidation",
                "Name observable conditions that would disprove the thesis; specify review dates or events where useful."),
        _prompt("uncertainty", "risks", "Risks and uncertainty",
                "List uncertainty, contrary evidence, liquidity, valuation, dilution and downside risks. "
                "Which important estimates are most sensitive? No stop or model eliminates gap risk."),
        _prompt("missing", "missing_information", "Missing information",
                "List unavailable, stale or unresolved evidence and how it limits the thesis. Leave empty only "
                "when no missing information is known; this does not certify data completeness.", False),
        _prompt("conclusion", "conclusion", "Manual research conclusion",
                "Summarize the evidence, assumptions, uncertainty and reasons to avoid a trade. "
                "Do not substitute an unexplained buy/sell instruction."),
    )
    if engine == "swing":
        specific = (
            _prompt("momentum", "facts", "Price action and momentum evidence",
                    "Using explicitly dated manual observations, describe trend, support/resistance, "
                    "momentum, volume and volatility. What contrary chart evidence weakens the setup?"),
            _prompt("liquidity", "facts", "Liquidity and execution evidence",
                    "Document spread, trading volume, liquidity and quote currency/GBp units with sources/as-of. "
                    "What cash, fees, slippage or event gap could make a whole-share plan unusable?"),
            _prompt("catalyst", "catalysts", "Dated swing catalysts",
                    "Enter each expected catalyst or earnings event with calendar date and manual source/as-of. "
                    "An empty list means no documented dated catalyst, not a fabricated event."),
            _prompt("entry", "assumptions", "Conditional entry and holding horizon",
                    "Describe a conditional entry zone/trigger, intended holding period and avoidance conditions. "
                    "Keep assumed levels separate from sourced prices; use a separate confirmed trade plan for sizing."),
            _prompt("swing-invalidation", "invalidation", "Setup invalidation rationale",
                    "Why would the setup fail at the proposed technical/fundamental invalidation? "
                    "Identify stop rationale, target rationale and adverse event conditions without promising fills."),
        )
        title = "Manual swing research checklist"
    else:
        specific = (
            _prompt("growth", "facts", "Growth evidence",
                    "Compare dated revenue/earnings growth across reporting periods. Is acceleration durable "
                    "or driven by temporary comparisons, acquisition, concentration or dilution?"),
            _prompt("margins-cash", "facts", "Margins and cash generation",
                    "Compare gross/operating margins and operating/free cash flow with source, period and units. "
                    "Explain working capital, reinvestment and cash-conversion weaknesses."),
            _prompt("balance", "facts", "Balance sheet and dilution",
                    "Document cash, debt, maturities, leverage and diluted shares. Which financing or dilution "
                    "risks could prevent growth from reaching existing shareholders?"),
            _prompt("valuation", "valuation_scenarios", "User-entered valuation scenarios",
                    "Enter explicitly sourced/assumed revenue, growth, margin, cash conversion, discount/terminal "
                    "rates, cash, debt and diluted shares. Compare bull/base/bear assumptions; never invent financial inputs."),
            _prompt("competition", "opinions", "Competitive durability",
                    "What evidence supports pricing power or defensibility, and what competition or customer "
                    "behavior could erode it? Label interpretation separately from evidence."),
            _prompt("capital-allocation", "opinions", "Capital allocation and shareholder outcomes",
                    "Assess reinvestment, acquisitions, buybacks, dividends and share issuance using dated evidence. "
                    "Why might management choices create or destroy per-share value?"),
            _prompt("longterm-catalyst", "catalysts", "Dated long-term review catalysts",
                    "Enter known review events with dates and manual sources. Do not invent catalysts; "
                    "document the absence of a dated event as missing information when relevant.", False),
        )
        title = "Manual long-term research checklist"
    # Source questions come first; both horizons retain all common challenge fields.
    return ResearchTemplate(engine, title, common[:4] + specific + common[4:],
                            _REFERENCE, _DISCLAIMER)


def assess_research_completeness(revision: ResearchRevision) -> ResearchCompleteness:
    """Presence and explicit source/date checks using actual ResearchRevision fields.

    Does not infer whether free text answers each topic. Reporting period and
    units remain ResearchItem requirements; provenance dates must not follow the
    revision. Identity/citations are never externally verified here.
    """
    if not isinstance(revision, ResearchRevision):
        raise DecisionInputError("ResearchRevision required.")
    template = get_research_template(revision.horizon)
    missing = []
    for prompt in template.prompts:
        value = getattr(revision, prompt.field)
        absent = not value or (isinstance(value, str) and not value.strip())
        if prompt.required and absent and prompt.field not in missing:
            missing.append(prompt.field)
    issues = []
    for field in ("facts", "assumptions", "opinions", "catalysts", "valuation_scenarios"):
        for index, item in enumerate(getattr(revision, field), 1):
            source = item.provenance
            if not isinstance(source, ManualProvenance):
                issues.append(f"{field}[{index}]: manual citation/declaration date missing.")
            elif source.declared_at > revision.created_at:
                issues.append(f"{field}[{index}]: declaration date follows research revision.")
    return ResearchCompleteness(revision.horizon, tuple(missing), tuple(issues))
