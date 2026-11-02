@echo off
cd /d "%~dp0"
set LINKAGE_APP_OPEN_BROWSER=1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
) else (
    python app.py
)
pause
