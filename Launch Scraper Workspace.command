#!/bin/bash
set -e
cd "$(dirname "$0")"
python_path="./.venv/bin/python"
if [ ! -x "$python_path" ]; then
    if ! command -v python3.11 >/dev/null 2>&1; then
        echo "Python 3.11 was not found. Follow the macOS setup steps in the README."
        exit 1
    fi
    echo "Creating the Python environment..."
    python3.11 -m venv .venv
fi
exec "$python_path" -m workspace_app.launch
