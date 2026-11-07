"""Isolated native startup view; the desktop runtime owns readiness and work.

Create and call on the main thread. The caller pumps while checking its own
worker/queue, reports real stages, and closes immediately when ready. This
module never starts workers, reads application data, or waits for animation.
"""

import ctypes
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont


def _windows_reduced_motion() -> bool:
    """Conservatively stay static if Windows' animation preference is unknown."""
    if sys.platform != "win32":
        return True
    try:
        query = ctypes.WinDLL("user32", use_last_error=True).SystemParametersInfoW
        query.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        query.restype = ctypes.c_int
        enabled = ctypes.c_int()
        # SPI_GETCLIENTAREAANIMATION returns a BOOL through pvParam.
        if query(0x1042, 0, ctypes.byref(enabled), 0):
            return not bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


class DesktopSplash:
    """Main-thread-only Tk control, independent of server/WebView lifecycle.

    ``pump()`` returns False after closing. Skip/Escape only stop motion.
    Window close or Cancel startup mark ``cancelled`` and invoke ``on_cancel``
    at most once; programmatic ``close()`` never cancels startup.
    Tk unavailability raises ``tkinter.TclError`` for the runtime to handle.
    """

    _BACKGROUND = "#050505"
    _AMBER = "#ffc15a"
    _MUTED = "#c6ad83"
    _CYAN = "#65dce8"
    _TRACK = "#6e6045"
    _SEGMENTS = 18
    _TICK_MS = 140

    def __init__(
        self,
        on_cancel: Callable[[], None] | None = None,
        reduced_motion: bool | None = None,
    ) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("DesktopSplash must be created on the main thread.")
        self._thread = threading.get_ident()
        self._on_cancel = on_cancel
        self._closed = False
        self._cancelled = False
        self._after_id: str | None = None
        self._position = 0
        self._fonts: list[tkfont.Font] = []
        self._static = (
            _windows_reduced_motion() if reduced_motion is None else reduced_motion
        )
        self._root = tk.Tk()
        self._root.withdraw()
        try:
            self._build()
            self._root.deiconify()
            if not self._static:
                self._schedule()
        except Exception:
            self.close()
            raise

    @property
    def cancelled(self) -> bool:
        """Whether the user explicitly requested cancellation (not Skip)."""
        return self._cancelled

    def _build(self) -> None:
        root = self._root
        root.title("EDGE — Starting workspace")
        root.configure(background=self._BACKGROUND)
        root.resizable(True, True)
        width = min(640, max(320, root.winfo_screenwidth() - 64))
        height = min(360, max(280, root.winfo_screenheight() - 96))
        root.geometry(
            f"{width}x{height}"
            f"+{max(0, (root.winfo_screenwidth() - width) // 2)}"
            f"+{max(0, (root.winfo_screenheight() - height) // 2)}"
        )
        root.minsize(min(width, 520), min(height, 360))
        root.protocol("WM_DELETE_WINDOW", self._request_cancel)
        root.bind("<Escape>", self.disable_animation)
        root.bind("<Unmap>", self._on_unmap)
        root.bind("<Map>", self._on_map)

        body = tk.Frame(root, background=self._BACKGROUND, padx=28, pady=24)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)

        def label(text: str, *, size: int = 11, color: str = self._AMBER) -> tk.Label:
            return tk.Label(
                body,
                text=text,
                background=self._BACKGROUND,
                foreground=color,
                font=self._font(size),
                anchor="w",
                justify="left",
            )

        label("EDGE", size=24).grid(row=0, column=0, sticky="w")
        tk.Frame(body, background=self._CYAN, height=1).grid(
            row=1, column=0, sticky="ew", pady=(14, 18)
        )
        label("Starting your workspace", size=13).grid(row=2, column=0, sticky="w")
        self._stage = label("Waiting for startup status…")
        self._stage.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self._cursor = tk.Canvas(
            body,
            height=12,
            background=self._BACKGROUND,
            highlightthickness=0,
            borderwidth=0,
        )
        self._cursor.grid(row=5, column=0, sticky="w", pady=(16, 12))
        for index in range(self._SEGMENTS):
            left = index * 14
            self._cursor.create_rectangle(
                left,
                2,
                left + 9,
                8,
                fill=self._TRACK,
                outline="",
                tags=f"segment-{index}",
            )
        self._paint_cursor()
        self._motion_note = label(
            "Static view · startup continues"
            if self._static
            else "Waiting for readiness · no progress estimate",
            size=10,
            color=self._MUTED,
        )
        self._motion_note.grid(row=6, column=0, sticky="w", pady=(0, 16))
        body.bind("<Configure>", self._wrap_status)
        tk.Frame(body, background=self._CYAN, height=1).grid(
            row=7, column=0, sticky="ew", pady=(0, 14)
        )
        controls = tk.Frame(body, background=self._BACKGROUND)
        controls.grid(row=8, column=0, sticky="ew")
        self._skip = self._button(controls, "Skip animation", self.disable_animation)
        self._skip.pack(side="left")
        if self._static:
            self._skip.configure(state="disabled")
        else:
            self._skip.focus_set()
        if self._on_cancel is not None:
            self._button(controls, "Cancel startup", self._request_cancel).pack(
                side="right"
            )

    def _button(self, parent: tk.Frame, text: str, command: Callable) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=self._font(10),
            background="#10100d",
            foreground=self._AMBER,
            activebackground=self._AMBER,
            activeforeground=self._BACKGROUND,
            disabledforeground=self._MUTED,
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=self._TRACK,
            highlightcolor=self._CYAN,
            padx=12,
            pady=7,
            takefocus=True,
        )

    def _font(self, size: int) -> tkfont.Font:
        # Copy the native named font; treating "TkFixedFont" as a font family
        # would silently fall back to a proportional system font on Windows.
        font = tkfont.nametofont("TkFixedFont", root=self._root).copy()
        font.configure(size=size)
        self._fonts.append(font)
        return font

    def _check_thread(self) -> None:
        if threading.get_ident() != self._thread:
            raise RuntimeError(
                "DesktopSplash methods must run on its creating main thread."
            )

    def _wrap_status(self, event: tk.Event) -> None:
        width = max(120, event.width - 56)
        self._stage.configure(wraplength=width)
        self._motion_note.configure(wraplength=width)

    def set_stage(self, text: str) -> None:
        """Show the caller's actual stage as plain text, never an estimated %."""
        self._check_thread()
        if not isinstance(text, str):
            raise TypeError("Startup stage must be text.")
        if not self._closed:
            self._stage.configure(text=text)

    def pump(self) -> bool:
        """Service Tk events without blocking; call regularly during startup."""
        self._check_thread()
        if self._closed:
            return False
        try:
            self._root.update()
        except tk.TclError:
            self.close()
        return not self._closed

    def _paint_cursor(self) -> None:
        for index in range(self._SEGMENTS):
            self._cursor.itemconfigure(
                f"segment-{index}",
                fill=self._AMBER
                if not self._static and index == self._position
                else self._TRACK,
            )

    def _schedule(self) -> None:
        if not self._closed and not self._static and self._after_id is None:
            self._after_id = self._root.after(self._TICK_MS, self._tick)

    def _tick(self) -> None:
        self._after_id = None
        if self._closed or self._static:
            return
        self._position = (self._position + 1) % self._SEGMENTS
        self._paint_cursor()
        self._schedule()

    def _cancel_timer(self) -> None:
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self._root:
            self._cancel_timer()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self._root:
            self._schedule()

    def disable_animation(self, _event: tk.Event | None = None) -> None:
        """Freeze only the animation; keep receiving real readiness stages."""
        self._check_thread()
        if self._closed:
            return
        self._static = True
        self._cancel_timer()
        self._paint_cursor()
        self._motion_note.configure(text="Static view · startup continues")
        self._skip.configure(state="disabled")

    def _request_cancel(self) -> None:
        if self._closed:
            return
        self._cancelled = True
        self.close()
        if self._on_cancel is not None:
            self._on_cancel()

    def close(self) -> None:
        """Destroy immediately and idempotently; never invoke cancellation."""
        self._check_thread()
        if self._closed:
            return
        self._closed = True
        self._cancel_timer()
        try:
            self._root.destroy()
        except tk.TclError:
            pass
