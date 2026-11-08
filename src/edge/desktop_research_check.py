"""Opt-in real native UI exercise; fixed synthetic inputs, no provider/user data."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


def _wait(window: Any, predicate: Any, seconds: float) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = window.evaluate_js("document.body.innerText") or ""
        if predicate(body):
            return body
        time.sleep(0.25)
    raise TimeoutError("Fixed diagnostic UI stage expired.")


def _contains(body: str, marker: str) -> bool:
    """Native innerText reflects CSS uppercase headings; match rendered text."""
    return marker.casefold() in body.casefold()


def _click(window: Any, text: str, selector: str = "button") -> None:
    # Streamlit can expose body text before React has remounted its controls.
    # Like the existing native smoke, wait for a real enabled DOM control.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        clicked = window.evaluate_js(
            "(() => { const el = Array.from(document.querySelectorAll("
            + json.dumps(selector)
            + ")).find(e => e.innerText.trim().toLowerCase() === "
            + json.dumps(text.lower())
            + "); if (!el || el.disabled) return false; el.click(); return true; })()"
        )
        if clicked:
            return
        time.sleep(0.25)
    raise RuntimeError("Fixed diagnostic UI control unavailable.")


def _select(window: Any, engine: str) -> None:
    window.evaluate_js(
        '(() => { const e = document.querySelector(\'[data-testid="stSelectbox"] '
        '[role="combobox"]\'); if (!e) return false; e.click(); return true; })()'
    )
    _wait(window, lambda body: engine in body, 5)
    _click(window, engine, '[role="option"]')


def exercise_research_window(window: Any, report_path: Path, server_pid: int) -> None:
    """Actual DOM clicks only; no direct wrapper calls, mocks or accepted status injection."""
    report: dict[str, Any] = {
        "ok": False,
        "native_window": True,
        "server_pid": server_pid,
        "parent_pid": os.getpid(),
        "synthetic_only": True,
        "canonical_research_paths_unchanged": True,
        "scope": "lab_only",
        "engines": [],
    }
    try:
        report["stage"] = "lab-entry"
        _wait(window, lambda body: _contains(body, "Live trading disabled"), 30)
        _click(window, "LAB · Research lab", '[data-testid="stRadio"] label')
        _wait(
            window,
            lambda body: _contains(body, "Research lab / experimental workflows"),
            15,
        )
        time.sleep(1)
        body = window.evaluate_js("document.body.innerText")
        report["streamlit_exception_present"] = bool(
            window.evaluate_js(
                "document.querySelector('[data-testid=\"stException\"]') !== null"
            )
        )
        if report["streamlit_exception_present"]:
            raise RuntimeError("Actual Streamlit exception rendered.")
        report["no_autostart"] = "Cancel synthetic experiment" not in body
        if not report["no_autostart"]:
            raise RuntimeError("Unexpected diagnostic autostart.")
        for engine, framework in (
            ("Qlib", "qlib"),
            ("skfolio", "skfolio"),
            ("Kronos", "kronos"),
        ):
            report["stage"] = "engine-select:" + framework
            if engine != "Qlib":
                _select(window, engine)
            _wait(window, lambda body: "Brief cooldown" not in body, 10)
            # Wait for the selected fixture to render, not just a DOM dropdown.
            _wait(
                window,
                lambda body, framework=framework: (
                    {
                        "qlib": "qlib-linear-three-assets-v1",
                        "skfolio": "minimum-variance-three-assets",
                        "kronos": "kronos-mini-ohlcv-v1",
                    }[framework]
                    in body
                ),
                10,
            )
            start = time.monotonic()
            report["stage"] = "engine-run:" + framework
            _click(window, "Run selected synthetic experiment")
            body = _wait(
                window,
                lambda body, framework=framework: (
                    f"Engine: {framework} / fixed fixture:" in body
                    or "No research result accepted." in body
                    or "failed identity or provenance" in body
                ),
                124,
            )
            accepted = (
                "Synthetic experiment completed. This is not financial evidence or a trading signal."
                in body
                and f"Engine: {framework} / fixed fixture:" in body
            )
            digests = re.findall(
                r"(?:Worker|Request|Fixture|Input panel) SHA-256: ([a-fA-F0-9]{64})",
                body,
            )
            report["engines"].append(
                {
                    "framework": framework,
                    "accepted_ui": accepted,
                    "elapsed_seconds": time.monotonic() - start,
                    "digests": digests,
                    "runtime_31216": "Runtime: Python 3.12.16" in body,
                }
            )
            if not accepted or len(digests) != 3:
                raise RuntimeError("Synthetic UI result not accepted.")
            time.sleep(5.5)
        # Actual cancellation, navigation revocation, then closing while RUNNING.
        report["stage"] = "explicit-cancel"
        _select(window, "Qlib")
        _wait(window, lambda body: "qlib-linear-three-assets-v1" in body, 10)
        _click(window, "Run selected synthetic experiment")
        _wait(window, lambda body: "Cancel synthetic experiment" in body, 10)
        _click(window, "Cancel synthetic experiment")
        _wait(window, lambda body: "Experiment cancelled." in body, 10)
        report["explicit_cancel_ui"] = True
        time.sleep(5.5)
        report["stage"] = "navigation-cancel"
        _click(window, "Run selected synthetic experiment")
        _wait(window, lambda body: "Cancel synthetic experiment" in body, 10)
        _click(window, "ENG · Engine", '[data-testid="stRadio"] label')
        _wait(window, lambda body: _contains(body, "SYNTHETIC DATA ONLY"), 15)
        time.sleep(3)
        _click(window, "LAB · Research lab", '[data-testid="stRadio"] label')
        _wait(window, lambda body: "Experiment cancelled." in body, 10)
        report["navigation_cancel_ui"] = True
        _wait(window, lambda body: "Brief cooldown" not in body, 10)
        _click(window, "Run selected synthetic experiment")
        _wait(window, lambda body: "Cancel synthetic experiment" in body, 10)
        time.sleep(1)
        report["stage"] = "close-while-running"
        report["closed_while_research_running"] = True
        report["ok"] = True
    except Exception as error:  # noqa: BLE001 -- sanitized diagnostic boundary
        report["error_type"] = type(error).__name__
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        window.destroy()
