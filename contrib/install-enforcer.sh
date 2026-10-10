#!/bin/sh
# Installs the noise-limit timer for the current user (no sudo):
# ~/.config/systemd/user/llano-enforce.{service,timer}, enabled and started.
# Remove: systemctl --user disable --now llano-enforce.timer
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
DEST=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
mkdir -p "$DEST"
sed "s|@REPO@|$REPO|" "$REPO/contrib/llano-enforce.service" >"$DEST/llano-enforce.service"
cp "$REPO/contrib/llano-enforce.timer" "$DEST/llano-enforce.timer"
systemctl --user daemon-reload
systemctl --user enable --now llano-enforce.timer
systemctl --user list-timers llano-enforce.timer --no-pager
