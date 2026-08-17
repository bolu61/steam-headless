REPO_DIR := $(abspath .)

XDG_CONFIG_HOME ?= $(HOME)/.config
XDG_DATA_HOME ?= $(HOME)/.local/share

SERVICE_LINK := $(XDG_CONFIG_HOME)/systemd/user/steam-headless.service
START_LINK := $(XDG_DATA_HOME)/steam-headless/start.sh
PIPEWIRE_LINK := $(XDG_CONFIG_HOME)/pipewire/pipewire-pulse.conf.d/50-headless-fallback-audio.conf
WIREPLUMBER_LINK := $(XDG_CONFIG_HOME)/wireplumber/wireplumber.conf.d/99-no-default-node-persistence.conf
PORTAL_WLR_LINK := $(XDG_CONFIG_HOME)/xdg-desktop-portal-wlr/config

LINKS := $(SERVICE_LINK) $(START_LINK) $(PIPEWIRE_LINK) $(WIREPLUMBER_LINK) $(PORTAL_WLR_LINK)

XSERVER_DIR := $(REPO_DIR)/ext/xserver
XSERVER_PATCHES := $(sort $(wildcard $(REPO_DIR)/src/patches/xserver/*.patch))
XKB_OUTPUT_DIR ?= $(HOME)/.cache/xwayland-xkb

.PHONY: install uninstall xwayland

install:
	mkdir -p $(dir $(SERVICE_LINK))
	ln -sf $(REPO_DIR)/etc/systemd/user/steam-headless.service $(SERVICE_LINK)
	mkdir -p $(dir $(START_LINK))
	ln -sf $(REPO_DIR)/bin/start.sh $(START_LINK)
	mkdir -p $(dir $(PIPEWIRE_LINK))
	ln -sf $(REPO_DIR)/etc/pipewire/pipewire-pulse.conf.d/50-headless-fallback-audio.conf $(PIPEWIRE_LINK)
	mkdir -p $(dir $(WIREPLUMBER_LINK))
	ln -sf $(REPO_DIR)/etc/wireplumber/wireplumber.conf.d/99-no-default-node-persistence.conf $(WIREPLUMBER_LINK)
	mkdir -p $(dir $(PORTAL_WLR_LINK))
	ln -sf $(REPO_DIR)/etc/xdg-desktop-portal-wlr/config $(PORTAL_WLR_LINK)
	systemctl --user daemon-reload

uninstall:
	rm -f $(LINKS)
	systemctl --user daemon-reload

# Patched Xwayland (see project_nte_menu_drag_is_engine_bug memory for why):
# resets ext/xserver to its pinned tag, applies our local patches on top, and
# builds. Re-run any time the submodule pointer or patches change; safe to
# re-run otherwise (git checkout -- is a no-op on an already-clean tree).
xwayland:
	git -C $(XSERVER_DIR) checkout xwayland-24.1.13 -- .
	git -C $(XSERVER_DIR) checkout xwayland-24.1.13
	for p in $(XSERVER_PATCHES); do \
		git -C $(XSERVER_DIR) apply "$$p" || exit 1; \
	done
	mkdir -p $(XKB_OUTPUT_DIR)
	meson setup --reconfigure $(XSERVER_DIR)/build $(XSERVER_DIR) \
		-Dxvfb=false -Ddocs=false -Ddevel-docs=false -Ddocs-pdf=false \
		-Dxv=false -Dxdmcp=false -Dxdm-auth-1=false -Dxinerama=false \
		-Dxkb_dir=/usr/share/X11/xkb -Dxkb_bin_dir=/usr/bin \
		-Dxkb_output_dir=$(XKB_OUTPUT_DIR)
	ninja -C $(XSERVER_DIR)/build
