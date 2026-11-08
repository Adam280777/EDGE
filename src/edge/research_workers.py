"""Explicit isolated research worker transport; never imports ML frameworks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Framework = Literal["qlib", "skfolio", "kronos"]


@dataclass(frozen=True)
class WorkerResult:
    status: Literal["ok", "blocked", "error"]
    response: Mapping[str, object] | None
    error: str | None
    diagnostics: Mapping[str, object] | None = None


_deadline: ContextVar[float | None] = ContextVar("research_deadline", default=None)


def _checkpoint() -> None:
    deadline = _deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("Research API deadline expired.")


def _minimal_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "USERPROFILE",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
        }
    }


def _verification_argv(root: Path) -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        return (sys.executable, "--research-verify")
    # Bootstrap uses the running desktop interpreter, NEVER the unverified
    # canonical research interpreter. No cwd/PATH module lookup under -I.
    code = (
        "import sys;sys.path.insert(0," + repr(str(root / "src")) + ");"
        "from edge.research_workers import _verification_child_main;"
        "_verification_child_main()"
    )
    return (_desktop_python(), "-I", "-B", "-c", code)


def _desktop_python() -> str:
    """Loaded desktop runtime's own base, not an unverified research runtime.

    Avoid an unnecessary source venv redirector/Store activation hop. No
    executable override, PATH lookup or alternative-runtime fallback exists.
    """
    executable = getattr(sys, "_base_executable", sys.executable)
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        raise RuntimeError("Desktop runtime executable unavailable.")
    return executable


def _bounded_child(
    root: Path, framework: Framework, request: Mapping[str, object] | None,
    deadline: float, max_response_bytes: int,
    cancel_event: threading.Event | None,
    *, enrich_runtime: bool = False,
    fixture_id: str | None = None,
) -> WorkerResult:
    """Trusted desktop verification/supervision child owns ALL blocking file I/O."""
    start = time.monotonic()
    if cancel_event is not None and cancel_event.is_set():
        return WorkerResult("error", None, "Research API cancelled; no data accepted.")
    if start >= deadline:
        return WorkerResult("error", None, "Research API timeout; no data accepted.")
    payload = json.dumps({
        "framework": framework, "request": request, "deadline": deadline,
        "max_response_bytes": max_response_bytes,
        "enrich_runtime": enrich_runtime,
        "fixture_id": fixture_id,
    }, allow_nan=False).encode("utf-8")
    if len(payload) > 1_048_576:
        raise ValueError("Worker request exceeds bounded supervisor payload.")
    from edge.desktop_research_job import run_job_worker

    try:
        execution = run_job_worker(
            _verification_argv(root), cwd=root, env=_minimal_environment(),
            payload=payload, timeout_seconds=min(300, max(1, math.ceil(deadline - start))),
            deadline_monotonic=deadline, max_response_bytes=1_048_576,
            cancel_event=cancel_event,
        )
    except (OSError, RuntimeError) as exc:
        return WorkerResult(
            "blocked", None, "Owned verification child could not start or reap safely.",
            {"bootstrap_exception": type(exc).__name__,
             "total_seconds": time.monotonic() - start},
        )
    timing = {"total_seconds": time.monotonic() - start, "stage": "owned-supervisor"}
    timing["supervisor_job_phases"] = dict(getattr(execution, "phase_timings", ()))
    lines = execution.stdout.splitlines()
    events = []
    for line in lines[:-1] if execution.status == "succeeded" else lines:
        try:
            event = json.loads(line, object_pairs_hook=_unique_object)
            if (
                event.get("phase") in {
                    "verification", "fixture-construction", "worker", "runtime-envelope",
                }
                and event.get("event") in {"started", "finished"}
                and type(event.get("elapsed_seconds")) in {float, int}
                and math.isfinite(event["elapsed_seconds"])
                and event["elapsed_seconds"] >= 0
            ):
                events.append({
                    "phase": event["phase"], "event": event["event"],
                    "elapsed_seconds": event["elapsed_seconds"],
                })
        except (ValueError, TypeError, AttributeError):
            pass
    timing["phase_events"] = events
    if execution.status != "succeeded":
        return WorkerResult(
            "error", None, f"Research API {execution.status}; no data accepted.", timing,
        )
    try:
        envelope = json.loads(
            lines[-1].decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
        )
        if (
            envelope["status"] not in {"ok", "blocked", "error"}
            or not isinstance(envelope["diagnostics"], dict)
            or (envelope["response"] is not None and not isinstance(envelope["response"], dict))
            or (envelope["error"] is not None and not isinstance(envelope["error"], str))
        ):
            raise ValueError("Invalid supervision envelope.")
        timing.update(envelope["diagnostics"])
        if time.monotonic() >= deadline:
            return WorkerResult("error", None, "Research API timeout; no data accepted.", timing)
        return WorkerResult(envelope["status"], envelope["response"], envelope["error"], timing)
    except (ValueError, UnicodeError, KeyError, TypeError, IndexError):
        return WorkerResult("error", None, "Malformed verification child response.", timing)


def _verification_child_main() -> None:
    """Desktop-only internal stdin mode, no server/data store/provider startup."""
    start = time.monotonic()
    diagnostics: dict[str, object] = {}

    def emit(phase: str, event: str) -> None:
        sys.stdout.write(json.dumps({
            "phase": phase, "event": event,
            "elapsed_seconds": time.monotonic() - start,
        }) + "\n")
        sys.stdout.flush()

    try:
        payload = sys.stdin.buffer.read(1_048_577)
        if len(payload) > 1_048_576:
            raise ValueError("Oversized supervisor request.")
        data = json.loads(payload, object_pairs_hook=_unique_object)
        framework = data["framework"]
        if framework not in {"qlib", "skfolio", "kronos"}:
            raise ValueError("Unknown framework.")
        deadline = data["deadline"]
        if (
            type(deadline) not in {int, float}
            or not math.isfinite(deadline) or deadline > start + 300
        ):
            raise ValueError("Invalid deadline.")
        _deadline.set(float(deadline))
        _checkpoint()
        root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parents[2]
        canonical = Path(os.environ["LOCALAPPDATA"]) / "EDGE" / "research"
        resources = _resource_root(root)
        if resources is None:
            raise ValueError("Missing resources.")
        phase = time.monotonic()
        emit("verification", "started")
        failure = _verify_attestation(canonical, resources, framework)
        diagnostics["verification_seconds"] = time.monotonic() - phase
        emit("verification", "finished")
        _checkpoint()
        if failure:
            result = WorkerResult("blocked", None, failure)
        elif data["request"] is None:
            result = WorkerResult("ok", None, None)
        else:
            phase = time.monotonic()
            emit("fixture-construction", "started")
            if data.get("fixture_id") is not None:
                expected = {
                    "qlib": "qlib-linear-three-assets-v1",
                    "skfolio": "minimum-variance-three-assets",
                    "kronos": "kronos-mini-ohlcv-v1",
                }
                if data["fixture_id"] != expected[framework]:
                    raise ValueError("Unknown fixed fixture.")
                if framework == "skfolio":
                    from edge.portfolio_optimization import skfolio_synthetic_request

                    data["request"] = skfolio_synthetic_request(data["fixture_id"])
                _checkpoint()
            diagnostics["fixture_construction_seconds"] = time.monotonic() - phase
            emit("fixture-construction", "finished")
            phase = time.monotonic()
            emit("worker", "started")
            result = _run_research_worker_local(
                project_root=root, framework=framework, request=data["request"],
                timeout_seconds=min(300, max(1, math.ceil(deadline - time.monotonic()))),
                max_response_bytes=data["max_response_bytes"],
            )
            diagnostics["worker_and_cleanup_seconds"] = time.monotonic() - phase
            diagnostics["nested_worker_job"] = dict(result.diagnostics or {})
            emit("worker", "finished")
            if data.get("enrich_runtime") is True:
                emit("runtime-envelope", "started")
                result = _enrich_runtime(result, framework)
                emit("runtime-envelope", "finished")
        _checkpoint()
    except TimeoutError:
        result = WorkerResult("error", None, "Research API timeout; no data accepted.")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        result = WorkerResult("blocked", None, "Verification child failed closed.")
    diagnostics["child_total_seconds"] = time.monotonic() - start
    sys.stdout.write(json.dumps({
        "status": result.status, "response": result.response,
        "error": result.error, "diagnostics": diagnostics,
    }, allow_nan=False) + "\n")


def _resource_root(root: Path) -> Path | None:
    if not getattr(sys, "frozen", False):
        return root / "scripts" / "research"
    bundled = getattr(sys, "_MEIPASS", None)
    if not isinstance(bundled, str) or not Path(bundled).is_absolute():
        return None
    return Path(bundled) / "research"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate worker response field.")
        result[name] = value
    return result


def run_research_worker(
    *,
    project_root: Path,
    framework: Framework,
    request: Mapping[str, object],
    timeout_seconds: int,
    max_response_bytes: int,
    cancel_event: threading.Event | None = None,
) -> WorkerResult:
    """Entry-to-exit deadline includes owned verification, execution and cleanup.

    OS launch/termination allowance is owned/documented by run_job_worker.
    No unverified canonical interpreter executes even if verification times out.
    """
    start = time.monotonic()
    _validate_request(framework, request, timeout_seconds, max_response_bytes)
    result = _bounded_child(
        project_root, framework, request, start + timeout_seconds,
        max_response_bytes, cancel_event,
    )
    return _api_result(result, start, start + timeout_seconds, cancel_event)


def _api_result(
    result: WorkerResult, start: float, deadline: float,
    cancel_event: threading.Event | None,
) -> WorkerResult:
    diagnostics = dict(result.diagnostics or {})
    diagnostics["api_total_seconds"] = time.monotonic() - start
    if cancel_event is not None and cancel_event.is_set():
        return WorkerResult("error", None, "Research API cancelled; no data accepted.", diagnostics)
    if result.status == "ok" and time.monotonic() >= deadline:
        return WorkerResult("error", None, "Research API timeout; no data accepted.", diagnostics)
    return WorkerResult(result.status, result.response, result.error, diagnostics)


def _validate_request(
    framework: Framework, request: Mapping[str, object], timeout_seconds: int,
    max_response_bytes: int,
) -> None:
    if framework not in {"qlib", "skfolio", "kronos"}:
        raise ValueError("Unknown research framework.")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("Explicit timeout must be 1..300 seconds.")
    if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1_048_576:
        raise ValueError("Response cap must be 1024..1048576 bytes.")
    if (
        type(request.get("schema_version")) is not int
        or request.get("schema_version") != 1 or request.get("framework") != framework
    ):
        raise ValueError("Request schema/framework mismatch.")
    try:
        payload = json.dumps(dict(request), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise ValueError("Request must be finite JSON data.") from exc
    if len(payload) > 1_048_576:
        raise ValueError("Worker request exceeds 1 MiB.")


def _run_research_worker_local(
    *,
    project_root: Path,
    framework: Framework,
    request: Mapping[str, object],
    timeout_seconds: int,
    max_response_bytes: int,
    cancel_event: threading.Event | None = None,
) -> WorkerResult:
    """Run a fixed worker with bounded stdin/stdout and explicit caller timeout.

    Availability/transport success is not financial-source or production acceptance.
    Worker request semantics and synthetic-only gates are checked by each worker.
    No arbitrary executable, script, model URL or Python configuration is accepted.
    """
    if framework not in {"qlib", "skfolio", "kronos"}:
        raise ValueError("Unknown research framework.")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("Explicit timeout must be 1..300 seconds.")
    if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1_048_576:
        raise ValueError("Response cap must be 1024..1048576 bytes.")
    _checkpoint()
    root = project_root
    canonical = Path(os.environ["LOCALAPPDATA"]) / "EDGE" / "research"
    frozen = bool(getattr(sys, "frozen", False))
    resources = _resource_root(root)
    if resources is None:
        return WorkerResult("blocked", None, "Frozen bundled research resources unavailable.")
    worker = resources / f"{framework}_worker.py"
    executable = canonical / "envs" / framework / "Scripts" / "python.exe"
    if not executable.is_file() or not worker.is_file():
        return WorkerResult("blocked", None, "Isolated interpreter or worker unavailable.")
    if (
        request.get("schema_version") != 1
        or type(request.get("schema_version")) is not int
        or request.get("framework") != framework
    ):
        raise ValueError("Request schema/framework mismatch.")
    try:
        payload = json.dumps(dict(request), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Request must be finite JSON data.") from exc
    if len(payload) > 1_048_576:
        raise ValueError("Worker request exceeds 1 MiB.")
    # Explicit minimal Windows runtime environment; no inherited API credentials.
    environment = _minimal_environment()
    temporary = canonical / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment.update({
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HOME": str(canonical / "models"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TEMP": str(temporary), "TMP": str(temporary),
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    from edge.desktop_research_job import run_job_worker

    _checkpoint()
    try:
        execution = run_job_worker(
            (str(executable), "-I", "-B", str(worker)),
            cwd=canonical if frozen else root, env=environment, payload=payload,
            timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes,
            deadline_monotonic=_deadline.get(), cancel_event=cancel_event,
        )
    except (OSError, RuntimeError):
        return WorkerResult("blocked", None, "Windows Job-controlled worker could not start.")
    if execution.status != "succeeded":
        return WorkerResult(
            "error", None, f"Isolated worker {execution.status}; no data accepted.",
            {"elapsed_seconds": getattr(execution, "elapsed_seconds", None),
             "phases": dict(getattr(execution, "phase_timings", ()))},
        )
    _checkpoint()
    try:
        response = json.loads(
            execution.stdout.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
        )
        if (
            not isinstance(response, dict)
            or type(response.get("schema_version")) is not int
            or response.get("schema_version") != 1
            or response.get("framework") != framework
            or response.get("status") not in {"ok", "blocked", "error"}
        ):
            raise ValueError("Invalid worker response envelope.")
    except (ValueError, UnicodeError):
        return WorkerResult("error", None, "Malformed isolated worker response.")
    transport_hash = hashlib.sha256(payload).hexdigest()
    if response.get("request_sha256", transport_hash) != transport_hash:
        return WorkerResult("error", None, "Worker request digest mismatch.")
    response["request_sha256"] = transport_hash
    return WorkerResult(
        response["status"], response, None,
        {"elapsed_seconds": getattr(execution, "elapsed_seconds", None),
         "phases": dict(getattr(execution, "phase_timings", ()))},
    )


def _sha256(path: Path) -> str:
    _checkpoint()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            _checkpoint()
            chunk = stream.read(65536)
            _checkpoint()
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_attestation(canonical: Path, resources: Path, framework: Framework) -> str | None:
    """Local consistency evidence only; not a signature or financial acceptance."""
    try:
        _checkpoint()
        path = canonical / "attestation-v1.json"
        if path.stat().st_size > 262144:
            return "Research attestation is oversized."
        with path.open("rb") as stream:
            raw = stream.read(262145)
        _checkpoint()
        if len(raw) > 262144:
            return "Research attestation is oversized."
        attestation = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        expected_base = (
            Path(os.environ["LOCALAPPDATA"]) / "Programs" / "EDGE-Research-Python312"
        )
        base = attestation["base"]
        if (
            attestation["schema_version"] != 1
            or Path(base["path"]) != expected_base
            or base["version"] != "3.12.16"
            or _sha256(expected_base / "python.exe") != base["python_sha256"]
            or _sha256(expected_base / "python312.dll") != base["dll_sha256"]
        ):
            return "Research base runtime attestation mismatch."
        engine = attestation["engines"][framework]
        interpreter = canonical / "envs" / framework / "Scripts" / "python.exe"
        if (
            Path(engine["path"]) != interpreter.parent.parent
            or engine["python_version"] != "3.12.16"
            or _sha256(interpreter) != engine["python_sha256"]
            or _sha256(interpreter.parent.parent / "pyvenv.cfg") != engine["config_sha256"]
        ):
            return "Research environment attestation mismatch."
        expected_primary = {
            "qlib": ("pyqlib", "0.9.7"), "skfolio": ("skfolio", "1.12.5"),
            "kronos": ("torch", "2.7.1+cpu"),
        }[framework]
        if (engine["primary_package"], engine["primary_version"]) != expected_primary:
            return "Research pinned primary dependency mismatch."
        if not any(
            (package["name"].lower(), package["version"]) == expected_primary
            for package in engine["packages"]
        ):
            return "Research primary package metadata missing."
        for package in engine["packages"]:
            relative = Path(package["metadata_path"])
            parts = relative.parts
            if (
                len(parts) != 4 or parts[:2] != ("Lib", "site-packages")
                or not parts[2].endswith(".dist-info") or parts[3] != "METADATA"
                or _sha256(interpreter.parent.parent / relative) != package["metadata_sha256"]
            ):
                return "Research dependency metadata attestation mismatch."
        allowed_resources = {
            "qlib_worker.py", "skfolio_worker.py", "kronos_worker.py",
            "worker_protocol.py", "kronos-artifacts.json", "kronos-payload-verification.json",
            "vendor/kronos/model/__init__.py", "vendor/kronos/model/kronos.py",
            "vendor/kronos/model/module.py", "vendor/kronos/LICENSE",
        }
        required = {f"{framework}_worker.py"}
        if framework in {"qlib", "kronos"}:
            required.add("worker_protocol.py")
        if framework == "kronos":
            required.update(allowed_resources - {"qlib_worker.py", "skfolio_worker.py"})
        if not required.issubset(attestation["resource_hashes"]):
            return "Worker resource attestation missing."
        for relative, digest in attestation["resource_hashes"].items():
            if relative not in allowed_resources or _sha256(resources / relative) != digest:
                return "Bundled/source worker resource attestation mismatch."
        if framework == "kronos":
            expected_models = {
                "Kronos-mini/model.safetensors", "Kronos-mini/config.json",
                "Kronos-Tokenizer-2k/model.safetensors", "Kronos-Tokenizer-2k/config.json",
            }
            if set(attestation["model_hashes"]) != expected_models:
                return "Canonical model payload attestations missing."
            for relative, digest in attestation["model_hashes"].items():
                if relative not in expected_models or _sha256(canonical / "models" / relative) != digest:
                    return "Canonical model payload attestation mismatch."
    except TimeoutError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        return "Research attestation unavailable or malformed."
    return None


def research_runtime_readiness(
    framework: Framework, *, project_root: Path | None = None,
    timeout_seconds: int = 10, cancel_event: threading.Event | None = None,
) -> tuple[str, ...]:
    """Bounded owned verification child; no framework imports or downloads."""
    start = time.monotonic()
    if framework not in {"qlib", "skfolio", "kronos"}:
        raise ValueError("Unknown research framework.")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("Readiness timeout must be 1..300 seconds.")
    frozen = bool(getattr(sys, "frozen", False))
    if frozen and project_root is not None:
        return ("Frozen research rejects workspace root overrides.",)
    root = project_root or Path(__file__).parents[2]
    result = _bounded_child(root, framework, None, start + timeout_seconds, 1024, cancel_event)
    result = _api_result(result, start, start + timeout_seconds, cancel_event)
    return () if result.status == "ok" else (result.error or "Research verification blocked.",)


def run_synthetic(
    framework: Framework,
    fixture_id: str,
    *,
    project_root: Path | None = None,
    timeout_seconds: int = 120,
    cancel_event: threading.Event | None = None,
) -> WorkerResult:
    """Blocking explicit fixture call; UI must use a thread and close/cancel Event."""
    start = time.monotonic()
    fixtures = {
        "qlib": "qlib-linear-three-assets-v1",
        "kronos": "kronos-mini-ohlcv-v1",
        "skfolio": "minimum-variance-three-assets",
    }
    if framework not in fixtures or fixture_id != fixtures[framework]:
        raise ValueError("Unknown fixed synthetic fixture.")
    frozen = bool(getattr(sys, "frozen", False))
    if frozen and project_root is not None:
        return WorkerResult("blocked", None, "Frozen research rejects workspace root overrides.")
    root = project_root or Path(__file__).parents[2]
    # Even the cold import/request construction for skfolio belongs in the
    # owned child, not in a potentially blocking caller-side import.
    request = {
        "schema_version": 1, "framework": framework, "operation": "synthetic-fixture",
        "fixture_id": fixture_id, "synthetic": True,
    }
    _validate_request(framework, request, timeout_seconds, 1_048_576)
    result = _bounded_child(
        root, framework, request, start + timeout_seconds, 1_048_576, cancel_event,
        enrich_runtime=True,
        fixture_id=fixture_id,
    )
    if result.response is None:
        return _api_result(result, start, start + timeout_seconds, cancel_event)
    response = dict(result.response)
    response["fixture_id"] = fixture_id
    response["synthetic"] = True
    response["limitations"] = [
        "Fixed synthetic fixture only; no real-data acceptance, trading signal or production certification.",
        "Private personal research purpose; no provider rights or market validation inferred.",
        "Astral's third-party Python 3.12.16 prebuilt has recorded provenance; no production security certification is inferred.",
    ]
    return _api_result(
        WorkerResult(result.status, response, result.error, result.diagnostics),
        start, start + timeout_seconds, cancel_event,
    )


def _enrich_runtime(result: WorkerResult, framework: Framework) -> WorkerResult:
    """Runs inside owned supervisor, never unbounded file I/O in caller."""
    _checkpoint()
    if result.response is None:
        return result
    response = dict(result.response)
    canonical = Path(os.environ["LOCALAPPDATA"]) / "EDGE" / "research"
    if (canonical / "attestation-v1.json").is_file():
        try:
            with (canonical / "attestation-v1.json").open("rb") as stream:
                data = stream.read(262145)
            if len(data) > 262144:
                raise ValueError("Oversized runtime attestation.")
            attestation = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
            engine = attestation["engines"][framework]
            worker_digest = attestation["resource_hashes"][f"{framework}_worker.py"]
        except (OSError, ValueError, KeyError, TypeError):
            return WorkerResult("blocked", None, "Runtime attestation changed during execution.")
        response["runtime"] = {
            "python_version": engine["python_version"],
            "primary_package": engine["primary_package"], "primary_version": engine["primary_version"],
            "worker_sha256": worker_digest,
            "local_consistency_verified": True, "signed_security_attestation": False,
        }
    else:
        response["runtime"] = {"local_consistency_verified": False}
    _checkpoint()
    return WorkerResult(result.status, response, result.error)
