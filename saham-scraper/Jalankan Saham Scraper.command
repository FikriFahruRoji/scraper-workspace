#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
# Prefer the workspace environment; standalone copies keep their own environment.
if [ -x "$DIR/../.venv/bin/python" ]; then
  VENV="$DIR/../.venv"
else
  VENV="$DIR/.venv"
fi
if [ ! -x "$VENV/bin/python" ]; then
  PY=$(command -v python3.11 || command -v python3.12 || command -v python3)
  "$PY" -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c 'import streamlit, scrapling, pandas, plotly' >/dev/null 2>&1; then
  "$VENV/bin/python" -m ensurepip --upgrade
  "$VENV/bin/python" -m pip install -r "$DIR/requirements.txt"
fi
exec "$VENV/bin/python" -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
