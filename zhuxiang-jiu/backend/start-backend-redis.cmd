@echo off
REM ============================================================
REM Redis-mode backend startup (fixed env vars + uvicorn)
REM Double-click or run from any directory; Ctrl+C to stop.
REM To switch back to in-memory mode: start uvicorn without
REM this script (env vars live only in this window).
REM ============================================================

cd /d "%~dp0"

set STORE_MODE=redis
set LOCK_MODE=redis
set REDIS_URL=redis://127.0.0.1:6379/0

echo [OK] STORE_MODE=%STORE_MODE%
echo [OK] LOCK_MODE=%LOCK_MODE%
echo [OK] REDIS_URL=%REDIS_URL%
echo [..] starting uvicorn on port 8000 ...
echo.

python -m uvicorn main:app --port 8000

echo.
echo [EXIT] backend stopped. errorlevel=%ERRORLEVEL%
pause
