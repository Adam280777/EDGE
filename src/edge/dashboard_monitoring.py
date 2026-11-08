"""Session-opt-in timer checks saved manual evidence, never remote services.

Presentation only: rule status tiles reuse ``edge.monitoring.evaluate`` (the same
pure function a check runs) on saved rules and the latest saved manual quote.
Rendering never records alerts; only an explicit or scheduled check does.
"""

# ruff: noqa: BLE001 -- fixed UI errors must not leak user records or SQL.
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st

from edge import ui_charts as charts
from edge.dashboard_manual import (
    boundary,
    decimal_input,
    failure,
    readout,
    timestamp_input,
)
from edge.decision_contracts import ManualProvenance, PriceQuote
from edge.monitoring import MonitoringRule, evaluate
from edge.monitoring_store import MonitoringStore
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    empty,
    fmt_money,
    fmt_time,
    legend,
    panel,
    tiles,
)

# Display labels for the fixed reason codes produced by edge.monitoring.evaluate.
REASONS: dict[str, tuple[str, str, str]] = {
    # code: (label, kind, tone)
    "manual_quote_at_or_below_stop": ("At or below stop", "Price threshold", "red"),
    "manual_quote_at_or_above_target": ("At or above target", "Price threshold", "green"),
    "thesis_invalidated_user_declared": ("THESIS INVALIDATED · user-declared", "Thesis review", "red"),
    "thesis_review_user_declared": ("THESIS REVIEW · user-declared", "Thesis review", "violet"),
    "quote_missing_no_threshold_evaluation": ("No quote · thresholds not evaluated", "Evidence gap", "amber"),
    "quote_unit_mismatch_no_threshold_evaluation": ("Quote unit ≠ rule unit · not evaluated", "Evidence gap", "amber"),
    "quote_future_no_threshold_evaluation": ("Future-dated quote · not evaluated", "Evidence gap", "amber"),
    "quote_stale_no_threshold_evaluation": ("Stale quote · not evaluated", "Evidence gap", "amber"),
}
# Most severe first; decides the single headline shown on a rule's status tile.
_PRIORITY = (
    "manual_quote_at_or_below_stop", "thesis_invalidated_user_declared",
    "manual_quote_at_or_above_target", "thesis_review_user_declared",
    "quote_stale_no_threshold_evaluation", "quote_future_no_threshold_evaluation",
    "quote_unit_mismatch_no_threshold_evaluation", "quote_missing_no_threshold_evaluation",
)

TASKS = ("Alerts", "New rule", "Manual quote")
# The task panel's pane header follows the chosen task: (code, title, meta, badges).
TASK_HEADS = {
    "Alerts": ("SCH", "Session schedule", "Opt-in ? this open workspace only", ("local",)),
    "New rule": ("RUL", "New monitoring rule", "Append-only ? long-only stop < target", ("manual",)),
    "Manual quote": ("QTE", "Manual quote observation", "Append-only ? newest observation wins",
                     ("manual",)),
}


def _reason(code: str) -> tuple[str, str, str]:
    return REASONS.get(code, (code, "Other", "muted"))


def _age(observed: datetime | None, now: datetime) -> str:
    if observed is None:
        return UNAVAILABLE
    seconds = int((now - observed).total_seconds())
    if seconds < 0:
        return "Future-dated"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _quote_text(quote: PriceQuote | None) -> str:
    return "No saved quote" if quote is None else fmt_money(quote.amount, quote.unit, places=4)


def _threshold(value, unit: str) -> str:
    return "Not set" if value is None else fmt_money(value, unit, places=4)


