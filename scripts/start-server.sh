#!/usr/bin/env bash
# Start llama-server on loopback only and wait until it is healthy.
set -euo pipefail
BIN_DIR="${1:?llama dir}"; MODEL="${2:?model path}"
RUN="${VIBECI_RUN:-${RUNNER_TEMP:-/tmp}}"; mkdir -p "$RUN"
PORT="${VIBECI_PORT:-8080}"; THREADS="${VIBECI_THREADS:-0}"; SPEC="${VIBECI_SPEC:-none}"
[ "$THREADS" = "0" ] && THREADS="$(nproc)"
export LD_LIBRARY_PATH="$BIN_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
ARGS=(-m "$MODEL" --host 127.0.0.1 --port "$PORT" -c 8192 -t "$THREADS" -fa on --parallel 1
      --no-webui --alias local --cache-reuse 256 --no-warmup)
[ "$SPEC" != "none" ] && ARGS+=(--spec-type "$SPEC")
nohup "$BIN_DIR/llama-server" "${ARGS[@]}" > "$RUN/server.log" 2>&1 &
echo $! > "$RUN/server.pid"
for _ in $(seq 1 180); do
  if curl --silent --fail "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then echo "llama-server ready"; exit 0; fi
  kill -0 "$(cat "$RUN/server.pid")" 2>/dev/null || { echo "::error::llama-server died"; tail -n 30 "$RUN/server.log"; exit 1; }
  sleep 1
done
echo "::error::llama-server not healthy after 180s"; tail -n 30 "$RUN/server.log"; exit 1
