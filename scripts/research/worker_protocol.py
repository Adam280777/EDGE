"""First-party strict synthetic worker protocol; no framework imports."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable

MAX_INPUT = 1_048_576


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate request key.")
        result[key] = value
    return result


def execute(framework: str, fixture_id: str, run: Callable[[], dict]) -> None:
    envelope = {"schema_version": 1, "framework": framework, "status": "error"}
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(payload) > MAX_INPUT:
            raise ValueError("Request too large.")
        request = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
        if request != {
            "schema_version": 1, "framework": framework,
            "operation": "synthetic-fixture", "fixture_id": fixture_id, "synthetic": True,
        } or type(request["schema_version"]) is not int or type(request["synthetic"]) is not bool:
            envelope.update(status="blocked", reasons=["Only the fixed synthetic fixture is accepted."])
        else:
            result = run()
            envelope.update(
                status="ok", synthetic=True, fixture_id=fixture_id,
                request_sha256=hashlib.sha256(payload).hexdigest(), result=result,
                limitations=[
                    "Synthetic fixture only; not financial data, market validation or a trading signal.",
                    "No source rights, calendar/actions/PIT acceptance or production certification.",
                ],
            )
    except Exception as exc:  # noqa: BLE001 -- worker boundary emits sanitized exception type only
        envelope.update(status="error", reasons=[f"Fixture failed: {type(exc).__name__}"])
    sys.stdout.write(json.dumps(envelope, allow_nan=False) + "\n")
