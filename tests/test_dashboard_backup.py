"""Offline recovery UI acceptance: synthetic notes, temp DBs, mocked dialogs."""

from __future__ import annotations

import socket
import sys
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import dashboard_backup as panel
from edge import native_dialogs_backup as dialogs
from edge.journal import JournalDraft
from edge.journal_backup import DATABASE, backup_journal
from edge.journal_store import JournalStore

PRIVATE = "PRIVATE-NOTE SQL /private/path credential"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Backup UI must not access providers, engines or network")

    import edge.data
    import edge.simulation_service

    monkeypatch.setattr(edge.data, "lookup_sec_filings", forbidden)
    monkeypatch.setattr(edge.simulation_service, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    real_connect = socket.socket.connect

    def local_only(sock: socket.socket, address: object) -> object:
        # Windows asyncio's AppTest runtime needs its internal socket pair.
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            forbidden()
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    selected: set[str] = set()
    fake = SimpleNamespace(
        header=Mock(),
        caption=Mock(),
        warning=Mock(),
        subheader=Mock(),
        divider=Mock(),
        error=Mock(),
        success=Mock(),
        session_state={"edge_active_journal_root": Path("unchanged")},
        button=Mock(side_effect=lambda label, *, key, **kwargs: key in selected),
        selected=selected,
    )
    monkeypatch.setattr(panel, "st", fake)
    for name in (
        "choose_backup_destination",
        "choose_backup_source",
        "choose_restore_destination",
    ):
        monkeypatch.setattr(dialogs, name, Mock(return_value=None))
    return fake


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    store = JournalStore(root)
    for horizon in ("swing", "long-term"):
        store.create(
            JournalDraft(
                horizon,
                "NOTE",
                "Synthetic company",
                "Temporary user opinion",
                date(2020, 1, 2),
                "Test only, not financial research",
                "",
                "",
                "",
                "",
                (),
            )
        )
    return root


def files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def click(ui: SimpleNamespace, key: str, root: Path) -> None:
    ui.selected.add(key)
    panel.render(root)


def test_idle_render_no_storage_dialog_or_session_selection(
    ui: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "absent"
    backup = Mock(side_effect=AssertionError("No backup on render"))
    restore = Mock(side_effect=AssertionError("No restore on render"))
    monkeypatch.setattr(panel, "backup_journal", backup)
    monkeypatch.setattr(panel, "restore_journal", restore)
    state = ui.session_state.copy()
    for _ in range(3):
        panel.render(root)
    assert not root.exists()
    assert not files(tmp_path)
    assert ui.session_state == state
    backup.assert_not_called()
    restore.assert_not_called()
    dialogs.choose_backup_destination.assert_not_called()
    dialogs.choose_backup_source.assert_not_called()
    dialogs.choose_restore_destination.assert_not_called()


def test_backup_success_counts_and_current_root_unchanged(
    ui: SimpleNamespace, source: Path, tmp_path: Path
) -> None:
    before = files(source)
    archive = tmp_path / "backup.zip"
    dialogs.choose_backup_destination.return_value = archive
    click(ui, "edge_backup_create", source)
    assert archive.is_file()
    assert files(source) == before
    assert ui.session_state == {"edge_active_journal_root": Path("unchanged")}
    ui.success.assert_called_once_with("Backup created · 2 journal entries.")
    ui.error.assert_not_called()


def test_backup_of_missing_journal_does_not_initialize_it(
    ui: SimpleNamespace, tmp_path: Path
) -> None:
    dialogs.choose_backup_destination.return_value = tmp_path / "backup.zip"
    click(ui, "edge_backup_create", tmp_path / "absent")
    assert not files(tmp_path)
    assert not (tmp_path / "absent").exists()
    ui.error.assert_called_once_with(panel._BACKUP_ERROR)
    ui.success.assert_not_called()


@pytest.mark.parametrize("contents", [b"", b"Existing backup", b"Simulation output"])
def test_backup_never_overwrites_dialog_approved_target(
    ui: SimpleNamespace, source: Path, tmp_path: Path, contents: bytes
) -> None:
    target = tmp_path / "existing.zip"
    target.write_bytes(contents)
    before = files(tmp_path)
    dialogs.choose_backup_destination.return_value = target
    click(ui, "edge_backup_create", source)
    assert files(tmp_path) == before
    ui.error.assert_called_once_with(panel._BACKUP_ERROR)
    ui.success.assert_not_called()


@pytest.mark.parametrize("existing_empty", [False, True])
def test_restore_offers_candidate_only_and_keeps_default_and_simulation(
    ui: SimpleNamespace, source: Path, tmp_path: Path, existing_empty: bool
) -> None:
    archive = tmp_path / "backup.zip"
    backup_journal(source, archive)
    simulation = source / "simulation"
    simulation.mkdir()
    (simulation / "output.json").write_bytes(b"synthetic simulation sentinel")
    before = files(source)
    destination = tmp_path / "restored"
    if existing_empty:
        destination.mkdir()
    dialogs.choose_backup_source.return_value = archive
    dialogs.choose_restore_destination.return_value = destination
    click(ui, "edge_backup_restore", source)
    assert ui.session_state[panel.RESTORED_JOURNAL_ROOT_KEY] == destination.absolute()
    assert ui.session_state["edge_active_journal_root"] == Path("unchanged")
    assert files(source) == before
    assert set(files(destination)) == {DATABASE}
    assert len(JournalStore(destination).list_entries(horizon="swing", limit=10)) == 1
    ui.success.assert_called_once_with("Restore completed · 2 journal entries.")
    ui.error.assert_not_called()
    # Navigating back must not rerun restore or silently choose the candidate.
    ui.selected.clear()
    state = ui.session_state.copy()
    panel.render(source)
    assert ui.session_state == state
    dialogs.choose_restore_destination.assert_called_once_with()


@pytest.mark.parametrize("target_kind", ["journal", "simulation", "other", "empty_db"])
def test_restore_refuses_all_nonempty_destinations(
    ui: SimpleNamespace, source: Path, tmp_path: Path, target_kind: str
) -> None:
    archive = tmp_path / "backup.zip"
    backup_journal(source, archive)
    destination = source if target_kind == "journal" else tmp_path / "occupied"
    if target_kind != "journal":
        destination.mkdir()
        name = DATABASE if target_kind == "empty_db" else f"{target_kind}.json"
        (destination / name).write_bytes(
            b"" if target_kind == "empty_db" else b"sentinel"
        )
    before = files(tmp_path)
    state = ui.session_state.copy()
    dialogs.choose_backup_source.return_value = archive
    dialogs.choose_restore_destination.return_value = destination
    click(ui, "edge_backup_restore", source)
    assert files(tmp_path) == before
    assert ui.session_state == state
    ui.error.assert_called_once_with(panel._RESTORE_ERROR)
    ui.success.assert_not_called()


@pytest.mark.parametrize("stage", ["backup", "source", "destination"])
def test_cancel_no_write_or_state_change(
    ui: SimpleNamespace, tmp_path: Path, stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = tmp_path / "earlier-restored"
    ui.session_state[panel.RESTORED_JOURNAL_ROOT_KEY] = previous
    state = ui.session_state.copy()
    backup = Mock()
    restore = Mock()
    monkeypatch.setattr(panel, "backup_journal", backup)
    monkeypatch.setattr(panel, "restore_journal", restore)
    if stage == "destination":
        dialogs.choose_backup_source.return_value = tmp_path / "selected.zip"
    click(
        ui,
        "edge_backup_create" if stage == "backup" else "edge_backup_restore",
        tmp_path,
    )
    assert ui.session_state == state
    assert not files(tmp_path)
    backup.assert_not_called()
    restore.assert_not_called()
    ui.success.assert_not_called()
    ui.error.assert_not_called()
    if stage == "source":
        dialogs.choose_restore_destination.assert_not_called()


@pytest.mark.parametrize(
    "stage",
    [
        "backup_dialog",
        "source_dialog",
        "destination_dialog",
        "backup_api",
        "restore_api",
    ],
)
def test_failures_sanitized_and_prior_candidate_retained(
    ui: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    dialogs.choose_backup_destination.return_value = tmp_path / "backup.zip"
    dialogs.choose_backup_source.return_value = tmp_path / "backup.zip"
    dialogs.choose_restore_destination.return_value = tmp_path / "restored"
    targets = {
        "backup_dialog": (dialogs, "choose_backup_destination"),
        "source_dialog": (dialogs, "choose_backup_source"),
        "destination_dialog": (dialogs, "choose_restore_destination"),
        "backup_api": (panel, "backup_journal"),
        "restore_api": (panel, "restore_journal"),
    }
    owner, name = targets[stage]
    monkeypatch.setattr(owner, name, Mock(side_effect=RuntimeError(PRIVATE)))
    ui.session_state[panel.RESTORED_JOURNAL_ROOT_KEY] = tmp_path / "previous"
    state = ui.session_state.copy()
    click(
        ui,
        "edge_backup_create" if stage.startswith("backup") else "edge_backup_restore",
        tmp_path,
    )
    ui.success.assert_not_called()
    ui.error.assert_called_once()
    assert PRIVATE not in str(ui.error.call_args)
    assert ui.session_state == state
    assert not files(tmp_path)


def test_corrupt_restore_creates_nothing_and_sanitizes(
    ui: SimpleNamespace, tmp_path: Path
) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(PRIVATE.encode())
    dialogs.choose_backup_source.return_value = archive
    dialogs.choose_restore_destination.return_value = tmp_path / "never"
    before = files(tmp_path)
    click(ui, "edge_backup_restore", tmp_path)
    assert files(tmp_path) == before
    assert not (tmp_path / "never").exists()
    assert panel.RESTORED_JOURNAL_ROOT_KEY not in ui.session_state
    ui.error.assert_called_once_with(panel._RESTORE_ERROR)
    ui.success.assert_not_called()


@pytest.mark.parametrize("count", [PRIVATE, True, -1, None])
def test_untrusted_count_never_rendered(
    ui: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: object
) -> None:
    dialogs.choose_backup_destination.return_value = tmp_path / "backup.zip"
    monkeypatch.setattr(
        panel, "backup_journal", Mock(return_value={"entry_count": count})
    )
    click(ui, "edge_backup_create", tmp_path)
    ui.success.assert_not_called()
    assert PRIVATE not in str(ui.error.call_args)


def test_actual_streamlit_buttons_and_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def script(root: str) -> None:
        from pathlib import Path

        from edge.dashboard_backup import render

        render(Path(root))

    choose = Mock(return_value=None)
    monkeypatch.setattr(dialogs, "choose_backup_destination", choose)
    app = AppTest.from_function(script, args=(str(tmp_path / "absent"),)).run()
    assert not app.exception
    choose.assert_not_called()
    app.button(key="edge_backup_create").click().run()
    assert not app.exception
    choose.assert_called_once_with()
    assert not app.error
    assert not app.success
    assert not files(tmp_path)


def test_actual_streamlit_restore_sets_pending_path_only(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def script(root: str) -> None:
        from pathlib import Path

        from edge.dashboard_backup import render

        render(Path(root))

    archive = tmp_path / "backup.zip"
    backup_journal(source, archive)
    destination = tmp_path / "restored"
    monkeypatch.setattr(dialogs, "choose_backup_source", Mock(return_value=archive))
    monkeypatch.setattr(
        dialogs, "choose_restore_destination", Mock(return_value=destination)
    )
    app = AppTest.from_function(script, args=(str(source),)).run()
    app.session_state["edge_open_journal_root"] = source
    app.button(key="edge_backup_restore").click().run()
    assert not app.exception
    assert app.session_state[panel.RESTORED_JOURNAL_ROOT_KEY] == destination.absolute()
    assert app.session_state["edge_open_journal_root"] == source
    assert app.success[0].value == "Restore completed · 2 journal entries."
    app.run()
    assert not app.exception
    assert app.session_state[panel.RESTORED_JOURNAL_ROOT_KEY] == destination.absolute()
    assert app.session_state["edge_open_journal_root"] == source
    dialogs.choose_restore_destination.assert_called_once_with()


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    receiver = Mock()
    receiver.poll.return_value = True
    receiver.recv.return_value = ("cancelled", None)
    sender = Mock()
    process = Mock()
    process.is_alive.return_value = False
    context = Mock()
    context.Pipe.return_value = (receiver, sender)
    context.Process.return_value = process
    monkeypatch.setattr(
        dialogs.multiprocessing, "get_context", Mock(return_value=context)
    )
    return SimpleNamespace(
        receiver=receiver, sender=sender, process=process, context=context
    )


@pytest.mark.parametrize(
    ("name", "operation"),
    [
        ("choose_backup_destination", "save"),
        ("choose_backup_source", "open"),
        ("choose_restore_destination", "directory"),
    ],
)
def test_native_spawn_selected_path_and_closed_lifecycle(
    native: SimpleNamespace, tmp_path: Path, name: str, operation: str
) -> None:
    selected = tmp_path / "selected"
    native.receiver.recv.return_value = ("selected", str(selected))
    assert getattr(dialogs, name)() == selected
    dialogs.multiprocessing.get_context.assert_called_once_with("spawn")
    native.context.Pipe.assert_called_once_with(duplex=False)
    native.context.Process.assert_called_once_with(
        target=dialogs._dialog_worker, args=(native.sender, operation), daemon=True
    )
    native.sender.close.assert_called()
    native.receiver.close.assert_called_once()
    native.process.join.assert_called_once_with(timeout=1)
    native.process.close.assert_called_once()


def test_native_cancel_releases_lock(native: SimpleNamespace) -> None:
    assert dialogs.choose_backup_source() is None
    assert dialogs.choose_restore_destination() is None
    assert not dialogs._DIALOG_LOCK.locked()


@pytest.mark.parametrize("failure", ["timeout", "eof", "error", "relative", "start"])
def test_native_failures_reap_and_sanitize(
    native: SimpleNamespace, failure: str
) -> None:
    if failure == "timeout":
        native.receiver.poll.return_value = False
        native.process.is_alive.side_effect = [True, True, False]
    elif failure == "eof":
        native.receiver.recv.side_effect = EOFError(PRIVATE)
    elif failure == "error":
        native.receiver.recv.return_value = ("error", PRIVATE)
    elif failure == "relative":
        native.receiver.recv.return_value = ("selected", "../relative")
    else:
        native.process.start.side_effect = OSError(PRIVATE)
    with pytest.raises(dialogs.NativeDialogError) as error:
        dialogs.choose_backup_source()
    assert PRIVATE not in str(error.value)
    assert not dialogs._DIALOG_LOCK.locked()
    native.sender.close.assert_called()
    native.receiver.close.assert_called_once()
    native.process.close.assert_called_once()
    if failure == "timeout":
        native.process.terminate.assert_called_once()
        native.process.kill.assert_called_once()


def test_native_simultaneous_request_fails_closed(native: SimpleNamespace) -> None:
    with dialogs._DIALOG_LOCK, pytest.raises(dialogs.NativeDialogError):
        dialogs.choose_backup_source()
    native.context.Process.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_tk_worker_root_destroyed_and_fixed_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: bool
) -> None:
    root = Mock()
    picker = (
        Mock(side_effect=RuntimeError(PRIVATE))
        if failure
        else Mock(return_value=str(tmp_path))
    )
    tkinter = SimpleNamespace(
        Tk=Mock(return_value=root),
        filedialog=SimpleNamespace(asksaveasfilename=picker),
    )
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    sender = Mock()
    dialogs._dialog_worker(sender, "save")
    root.withdraw.assert_called_once()
    root.destroy.assert_called_once()
    sender.send.assert_called_once_with(
        ("error", None) if failure else ("selected", str(tmp_path))
    )
    sender.close.assert_called_once()
    assert PRIVATE not in str(sender.send.call_args)


def test_tk_rejected_on_script_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    tk = SimpleNamespace(Tk=Mock())
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    errors: list[Exception] = []

    def call() -> None:
        try:
            dialogs._show_dialog("open")
        except dialogs.NativeDialogError as error:
            errors.append(error)

    worker = threading.Thread(target=call)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], dialogs.NativeDialogError)
    tk.Tk.assert_not_called()


def test_worker_cancel_and_closed_parent_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dialogs, "_show_dialog", Mock(return_value=""))
    sender = Mock()
    dialogs._dialog_worker(sender, "open")
    sender.send.assert_called_once_with(("cancelled", None))
    sender.close.assert_called_once()
    sender.reset_mock()
    sender.send.side_effect = BrokenPipeError(PRIVATE)
    dialogs._dialog_worker(sender, "directory")
    sender.close.assert_called_once()
