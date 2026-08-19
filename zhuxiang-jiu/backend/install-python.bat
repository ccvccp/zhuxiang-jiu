@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

REM ============================================================
REM  Python 3.11+ 环境自动检测与安装 + 一键测试
REM  适用于: 竹香酒官网 · AI决策筹划模块(29) 后端
REM  自包含: 自动生成 requirements.txt 和 pytest.ini
REM  用法: 双击运行 或 在终端执行 install-python.bat
REM ============================================================

title Python 环境检测与一键测试

echo ============================================
echo   Python 3.11+ 环境检测与一键测试
echo   AI决策筹划模块(29) 后端
echo ============================================
echo.

set "BACKEND_DIR=%~dp0"

REM ---------- 检测已有 Python ----------
echo [1/6] 检测已安装的 Python...
set "PYTHON_CMD="
set "PYTHON_VERSION="

for /f "delims=" %%i in ('py -3 --version 2^>nul') do set "PYTHON_VERSION=%%i"
if defined PYTHON_VERSION (
    set "PYTHON_CMD=py -3"
    goto :found_python
)

for /f "delims=" %%i in ('python --version 2^>nul') do set "PYTHON_VERSION=%%i"
if defined PYTHON_VERSION (
    set "PYTHON_CMD=python"
    goto :found_python
)

for /f "delims=" %%i in ('python3 --version 2^>nul') do set "PYTHON_VERSION=%%i"
if defined PYTHON_VERSION (
    set "PYTHON_CMD=python3"
    goto :found_python
)

echo   未检测到 Python,准备安装...
echo.
goto :install_python

:found_python
echo   已安装: !PYTHON_VERSION!
echo   命令: !PYTHON_CMD!

set "VER_NUM=!PYTHON_VERSION:Python =!"
for /f "tokens=1,2 delims=." %%a in ("!VER_NUM!") do (
    set "MAJOR=%%a"
    set "MINOR=%%b"
)

if !MAJOR! lss 3 (
    echo   [警告] Python !VER_NUM! 版本过低,需要 3.11+
    goto :install_python
)
if !MAJOR! equ 3 if !MINOR! lss 11 (
    echo   [警告] Python !VER_NUM! 版本过低,需要 3.11+
    goto :install_python
)

echo   版本满足要求 (^>= 3.11),跳过安装
echo.
goto :gen_config

REM ---------- 安装 Python ----------
:install_python
echo [2/6] 下载并安装 Python 3.12...
echo.

set "PYTHON_INSTALLER=python-3.12.6-amd64.exe"
set "PYTHON_URL=https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe"
set "PYTHON_DIR=%LOCALAPPDATA%\Programs\Python\Python312"
set "INSTALLER_PATH=%TEMP%\%PYTHON_INSTALLER%"

if exist "%INSTALLER_PATH%" (
    echo   安装包已存在: %INSTALLER_PATH%
) else (
    echo   正在下载 Python 3.12.6 ...
    echo   URL: %PYTHON_URL%
    echo.

    powershell -NoProfile -Command ^
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ^
         $ProgressPreference = 'SilentlyContinue'; ^
         Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%INSTALLER_PATH%'"

    if not exist "%INSTALLER_PATH%" (
        echo   [错误] 下载失败
        echo   请手动从 https://www.python.org/downloads/ 下载
        echo   或从 Microsoft Store: ms-windows-store://pdp/?productid=9NJ46SX7X90P
        pause
        exit /b 1
    )
    echo   下载完成
)
echo.

echo [3/6] 静默安装 Python(添加到PATH)...
echo   安装目录: %PYTHON_DIR%
echo.

"%INSTALLER_PATH%" /quiet ^
    InstallAllUsers=0 ^
    PrependPath=1 ^
    Include_test=0 ^
    Include_doc=0 ^
    InstallDir="%PYTHON_DIR%"

if errorlevel 1 (
    "%INSTALLER_PATH%" /quiet InstallAllUsers=0 PrependPath=1
)

if errorlevel 1 (
    echo   [错误] 安装失败,请手动运行: %INSTALLER_PATH%
    pause
    exit /b 1
)

echo   安装完成
echo.

set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%"

