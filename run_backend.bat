@echo off
REM ===================================================================
REM  DSEX DOM - one-shot backend launcher (Windows)
REM  Creates a venv + installs deps on first run, then starts the broker.
REM  Open http://127.0.0.1:8000/ in your browser to see the live DOM.
REM ===================================================================
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo [DSEX-DOM] Creating virtual environment...
  py -3 -m venv venv 2>nul || python -m venv venv
  call "venv\Scripts\activate.bat"
  echo [DSEX-DOM] Installing dependencies...
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call "venv\Scripts\activate.bat"
)

echo.
echo [DSEX-DOM] Backend starting at http://127.0.0.1:8000/   (Ctrl+C to stop)
echo [DSEX-DOM] WebSocket feed:      ws://127.0.0.1:8000/dom
echo [DSEX-DOM] Open the URL above in your browser to watch the DOM live.
echo.
python -m backend.server

endlocal
