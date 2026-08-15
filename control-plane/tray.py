from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

import server


URL = f"http://{server.HOST}:{server.PORT}/"


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


def start_local_server() -> server.ThreadingHTTPServer:
    httpd = server.ThreadingHTTPServer((server.HOST, server.PORT), server.AriadneHandler)
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
        server.release_workloads(force=True)
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
