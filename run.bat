@echo off
setlocal
cd /d "%~dp0"
title EasyOats Order Manager
if exist ".venv\Scripts\python.exe" goto install
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    py -3.12 -m venv .venv
    goto checkvenv
)
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if not errorlevel 1 (
    python -m venv .venv
    goto checkvenv
)
echo Python 3.12 is required. Install Python 3.12 from python.org, then run this file again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1
:checkvenv
if not exist ".venv\Scripts\python.exe" goto failed
:install
echo Preparing EasyOats. First startup needs an internet connection.
set "EASYOATS_REQUIREMENTS=requirements.txt"
if exist requirements-lock.txt set "EASYOATS_REQUIREMENTS=requirements-lock.txt"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%EASYOATS_REQUIREMENTS%"
if errorlevel 1 goto failed
echo Opening EasyOats at http://localhost:8501
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.headless false --browser.gatherUsageStats false
if errorlevel 1 goto failed
exit /b 0
:failed
echo EasyOats could not start. Check your internet connection and Python installation.
pause
exit /b 1
