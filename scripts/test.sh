#!/bin/sh
set -eu
LUNA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$LUNA_ROOT"
if [ -d "$LUNA_ROOT/work/deps/root/usr/lib64" ]; then
  export LD_LIBRARY_PATH="$LUNA_ROOT/work/deps/root/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
.venv/bin/ruff check luna_iptv tests scripts
.venv/bin/ruff format --check luna_iptv tests scripts
.venv/bin/python -m pytest -q -W error
.venv/bin/python scripts/player_probe.py
.venv/bin/python scripts/smoke.py