def _summary(data) -> None:
    """Top strip of recorded counts; reads never create the database."""
    if data is None:
        failure("Monitoring read")
        return
    rules, quotes, alerts = data
    open_alerts = [alert for alert in alerts if alert.acknowledged_at is None]
    thesis = [alert for alert in open_alerts if alert.reason.startswith("thesis_")]
    latest = max((quote.as_of for quote in quotes.values()), default=None)
    tiles([
        Tile("Saved rules", str(len(rules)), "Immutable user declarations", "amber"),
        Tile("Stop + target rules", str(sum(r.stop is not None and r.target is not None for r in rules)),
             "Drawn on the proximity track", "cyan"),
        Tile("Quoted instruments", str(len(quotes)), "Latest manual quote each", "cyan"),
        Tile("Latest manual quote", fmt_time(latest) if latest else UNAVAILABLE,
             "Not a live feed", "ink"),
        Tile("Open alerts", str(len(open_alerts)), f"{len(alerts) - len(open_alerts)} acknowledged",
             "red" if open_alerts else "ink"),
        Tile("Open thesis reviews", str(len(thesis)), "User-declared status",
             "violet" if thesis else "ink"),
    ], columns=6)


def _saved(flash_key: str, message: str) -> None:
    """Rerun so every panel reads the new record; the confirmation shows on that run."""
    st.session_state[flash_key] = message
    st.rerun()


def _rule_form(store: MonitoringStore, flash_key: str) -> None:
    with st.form("edge_monitor_rule", enter_to_submit=False):
        left, middle, right = st.columns(3)
        with left:
            symbol = st.text_input("Ticker / user-declared", max_chars=32)
        with middle:
            market = st.selectbox("Declared market", ["US", "UK"])
        with right:
            unit = st.selectbox("Threshold unit / per share", ["USD", "GBP", "GBp"])
        left, right = st.columns(2)
        with left:
            stop = st.text_input("Stop / optional exact decimal")
        with right:
            target = st.text_input("Target / optional exact decimal")
        left, right = st.columns(2)
        with left:
            thesis = st.selectbox(
                "Thesis status / user-declared", ["unchanged", "review", "invalidated"]
            )
        with right:
            age = st.number_input(
                "Maximum manual quote age (seconds)", min_value=1,
                max_value=2592000, value=86400,
            )
        reference = st.text_input("Rule source / user declaration", max_chars=200)
        submitted = st.form_submit_button("Save immutable monitoring rule", type="primary")
    if submitted:
        try:
            rule = MonitoringRule(
                uuid4().hex, symbol.strip().upper(), market, unit,
                decimal_input(stop) if stop.strip() else None,
                decimal_input(target) if target.strip() else None,
                thesis, age, ManualProvenance(reference, datetime.now(UTC)),
            )
            store.append_rule(rule)
            _saved(flash_key, "Rule saved locally. No check or trade performed.")
        except Exception:
            failure("Rule save")


def _quote_form(store: MonitoringStore, flash_key: str) -> None:
    st.caption("Manual observations are append-only, not a live feed or validated market data.")
    with st.form("edge_monitor_quote", enter_to_submit=False):
        left, right = st.columns(2)
        with left:
            symbol = st.text_input("Ticker / user-declared", max_chars=32)
        with right:
            market = st.selectbox("Declared market", ["US", "UK"])
        left, right = st.columns(2)
        with left:
            amount = st.text_input("Manual price / exact decimal")
        with right:
            unit = st.selectbox("Price unit / per share", ["USD", "GBP", "GBp"])
        observed = st.text_input("Observed at / ISO timestamp with UTC offset")
        reference = st.text_input("Observation source / user declaration", max_chars=200)
        submitted = st.form_submit_button("Save manual observation", type="primary")
    if submitted:
        try:
            quote = PriceQuote(
                decimal_input(amount), unit, timestamp_input(observed),
                ManualProvenance(reference, datetime.now(UTC)),
            )
            store.append_quote(symbol.strip().upper(), market, quote)
            _saved(flash_key, "Manual observation saved. No automatic threshold check performed.")
        except Exception:
            failure("Observation save")


