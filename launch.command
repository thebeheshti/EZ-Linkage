#!/bin/bash
# Double-click in Finder to start EZ Linkage on macOS. It opens in Terminal;
# close that window, or press Ctrl+C in it, to stop the app.
cd "$(dirname "$0")" || exit 1
export LINKAGE_APP_OPEN_BROWSER=1
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    PYTHON="python"
fi
"$PYTHON" app.py
