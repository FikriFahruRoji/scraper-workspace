#!/bin/bash
set -e
cd "$(dirname "$0")"
python_path="./.venv/bin/python"
if [ ! -x "$python_path" ]; then
    python3.11 -m venv .venv
fi
if ! "$python_path" -c 'import importlib.metadata as m; import plotly, pandas, xlsxwriter; assert m.version("streamlit")=="1.67.0" and m.version("scrapling")=="0.5.1" and m.version("google-play-scraper")=="1.2.7"'; then
    "$python_path" -m ensurepip --upgrade
    "$python_path" -m pip install -r requirements.txt
fi
"$python_path" -m workspace_app.setup_runtime
exec "$python_path" -m streamlit run workspace_app.py --server.address 127.0.0.1 --server.port 8510
