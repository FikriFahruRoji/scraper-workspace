@echo off
setlocal
cd /d "%~dp0" || goto failed
set "WORKSPACE_PY=.venv\Scripts\python.exe"
if exist "%WORKSPACE_PY%" goto run

py -3.11 -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 11))" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 belum ditemukan. Buka README dan ikuti petunjuk Windows.
    goto failed
)
echo Menyiapkan lingkungan Python...
py -3.11 -m venv .venv
if errorlevel 1 goto failed

:run
"%WORKSPACE_PY%" -m workspace_app.launch
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Aplikasi gagal dibuka. Baca pesan di atas dan bagian Pemecahan masalah di README.
pause
exit /b 1
