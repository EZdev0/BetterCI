#!/usr/bin/env bash
# Download the pinned llama.cpp CPU build and verify its SHA-256. Fails closed.
#
# The verified archive stays in ~/.vibeci. On EVERY run its hash is checked
# against tools.lock.json and the extracted directory is rebuilt from that
# archive. A poisoned cache entry (e.g. a replaced llama-server binary) is
# therefore never executed -- the same fail-closed rule that fetch-model.sh
# already applies to the GGUF model.
set -euo pipefail
ROOT="${VIBECI_HOME:?VIBECI_HOME not set}"
LOCK="${VIBECI_LOCK:?VIBECI_LOCK not set}"
read -r URL SHA BUILD < <(python3 - "$LOCK" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))["llama_cpp"]
print(d["url"], d["sha256"], d["build"])
PY
)
ARCHIVE="$ROOT/llama-$BUILD.tar.gz"
DEST="$ROOT/llama-$BUILD"
mkdir -p "$ROOT"
if [ -f "$ARCHIVE" ] && echo "$SHA  $ARCHIVE" | sha256sum -c - >/dev/null 2>&1; then
  echo "llama.cpp $BUILD archive cached (hash re-verified)"
else
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl --fail --silent --show-error --location --retry 3 --retry-delay 2 --proto '=https' --tlsv1.2 \
    -o "$TMP/llama.tgz" "$URL"
  if ! echo "$SHA  $TMP/llama.tgz" | sha256sum -c - >/dev/null 2>&1; then
    echo "::error::llama.cpp checksum mismatch"
    exit 1
  fi
  mv "$TMP/llama.tgz" "$ARCHIVE"
  echo "llama.cpp $BUILD downloaded and verified"
fi
# Re-extract from the just-verified archive; do not trust files already in DEST.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP"
if [ ! -x "$TMP/llama-$BUILD/llama-server" ]; then
  echo "::error::archive does not contain llama-$BUILD/llama-server"
  exit 1
fi
rm -rf "$DEST"
mv "$TMP/llama-$BUILD" "$DEST"
echo "llama.cpp $BUILD installed"
