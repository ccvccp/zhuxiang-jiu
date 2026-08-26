﻿# ============================================================
#  Python 3.12.6 安装 + 自动加入 PATH 脚本
#
#  用法: 以管理员身份打开 PowerShell,执行:
#    .\scripts\install-python.ps1
#
#  功能:
#    1. 下载 Python 3.12.6 安装包
#    2. 静默安装(自动加入 PATH)
#    3. 验证安装结果
#    4. 安装 pip 升级 + 基础依赖
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Message)
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
}

function Write-Ok { param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Err { param([string]$Message)
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
}

# ============================================================
# 步骤 1: 检查是否已安装
# ============================================================
Write-Step "步骤 1: 检查现有 Python 安装"

$existingPython = $null
$searchCommands = @("py", "python", "python3")
foreach ($cmd in $searchCommands) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        try {
            $verOutput = & $cmd --version 2>&1
            if ($verOutput -match "Python (\d+)\.(\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 11) {
                    $existingPython = $cmd
                    Write-Ok "已检测到 Python: $verOutput"
                    Write-Host "  跳过安装"
                    break
                }
            }
        } catch { }
    }
}

if ($existingPython) {
    Write-Host ""
    Write-Host "Python 已安装,无需重复安装" -ForegroundColor Green
    Write-Host "  命令: $existingPython"
    & $existingPython --version
    exit 0
}

Write-Host "  未检测到 Python 3.11+,开始安装..."

# ============================================================
# 步骤 2: 下载 Python 3.12.6
# ============================================================
Write-Step "步骤 2: 下载 Python 3.12.6 安装包"

$pythonVersion = "3.12.6"
$installerName = "python-3.12.6-amd64.exe"
$installerUrl = "https://www.python.org/ftp/python/$pythonVersion/$installerName"
$installerPath = "$env:TEMP\$installerName"

# 检查是否已下载
if (Test-Path $installerPath) {
    $fileSize = (Get-Item $installerPath).Length
    if ($fileSize -gt 20MB) {
        Write-Ok "安装包已存在: $installerPath ($([math]::Round($fileSize/1MB, 1)) MB)"
    } else {
        Write-Host "  已有文件不完整,重新下载..."
        Remove-Item $installerPath -Force
    }
}

if (-not (Test-Path $installerPath)) {
    Write-Host "  下载地址: $installerUrl"
    Write-Host "  保存到: $installerPath"
    Write-Host "  正在下载(约 25 MB),请耐心等待..."

    # 尝试使用 .NET WebClient(更稳定)
    try {
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing -TimeoutSec 300
        Write-Ok "下载完成"
    } catch {
        Write-Err "下载失败: $_"
        Write-Host ""
        Write-Host "  请手动下载:" -ForegroundColor Yellow
        Write-Host "  1. 浏览器打开: https://www.python.org/downloads/"
        Write-Host "  2. 下载 Windows installer (64-bit)"
        Write-Host "  3. 保存到: $installerPath"
        Write-Host "  4. 重新运行此脚本"
        exit 1
    }
}

# 验证下载文件
$fileSize = (Get-Item $installerPath).Length
if ($fileSize -lt 20MB) {
    Write-Err "下载文件不完整($([math]::Round($fileSize/1MB, 1)) MB),请重新下载"
    exit 1
}
Write-Ok "安装包大小: $([math]::Round($fileSize/1MB, 1)) MB"

# ============================================================
# 步骤 3: 静默安装(加入 PATH)
# ============================================================
Write-Step "步骤 3: 静默安装 Python $pythonVersion(自动加入 PATH)"

$installDir = "$env:LOCALAPPDATA\Programs\Python\Python312"
Write-Host "  安装目录: $installDir"
Write-Host "  正在静默安装(约 30 秒)..."

