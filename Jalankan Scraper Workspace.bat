@echo off
setlocal
cd /d "%~dp0"
set "WORKSPACE_PY=.venv\Scripts\python.exe"
if not exist "%WORKSPACE_PY%" (
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)
"%WORKSPACE_PY%" -c "import importlib.metadata as m; import plotly, pandas, xlsxwriter, Sastrawi, emoji, openpyxl, sklearn; assert m.version('streamlit')=='1.67.0' and m.version('scrapling')=='0.5.1' and m.version('google-play-scraper')=='1.2.7'" >nul 2>&1
if errorlevel 1 (
    "%WORKSPACE_PY%" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
"%WORKSPACE_PY%" -m workspace_app.setup_runtime
if errorlevel 1 goto failed
"%WORKSPACE_PY%" -m streamlit run workspace_app.py --server.address 127.0.0.1 --server.port 8510
if errorlevel 1 goto failed
exit /b 0
:failed
echo Gagal membuka Scraper Workspace. Periksa pesan di atas dan pastikan Python 3.11 tersedia.
pause
exit /b 1