def _status_board(rules, quotes, now: datetime) -> None:
    st.caption(
        "Display-only evaluation of saved rules against the latest saved manual quote, using "
        "the same logic as a check. Nothing is recorded until a check runs."
    )
    cells = []
    for rule in rules:
        quote = quotes.get((rule.symbol, rule.market))
        codes = [finding.reason for finding in evaluate(rule, quote, as_of=now)]
        if codes:
            head = min(codes, key=lambda code: _PRIORITY.index(code) if code in _PRIORITY else 99)
            label, _kind, tone = _reason(head)
            extra = len(codes) - 1
            value = label.split(" · ")[0].upper()
            note = (f"+{extra} more · " if extra else "") + f"quote age {_age(quote.as_of if quote else None, now)}"
        else:
            value, tone = "NO FINDINGS", "green"
            note = f"Quote age {_age(quote.as_of, now)}" if quote else "Thesis-only rule"
        cells.append(Tile(f"{rule.market}:{rule.symbol} · {rule.unit}", value, note, tone))
    tiles(cells, columns=min(4, max(1, len(cells))))
    legend({"Stop reached / thesis invalidated": "red", "Target reached": "green",
            "Thesis review": "violet", "Not evaluated (evidence gap)": "amber"})


def _proximity(rules, quotes, now: datetime) -> None:
    rows = []
    for rule in rules:
        if rule.stop is None or rule.target is None:
            continue
        quote = quotes.get((rule.symbol, rule.market))
        # Evaluation never compares across units; neither does the chart.
        usable = quote is not None and quote.unit == rule.unit
        rows.append({
            "symbol": f"{rule.market}:{rule.symbol}",
            "stop": rule.stop, "target": rule.target,
            "quote": quote.amount if usable else None,
            "quote_label": (f"{_quote_text(quote)} · {fmt_time(quote.as_of)} · "
                            f"age {_age(quote.as_of, now)}") if usable else None,
        })
    if not charts.show(charts.proximity(rows, title="Stop → target track · latest saved manual quote"),
                       alt_text="Track per rule from stop to target with the latest saved manual "
                                "quote marked where its unit matches the rule unit",
                       key="edge_monitor_proximity_chart"):
        empty("No rule has both a stop and a target to draw.",
              hint="Choose New rule and declare both thresholds to see the proximity track.")
    else:
        st.caption("Quotes in a different unit than the rule are not plotted (they are not evaluated). "
                   "Check quote age before relying on a position on the track.")


def _rules_table(rules, quotes, now: datetime) -> None:
    rows = []
    for rule in rules:
        quote = quotes.get((rule.symbol, rule.market))
        rows.append({
            "Symbol": rule.symbol, "Market": rule.market, "Unit": rule.unit,
            "Stop": _threshold(rule.stop, rule.unit), "Target": _threshold(rule.target, rule.unit),
            "Thesis": rule.thesis_status,
            "Latest quote": _quote_text(quote),
            "Quote observed": fmt_time(quote.as_of) if quote else UNAVAILABLE,
            "Quote age": _age(quote.as_of if quote else None, now),
            "Max age (s)": rule.max_age_seconds,
            "Source": rule.provenance.reference,
            "Declared": fmt_time(rule.provenance.declared_at),
        })
    st.dataframe(rows, hide_index=True, width="stretch")


