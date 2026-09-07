@echo off
REM ===================================================================
REM  DSEX DOM - standalone static frontend server (Windows)
REM  Simulates the Hostinger split: frontend on :5500, backend on :8000.
REM  Because the origin differs from the backend, pass the ws= override.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo [DSEX-DOM] Frontend served at:
echo     http://127.0.0.1:5500/?ws=ws://127.0.0.1:8000/dom
echo [DSEX-DOM] (make sure the backend is running on :8000)
echo.
py -3 -m http.server 5500 --directory frontend 2>nul || python -m http.server 5500 --directory frontend

endlocal
