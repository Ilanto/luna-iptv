#!/bin/sh
# Lint and every test that does not need a real screen, on Qt's offscreen platform
# at idle CPU and I/O priority: no window appears and a game or video keeps running
# smoothly. Native video and window-manager checks still need ./scripts/test.sh.
set -eu
LUNA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$LUNA_ROOT"
if [ -d "$LUNA_ROOT/work/deps/root/usr/lib64" ]; then
  export LD_LIBRARY_PATH="$LUNA_ROOT/work/deps/root/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
quiet() {
  nice -n 19 ionice -c3 env -u DISPLAY -u WAYLAND_DISPLAY -u GDK_BACKEND \
    QT_QPA_PLATFORM=offscreen "$@"
}
quiet .venv/bin/ruff check luna_iptv tests scripts
quiet .venv/bin/ruff format --check luna_iptv tests scripts
# These need libmpv to render into a real OpenGL surface or a compositor.
quiet .venv/bin/python -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_transport_ui.py \
  --ignore=tests/test_mini_player_native.py \
  --ignore=tests/test_preferences_native.py \
  -k "not account_dialog_shows_cache and not escape_invalidates_late_account" \
  "$@"
