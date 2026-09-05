#!/bin/sh
set -eu
LUNA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -d "$LUNA_ROOT/work/deps/root/usr/lib64" ]; then
  export LD_LIBRARY_PATH="$LUNA_ROOT/work/deps/root/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
cd "$LUNA_ROOT"
exec "$LUNA_ROOT/.venv/bin/python" -m luna_iptv "$@"
