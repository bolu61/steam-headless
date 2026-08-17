#!/bin/bash
# Runs sway under a private D-Bus (dbus-run-session): xdg-desktop-portal.service
# has Requisite=graphical-session.target, unreachable in a bare headless launch,
# so systemd-side portal activation fails and Steam falls back to a black
# X11 root capture. dbus-run-session lets D-Bus activate the portals directly.

export HEADLESS_STEAM_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"

export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=sway

# Makes Proton run its wrapped Windows games as native Wayland clients instead of XWayland.
#export PROTON_ENABLE_WAYLAND=1

# dmabuf zero-copy needs rendering on the same GPU Steam encodes on.
export WLR_RENDER_DRM_DEVICE=/dev/dri/renderD129
export WLR_RENDERER=vulkan
export WLR_BACKENDS=headless

export XDG_DATA_DIRS="$HEADLESS_STEAM_DIR/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XDG_CONFIG_DIRS="$HEADLESS_STEAM_DIR/etc:${XDG_CONFIG_DIRS:-/etc/xdg}"
export XDP_GENERIC_SOURCE_PICKER="$HEADLESS_STEAM_DIR/bin/picker.sh"

# Patched Xwayland: XTestFakeMotionEvent (absolute XTest injection) stored the
# absolute coordinate directly into XI2 RawMotion events instead of computing
# a delta against the device's last known position -- corrupting any client
# reading RawMotion for relative look/gesture input (e.g. NTE's touch-swipe
# menus). XWarpPointer got an equivalent fix for the same underlying issue in
# 2011; XTestFakeMotionEvent never did. See ext/xserver's patch on top of
# xwayland-24.1.13 and the project_nte_menu_drag_is_engine_bug memory.
export WLR_XWAYLAND="$HEADLESS_STEAM_DIR/ext/xserver/build/hw/xwayland/Xwayland"

exec dbus-run-session -- sway --config "$HEADLESS_STEAM_DIR/etc/sway/config"