def _alerts(store: MonitoringStore, alerts, rules, view: str = "both") -> None:
    by_rule = {rule.rule_id: rule for rule in rules}

    def symbol(alert) -> str:
        rule = by_rule.get(alert.rule_id)
        return f"{rule.market}:{rule.symbol}" if rule else "Unknown rule"

    if not alerts:
        st.info("No recorded alerts. This does not establish that a position is safe.")
        return
    events = [{"lane": symbol(alert), "when": alert.created_at,
               "label": _reason(alert.reason)[0], "kind": _reason(alert.reason)[1]}
              for alert in alerts]

    def timeline() -> None:
        charts.show(charts.timeline(events, tone="kind", title="Alerts recorded · by instrument"),
                    alt_text="Timeline of recorded alerts per instrument coloured by alert kind",
                    key="edge_monitor_alert_timeline")

    def steps() -> None:
        charts.show(charts.step_area(events, when="when", series="kind",
                                     title="Cumulative alerts recorded · by kind", y_title="Alerts"),
                    alt_text="Cumulative count of recorded alerts over time per alert kind",
                    key="edge_monitor_alert_steps")

    if view == "timeline":
        timeline()
    elif view == "steps":
        steps()
    elif view == "both":
        left, right = st.columns(2)
        with left:
            timeline()
        with right:
            steps()
    st.dataframe([{
        "Symbol": symbol(alert), "Kind": _reason(alert.reason)[1],
        "Alert": _reason(alert.reason)[0],
        "Observation at": fmt_time(datetime.fromisoformat(alert.observation_at))
        if alert.observation_at else "No observation",
        "Recorded": fmt_time(datetime.fromisoformat(alert.created_at)),
        "Acknowledged": fmt_time(datetime.fromisoformat(alert.acknowledged_at))
        if alert.acknowledged_at else "Open",
    } for alert in alerts], hide_index=True, width="stretch")
    st.caption("Thesis-review alerts restate your own declared thesis status; they are not market signals.")
    pending = [alert for alert in alerts if alert.acknowledged_at is None]
    if pending:
        panel("ACK", "Open alerts awaiting acknowledgement", meta=f"{len(pending)} open")
    for alert in pending:
        label, kind, _tone = _reason(alert.reason)
        text, action = st.columns([5, 1])
        with text:
            st.text(f"{symbol(alert)} · {kind} · {label} · recorded "
                    f"{fmt_time(datetime.fromisoformat(alert.created_at))}")
        with action:
            if st.button("Acknowledge alert", key=f"edge_monitor_ack_{alert.alert_id}"):
                store.acknowledge(alert.alert_id, as_of=datetime.now(UTC))
                st.rerun()


def _guarded(render: Callable[[PanelContext], None]) -> Callable[[PanelContext], None]:
    """Display panels keep the fixed failure message; no records or SQL leak."""
    def run(ctx: PanelContext) -> None:
        try:
            render(ctx)
        except Exception:
            failure("Monitoring read or check")
    return run


def _unreadable() -> None:
    empty(f"{UNAVAILABLE} — monitoring records could not be read.",
          hint="See the error above. Nothing was recorded or reset.")


def _status(data, now: datetime) -> None:
    if data is None:
        _unreadable()
        return
    rules, quotes, _alerts_seen = data
    if not rules:
        st.info("No saved rules. Choose New rule; schedules cannot invent rules or prices.")
        return
    _status_board(rules, quotes, now)


def _track(data, now: datetime, view: str) -> None:
    if data is None:
        _unreadable()
        return
    rules, quotes, _alerts_seen = data
    if not rules:
        empty("No saved rules to track.", hint="Choose New rule to declare a stop and target.")
        return
    if view != "table":
        _proximity(rules, quotes, now)
    if view != "track":
        _rules_table(rules, quotes, now)
    with st.expander("Saved rules / immutable user declarations"):
        for rule in rules:
            readout(rule)


@dataclass(frozen=True)
class _Schedule:
    enabled_key: str
    active_key: str
    check_key: str
    enabled: bool
    note: str


def _schedule(plan: _Schedule) -> None:
    def set_schedule():
        st.session_state[plan.active_key] = bool(st.session_state.get(plan.enabled_key, False))

    def request_check():
        st.session_state[plan.check_key] = True

    left, right = st.columns([3, 1])
    with left:
        st.checkbox(
            "Enable scheduled local checks for this open workspace / session only",
            value=False, key=plan.enabled_key, on_change=set_schedule,
        )
    with right:
        st.selectbox(
            "Check interval (seconds)", [30, 60, 300], index=1, key="edge_monitor_interval",
        )
    st.caption(
        f"Schedule: {'enabled' if plan.enabled else 'off'} · "
        "Stale/missing/unit-mismatched observations produce reasons, never price alerts."
    )
    st.button("Check saved manual evidence now", key="edge_monitor_check", on_click=request_check)
    if plan.note:
        st.caption(plan.note)


def _task(task: str, store: MonitoringStore, flash_key: str, flash, plan: _Schedule) -> None:
    st.radio("Monitoring task", list(TASKS), horizontal=True, key="edge_monitor_task")
    if task == "New rule":
        _rule_form(store, flash_key)
    elif task == "Manual quote":
        _quote_form(store, flash_key)
    else:
        _schedule(plan)
    if flash:
        st.success(flash)


