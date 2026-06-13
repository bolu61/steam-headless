#!/usr/bin/env python3
"""Serve live screenshots of the headless desktop on HTTP port 8080.

GET /            -> HTML page that reloads the screenshot every 3 seconds
GET /screen.png  -> freshly captured frame of the headless desktop

Finds the headless session's bus via the running mutter process, opens one
persistent ScreenCast portal session (served by the dialog-less headless
backend, see screencast-portal-headless.py), and per request pulls one frame
from the session's PipeWire node with gst-launch. If the capture session dies
(e.g. the steam session was restarted), it is re-established on next request.
"""

import http.server
import json
import os
import subprocess
import sys
import threading

from gi.repository import Gio, GLib

PORT = 8080
FRAME_PATH = "/tmp/screen-server-frame.png"

PAGE = b"""<!doctype html>
<title>steam-headless screen</title>
<style>body{margin:0;background:#111}img{width:100%}</style>
<img id="s" src="/screen.png">
<script>
setInterval(() => {
  const i = document.getElementById("s");
  i.src = "/screen.png?" + Date.now();
}, 3000);
</script>
"""


def find_headless_bus():
    mutter_pid = subprocess.run(
        ["pgrep", "--exact", "mutter"], capture_output=True, text=True
    ).stdout.split()
    if not mutter_pid:
        raise RuntimeError("no mutter process — is the headless session up?")
    with open(f"/proc/{mutter_pid[0]}/environ", "rb") as environ_file:
        for entry in environ_file.read().split(b"\0"):
            if entry.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                return entry.split(b"=", 1)[1].decode()
    raise RuntimeError("mutter has no DBUS_SESSION_BUS_ADDRESS")


class CaptureSession:
    """One persistent portal screencast session; thread-safe node access."""

    def __init__(self):
        self.lock = threading.Lock()
        self.node_id = None
        self.bus = None

    def ensure(self):
        with self.lock:
            if self.node_id is None:
                self.node_id = self._open_session()
            return self.node_id

    def invalidate(self):
        with self.lock:
            self.node_id = None
            self.bus = None  # drop connection; session closes with it

    def _open_session(self):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = find_headless_bus()
        self.bus = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        portal = Gio.DBusProxy.new_sync(
            self.bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast", None)

        sender_token = self.bus.get_unique_name()[1:].replace(".", "_")
        result = {}
        loop = GLib.MainLoop()
        counter = [0]

        def request(method, build_params, callback):
            counter[0] += 1
            token = f"srv{counter[0]}"
            path = (f"/org/freedesktop/portal/desktop/request/"
                    f"{sender_token}/{token}")
            self.bus.signal_subscribe(
                "org.freedesktop.portal.Desktop",
                "org.freedesktop.portal.Request", "Response", path, None,
                Gio.DBusSignalFlags.NO_MATCH_RULE,
                lambda *args: callback(*args[5].unpack()))
            portal.call_sync(method, build_params(token),
                             Gio.DBusCallFlags.NONE, 5000, None)

        def on_start(code, results):
            if code == 0:
                result["node"] = results["streams"][0][0]
            loop.quit()

        def on_selected(code, results, session_path):
            if code != 0:
                loop.quit()
                return
            request("Start", lambda t: GLib.Variant("(osa{sv})", (
                session_path, "", {"handle_token": GLib.Variant("s", t)})),
                on_start)

        def on_created(code, results):
            if code != 0:
                loop.quit()
                return
            session_path = results["session_handle"]
            request("SelectSources", lambda t: GLib.Variant("(oa{sv})", (
                session_path,
                {"handle_token": GLib.Variant("s", t),
                 "types": GLib.Variant("u", 1),
                 "cursor_mode": GLib.Variant("u", 2)})),
                lambda code, results: on_selected(code, results,
                                                  session_path))

        request("CreateSession", lambda t: GLib.Variant("(a{sv})", (
            {"handle_token": GLib.Variant("s", t),
             "session_handle_token": GLib.Variant("s", "screenserver")},)),
            on_created)
        GLib.timeout_add_seconds(15, loop.quit)
        loop.run()
        if "node" not in result:
            raise RuntimeError("portal screencast flow failed")
        return result["node"]


capture = CaptureSession()


def find_existing_relay():
    """Prefer the relay node Steam's own capture session already holds: it
    has a buffered last frame (keepalive), so grabs work even when the
    desktop is fully idle — a freshly created session would wait for screen
    damage that never comes. Returns a node id or None."""
    try:
        objects = json.loads(subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=5
        ).stdout or "[]")
    except (subprocess.TimeoutExpired, ValueError):
        return None
    relays = [
        obj for obj in objects
        if obj.get("type") == "PipeWire:Interface:Node"
        and obj.get("info", {}).get("props", {}).get(
            "node.name", "").startswith("steam-headless-relay-")]
    steam_relays = [
        obj for obj in relays
        if "steam_session" in obj["info"]["props"]["node.name"]]
    chosen = (steam_relays or relays)[-1:]
    return chosen[0]["id"] if chosen else None


def grab_frame():
    for attempt in (1, 2):
        node = find_existing_relay() or capture.ensure()
        try:
            gst = subprocess.run(
                ["gst-launch-1.0", "--quiet",
                 "pipewiresrc", f"path={node}", "num-buffers=3",
                 "!", "videoconvert", "!", "pngenc", "snapshot=true",
                 "!", "filesink", f"location={FRAME_PATH}"],
                capture_output=True, timeout=8)
            if gst.returncode == 0 and os.path.getsize(FRAME_PATH) > 0:
                with open(FRAME_PATH, "rb") as frame:
                    return frame.read()
        except subprocess.TimeoutExpired:
            pass  # no frames — session likely outlived a steam restart
        capture.invalidate()  # redo the portal session once
    raise RuntimeError("could not capture a frame after session retry")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith("/screen.png"):
                payload, content_type = grab_frame(), "image/png"
            else:
                payload, content_type = PAGE, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as error:
            message = f"capture failed: {error}".encode()
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    def log_message(self, format, *args):
        print(f"screen-server: {args[0]} {args[1]}", file=sys.stderr,
              flush=True)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"screen-server: serving on http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()
