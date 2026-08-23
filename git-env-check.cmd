@echo off
REM ============================================================
REM  git-env-check.cmd  (v8.0)
REM  Purpose: detect & fix "terminal cwd vs expected repo" mismatch
REM    - Clears GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE pollution
REM    - Auto cd to repo root (this script must live in repo root)
REM    - Verifies repo identity (remote / branch / HEAD / dirty files)
REM    - Scans D:\ and user profile for OTHER repo copies
REM    - Writes fingerprint file .git\terminal-ping.txt for AI-side check
REM  Usage: run "git-env-check.cmd" from any directory
REM  NOTE : run in cmd (not PowerShell), so the auto-cd persists
REM ============================================================

set "GT_EXPECTED=%~dp0"
if "%GT_EXPECTED:~-1%"=="\" set "GT_EXPECTED=%GT_EXPECTED:~0,-1%"
set "GT_PING=%GT_EXPECTED%\.git\terminal-ping.txt"

echo ============================================================
echo   Git terminal environment consistency check
echo ============================================================

REM ---------- [1/5] clear git-locator pollution ----------
echo.
echo [1/5] Checking git locator env vars...
set "GT_FIX1="
if defined GIT_DIR (
    echo     [FIX] GIT_DIR=%GIT_DIR%  cleared
    set "GIT_DIR="
    set "GT_FIX1=1"
)
if defined GIT_WORK_TREE (
    echo     [FIX] GIT_WORK_TREE=%GIT_WORK_TREE%  cleared
    set "GIT_WORK_TREE="
    set "GT_FIX1=1"
)
if defined GIT_INDEX_FILE (
    echo     [FIX] GIT_INDEX_FILE=%GIT_INDEX_FILE%  cleared
    set "GIT_INDEX_FILE="
    set "GT_FIX1=1"
)
if not defined GT_FIX1 echo     OK: no polluted vars

REM ---------- [2/5] cwd check & auto switch ----------
echo.
echo [2/5] Checking current directory...
echo     cwd: %CD%
if /i "%CD%"=="%GT_EXPECTED%" (
    echo     OK: already in expected repo
) else (
    if exist "%GT_EXPECTED%\.git" (
        echo     [FIX] wrong cwd, switching to %GT_EXPECTED%
        cd /d "%GT_EXPECTED%"
    ) else (
        echo     [WARN] expected repo not found or not a git repo
    )
)

REM ---------- [3/5] repo identity ----------
echo.
echo [3/5] Verifying repo identity...
where git >nul 2>&1
if errorlevel 1 (
    echo     [ERROR] git not in PATH
    goto :end
)
set "GT_ROOT="
for /f "delims=" %%i in ('git rev-parse --show-toplevel 2^>nul') do set "GT_ROOT=%%i"
if not defined GT_ROOT (
    echo     [ERROR] not inside any git repository
    goto :end
)
set "GT_BRANCH="
for /f "delims=" %%i in ('git branch --show-current 2^>nul') do set "GT_BRANCH=%%i"
set "GT_HEAD="
for /f "delims=" %%i in ('git rev-parse --short HEAD 2^>nul') do set "GT_HEAD=%%i"
set "GT_REMOTE="
for /f "delims=" %%i in ('git remote get-url origin 2^>nul') do set "GT_REMOTE=%%i"
echo     repo root : %GT_ROOT%
echo     branch    : %GT_BRANCH%
echo     HEAD      : %GT_HEAD%
echo     remote    : %GT_REMOTE%

REM ---------- [4/5] working tree status ----------
echo.
echo [4/5] Checking working tree...
set /a GT_DIRTYCOUNT=0
for /f %%i in ('git status --porcelain 2^>nul') do set /a GT_DIRTYCOUNT+=1
if exist "%GT_EXPECTED%\scripts\check-doc-code-sync.py" (
    echo     marker: scripts\check-doc-code-sync.py EXISTS [v8.0 changes present]
) else (
    echo     marker: scripts\check-doc-code-sync.py MISSING [v8.0 changes not here]
)
echo     uncommitted files: %GT_DIRTYCOUNT%
if %GT_DIRTYCOUNT% GTR 0 (
    echo     hint: uncommitted changes present - last commit may have run in ANOTHER copy
)

REM ---------- [5/5] scan other copies + write fingerprint ----------
echo.
echo [5/5] Scanning for other repo copies...
set "GT_COPIES=0"
for /f "delims=" %%d in ('dir /b /ad "D:\" 2^>nul') do call :scan_copy "D:\%%d"
for /f "delims=" %%d in ('dir /b /ad "%USERPROFILE%\Desktop" 2^>nul') do call :scan_copy "%USERPROFILE%\Desktop\%%d"
for /f "delims=" %%d in ('dir /b /ad "%USERPROFILE%\Documents" 2^>nul') do call :scan_copy "%USERPROFILE%\Documents\%%d"
if "%GT_COPIES%"=="0" echo     OK: no other repo copies found

> "%GT_PING%" echo pingTime=%date% %time%
>> "%GT_PING%" echo computer=%COMPUTERNAME%
>> "%GT_PING%" echo user=%USERNAME%
>> "%GT_PING%" echo cwd=%CD%
>> "%GT_PING%" echo root=%GT_ROOT%
>> "%GT_PING%" echo branch=%GT_BRANCH%
>> "%GT_PING%" echo head=%GT_HEAD%
>> "%GT_PING%" echo remote=%GT_REMOTE%
>> "%GT_PING%" echo dirtyFiles=%GT_DIRTYCOUNT%

echo.
echo     fingerprint written: %GT_PING%
echo.
echo ============================================================
echo   DONE. Send the output above to AI, or tell AI to read:
echo   %GT_PING%
echo   (fresh pingTime = terminal and sandbox share the same disk)
echo ============================================================

goto :end

:scan_copy
if exist "%~1\.git" (
    if /i not "%~f1"=="%GT_EXPECTED%" (
        for /f "delims=" %%h in ('git -C "%~1" rev-parse --short HEAD 2^>nul') do (
            echo     [COPY FOUND] %~f1  HEAD: %%h
            set /a GT_COPIES+=1
        )
    )
)
goto :eof

:end
set "GT_EXPECTED="
set "GT_PING="
set "GT_FIX1="
set "GT_ROOT="
set "GT_BRANCH="
set "GT_HEAD="
set "GT_REMOTE="
set "GT_DIRTYCOUNT="
set "GT_COPIES="