def _tick(interval: int, flag: str) -> None:
    """Session timer: each tick reruns the page so the check runs before any panel
    reads, and every panel shows the same post-check records. Renders nothing."""
    @st.fragment(run_every=interval)
    def tick():
        if st.session_state.pop(flag, False):
            return  # this full run already checked
        st.rerun()

    st.session_state[flag] = True
    tick()


def render(data_root: Path) -> None:
    boundary("Monitoring / local manual alerts")
    st.caption(
        "Checks run only in this open Monitoring workspace. No detached process, "
        "market-hours claim, provider retrieval, order, or stop/target changes. "
        "Closing EDGE or leaving this workspace stops scheduled checks; no catch-up."
    )
    store = MonitoringStore(data_root)
    suffix = data_root.resolve()
    enabled_key = f"edge_monitor_enabled_{suffix}"
    active_key = f"edge_monitor_active_{suffix}"
    check_key = f"edge_monitor_check_requested_{suffix}"
    flash_key = f"edge_monitor_flash_{suffix}"
    task = st.session_state.get("edge_monitor_task", TASKS[0])
    if task not in TASKS:
        task = TASKS[0]
    if task != "Alerts":
        st.session_state[enabled_key] = False
        st.session_state[active_key] = False
    elif not st.session_state.get(active_key, False):
        st.session_state[enabled_key] = False
    enabled = task == "Alerts" and bool(st.session_state.get(active_key, False))
    interval = st.session_state.get("edge_monitor_interval", 60)
    manual = bool(st.session_state.pop(check_key, False)) and task == "Alerts"
    flash = st.session_state.pop(flash_key, None)

    # The check (scheduled or explicit) runs before any panel reads so every panel,
    # in any arrangement, shows the same post-check records.
    note, failed = "", False
    if enabled or manual:
        try:
            findings = store.check(as_of=datetime.now(UTC))
            note = f"Local check at {datetime.now(UTC).isoformat()} · {len(findings)} findings"
        except Exception:
            failed = True
    try:
        data = (store.rules(), store.quotes(), store.alerts())
    except Exception:
        data = None
    now = datetime.now(UTC)
    if failed or (data is None and task == "Alerts"):
        failure("Monitoring read or check")
    plan = _Schedule(enabled_key, active_key, check_key, enabled, note)
    code, title, meta, badges = TASK_HEADS[task]
    alerts = data[2] if data else ()
    rules = data[0] if data else ()
    shown = grid("monitoring", [
        Panel("kpis", "SUM", "Monitoring summary", lambda c: _summary(data), w=12, h=3, min_w=4,
              min_h=2, fit=True, framed=False),
        Panel("task", code, title, lambda c: _task(task, store, flash_key, flash, plan), w=12,
              h=8, min_w=4, fit=True, meta=meta, badges=badges),
        Panel("status", "STS", "Rule status board", _guarded(lambda c: _status(data, now)), w=12,
              h=8, min_w=3, fit=True, meta=f"Evaluated for display at {fmt_time(now)}",
              badges=("manual",)),
        Panel("track", "PRX", "Threshold proximity", _guarded(lambda c: _track(data, now, c.view)),
              w=12, h=12, min_w=4, fit=True, meta="Stop (red) → target (green)", badges=("manual",),
              views=(("both", "Track + rules table"), ("track", "Proximity track"),
                     ("table", "Rules table"))),
        Panel("alerts", "ALR", "Recorded alerts",
              _guarded(lambda c: _alerts(store, alerts, rules, c.view) if data else _unreadable()),
              w=12, h=12, min_w=4, fit=True, badges=("manual",),
              meta=f"{len(alerts)} newest first · max 200" if data else UNAVAILABLE,
              views=(("both", "Timeline + cumulative"), ("timeline", "Timeline"),
                     ("steps", "Cumulative"), ("table", "Table only"))),
    ])
    if task == "Alerts" and "task" not in shown:
        # Hidden schedule controls must never leave an invisible timer running.
        st.session_state[enabled_key] = False
        st.session_state[active_key] = False
        enabled = False
    if enabled:
        _tick(interval, f"edge_monitor_tick_{suffix}")
