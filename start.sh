#!/usr/bin/env bash
# One-command bootstrap for EmberSight.
# Starts the agent service (port 8000) and the web app (port 3000) together.
# Ctrl-C cleanly stops both.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- prereq checks ----------------------------------------------------------
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "✗ Missing required tool: $1"
    echo "  $2"
    exit 1
  fi
}
need uv    "install: curl -LsSf https://astral.sh/uv/install.sh | sh"
need pnpm  "install: npm i -g pnpm   (requires Node 20+)"

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    echo "ℹ No .env found — copying .env.example to .env."
    echo "  Open .env and set ANTHROPIC_API_KEY before agents can call Claude."
    cp .env.example .env
  else
    echo "✗ No .env or .env.example found. Aborting."
    exit 1
  fi
fi

if ! grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
  echo "⚠ ANTHROPIC_API_KEY is not set in .env."
  echo "  Agents will fail to stream until you add it. Continuing anyway."
fi

# --- install deps -----------------------------------------------------------
echo "▶ Installing agent deps (uv sync)…"
( cd agent && uv sync )

echo "▶ Installing web deps (pnpm install)…"
( cd web && pnpm install )

# --- run both ---------------------------------------------------------------
mkdir -p .run
AGENT_LOG="$ROOT/.run/agent.log"
WEB_LOG="$ROOT/.run/web.log"

cleanup() {
  echo ""
  echo "⏹ Stopping…"
  # kill children of this script (agent + web + their subprocesses)
  pkill -P $$ 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "▶ Starting agent service on :8000  (logs: .run/agent.log)"
( cd agent && uv run uvicorn embersight_agent.main:app --reload --port 8000 ) \
  > "$AGENT_LOG" 2>&1 &
AGENT_PID=$!

echo "▶ Starting web app on :3000        (logs: .run/web.log)"
( cd web && pnpm dev ) > "$WEB_LOG" 2>&1 &
WEB_PID=$!

# Wait briefly for boot, then surface the open URL.
sleep 2
echo ""
echo "─────────────────────────────────────────────────────────────"
echo "  EmberSight is starting up."
echo "  Web:   http://localhost:3000"
echo "  Agent: http://localhost:8000  (FastAPI docs at /docs)"
echo ""
echo "  Tail both logs:   tail -f .run/agent.log .run/web.log"
echo "  Stop:             Ctrl-C in this terminal"
echo "─────────────────────────────────────────────────────────────"

# Block on whichever child exits first; cleanup will then kill the other.
wait -n "$AGENT_PID" "$WEB_PID"
echo "⚠ One of the services exited. Shutting the other down."
cleanup
