"""Pinned pywebview/WebView2 policy applied before the first page navigation."""

from __future__ import annotations

import os
from collections.abc import Callable, MutableMapping
from typing import Any
from urllib.parse import urlsplit


def allowed_resource(url: str, origin: str) -> bool:
    """Keep automatic browser resource requests inside the owned local origin."""
    parsed = urlsplit(url)
    if parsed.scheme in ("data", "blob", "about"):
        return True
    expected = urlsplit(origin)
    return (
        parsed.scheme in ("http", "ws")
        and parsed.hostname == "127.0.0.1"
        and parsed.port == expected.port
    )


def allowed_download(url: str, origin: str) -> bool:
    """Allow downloads only from the owned loopback server or its blob URLs."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme == "blob":
            parsed = urlsplit(parsed.path)
        expected = urlsplit(origin)
        return (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == expected.port
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _guard_download_starting(
    original: Callable[[Any, Any, Any], None], origin: str
) -> Callable[[Any, Any, Any], None]:
    """Cancel non-local downloads before pywebview can open its Save As dialog."""

    def guarded(self: Any, sender: Any, args: Any) -> None:
        source = str(args.DownloadOperation.Uri)
        if not allowed_download(source, origin):
            args.Cancel = True
            return
        original(self, sender, args)

    return guarded


def configure_window_settings(settings: MutableMapping[str, Any]) -> None:
    """Keep external navigation disabled while enabling native Save As exports."""
    settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    settings["ALLOW_DOWNLOADS"] = True
    settings["ALLOW_FILE_URLS"] = False
    settings["REMOTE_DEBUGGING_PORT"] = None


def configure_renderer(origin: str) -> None:
    """Install exact-version native hooks, not SSL bypasses or system configuration.

    Resource/navigation handlers prevent accidental remote page resources and
    links. The Chromium background-network switches also disable browser-side
    background collection. This is not an OS-wide network sandbox.
    """
    for key in (
        "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", "WEBVIEW2_USER_DATA_FOLDER",
        "WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER", "WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER",
    ):
        os.environ.pop(key, None)
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--disable-background-networking --disable-component-update --disable-sync "
        "--no-first-run --no-default-browser-check --disable-breakpad "
        "--disable-features=msEdgeShoppingAssistant,msEdgeSidebarV2,msEdgeTranslate"
    )
    # This import loads CLR assemblies BEFORE managed namespaces can be imported.
    from webview.platforms.edgechromium import EdgeChrome  # noqa: I001
    from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext

    original = EdgeChrome.on_webview_ready
    original_download = EdgeChrome.on_download_starting
    EdgeChrome.on_download_starting = _guard_download_starting(
        original_download, origin
    )

    def ready(self: Any, sender: Any, args: Any) -> None:
        if args.IsSuccess:
            core = sender.CoreWebView2
            core.Settings.IsGeneralAutofillEnabled = False
            core.Settings.IsPasswordAutosaveEnabled = False
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All)

            def resource(_sender: Any, event: Any) -> None:
                if not allowed_resource(str(event.Request.Uri), origin):
                    event.Response = core.Environment.CreateWebResourceResponse(
                        None, 403, "External resource disabled", ""
                    )

            def navigating(_sender: Any, event: Any) -> None:
                if not allowed_resource(str(event.Uri), origin):
                    event.Cancel = True

            core.WebResourceRequested += resource
            core.NavigationStarting += navigating
            # Keep managed delegates alive as long as their browser control.
            self._edge_policy_handlers = (resource, navigating)
        original(self, sender, args)

    EdgeChrome.on_webview_ready = ready
