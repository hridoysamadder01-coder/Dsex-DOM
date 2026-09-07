@echo off
REM ===================================================================
REM  DSEX DOM - terminal UI launcher (Windows)
REM  Backend must be running first (run_backend.bat in another window).
REM  Pass-through args, e.g.:  run_tui.bat --symbol BEXIMCO
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

python tui\dom_tui.py %*

endlocal
