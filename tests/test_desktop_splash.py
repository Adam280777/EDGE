"""Headless native startup control tests; no display or desktop runtime needed."""

import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edge import desktop_splash as splash


class Widget:
    def __init__(self, parent=None, **options):
        self.options = options
        self.bindings = {}
        self.protocols = {}
        self.timers = {}
        self.items = {}
        self.destroy_count = 0
        self.update_error = False

    def configure(self, **options):
        self.options.update(options)

    def bind(self, event, callback):
        self.bindings[event] = callback

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def after(self, delay, callback):
        key = f"timer-{len(self.timers)}"
        self.timers[key] = (delay, callback)
        return key

    def after_cancel(self, key):
        self.timers.pop(key, None)

    def update(self):
        if self.update_error:
            raise splash.tk.TclError("destroyed")

    def destroy(self):
        self.destroy_count += 1

    def create_rectangle(self, *args, **options):
        self.items[options["tags"]] = options

    def itemconfigure(self, key, **options):
        self.items[key].update(options)

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def __getattr__(self, name):
        if name in {
            "withdraw",
            "deiconify",
            "title",
            "resizable",
            "geometry",
            "minsize",
            "pack",
            "grid",
            "columnconfigure",
            "rowconfigure",
            "focus_set",
        }:
            return lambda *args, **kwargs: None
        raise AttributeError(name)


@pytest.fixture
def native(monkeypatch):
    root = Widget()
    monkeypatch.setattr(splash.tk, "Tk", lambda: root)
    monkeypatch.setattr(splash.tkfont, "nametofont", Mock(return_value=Mock()))
    for name in ("Frame", "Label", "Button", "Canvas"):
        monkeypatch.setattr(splash.tk, name, Widget)
    return root


def test_real_stages_immediate_and_plain_text(native):
    view = splash.DesktopSplash(reduced_motion=False)
    view.set_stage("Waiting for local server <ready> & checks")
    assert view._stage.options["text"] == "Waiting for local server <ready> & checks"
    assert view.pump() is True
    assert len(native.timers) == 1
    assert next(iter(native.timers.values()))[0] == 140
    assert "%" not in view._motion_note.options["text"]
    view.close()


@pytest.mark.parametrize("escape", [False, True])
def test_skip_only_freezes_motion_and_keeps_stages(native, escape):
    cancel = Mock()
    view = splash.DesktopSplash(on_cancel=cancel, reduced_motion=False)
    if escape:
        native.bindings["<Escape>"](SimpleNamespace(widget=native))
    else:
        view._skip.options["command"]()
    assert not native.timers
    assert not view.cancelled
    cancel.assert_not_called()
    assert view._skip.options["state"] == "disabled"
    view.set_stage("Waiting for workspace readiness")
    assert view._stage.options["text"] == "Waiting for workspace readiness"
    assert view.pump()
    # A callback already dispatched before Skip must not resurrect motion.
    view._tick()
    assert not native.timers
    view.close()


def test_reduced_motion_has_no_timer(native):
    view = splash.DesktopSplash(reduced_motion=True)
    assert not native.timers
    assert view._skip.options["state"] == "disabled"
    assert "Static view" in view._motion_note.options["text"]
    assert all(item["fill"] == view._TRACK for item in view._cursor.items.values())
    view.close()


def test_default_uses_windows_preference_and_override_bypasses_it(native, monkeypatch):
    preference = Mock(return_value=True)
    monkeypatch.setattr(splash, "_windows_reduced_motion", preference)
    view = splash.DesktopSplash()
    preference.assert_called_once_with()
    assert not native.timers
    view.close()
    preference.reset_mock()
    view = splash.DesktopSplash(reduced_motion=False)
    preference.assert_not_called()
    assert native.timers
    view.close()


@pytest.mark.parametrize("enabled, expected", [(0, True), (1, False)])
def test_spi_client_area_animation(monkeypatch, enabled, expected):
    def query(action, parameter, result, flags):
        assert (action, parameter, flags) == (0x1042, 0, 0)
        ctypes.cast(result, ctypes.POINTER(ctypes.c_int))[0] = enabled
        return 1

    native_query = Mock(side_effect=query)
    monkeypatch.setattr(splash.sys, "platform", "win32")
    monkeypatch.setattr(
        splash.ctypes,
        "WinDLL",
        Mock(return_value=SimpleNamespace(SystemParametersInfoW=native_query)),
        raising=False,
    )
    assert splash._windows_reduced_motion() is expected


