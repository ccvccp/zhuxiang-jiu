﻿# ============================================================
#  开发环境一键配置脚本 - 竹香酒官网 · AI决策筹划模块(29)
#
#  功能:
#    1. 设置 PowerShell 执行策略(当前用户)
#    2. 检测/安装 Python 3.12+(静默安装)
#    3. 升级 pip + 安装项目依赖(生产 + 测试)
#    4. 安装 Redis 测试依赖(fakeredis + lupa)
#    5. 验证环境配置(python/pip/pytest)
#    6. 运行快速测试确认环境可用
#
#  用法:
#    以管理员身份打开 PowerShell,执行:
#    .\scripts\setup-dev-env.ps1
#
#  说明:
#    - 脚本幂等,可重复运行
#    - 默认安装到 %LOCALAPPDATA%\Programs\Python\Python312
#    - 测试依赖安装到 .deps 目录(模拟 Docker 镜像结构)
# ============================================================

# 强制 UTF-8 输出(避免中文乱码)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

# 项目路径
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "zhuxiang-jiu\backend"

function Write-Step { param([int]$Step, [int]$Total, [string]$Message)
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  [$Step/$Total] $Message" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
}

function Write-Ok { param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn { param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
}

function Write-Err { param([string]$Message)
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
}

$TOTAL_STEPS = 6

# ============================================================
# 步骤 1: 设置 PowerShell 执行策略
# ============================================================
Write-Step 1 $TOTAL_STEPS "设置 PowerShell 执行策略"

$currentPolicy = Get-ExecutionPolicy -Scope CurrentUser
Write-Host "  当前执行策略: $currentPolicy"

if ($currentPolicy -ne "RemoteSigned" -and $currentPolicy -ne "Unrestricted" -and $currentPolicy -ne "Bypass") {
    Write-Host "  设置为 RemoteSigned(允许本地脚本运行)..."
    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
    Write-Ok "执行策略已设置为 RemoteSigned"
} else {
    Write-Ok "执行策略已满足要求($currentPolicy)"
}

# ============================================================
# 步骤 2: 检测/安装 Python 3.12+
# ============================================================
Write-Step 2 $TOTAL_STEPS "检测/安装 Python 3.12+"

$PythonCmd = $null
$PythonVersion = $null

# 尝试 py launcher
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    try {
        $verOutput = & py -3 --version 2>&1
        if ($verOutput -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                # 通过 py -3 -c 获取实际 python.exe 路径,避免 "py -3" 字符串调用问题
                $pythonExePath = (& py -3 -c "import sys; print(sys.executable)" 2>&1).ToString().Trim()
                if (Test-Path $pythonExePath) {
                    $PythonCmd = $pythonExePath
                    $PythonVersion = $verOutput.ToString().Trim()
                    Write-Ok "检测到 Python: $PythonVersion ($pythonExePath)"
                }
            }
        }
    } catch { }
}

# 尝试 python 命令
if (-not $PythonCmd) {
    $pythonExe = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonExe) {
        try {
            $verOutput = & python --version 2>&1
            if ($verOutput -match "Python (\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 11) {
                    $PythonCmd = "python"
                    $PythonVersion = $verOutput.ToString().Trim()
                    Write-Ok "检测到 Python: $PythonVersion"
                }
            }
        } catch { }
    }
}

# 检查常见安装路径
if (-not $PythonCmd) {
    $searchPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($p in $searchPaths) {
        if (Test-Path $p) {
            try {
                $verOutput = & $p --version 2>&1
                if ($verOutput -match "Python (\d+)\.(\d+)") {
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    if ($major -ge 3 -and $minor -ge 11) {
                        $PythonCmd = $p
                        $PythonVersion = $verOutput.ToString().Trim()
                        Write-Ok "检测到 Python: $PythonVersion ($p)"
                        break
                    }
                }
            } catch { }
        }
    }
}

