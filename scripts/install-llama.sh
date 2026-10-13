#!/usr/bin/env bash
# Download the pinned llama.cpp CPU build and verify its SHA-256. Fails closed.
set -euo pipefail
ROOT="${VIBECI_HOME:?VIBECI_HOME not set}"
LOCK="${VIBECI_LOCK:?VIBECI_LOCK not set}"
read -r URL SHA BUILD < <(python3 - "$LOCK" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))["llama_cpp"]
print(d["url"], d["sha256"], d["build"])
PY
)
DEST="$ROOT/llama-$BUILD"
if [ -x "$DEST/llama-server" ]; then echo "llama.cpp $BUILD cached"; exit 0; fi
mkdir -p "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl --fail --silent --show-error --location --retry 3 --retry-delay 2 --proto '=https' --tlsv1.2 -o "$TMP/llama.tgz" "$URL"
echo "$SHA  $TMP/llama.tgz" | sha256sum -c - >/dev/null || { echo "::error::llama.cpp checksum mismatch"; exit 1; }
tar -xzf "$TMP/llama.tgz" -C "$TMP"
mv "$TMP/llama-$BUILD" "$DEST"
echo "llama.cpp $BUILD installed"