# 静默安装参数:
#   /quiet              - 无界面静默安装
#   InstallAllUsers=0   - 仅当前用户(无需管理员,但管理员也可以)
#   PrependPath=1       - 添加到 PATH(关键!)
#   Include_test=0      - 不安装测试套件
#   Include_doc=0       - 不安装文档(节省空间)
#   InstallDir=...      - 指定安装目录
$installArgs = @(
    "/quiet",
    "InstallAllUsers=0",
    "PrependPath=1",
    "Include_test=0",
    "Include_doc=0",
    "InstallDir=`"$installDir`""
)

Write-Host "  安装参数: $($installArgs -join ' ')"
$process = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru

if ($process.ExitCode -ne 0) {
    Write-Warn "首次安装失败(退出码 $($process.ExitCode)),尝试默认路径..."
    $installArgs = @("/quiet", "InstallAllUsers=0", "PrependPath=1")
    $process = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru

    if ($process.ExitCode -ne 0) {
        Write-Err "安装失败(退出码 $($process.ExitCode))"
        Write-Host ""
        Write-Host "  请手动运行安装包:" -ForegroundColor Yellow
        Write-Host "  $installerPath"
        Write-Host "  安装时务必勾选 'Add Python to PATH'"
        exit 1
    }
}

Write-Ok "安装完成"

# ============================================================
# 步骤 4: 刷新 PATH 并验证
# ============================================================
Write-Step "步骤 4: 刷新 PATH 并验证安装"

# 刷新当前会话的 PATH
$machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = "$machinePath;$userPath"

# 查找 Python 可执行文件
$pythonExe = $null
$searchPaths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python311\python.exe",
    "C:\Program Files\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
)

foreach ($p in $searchPaths) {
    if (Test-Path $p) {
        $pythonExe = $p
        Write-Ok "找到 Python: $p"
        break
    }
}

# 如果搜索路径没找到,尝试 PATH 中的命令
if (-not $pythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $pythonExe = $cmd.Source
        Write-Ok "通过 PATH 找到 Python: $pythonExe"
    }
}

if (-not $pythonExe) {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) {
        $pythonExe = "py -3"
        Write-Ok "通过 py launcher 找到 Python"
    }
}

if (-not $pythonExe) {
    Write-Err "安装后仍无法找到 Python"
    Write-Host ""
    Write-Host "  请手动添加到 PATH:" -ForegroundColor Yellow
    Write-Host "  1. 搜索 '环境变量' → 编辑系统环境变量"
    Write-Host "  2. 环境变量 → Path → 新建"
    Write-Host "  3. 添加: $env:LOCALAPPDATA\Programs\Python\Python312"
    Write-Host "  4. 添加: $env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
    Write-Host "  5. 重启 PowerShell"
    exit 1
}

# 验证版本
Write-Host ""
Write-Host "  --- Python 版本 ---"
& $pythonExe --version
$versionOutput = & $pythonExe --version 2>&1

if ($versionOutput -match "Python $pythonVersion") {
    Write-Ok "版本匹配: $pythonVersion"
} elseif ($versionOutput -match "Python (\d+\.\d+\.\d+)") {
    Write-Ok "版本: $($Matches[1])"
} else {
    Write-Warn "无法确认版本: $versionOutput"
}

# 验证 pip
Write-Host ""
Write-Host "  --- pip 版本 ---"
& $pythonExe -m pip --version
if ($LASTEXITCODE -eq 0) {
    Write-Ok "pip 可用"
} else {
    Write-Warn "pip 不可用,尝试重新安装..."
    & $pythonExe -m ensurepip --upgrade
    & $pythonExe -m pip --version
}

# ============================================================
# 步骤 5: 升级 pip
# ============================================================
Write-Step "步骤 5: 升级 pip"

Write-Host "  升级 pip 到最新版本..."
& $pythonExe -m pip install --upgrade pip -q
if ($LASTEXITCODE -eq 0) {
    Write-Ok "pip 已升级"
} else {
    Write-Warn "pip 升级失败,但 pip 仍可使用"
}

# ============================================================
# 总结
# ============================================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Python 安装完成" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Python 路径: $pythonExe"
Write-Host "  Python 版本: $versionOutput"
Write-Host ""

# 检查是否需要重启 PowerShell
$pathHasPython = ($env:Path -like "*Python312*") -or ($env:Path -like "*Python311*") -or ($env:Path -like "*Python313*")
if (-not $pathHasPython) {
    Write-Host "  [注意] PATH 尚未在当前会话生效" -ForegroundColor Yellow
    Write-Host "  请关闭当前 PowerShell 窗口,重新打开后执行:"
    Write-Host "    python --version"
} else {
    Write-Ok "PATH 已包含 Python"
    Write-Host ""
    Write-Host "  下一步: 运行开发环境配置脚本"
    Write-Host "    cd d:\网站架构设计"
    Write-Host "    .\scripts\setup-dev-env.ps1"
}
Write-Host ""
