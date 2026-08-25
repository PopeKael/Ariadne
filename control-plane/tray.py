from __future__ import annotations

"""Legacy Python tray companion retained as the migration rollback path.

The normal resident entry point is now the Rust host. Keep this module until
the host has passed manual smoke testing and startup migration is accepted.
"""

import os
import sys
import threading
import webbrowser
import ctypes
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

import server


URL = f"http://{server.HOST}:{server.PORT}/"
TRAY_MUTEX_NAME = "Local\\AriadneControlPlaneTray"


def acquire_tray_instance() -> int | None | bool:
    """Return a Windows mutex handle, False when another tray owns it."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, TRAY_MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    return int(handle)


def release_tray_instance(handle: int | None | bool) -> None:
    if os.name == "nt" and isinstance(handle, int) and handle:
        ctypes.windll.kernel32.CloseHandle(handle)


def make_librarian_icon() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=(11, 20, 39, 255), outline=(116, 136, 255, 255), width=3)
    # A small stack of books: the deliberately simple librarian mark for the prototype.
    draw.rounded_rectangle((15, 39, 49, 49), radius=3, fill=(104, 216, 219, 255))
    draw.rounded_rectangle((17, 29, 47, 39), radius=3, fill=(240, 198, 110, 255))
    draw.rounded_rectangle((20, 20, 44, 30), radius=3, fill=(116, 136, 255, 255))
    draw.line((25, 24, 39, 24), fill=(11, 20, 39, 255), width=2)
    draw.line((22, 34, 42, 34), fill=(11, 20, 39, 255), width=2)
    draw.line((20, 44, 44, 44), fill=(11, 20, 39, 255), width=2)
    return image


def start_local_server() -> server.ThreadingHTTPServer | None:
    try:
        httpd = server.ThreadingHTTPServer((server.HOST, server.PORT), server.AriadneHandler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or "address already in use" in str(exc).lower():
            return None
        raise
    server.start_lifecycle_watchdog()
    thread = threading.Thread(target=httpd.serve_forever, name="ariadne-http", daemon=True)
    thread.start()
    return httpd


def restart(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    icon.stop()
    os.execv(sys.executable, [sys.executable, *sys.argv])


def open_dashboard(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    webbrowser.open(URL)


def quit_ariadne(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    icon.stop()


def main() -> None:
    tray_instance = acquire_tray_instance()
    if tray_instance is False:
        return
    httpd = start_local_server()
    menu = pystray.Menu(
        pystray.MenuItem("Open Ariadne dashboard", open_dashboard, default=True),
        pystray.MenuItem("Restart Ariadne", restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit Ariadne", quit_ariadne),
    )
    icon = pystray.Icon("Ariadne", make_librarian_icon(), "Ariadne — Local AI Control Plane", menu)
    try:
        icon.run()
    finally:
        if httpd is not None:
            server.release_workloads(force=True)
            httpd.shutdown()
            httpd.server_close()
        release_tray_instance(tray_instance)


if __name__ == "__main__":
    main()
