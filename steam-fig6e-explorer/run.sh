#!/usr/bin/env bash
# One-command launcher: makes a private virtualenv next to this script,
# installs dependencies into it, and starts the app in your browser.
#
#   ./run.sh
#
# Re-running is cheap — the venv is reused.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PY="${PYTHON:-}"

find_python() {
  for c in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}

if [ -z "$PY" ]; then
  PY="$(find_python)" || {
    echo "ERROR: need Python 3.10 or newer on your PATH."
    echo "  macOS:  brew install python@3.12     (or python.org installer)"
    echo "  Linux:  sudo apt install python3 python3-venv"
    echo "Already have one somewhere else?  PYTHON=/path/to/python3 ./run.sh"
    exit 1
  }
fi
echo "Using $($PY --version) at $(command -v "$PY")"

if [ ! -d "$VENV" ]; then
  echo "Creating virtualenv in $VENV ..."
  "$PY" -m venv "$VENV" || { echo "ERROR: could not create a virtualenv (is python3-venv installed?)"; exit 1; }
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [ ! -f "$VENV/.deps-ok" ]; then
  echo "Installing dependencies (one time, ~1-2 min) ..."
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r requirements.txt
  python - <<'PYCHECK'
import pyBigWig, streamlit, numpy, matplotlib, Bio  # noqa: F401
print("dependency check: OK")
PYCHECK
  touch "$VENV/.deps-ok"
fi

URL="http://localhost:${PORT:-8501}"
echo
echo "Starting the STEAM-v1 Fig 6e explorer ..."
echo "Opening $URL in your browser. Press Ctrl+C here to stop."
echo

# Open the browser ourselves once the server is listening. We run Streamlit
# headless because otherwise its first-run "Email:" prompt blocks on stdin,
# which looks like a hang.
( for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$URL" 2>/dev/null; then
      case "$(uname -s)" in
        Darwin) open "$URL" ;;
        *) command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1 ;;
      esac
      break
    fi
    sleep 1
  done ) &

exec streamlit run app.py \
  --server.port "${PORT:-8501}" \
  --server.headless true \
  --browser.gatherUsageStats false