# 安装 Python 3.12
if (-not $PythonCmd) {
    Write-Host "  未检测到 Python 3.11+,开始安装 Python 3.12.6..."
    $installerUrl = "https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe"
    $installerPath = "$env:TEMP\python-3.12.6-amd64.exe"
    $installDir = "$env:LOCALAPPDATA\Programs\Python\Python312"

    Write-Host "  下载: $installerUrl"
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing

    if (-not (Test-Path $installerPath)) {
        Write-Err "下载失败"
        Write-Host "  请手动安装: https://www.python.org/downloads/"
        exit 1
    }
    Write-Ok "下载完成"

    Write-Host "  静默安装到: $installDir"
    & $installerPath /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 InstallDir="$installDir"

    if ($LASTEXITCODE -ne 0) {
        Write-Warn "首次安装失败,尝试默认路径..."
        & $installerPath /quiet InstallAllUsers=0 PrependPath=1
    }

    # 刷新 PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

    Start-Sleep -Seconds 3

    $pythonExePath = Join-Path $installDir "python.exe"
    if (Test-Path $pythonExePath) {
        $PythonCmd = $pythonExePath
    } else {
        $PythonCmd = "python"
    }

    $verOutput = & $PythonCmd --version 2>&1
    if ($verOutput -match "Python") {
        $PythonVersion = $verOutput.ToString().Trim()
        Write-Ok "安装成功: $PythonVersion"
    } else {
        Write-Err "安装后仍无法调用 Python"
        Write-Host "  请手动运行安装包: $installerPath"
        Write-Host "  安装时务必勾选 'Add Python to PATH'"
        exit 1
    }
}

Write-Host ""
Write-Host "  Python 命令: $PythonCmd"
Write-Host "  Python 版本: $PythonVersion"

# ============================================================
# 步骤 3: 升级 pip + 安装生产依赖
# ============================================================
Write-Step 3 $TOTAL_STEPS "升级 pip + 安装项目依赖"

Write-Host "  升级 pip..."
& $PythonCmd -m pip install --upgrade pip -q
Write-Ok "pip 已升级"

$reqFile = Join-Path $BackendDir "requirements.txt"
if (Test-Path $reqFile) {
    Write-Host "  安装生产依赖(requirements.txt)..."
    & $PythonCmd -m pip install -r $reqFile -q
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "生产依赖安装完成"
    } else {
        Write-Warn "部分依赖安装失败,尝试逐个安装..."
        & $PythonCmd -m pip install "fastapi==0.115.0" -q
        & $PythonCmd -m pip install "uvicorn[standard]==0.32.0" -q
        & $PythonCmd -m pip install "pydantic==2.9.0" -q
        & $PythonCmd -m pip install "python-jose[cryptography]==3.3.0" -q
        & $PythonCmd -m pip install "passlib[bcrypt]==1.7.4" -q
        & $PythonCmd -m pip install "redis>=5.0.0" -q
        Write-Ok "生产依赖安装完成(逐个)"
    }
} else {
    Write-Err "未找到 requirements.txt: $reqFile"
    exit 1
}

# ============================================================
# 步骤 4: 安装测试依赖 + Redis 测试工具
# ============================================================
Write-Step 4 $TOTAL_STEPS "安装测试依赖 + Redis 测试工具"

$reqDevFile = Join-Path $BackendDir "requirements-dev.txt"
if (Test-Path $reqDevFile) {
    Write-Host "  安装测试依赖(requirements-dev.txt)..."
    & $PythonCmd -m pip install -r $reqDevFile -q
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "测试依赖安装完成"
    } else {
        Write-Warn "部分依赖失败,逐个安装..."
        & $PythonCmd -m pip install "pytest==8.3.0" -q
        & $PythonCmd -m pip install "httpx==0.27.0" -q
        & $PythonCmd -m pip install "pytest-cov==5.0.0" -q
        & $PythonCmd -m pip install "pytest-asyncio==0.24.0" -q
        Write-Ok "测试依赖安装完成(逐个)"
    }
}

