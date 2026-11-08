"""Research lab readiness is passive and does not imply engine acceptance."""

from streamlit.testing.v1 import AppTest


def test_research_lab_readiness_is_passive(tmp_path, monkeypatch):
    import edge.research_workers as workers

    def forbidden(*args, **kwargs):
        raise AssertionError("Opening readiness must not execute a worker.")

    monkeypatch.setattr(workers, "run_research_worker", forbidden)
    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_research_lab import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()
    assert not app.exception
    assert not root.exists()
    assert len(app.button) == 2
    assert any("EXPERIMENTAL ONLY" in item.value for item in app.warning)
    assert app.selectbox[0].options == ["Qlib", "skfolio", "Kronos"]
    assert any("Real-market experiments remain blocked" in item.value for item in app.caption)


def test_explicit_readiness_reports_blockers_without_worker(tmp_path, monkeypatch):
    import edge.research_workers as workers

    calls = []

    def readiness(framework):
        calls.append(framework)
        return ("Attestation unavailable.",)

    def forbidden(*args, **kwargs):
        raise AssertionError("A readiness check must not start a worker.")

    monkeypatch.setattr(workers, "research_runtime_readiness", readiness)
    monkeypatch.setattr(workers, "run_synthetic", forbidden)
    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_research_lab import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()
    assert calls == []
    app.selectbox[0].set_value("Kronos").run()
    app.button[0].click().run()
    assert calls == ["kronos"]
    assert any("Runtime consistency check blocked" in item.value for item in app.warning)
    assert any("Attestation unavailable." in item.value for item in app.text)
    assert not root.exists()


def test_fixture_worker_passes_fixed_cancel_event(monkeypatch):
    from edge import dashboard_research_lab as lab
    from edge import research_workers as workers

    calls = []
    job = lab._Job("qlib", "qlib-linear-three-assets-v1")

    def forbidden_readiness(*args, **kwargs):
        raise AssertionError("Experiment must not invoke a separate readiness budget.")

    monkeypatch.setattr(workers, "research_runtime_readiness", forbidden_readiness)
    def run(framework, fixture_id, *, timeout_seconds, cancel_event):
        calls.append((framework, fixture_id, timeout_seconds, cancel_event))
        return workers.WorkerResult("blocked", None, "Runtime unavailable.")

    monkeypatch.setattr(workers, "run_synthetic", run)
    lab._run(job)
    assert calls == [("qlib", job.fixture, 120, job.cancel)]
    assert job.done.is_set() and job.status == "blocked"
    assert job.message == "Runtime unavailable."


def test_pre_cancel_never_starts_worker(monkeypatch):
    from edge import dashboard_research_lab as lab
    from edge import research_workers as workers

    def forbidden(*args, **kwargs):
        raise AssertionError("Blocked or cancelled run must not start a worker.")

    monkeypatch.setattr(workers, "run_synthetic", forbidden)
    job = lab._Job("qlib", "qlib-linear-three-assets-v1")
    job.cancel.set()
    lab._run(job)
    assert job.status == "cancelled" and job.done.is_set()


def test_result_identity_and_digest_must_match():
    from edge import dashboard_research_lab as lab

    job = lab._Job("qlib", "qlib-linear-three-assets-v1")
    job.response = {
        "schema_version": 1, "framework": "qlib", "status": "ok",
        "synthetic": True, "fixture_id": job.fixture, "request_sha256": "a" * 64,
        "limitations": ["Synthetic only."],
        "runtime": {"python_version": "3.12.10", "primary_package": "pyqlib",
                    "primary_version": "test", "worker_sha256": "c" * 64,
                    "local_consistency_verified": True, "signed_security_attestation": False},
        "result": {"framework_executed": True, "engine_version": "test",
                   "fixture_sha256": "b" * 64},
    }
    assert lab._accepted_result(job) is job.response
    job.response["fixture_id"] = "wrong"
    assert lab._accepted_result(job) is None


