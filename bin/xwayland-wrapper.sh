#!/bin/sh
# wlroots has no config option to pass extra Xwayland flags; WLR_XWAYLAND lets
# us swap in this wrapper to append extra ones to whatever args wlroots
# already constructs.
export WAYLAND_DEBUG=1
exec /usr/bin/Xwayland "$@"
