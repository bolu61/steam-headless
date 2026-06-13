#!/usr/bin/env python3
"""Auto-approving ScreenCast portal backend for the headless steam session.

Implements org.freedesktop.impl.portal.ScreenCast by delegating to mutter's
trusted org.gnome.Mutter.ScreenCast API (the same one gnome-remote-desktop
uses), so no permission dialog is ever shown. This is what makes Steam Remote
Play's Desktop_MovieStream work on the headless mutter session: the stock
gnome backend pops a screen-share dialog on the invisible virtual monitor,
and Steam has no restore_token support to persist an approval.

SECURITY TRADE-OFF: any app on this session bus can capture the screen
without being asked. Acceptable on this dedicated, single-user game box.

Activated via D-Bus (see ~/.local/share/dbus-1/services/) and selected for
ScreenCast via ~/.config/xdg-desktop-portal/portals.conf. Only sessions whose
xdg-desktop-portal resolves that config (i.e. the headless one) use it.
"""

import atexit
import glob
import json
import os
import signal
import socket
import subprocess
import sys
import time

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.impl.portal.desktop.headless"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

MUTTER_SCREENCAST_BUS = "org.gnome.Mutter.ScreenCast"
MUTTER_SCREENCAST_PATH = "/org/gnome/Mutter/ScreenCast"
MUTTER_REMOTEDESKTOP_BUS = "org.gnome.Mutter.RemoteDesktop"
MUTTER_REMOTEDESKTOP_PATH = "/org/gnome/Mutter/RemoteDesktop"
MUTTER_DISPLAYCONFIG_BUS = "org.gnome.Mutter.DisplayConfig"
MUTTER_DISPLAYCONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"

# Portal cursor_mode bitmask -> mutter cursor-mode enum
CURSOR_MODE_MAP = {1: 0, 2: 1, 4: 2}  # hidden, embedded, metadata

# evdev BTN_* codes (what the portal speaks) -> X11 core button numbers
EVDEV_TO_X11_BUTTON = {0x110: 1, 0x111: 3, 0x112: 2, 0x113: 8, 0x114: 9}
SCROLL_NOTCH_PIXELS = 15.0  # one wheel click per this much smooth-axis delta

SCREENCAST_IFACE_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.ScreenCast">
    <property name="version" type="u" access="read"/>
    <property name="AvailableSourceTypes" type="u" access="read"/>
    <property name="AvailableCursorModes" type="u" access="read"/>
    <method name="CreateSession">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SelectSources">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="Start">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="OpenPipeWireRemote">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="h" name="fd" direction="out"/>
    </method>
  </interface>
</node>
"""

REMOTEDESKTOP_IFACE_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.RemoteDesktop">
    <property name="version" type="u" access="read"/>
    <property name="AvailableDeviceTypes" type="u" access="read"/>
    <method name="CreateSession">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SelectDevices">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="Start">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="NotifyPointerMotion">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="d" name="dx" direction="in"/>
      <arg type="d" name="dy" direction="in"/>
    </method>
    <method name="NotifyPointerMotionAbsolute">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="stream" direction="in"/>
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
    </method>
    <method name="NotifyPointerButton">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="i" name="button" direction="in"/>
      <arg type="u" name="state" direction="in"/>
    </method>
    <method name="NotifyPointerAxis">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="d" name="dx" direction="in"/>
      <arg type="d" name="dy" direction="in"/>
    </method>
    <method name="NotifyPointerAxisDiscrete">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="axis" direction="in"/>
      <arg type="i" name="steps" direction="in"/>
    </method>
    <method name="NotifyKeyboardKeycode">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="i" name="keycode" direction="in"/>
      <arg type="u" name="state" direction="in"/>
    </method>
    <method name="NotifyKeyboardKeysym">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="i" name="keysym" direction="in"/>
      <arg type="u" name="state" direction="in"/>
    </method>
  </interface>
</node>
"""

SESSION_IFACE_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Session">
    <property name="version" type="u" access="read"/>
    <method name="Close"/>
    <signal name="Closed"/>
  </interface>