def test_skfolio_requires_common_runtime_and_actual_panel_digest():
    from edge import dashboard_research_lab as lab

    job = lab._Job("skfolio", "minimum-variance-three-assets")
    job.response = {
        "schema_version": 1, "framework": "skfolio", "status": "ok",
        "synthetic": True, "fixture_id": job.fixture, "request_sha256": "a" * 64,
        "limitations": ["Synthetic only."],
        "runtime": {"python_version": "3.12.10", "primary_package": "skfolio",
                    "primary_version": "test", "worker_sha256": "b" * 64,
                    "local_consistency_verified": True, "signed_security_attestation": False},
        "result": {"package_version": "test", "panel_digest": "c" * 64,
                   "limitations": ["No market data acceptance."]},
    }
    assert lab._accepted_result(job) is job.response
    job.response["result"]["panel_digest"] = "unavailable"
    assert lab._accepted_result(job) is None
    job.response["result"]["panel_digest"] = "c" * 64
    job.response["runtime"]["local_consistency_verified"] = False
    assert lab._accepted_result(job) is None


def test_explicit_run_is_responsive_cancelled_and_cooled_down(tmp_path, monkeypatch):
    import threading

    from edge import research_workers as workers

    started = threading.Event()
    def run(framework, fixture_id, *, timeout_seconds, cancel_event):
        started.set()
        assert cancel_event.wait(10)
        return workers.WorkerResult("error", None, "Isolated worker cancelled.")

    monkeypatch.setattr(workers, "run_synthetic", run)
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_research_lab import render\n"
        f"render(Path({str(tmp_path / 'data')!r}))\n",
        default_timeout=30,
    ).run()
    next(button for button in app.button if button.label == "Run selected synthetic experiment").click().run()
    assert started.wait(5)
    assert not app.exception
    assert next(button for button in app.button if button.label == "Run selected synthetic experiment").disabled
    next(button for button in app.button if button.label == "Cancel synthetic experiment").click().run()
    job = app.session_state["edge_research_lab_job"]
    assert job.done.wait(10)
    app.run()
    assert not app.exception
    assert job.status == "cancelled"
    assert any("Experiment cancelled" in item.value for item in app.warning)
    assert next(button for button in app.button if button.label == "Run selected synthetic experiment").disabled


def test_full_app_lab_route_does_not_create_storage(tmp_path):
    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "import app\n"
        f"app.main(Path({str(root)!r}))\n",
        default_timeout=30,
    )
    app.session_state["edge_workspace"] = "Research lab"
    app.run()
    assert not app.exception
    assert app.radio(key="edge_workspace").value == "Research lab"
    assert "LAB · Research lab" in app.radio(key="edge_workspace").options
    assert any("Research lab / experimental workflows" in item.value for item in app.header)
    assert not root.exists()


def test_template_prompts_change_with_horizon_without_filling_draft(tmp_path):
    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_decisions import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()
    app.selectbox(key="edge_decision_task").set_value("New research").run()
    assert not app.exception
    assert any("Price action and momentum evidence" in item.value for item in app.text)
    assert all(not item.value for item in app.text_area)
    app.radio(key="edge_decision_horizon").set_value("longterm").run()
    assert not app.exception
    assert any("Capital allocation" in item.value for item in app.text)
    assert all(not item.value for item in app.text_area)
    assert not root.exists()


def test_navigating_to_lab_revokes_scheduled_monitoring(tmp_path):
    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "import app\n"
        f"app.main(Path({str(root)!r}))\n",
        default_timeout=30,
    )
    app.session_state["edge_workspace"] = "Monitoring"
    app.run()
    suffix = str(root.resolve())
    enabled = f"edge_monitor_enabled_{suffix}"
    active = f"edge_monitor_active_{suffix}"
    app.checkbox(key=enabled).check().run()
    assert app.session_state[active] is True
    app.radio(key="edge_workspace").set_value("Research lab").run()
    assert not app.exception
    assert app.session_state[enabled] is False
    assert app.session_state[active] is False
    assert not root.exists()


def test_leaving_lab_requests_cancellation(tmp_path):
    from edge.dashboard_research_lab import _Job

    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "import app\n"
        f"app.main(Path({str(root)!r}))\n",
        default_timeout=30,
    )
    app.session_state["edge_workspace"] = "Research lab"
    app.run()
    job = _Job("qlib", "qlib-linear-three-assets-v1")
    app.session_state["edge_research_lab_job"] = job
    app.radio(key="edge_workspace").set_value("Manual portfolio").run()
    assert not app.exception
    assert job.cancel.is_set()
    assert not root.exists()
