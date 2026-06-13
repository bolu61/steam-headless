#!/bin/bash
# Minimal headless Steam Remote Play session — rebuilt from scratch 2026-06-13.
#
# The previous version carried the full ChimeraOS/SteamOS-derived env + a
# ready-fd handshake (saved as start.sh.gamescope). On this box it wedged the
# NVIDIA GPU hard enough to hang the whole machine on two different games
# (NTE, Stellar Blade). So: back to bare metal. gamescope launches Steam as its
# own child and manages its lifecycle; add flags/env back ONE at a time, only
# with evidence each is needed.
#
# Flags, and why each is here (the "required" set):
#   dbus-run-session        Steam needs a session bus; an SSH login usually has
#                           none. Tears the bus down when gamescope exits.
#   --backend headless      No physical display; output goes to a pipewire
#                           stream that Steam Remote Play captures.
#   --prefer-vk-device      Dual-GPU box (RTX 5090 + AMD iGPU). Pin compositing
#                           to the 5090 (10de:2b85) or gamescope may pick iGPU.
#   --output-width/-height  Headless has no monitor to infer geometry from.
#   --nested-refresh        Likewise no monitor to infer refresh from.
#   --steam                 Window tagging for Steam Input controller routing.
#   steam -pipewire         Steam's pipewire capture path for Remote Play video.
#   steam -gamepadui        Big Picture / console UI for a streamed session.

# Force the guest (Steam UI + games) onto the RTX 5090. gamescope composites on
# the 5090 (--prefer-vk-device), but without these the guest's GL/Vulkan can land
# on the AMD iGPU; the cross-GPU buffer import then shows as a BLACK window while
# the gamescope-drawn cursor still appears. Added back 2026-06-13 after the
# stripped config rendered Big Picture black with a working cursor.
export __NV_PRIME_RENDER_OFFLOAD=1       # select the dGPU under PRIME offload
export __GLX_VENDOR_LIBRARY_NAME=nvidia  # GLX (Steam's CEF UI) uses the NVIDIA driver
export __VK_LAYER_NV_optimus=NVIDIA_only # Vulkan enumerates only the NVIDIA GPU
export MESA_VK_DEVICE_SELECT=10de:2b85   # any Mesa/Vulkan path also picks the 5090
# EGL → NVIDIA only. Steam Remote Play's pipewire capture imports gamescope's
# dmabuf via EGL; gamescope allocates it on the 5090 (renderD129) but EGL
# otherwise defaults to the AMD iGPU (card0/renderD128), and an AMD context
# can't import an NVIDIA dmabuf → "CDesktopCapturePipeWire: Couldn't import
# dmabuf: Invalid argument" → black screen. Force EGL onto the NVIDIA vendor.
export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json

exec dbus-run-session -- gamescope \
	--backend headless \
	--prefer-vk-device 10de:2b85 \
	--output-width 2560 --output-height 1440 \
	--nested-refresh 144 \
	--steam \
	-- steam -pipewire -gamepadui