</node>
"""

REQUEST_IFACE_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Request">
    <method name="Close"/>
  </interface>
</node>
"""


def log(message):
    print(f"screencast-portal-headless: {message}", file=sys.stderr, flush=True)


RELAY_PROCESSES = []


@atexit.register
def kill_relays():
    for process in RELAY_PROCESSES:
        if process.poll() is None:
            process.terminate()


def wiggle_pointer(connection):
    """Move the cursor 1px right and back via mutter's trusted RemoteDesktop
    API. mutter's screencast only emits frames on screen damage, so a fresh
    relay on an idle desktop would otherwise have no first buffer to serve
    (and keepalive can only re-send a buffer that exists)."""
    try:
        (session_path,) = connection.call_sync(
            "org.gnome.Mutter.RemoteDesktop", "/org/gnome/Mutter/RemoteDesktop",
            "org.gnome.Mutter.RemoteDesktop", "CreateSession", None,
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None
        ).unpack()
        session = ("org.gnome.Mutter.RemoteDesktop", session_path,
                   "org.gnome.Mutter.RemoteDesktop.Session")
        connection.call_sync(*session, "Start", None, None,
                             Gio.DBusCallFlags.NONE, 5000, None)
        for dx in (1.0, -1.0):
            connection.call_sync(
                *session, "NotifyPointerMotionRelative",
                GLib.Variant("(dd)", (dx, 0.0)), None,
                Gio.DBusCallFlags.NONE, 5000, None)
        connection.call_sync(*session, "Stop", None, None,
                             Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as error:
        log(f"pointer wiggle failed (non-fatal): {error}")


def probe_relay(node_id, timeout_seconds=5):
    """True if the relay node serves a frame within the timeout."""
    try:
        gst = subprocess.run(
            ["gst-launch-1.0", "--quiet",
             "pipewiresrc", f"path={node_id}", "num-buffers=1",
             "!", "fakesink"],
            capture_output=True, timeout=timeout_seconds)
        return gst.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def spawn_relay(mutter_node_id, relay_name):
    """Re-publish a mutter screencast node as a system-memory-only node.

    mutter offers dmabuf buffers and Steam's CDesktopCapturePipeWire prefers
    them, but its EGL import of NVIDIA dmabufs fails (Couldn't import dmabuf:
    Invalid argument) and so does mmap on dmabuf fds. The video/x-raw caps
    filter here excludes memory:DMABuf, so the relay consumes via mmap-able
    buffers and consumers can only negotiate system memory, which Steam
    handles fine. Costs one frame copy — acceptable for the desktop-capture
    fallback path (launchers, desktop view); gameplay uses the GameOverlay
    hook and never touches this.

    Returns (process, relay_node_id)."""
    process = subprocess.Popen(
        ["gst-launch-1.0", "--quiet",
         # keepalive-time: mutter only emits frames on screen damage; resend
         # the last frame every 500ms so consumers (Steam at idle, the
         # screen-server) never stall waiting on a static desktop.
         "pipewiresrc", f"path={mutter_node_id}", "keepalive-time=500",
         "!", "video/x-raw",
         "!", "pipewiresink", "mode=provide",
         f"stream-properties=props,media.class=Video/Source,"
         f"node.name={relay_name},node.description={relay_name}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    RELAY_PROCESSES.append(process)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"relay died: {process.stderr.read().decode(errors='replace')[-500:]}")
        objects = json.loads(subprocess.run(
            ["pw-dump"], capture_output=True, text=True).stdout or "[]")
        for obj in objects:
            if obj.get("type") != "PipeWire:Interface:Node":
                continue
            props = obj.get("info", {}).get("props", {})
            if props.get("node.name") == relay_name:
                return process, obj["id"]
        time.sleep(0.2)
    process.terminate()
    raise RuntimeError("relay node did not appear in pw-dump within 10s")


class Session:
    """One portal session, backed by one mutter screencast session."""

    def __init__(self, backend, session_handle, app_id):
        self.backend = backend
        self.handle = session_handle
        self.app_id = app_id
        self.cursor_mode = 1  # mutter "embedded" — right default for streaming
        self.mutter_session_path = None
        self.relay_process = None
        self.relay_node_id = None
        # remote desktop state (combined capture+input sessions, like Steam's)
        self.remote_desktop = False
        self.device_types = 3  # KEYBOARD | POINTER
        self.sources_selected = False
        self.mutter_rd_session_path = None
        self.mutter_stream_path = None
        self.scroll_remainder = [0.0, 0.0]  # vertical, horizontal
        self.created_at = time.monotonic()
        self.registration_id = backend.connection.register_object(
            session_handle,
            backend.session_node.interfaces[0],
            self.on_method_call,
            self.on_get_property,
            None)

    def on_method_call(self, connection, sender, path, iface, method,
                       parameters, invocation):
        if method == "Close":
            self.close()
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD,
                f"Unknown method {method}")

    def on_get_property(self, connection, sender, path, iface, prop):
        if prop == "version":
            return GLib.Variant("u", 1)
        return None

    def start(self):
        """Start the session: mutter screencast of the first monitor, plus a
        mutter remote desktop session for input when this is a remote desktop
        session. Returns the Start results vardict."""
        conn = self.backend.connection
        results = {}
        sc_properties = {}

        if self.remote_desktop:
            (self.mutter_rd_session_path,) = conn.call_sync(
                MUTTER_REMOTEDESKTOP_BUS, MUTTER_REMOTEDESKTOP_PATH,
                MUTTER_REMOTEDESKTOP_BUS, "CreateSession", None,
                GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None
            ).unpack()
            (session_id,) = conn.call_sync(
                MUTTER_REMOTEDESKTOP_BUS, self.mutter_rd_session_path,
                "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.gnome.Mutter.RemoteDesktop.Session",
                                      "SessionId")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None
            ).unpack()
            sc_properties["remote-desktop-session-id"] = \
                GLib.Variant("s", session_id)
            results["devices"] = GLib.Variant("u", self.device_types)
            results["clipboard_enabled"] = GLib.Variant("b", False)

            if not self.sources_selected:
                # input-only session: no screencast to set up
                conn.call_sync(
                    MUTTER_REMOTEDESKTOP_BUS, self.mutter_rd_session_path,
                    "org.gnome.Mutter.RemoteDesktop.Session", "Start",
                    None, None, Gio.DBusCallFlags.NONE, 5000, None)
                log(f"session {self.handle}: remote desktop (input only) "
                    f"for app '{self.app_id or 'unsandboxed'}'")
                return results

        (self.mutter_session_path,) = conn.call_sync(
            MUTTER_SCREENCAST_BUS, MUTTER_SCREENCAST_PATH,
            MUTTER_SCREENCAST_BUS, "CreateSession",
            GLib.Variant("(a{sv})", (sc_properties,)),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None
        ).unpack()

        connector = self.backend.get_first_monitor_connector()
        (stream_path,) = conn.call_sync(
            MUTTER_SCREENCAST_BUS, self.mutter_session_path,
            "org.gnome.Mutter.ScreenCast.Session", "RecordMonitor",
            GLib.Variant("(sa{sv})", (connector, {
                "cursor-mode": GLib.Variant("u", self.cursor_mode)})),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None
        ).unpack()
        self.mutter_stream_path = stream_path

        # The PipeWire node id arrives via signal after Session.Start.
        node_info = {"node_id": None}
        wait_loop = GLib.MainLoop()

        def on_stream_added(connection, sender, path, iface, signal, params):
            (node_info["node_id"],) = params.unpack()
            wait_loop.quit()

        subscription = conn.signal_subscribe(
            MUTTER_SCREENCAST_BUS, "org.gnome.Mutter.ScreenCast.Stream",
            "PipeWireStreamAdded", stream_path, None,
            Gio.DBusSignalFlags.NONE, on_stream_added)

        if self.remote_desktop:
            # starting the remote desktop session starts the bound screencast
            conn.call_sync(
                MUTTER_REMOTEDESKTOP_BUS, self.mutter_rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session", "Start",
                None, None, Gio.DBusCallFlags.NONE, 5000, None)
        else:
            conn.call_sync(
                MUTTER_SCREENCAST_BUS, self.mutter_session_path,
                "org.gnome.Mutter.ScreenCast.Session", "Start",
                None, None, Gio.DBusCallFlags.NONE, 5000, None)

        GLib.timeout_add_seconds(5, wait_loop.quit)
        wait_loop.run()
        conn.signal_unsubscribe(subscription)

        if node_info["node_id"] is None:
            raise RuntimeError("timed out waiting for PipeWireStreamAdded")

        stream_props = {"source_type": GLib.Variant("u", 1)}
        try:
            (params_variant,) = conn.call_sync(
                MUTTER_SCREENCAST_BUS, stream_path,
                "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)",
                             ("org.gnome.Mutter.ScreenCast.Stream",
                              "Parameters")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None
            ).unpack()
            for key in ("position", "size"):
                if key in params_variant:
                    stream_props[key] = GLib.Variant(
                        "(ii)", tuple(params_variant[key]))
        except GLib.Error as error:
            log(f"could not read stream Parameters: {error}")

        relay_name = f"steam-headless-relay-{self.handle.rsplit('/', 1)[-1]}"
        self.relay_process, relay_node_id = spawn_relay(
            node_info["node_id"], relay_name)
        self.relay_node_id = relay_node_id
        wiggle_pointer(conn)  # guarantee a first frame on an idle desktop

        log(f"session {self.handle}: monitor '{connector}' -> mutter node "
            f"{node_info['node_id']} -> relay node {relay_node_id} for app "
            f"'{self.app_id or 'unsandboxed'}'"
            + (" [with remote desktop input]" if self.remote_desktop else ""))
        results["streams"] = GLib.Variant(
            "a(ua{sv})", [(relay_node_id, stream_props)])
        return results

    def forward_input(self, method, args):
        """Deliver an impl.portal.RemoteDesktop Notify* call.

        Absolute motion goes through mutter's RemoteDesktop API (it moves the
        real compositor pointer, which also keeps Xwayland's pointer in
        sync). Everything else goes through XTest into Xwayland: mutter 50's
        legacy NotifyPointerButton/Keyboard* D-Bus methods are accepted but
        never delivered (GNOME moved to libei; Steam only speaks the legacy
        portal methods), verified empirically 2026-06-12 — X11's button mask
        never changed. All input targets here (Steam CEF, Wine launchers and
        games) are X11 clients, so XTest reaches them."""
        if method == "NotifyPointerMotionAbsolute":
            _options, _stream_node, x, y = args
            if self.mutter_rd_session_path is None or \
                    self.mutter_stream_path is None:
                return
            self.backend.connection.call_sync(
                MUTTER_REMOTEDESKTOP_BUS, self.mutter_rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session",
                "NotifyPointerMotionAbsolute",
                GLib.Variant("(sdd)", (self.mutter_stream_path, x, y)),
                None, Gio.DBusCallFlags.NONE, 5000, None)
        elif method == "NotifyPointerMotion":
            _options, dx, dy = args
            self.backend.xtest_motion_relative(dx, dy)
        elif method == "NotifyPointerButton":
            _options, button, state = args
            x11_button = EVDEV_TO_X11_BUTTON.get(button)
            if x11_button is None:
                log(f"unmapped evdev button {button}, dropping")
                return
            self.backend.xtest_button(x11_button, bool(state))
        elif method == "NotifyPointerAxis":
            _options, dx, dy = args
            # axis numbering matches NotifyPointerAxisDiscrete: 0 = vertical
            self.scroll_remainder[0] += dy
            self.scroll_remainder[1] += dx
            for axis in (0, 1):
                while abs(self.scroll_remainder[axis]) >= SCROLL_NOTCH_PIXELS:
                    sign = 1 if self.scroll_remainder[axis] > 0 else -1
                    self.scroll_remainder[axis] -= sign * SCROLL_NOTCH_PIXELS
                    self.backend.xtest_scroll(axis, sign)
        elif method == "NotifyPointerAxisDiscrete":
            _options, axis, steps = args
            for _ in range(abs(steps)):
                self.backend.xtest_scroll(axis, 1 if steps > 0 else -1)
        elif method == "NotifyKeyboardKeycode":
            _options, keycode, state = args
            # X11 keycodes are evdev keycodes + 8
            self.backend.xtest_key(keycode + 8, bool(state))
        elif method == "NotifyKeyboardKeysym":
            _options, keysym, state = args
            x_keycode = self.backend.keysym_to_keycode(keysym)
            if not x_keycode:
                log(f"keysym {keysym} has no X11 keycode, dropping")
                return
            self.backend.xtest_key(x_keycode, bool(state))

    def close_and_notify(self):
        """Close and tell the app (via the portal Closed signal) so it knows
        to re-create its capture session — used when the relay wedges."""
        try:
            self.backend.connection.emit_signal(
                None, self.handle, "org.freedesktop.impl.portal.Session",
                "Closed", None)
        except GLib.Error as error:
            log(f"emitting Closed failed: {error}")
        self.close()

    def close(self):
        if self.relay_process is not None:
            process = self.relay_process
            self.relay_process = None
            if process.poll() is None:
                process.terminate()
                # a hung gst can ignore SIGTERM; its zombie node would keep
                # being picked up by consumers — escalate after a grace period
                GLib.timeout_add_seconds(
                    3, lambda: process.poll() is None and process.kill()
                    and False)
        if self.mutter_rd_session_path is not None:
            # stopping the remote desktop session also stops its screencast
            try:
                self.backend.connection.call_sync(
                    MUTTER_REMOTEDESKTOP_BUS, self.mutter_rd_session_path,
                    "org.gnome.Mutter.RemoteDesktop.Session", "Stop",
                    None, None, Gio.DBusCallFlags.NONE, 5000, None)
            except GLib.Error as error:
                log(f"stopping mutter rd session failed: {error}")
            self.mutter_rd_session_path = None
            self.mutter_session_path = None
        if self.mutter_session_path is not None:
            try:
                self.backend.connection.call_sync(
                    MUTTER_SCREENCAST_BUS, self.mutter_session_path,
                    "org.gnome.Mutter.ScreenCast.Session", "Stop",
                    None, None, Gio.DBusCallFlags.NONE, 5000, None)
            except GLib.Error as error:
                log(f"stopping mutter session failed: {error}")
            self.mutter_session_path = None
        if self.registration_id is not None:
            self.backend.connection.unregister_object(self.registration_id)
            self.registration_id = None
        self.backend.sessions.pop(self.handle, None)
        log(f"session {self.handle}: closed")


class Backend:
    def __init__(self, connection):
        self.connection = connection
        self.sessions = {}
        self.portal_node = Gio.DBusNodeInfo.new_for_xml(SCREENCAST_IFACE_XML)
        self.rd_node = Gio.DBusNodeInfo.new_for_xml(REMOTEDESKTOP_IFACE_XML)
        self.session_node = Gio.DBusNodeInfo.new_for_xml(SESSION_IFACE_XML)
        self.request_node = Gio.DBusNodeInfo.new_for_xml(REQUEST_IFACE_XML)
        connection.register_object(
            PORTAL_PATH, self.portal_node.interfaces[0],
            self.on_method_call, self.on_get_property, None)
        connection.register_object(
            PORTAL_PATH, self.rd_node.interfaces[0],
            self.on_method_call, self.on_get_property, None)
        GLib.timeout_add_seconds(30, self.watchdog)

    def watchdog(self):
        """Replace wedged relays. gst-launch's provide-mode sink can stop
        serving after a consumer (Steam) disconnects uncleanly; a healthy
        relay always answers a probe thanks to keepalive. The wiggle between
        probes rules out 'fresh session on an idle desktop, no first frame
        yet' — only a double probe failure is a real wedge. Closing the
        session (with the Closed signal) makes Steam re-create it on next
        use, getting a fresh relay."""
        for session in list(self.sessions.values()):
            if session.relay_node_id is None:
                continue
            if time.monotonic() - session.created_at < 90:
                continue  # boot/load can stall probes; avoid false positives
            if probe_relay(session.relay_node_id):
                continue
            wiggle_pointer(self.connection)
            if probe_relay(session.relay_node_id):
                continue
            if "steam_session" in session.handle:
                # Steam never re-creates its capture session mid-run (it
                # ignores Closed and reuses the stale node id until Steam
                # itself restarts), so closing here only guarantees a black
                # stream. Log loudly instead; recovery = restart the session.
                log(f"WARNING: session {session.handle}: relay node "
                    f"{session.relay_node_id} looks wedged — Steam desktop "
                    f"capture will be black until the session is restarted")
                continue
            log(f"session {session.handle}: relay node "
                f"{session.relay_node_id} wedged — closing session")
            session.close_and_notify()
        return True  # keep the timer

    # --- XTest injection into Xwayland (see Session.forward_input) ---

    def x_display(self):
        if getattr(self, "_x_display", None) is None:
            if "XAUTHORITY" not in os.environ:
                # dbus activation env may predate this var; mutter names its
                # Xwayland auth file predictably
                auth_files = sorted(
                    glob.glob(f"{os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid()))}"
                              f"/.mutter-Xwaylandauth.*"),
                    key=os.path.getmtime)
                if auth_files:
                    os.environ["XAUTHORITY"] = auth_files[-1]
            from Xlib import display as xlib_display
            self._x_display = xlib_display.Display()  # DISPLAY/XAUTHORITY env
        return self._x_display

    def xtest_button(self, button, pressed):
        from Xlib import X
        from Xlib.ext import xtest
        d = self.x_display()
        xtest.fake_input(
            d, X.ButtonPress if pressed else X.ButtonRelease, button)
        d.sync()

    def xtest_motion_relative(self, dx, dy):
        from Xlib import X
        from Xlib.ext import xtest
        d = self.x_display()
        xtest.fake_input(d, X.MotionNotify, 1, x=int(dx), y=int(dy))
        d.sync()

    def xtest_scroll(self, axis, direction):
        # X11 wheel buttons: 4=up 5=down 6=left 7=right; portal positive
        # steps mean down/right
        button = (5 if direction > 0 else 4) if axis == 0 else \
                 (7 if direction > 0 else 6)
        self.xtest_button(button, True)
        self.xtest_button(button, False)

    def xtest_key(self, x_keycode, pressed):
        from Xlib import X
        from Xlib.ext import xtest
        d = self.x_display()
        xtest.fake_input(
            d, X.KeyPress if pressed else X.KeyRelease, x_keycode)
        d.sync()

    def keysym_to_keycode(self, keysym):
        return self.x_display().keysym_to_keycode(keysym)

    def get_first_monitor_connector(self):
        state = self.connection.call_sync(
            MUTTER_DISPLAYCONFIG_BUS, MUTTER_DISPLAYCONFIG_PATH,
            MUTTER_DISPLAYCONFIG_BUS, "GetCurrentState",
            None, None, Gio.DBusCallFlags.NONE, 5000, None).unpack()
        monitors = state[1]
        if not monitors:
            raise RuntimeError("mutter reports no monitors")
        connector = monitors[0][0][0]
        return connector

    def export_request_stub(self, handle):
        # The frontend may Close() the request; ours complete synchronously,
        # so a no-op object is enough.
        try:
            self.connection.register_object(
                handle, self.request_node.interfaces[0],
                lambda conn, sender, path, iface, method, params, inv:
                    inv.return_value(None),
                None, None)
        except GLib.Error:
            pass  # duplicate registration on a reused handle

    def on_get_property(self, connection, sender, path, iface, prop):
        if iface.endswith("RemoteDesktop"):
            if prop == "version":
                return GLib.Variant("u", 1)
            if prop == "AvailableDeviceTypes":
                return GLib.Variant("u", 3)  # keyboard | pointer
            return None
        if prop == "version":
            return GLib.Variant("u", 2)
        if prop == "AvailableSourceTypes":
            return GLib.Variant("u", 1)  # monitors only
        if prop == "AvailableCursorModes":
            return GLib.Variant("u", 7)  # hidden | embedded | metadata
        return None

    def on_method_call(self, connection, sender, path, iface, method,
                       parameters, invocation):
        try:
            if method.startswith("Notify"):
                args = parameters.unpack()
                session = self.sessions.get(args[0])
                if session is not None:
                    session.forward_input(method, args[1:])
                invocation.return_value(None)
                return
            handler = getattr(self, f"handle_{method}", None)
            if handler is None:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD,
                    f"Unknown method {method}")
                return
            handler(parameters, invocation,
                    remote_desktop=iface.endswith("RemoteDesktop"))
        except Exception as error:  # report, don't crash the daemon
            log(f"{method} failed: {error}")
            if method.startswith("Notify"):
                invocation.return_value(None)
            elif method in ("CreateSession", "SelectSources", "SelectDevices",
                            "Start"):
                invocation.return_value(GLib.Variant("(ua{sv})", (2, {})))
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.FAILED, str(error))

    def handle_CreateSession(self, parameters, invocation,
                             remote_desktop=False):
        handle, session_handle, app_id, _options = parameters.unpack()
        self.export_request_stub(handle)
        session = Session(self, session_handle, app_id)
        session.remote_desktop = remote_desktop
        self.sessions[session_handle] = session
        log(f"session {session_handle}: created for "
            f"app '{app_id or 'unsandboxed'}'"
            + (" [remote desktop]" if remote_desktop else ""))
        invocation.return_value(GLib.Variant("(ua{sv})", (0, {})))

    def handle_SelectSources(self, parameters, invocation,
                             remote_desktop=False):
        handle, session_handle, _app_id, options = parameters.unpack()
        self.export_request_stub(handle)
        session = self.sessions[session_handle]
        session.cursor_mode = CURSOR_MODE_MAP.get(
            options.get("cursor_mode", 2), 1)
        session.sources_selected = True
        invocation.return_value(GLib.Variant("(ua{sv})", (0, {})))

    def handle_SelectDevices(self, parameters, invocation,
                             remote_desktop=False):
        handle, session_handle, _app_id, options = parameters.unpack()
        self.export_request_stub(handle)
        session = self.sessions[session_handle]
        session.device_types = options.get("types", 3) & 3
        invocation.return_value(GLib.Variant("(ua{sv})", (0, {})))

    def handle_Start(self, parameters, invocation, remote_desktop=False):
        handle, session_handle, _app_id, _parent, _options = \
            parameters.unpack()
        self.export_request_stub(handle)
        results = self.sessions[session_handle].start()
        invocation.return_value(GLib.Variant("(ua{sv})", (0, results)))

    def handle_OpenPipeWireRemote(self, parameters, invocation,
                                  remote_desktop=False):
        runtime_dir = os.environ.get(
            "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        pipewire_socket = os.environ.get("PIPEWIRE_REMOTE", "pipewire-0")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(os.path.join(runtime_dir, pipewire_socket))
        fd_list = Gio.UnixFDList.new()
        fd_index = fd_list.append(sock.fileno())
        sock.close()  # fd_list holds a dup
        invocation.return_value_with_unix_fd_list(
            GLib.Variant("(h)", (fd_index,)), fd_list)


def main():
    # atexit (which kills the relays) doesn't run on plain SIGTERM
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    loop = GLib.MainLoop()
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    connection.connect("closed", lambda *args: loop.quit())
    Backend(connection)
    Gio.bus_own_name_on_connection(
        connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        None, lambda conn, name: (log(f"lost bus name {name}"), loop.quit()))
    log(f"ready, owning {BUS_NAME}")
    loop.run()


if __name__ == "__main__":
    main()