@pytest.mark.parametrize("failure", ["error", "unavailable", "non-windows"])
def test_unknown_preference_fails_static(monkeypatch, failure):
    monkeypatch.setattr(
        splash.sys, "platform", "linux" if failure == "non-windows" else "win32"
    )
    loader = Mock(
        side_effect=OSError("unavailable") if failure == "error" else None,
        return_value=SimpleNamespace(SystemParametersInfoW=Mock(return_value=0)),
    )
    monkeypatch.setattr(splash.ctypes, "WinDLL", loader, raising=False)
    assert splash._windows_reduced_motion() is True
    if failure == "non-windows":
        loader.assert_not_called()


def test_cursor_moves_one_segment_and_has_one_timer(native):
    view = splash.DesktopSplash(reduced_motion=False)
    _, tick = native.timers.pop(view._after_id)
    tick()
    assert view._position == 1
    assert len(native.timers) == 1
    lit = [
        key for key, item in view._cursor.items.items() if item["fill"] == view._AMBER
    ]
    assert lit == ["segment-1"]
    view.close()


def test_minimise_pauses_and_restore_does_not_duplicate_timer(native):
    view = splash.DesktopSplash(reduced_motion=False)
    event = SimpleNamespace(widget=native)
    native.bindings["<Unmap>"](event)
    assert not native.timers
    native.bindings["<Map>"](event)
    native.bindings["<Map>"](event)
    assert len(native.timers) == 1
    view.disable_animation()
    native.bindings["<Map>"](event)
    assert not native.timers
    view.close()


@pytest.mark.parametrize("with_callback", [True, False])
def test_window_close_marks_cancelled_once(native, with_callback):
    cancel = Mock() if with_callback else None
    view = splash.DesktopSplash(on_cancel=cancel, reduced_motion=False)
    close_window = native.protocols["WM_DELETE_WINDOW"]
    close_window()
    close_window()
    assert view.cancelled
    assert not view.pump()
    assert native.destroy_count == 1
    assert not native.timers
    if cancel is not None:
        cancel.assert_called_once_with()


def test_close_is_immediate_idempotent_and_does_not_cancel(native):
    cancel = Mock()
    view = splash.DesktopSplash(on_cancel=cancel, reduced_motion=False)
    view.close()
    view.close()
    view.disable_animation()
    view.set_stage("Already closed")
    assert not view.pump()
    assert not view.cancelled
    assert native.destroy_count == 1
    assert not native.timers
    cancel.assert_not_called()


def test_external_tk_destruction_cleans_up(native):
    view = splash.DesktopSplash(reduced_motion=False)
    native.update_error = True
    assert not view.pump()
    assert not native.timers


def test_constructor_failure_destroys_owned_window(native, monkeypatch):
    monkeypatch.setattr(
        splash.DesktopSplash, "_build", Mock(side_effect=RuntimeError("build"))
    )
    with pytest.raises(RuntimeError, match="build"):
        splash.DesktopSplash()
    assert native.destroy_count == 1


def test_non_text_stage_rejected(native):
    view = splash.DesktopSplash(reduced_motion=True)
    with pytest.raises(TypeError, match="text"):
        view.set_stage(50)
    view.close()


def test_status_wraps_at_smaller_window_width(native):
    view = splash.DesktopSplash(reduced_motion=True)
    view._wrap_status(SimpleNamespace(width=520))
    assert view._stage.options["wraplength"] == 464
    assert view._motion_note.options["wraplength"] == 464
    view.close()


def test_callback_failure_cannot_leave_live_window_or_timer(native):
    cancel = Mock(side_effect=RuntimeError("cancel handler"))
    view = splash.DesktopSplash(on_cancel=cancel, reduced_motion=False)
    with pytest.raises(RuntimeError, match="cancel handler"):
        view._request_cancel()
    assert not view.pump()
    assert view.cancelled
    assert not native.timers
    assert native.destroy_count == 1


def test_thread_contract(native):
    view = splash.DesktopSplash(reduced_motion=True)
    errors = []

    def worker():
        for method in (
            lambda: splash.DesktopSplash(),
            lambda: view.set_stage("Wrong thread"),
            view.pump,
            view.close,
            view.disable_animation,
        ):
            try:
                method()
            except RuntimeError as error:
                errors.append(str(error))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert len(errors) == 5
    assert all("main thread" in error for error in errors)
    assert view.pump()
    view.close()
