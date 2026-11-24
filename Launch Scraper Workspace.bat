@echo off
setlocal
cd /d "%~dp0" || goto failed
set "WORKSPACE_PY=.venv\Scripts\python.exe"
if exist "%WORKSPACE_PY%" goto run

py -3.11 -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 11))" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 was not found. Follow the Windows setup steps in the README.
    goto failed
)
echo Creating the Python environment...
py -3.11 -m venv .venv
if errorlevel 1 goto failed

:run
"%WORKSPACE_PY%" -m workspace_app.launch
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Scraper Workspace failed to start. See the message above and Troubleshooting in the README.
pause
exit /b 1