set "PYTHON_CMD=python"
for /f "delims=" %%i in ('!PYTHON_CMD! --version 2^>nul') do set "PYTHON_VERSION=%%i"
if not defined PYTHON_VERSION (
    set "PYTHON_CMD=%PYTHON_DIR%\python.exe"
    for /f "delims=" %%i in ('"!PYTHON_CMD!" --version 2^>nul') do set "PYTHON_VERSION=%%i"
)

if not defined PYTHON_VERSION (
    echo   [错误] Python 安装后仍无法调用
    echo   请关闭窗口后重新打开终端,运行 python --version 验证
    pause
    exit /b 1
)

echo   验证成功: !PYTHON_VERSION!
echo.

REM ---------- 生成配置文件 ----------
:gen_config
echo [4/6] 检查配置文件...
echo.

REM 生成 requirements.txt (如果不存在)
if not exist "%BACKEND_DIR%requirements.txt" (
    echo   生成 requirements.txt...
    powershell -NoProfile -Command ^
        "$content = @'^^fastapi==0.115.0^^uvicorn[standard]==0.32.0^^pydantic==2.9.0^^python-jose[cryptography]==3.3.0^^passlib[bcrypt]==1.7.4^^^^# 测试依赖^^pytest==8.3.0^^httpx==0.27.0^^pytest-cov==5.0.0^^pytest-asyncio==0.24.0^^'@; ^
         $content = $content -replace '\^\^', \"`n\"; ^
         [System.IO.File]::WriteAllText('%BACKEND_DIR%requirements.txt', $content, [System.Text.Encoding]::UTF8)"
    echo   requirements.txt 已生成
) else (
    echo   requirements.txt 已存在,跳过
)

REM 生成 pytest.ini (如果不存在)
if not exist "%BACKEND_DIR%pytest.ini" (
    echo   生成 pytest.ini...
    powershell -NoProfile -Command ^
        "$lines = @('[pytest]', 'minversion = 8.0', 'testpaths = .', 'python_files = test_*.py', 'python_classes = Test*', 'python_functions = test_*', 'addopts = -v --tb=short --strict-markers --color=yes -ra', 'log_cli = true', 'log_cli_level = INFO', 'log_cli_format = %%(asctime)s [%%(levelname)s] %%(message)s', 'log_cli_date_format = %%H:%%M:%%S', 'asyncio_mode = auto', 'filterwarnings =', '    ignore::DeprecationWarning', '    ignore::PendingDeprecationWarning'; ^
         $content = $lines -join \"`n\"; ^
         [System.IO.File]::WriteAllText('%BACKEND_DIR%pytest.ini', $content, [System.Text.Encoding]::UTF8)"
    echo   pytest.ini 已生成
) else (
    echo   pytest.ini 已存在,跳过
)
echo.

REM ---------- 安装依赖 ----------
echo [5/6] 安装项目依赖...
echo.

echo   升级 pip...
!PYTHON_CMD! -m pip install --upgrade pip -q

echo   安装依赖(从 requirements.txt)...
!PYTHON_CMD! -m pip install -r "%BACKEND_DIR%requirements.txt" -q

if errorlevel 1 (
    echo   [警告] 批量安装失败,尝试逐个安装...
    !PYTHON_CMD! -m pip install fastapi uvicorn pydantic -q
    !PYTHON_CMD! -m pip install python-jose passlib -q
    !PYTHON_CMD! -m pip install pytest httpx pytest-cov pytest-asyncio -q
)

echo   依赖安装完成
echo.

REM ---------- 运行测试 ----------
echo [6/6] 运行单元测试...
echo.

cd /d "%BACKEND_DIR%"

echo   执行: pytest -v
echo   配置: pytest.ini (asyncio_mode=auto)
echo.
!PYTHON_CMD! -m pytest -v

if errorlevel 1 (
    echo.
    echo   [警告] 部分测试未通过,请检查上方输出
) else (
    echo.
    echo   全部测试通过!
)

echo.
echo ============================================
echo   环境安装与测试完成
echo   Python: !PYTHON_VERSION!
echo   命令:   !PYTHON_CMD!
echo   目录:   %BACKEND_DIR%
echo ============================================
pause
