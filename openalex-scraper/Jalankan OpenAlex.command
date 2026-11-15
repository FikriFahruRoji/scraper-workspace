#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -x ../.venv/bin/python ]] && ../.venv/bin/python -c 'import scrapling.fetchers, streamlit, pandas' 2>/dev/null; then
  exec ../.venv/bin/python -m streamlit run app.py
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import scrapling.fetchers, streamlit, pandas' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m streamlit run app.py
