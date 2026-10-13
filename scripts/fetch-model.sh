#!/usr/bin/env bash
# Fetch a pinned GGUF (commit-pinned URL) and verify SHA-256. Fails closed.
# VIBECI_VERIFY=full (default) re-hashes cached files; =size trusts a marker written after a full verification.
set -euo pipefail
NAME="${1:?model name}"
ROOT="${VIBECI_HOME:?VIBECI_HOME not set}"
LOCK="${VIBECI_LOCK:?VIBECI_LOCK not set}"
read -r REPO REV FILE SHA < <(python3 - "$LOCK" "$NAME" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))["models"].get(sys.argv[2])
if not m:
    sys.exit("unknown model: " + sys.argv[2])
print(m["repo"], m["revision"], m["file"], m["sha256"])
PY
)
DIR="$ROOT/models"; TARGET="$DIR/$FILE"; MARK="$TARGET.verified"
mkdir -p "$DIR"
if [ -f "$TARGET" ] && [ -f "$MARK" ]; then
  if [ "${VIBECI_VERIFY:-full}" = "size" ]; then
    [ "$(cat "$MARK")" = "$SHA $(stat -c %s "$TARGET")" ] && { echo "model cached (size-verified)"; echo "$TARGET"; exit 0; }
  elif echo "$SHA  $TARGET" | sha256sum -c - >/dev/null 2>&1; then
    echo "model cached (hash-verified)"; echo "$TARGET"; exit 0
  fi
  echo "cached model failed verification, re-downloading"; rm -f "$TARGET" "$MARK"
fi
curl --fail --silent --show-error --location --retry 3 --retry-delay 2 --proto '=https' --tlsv1.2 \
  -o "$TARGET.part" "https://huggingface.co/$REPO/resolve/$REV/$FILE"
echo "$SHA  $TARGET.part" | sha256sum -c - >/dev/null || { rm -f "$TARGET.part"; echo "::error::model checksum mismatch"; exit 1; }
mv "$TARGET.part" "$TARGET"
echo "$SHA $(stat -c %s "$TARGET")" > "$MARK"
echo "model downloaded and verified"; echo "$TARGET"
