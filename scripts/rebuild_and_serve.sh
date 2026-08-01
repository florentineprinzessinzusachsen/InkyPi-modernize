#!/usr/bin/env bash
# rebuild_and_serve.sh — rebuild CSS (and optionally the JS/CSS asset bundle),
# then (re)start the InkyPi dev server on port 8080.
#
# Usage:
#   scripts/rebuild_and_serve.sh              # rebuild CSS, start dev server
#   scripts/rebuild_and_serve.sh --bundle      # also rebuild the JS/CSS bundle
#                                               # (src/static/dist/) — opt-in;
#                                               # tests the production bundled
#                                               # path instead of the normal
#                                               # always-current unbundled dev
#                                               # experience
#
# Safety: before killing whatever is bound to the port, this verifies the
# process is actually InkyPi's own dev server (its command line contains
# "src/inkypi.py"). If something else owns the port, it refuses to touch it.
#
# Env overrides:
#   INKYPI_PORT   — port to use (default 8080)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
cd "${REPO_ROOT}"

PORT="${INKYPI_PORT:-8080}"

# Prefer the repo venv's python, matching every other script here.
PYTHON="python3"
if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
fi

BUNDLE=0
for arg in "$@"; do
  case "$arg" in
    --bundle) BUNDLE=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
  esac
done

echo "==> Rebuilding CSS (scripts/build_css.py)"
"${PYTHON}" scripts/build_css.py

if [ "${BUNDLE}" -eq 1 ]; then
  echo "==> Rebuilding JS/CSS asset bundle (scripts/build_assets.py)"
  "${PYTHON}" scripts/build_assets.py
else
  # Remove a stale bundle from a previous --bundle run so base.html falls
  # back to the normal always-current unbundled asset path, matching
  # CLAUDE.md's documented default dev experience.
  if [ -d "src/static/dist" ]; then
    echo "==> Removing stale src/static/dist/ (unbundled dev mode)"
    rm -rf src/static/dist
  fi
fi

echo "==> Checking port ${PORT}"
EXISTING_PID="$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "${EXISTING_PID}" ]; then
  CMD="$(ps -p "${EXISTING_PID}" -o command= 2>/dev/null || true)"
  case "$CMD" in
    *src/inkypi.py*)
      echo "==> Stopping existing InkyPi dev server (pid ${EXISTING_PID})"
      kill "${EXISTING_PID}"
      for _ in $(seq 1 25); do
        lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
        sleep 0.2
      done
      if lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "==> Still running after 5s, force-killing (pid ${EXISTING_PID})"
        kill -9 "${EXISTING_PID}" 2>/dev/null || true
        sleep 0.5
      fi
      ;;
    *)
      echo "error: port ${PORT} is in use by something that is NOT InkyPi's dev server:" >&2
      echo "  pid ${EXISTING_PID}: ${CMD}" >&2
      echo "Refusing to kill it. Stop it yourself, or set INKYPI_PORT to use a different port." >&2
      exit 1
      ;;
  esac
fi

echo "==> Starting InkyPi dev server on http://localhost:${PORT} (Ctrl+C to stop)"
export PYTHONPATH=src
exec "${PYTHON}" src/inkypi.py --dev