Write-Host ""
Write-Host "  安装 Redis 测试工具(fakeredis + lupa)..."
& $PythonCmd -m pip install fakeredis lupa -q
if ($LASTEXITCODE -eq 0) {
    Write-Ok "fakeredis + lupa 安装完成"
} else {
    Write-Warn "lupa 安装失败(C 扩展),Redis 锁测试将跳过 Lua 脚本部分"
    & $PythonCmd -m pip install fakeredis -q
    Write-Ok "fakeredis 安装完成"
}

# ============================================================
# 步骤 5: 验证环境配置
# ============================================================
Write-Step 5 $TOTAL_STEPS "验证环境配置"

Write-Host ""
Write-Host "  --- Python 环境 ---"
& $PythonCmd --version
Write-Host ""

Write-Host "  --- pip 版本 ---"
& $PythonCmd -m pip --version
Write-Host ""

Write-Host "  --- 关键依赖验证 ---"
$packages = @(
    @{Name="fastapi"; Import="fastapi"},
    @{Name="uvicorn"; Import="uvicorn"},
    @{Name="pydantic"; Import="pydantic"},
    @{Name="redis"; Import="redis"},
    @{Name="pytest"; Import="pytest"},
    @{Name="httpx"; Import="httpx"},
    @{Name="pytest_asyncio"; Import="pytest_asyncio"},
    @{Name="fakeredis"; Import="fakeredis"},
    @{Name="lupa"; Import="lupa"}
)

$allOk = $true
foreach ($pkg in $packages) {
    $importCheck = & $PythonCmd -c "import $($pkg.Import); print('OK')" 2>&1
    if ($importCheck -match "OK") {
        Write-Ok "$($pkg.Name) 可用"
    } else {
        Write-Warn "$($pkg.Name) 不可用: $importCheck"
        $allOk = $false
    }
}

if (-not $allOk) {
    Write-Host ""
    Write-Warn "部分依赖不可用,但核心功能仍可运行"
}

# ============================================================
# 步骤 6: 运行快速测试确认环境可用
# ============================================================
Write-Step 6 $TOTAL_STEPS "运行快速测试确认环境可用"

Write-Host "  切换到后端目录: $BackendDir"
Set-Location $BackendDir

Write-Host ""
Write-Host "  运行内存模式测试(无需 Redis)..."
Write-Host "  命令: python -m pytest test_business_routes.py -v --tb=short -q"
Write-Host ""

# 设置环境变量(内存模式)
$env:LOCK_MODE = "asyncio"
$env:STORE_MODE = "asyncio"

& $PythonCmd -m pytest test_business_routes.py -v --tb=short -q 2>&1 | Select-Object -Last 30

Write-Host ""
if ($LASTEXITCODE -eq 0) {
    Write-Ok "环境配置完成!测试全部通过"
} else {
    Write-Warn "部分测试未通过,请检查上方输出"
}

# ============================================================
# 总结
# ============================================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  开发环境配置完成" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Python:   $PythonVersion"
Write-Host "  命令:     $PythonCmd"
Write-Host "  后端目录: $BackendDir"
Write-Host ""
Write-Host "  常用命令:"
Write-Host "    cd $BackendDir"
Write-Host "    $PythonCmd -m pytest -v                        # 运行所有测试"
Write-Host "    $PythonCmd -m pytest test_business_routes.py -v # 运行业务路由测试"
Write-Host "    $PythonCmd -m pytest test_redis_integration.py -p fakeredis_plugin -v  # Redis 集成测试"
Write-Host "    $PythonCmd -m pytest --cov=main --cov=models --cov-report=term-missing  # 覆盖率报告"
Write-Host ""
Write-Host "  启动开发服务器:"
Write-Host "    cd $BackendDir"
Write-Host "    $PythonCmd -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"
Write-Host ""
