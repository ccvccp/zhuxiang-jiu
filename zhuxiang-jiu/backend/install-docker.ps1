#requires -Version 5.1
# ============================================================
#  Docker Desktop 自动检测安装 + 构建镜像 + 运行测试
#  适用于: Windows 10 (2004+) / Windows 11 (Home/Pro)
#  竹香酒官网 · AI决策筹划模块(29) 后端
#  用法: 以管理员身份运行 PowerShell,执行:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install-docker.ps1
# ============================================================

param(
    [string]$BackendDir = "",
    [string]$ImageName = "zhuxiang-decision-api",
    [string]$ImageTag = "1.0",
    [switch]$SkipRestartCheck
)

$ErrorActionPreference = "Stop"

# ---------- 颜色输出 ----------
function Write-Info  { param($msg) Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  $msg" -ForegroundColor Red }

# 自动定位 backend 目录
if (-not $BackendDir) {
    $BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not $BackendDir) { $BackendDir = $PSScriptRoot }
    if (-not $BackendDir) { $BackendDir = (Get-Location).Path }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  Docker Desktop 检测安装 + 构建镜像 + 测试" -ForegroundColor White
Write-Host "  AI决策筹划模块(29) 后端" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""

# ============================================================
# [0/7] 环境预检 (Win10/Win11 兼容性修复)
# ============================================================
Write-Host "[0/7] 环境预检..." -ForegroundColor Blue
Write-Host ""

# --- 检查管理员权限 ---
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$currentUser).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "请以管理员身份运行此脚本"
    Write-Err "右键 PowerShell -> 以管理员身份运行"
    exit 1
}
Write-Ok "管理员权限: 已确认"

# --- 检查 Windows 版本和 Build 号 ---
$osInfo = Get-CimInstance Win32_OperatingSystem
$osCaption = $osInfo.Caption
$osBuild = [int]$osInfo.BuildNumber

# 判断 Windows 10 vs 11
$isWin11 = $false
if ($osBuild -ge 22000) {
    $isWin11 = $true
    $winName = "Windows 11"
} elseif ($osBuild -ge 19041) {
    $winName = "Windows 10"
} else {
    Write-Err "Windows 版本过低 (Build $osBuild)"
    Write-Err "Docker Desktop 需要 Windows 10 2004 (Build 19041) 或 Windows 11"
    Write-Err "当前: $osCaption (Build $osBuild)"
    Write-Err "请通过 Windows Update 升级系统后重试"
    exit 1
}
Write-Ok "系统版本: $osCaption (Build $osBuild)"

# --- 检查 Windows 版本 (Home/Pro) ---
$isHome = $osCaption -match "Home"
if ($isHome) {
    Write-Warn "Windows Home 版本: 不支持 Hyper-V,将使用 WSL2 后端"
} else {
    Write-Ok "Windows Pro/Enterprise: 支持 Hyper-V 和 WSL2 双后端"
}

# --- 检查 BIOS 虚拟化 (VT-x/SVM) ---
$vmPlatform = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent
$cpuInfo = Get-CimInstance Win32_Processor
$vtSupported = $false
if ($cpuInfo.VirtualizationFirmwareEnabled -or $cpuInfo.VMMonitorModeExtensions) {
    $vtSupported = $true
}
# 备用检测: systeminfo
if (-not $vtSupported) {
    $sysInfo = systeminfo 2>&1 | Out-String
    if ($sysInfo -match "虚拟化已启用|Virtualization Enabled.*Yes|Hyper-V Requirements.*Yes") {
        $vtSupported = $true
    }
}
if (-not $vtSupported) {
    Write-Err "BIOS 虚拟化未启用 (VT-x/SVM)"
    Write-Err "请重启进入 BIOS,启用 Intel VT-x 或 AMD SVM"
    Write-Err "  - Intel: 启用 'Intel Virtualization Technology'"
    Write-Err "  - AMD: 启用 'SVM Mode'"
    exit 1
}
Write-Ok "BIOS 虚拟化: 已启用"

# --- 检查 WSL2 状态 (Win10 需手动启用,Win11 默认已有) ---
$wsl2Ready = $false
$wslCmd = Get-Command wsl -ErrorAction SilentlyContinue
if ($wslCmd) {
    $wslStatus = wsl --status 2>&1 | Out-String
    if ($wslStatus -match "2|WSL2|default") {
        $wsl2Ready = $true
    }
} else {
    # wsl 命令不存在,检查功能是否已安装
    $vmPlatformFeature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue
    $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    if ($vmPlatformFeature.InstallState -eq "Enabled" -and $wslFeature.InstallState -eq "Enabled") {
        $wsl2Ready = $true
    }
}

if ($wsl2Ready) {
    Write-Ok "WSL2: 已就绪"
} else {
    if ($isWin11) {
        Write-Warn "WSL2 未检测到,正在安装 (Windows 11)..."
        wsl --install --no-distribution 2>&1 | Out-Null
        Write-Ok "WSL2 已安装,需要重启系统生效"
        $needRestart = $true
    } else {
        Write-Warn "WSL2 未启用,正在配置 (Windows 10)..."
        # 启用 WSL 功能
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -ErrorAction SilentlyContinue | Out-Null
        # 启用虚拟机平台
        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -ErrorAction SilentlyContinue | Out-Null
        Write-Ok "WSL2 功能已启用,需要重启系统生效"
        $needRestart = $true
    }
}
Write-Host ""

# ============================================================
# [1/7] 检测已安装的 Docker
# ============================================================
Write-Host "[1/7] 检测 Docker Desktop..." -ForegroundColor Blue
Write-Host ""

$dockerInstalled = $false
$dockerCmd = $null

$dockerPath = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerPath) {
    $dockerCmd = "docker"
    $dockerVer = & docker --version 2>&1
    Write-Ok "Docker 已安装: $dockerVer"
    $dockerInstalled = $true
} else {
    $dockerExePaths = @(
        "${env:ProgramFiles}\Docker\Docker\resources\bin\docker.exe",
        "${env:ProgramFiles}\Docker\Docker\resources\bin\docker.cmd",
        "${env:LOCALAPPDATA}\Docker\resources\bin\docker.exe"
    )
    foreach ($p in $dockerExePaths) {
        if (Test-Path $p) {
            $dockerCmd = $p
            $dockerVer = & $p --version 2>&1
            Write-Ok "Docker 已安装: $dockerVer"
            $dockerInstalled = $true
            break
        }
    }
}

if ($dockerInstalled) {
    Write-Host ""
    Write-Host "  检测 Docker Engine 运行状态..."
    $engineOk = $false
    for ($i = 0; $i -lt 5; $i++) {
        $info = & $dockerCmd info --format "{{.ServerVersion}}" 2>&1
        if ($info -and $info -notmatch "error|Error|not|cannot") {
            Write-Ok "Docker Engine 运行中 (Server $info)"
            $engineOk = $true
            break
        }
        if ($i -lt 4) {
            Write-Warn "Engine 未就绪,等待 5 秒后重试 ($($i+1)/5)..."
            Start-Sleep -Seconds 5
        }
    }
    if (-not $engineOk) {
        Write-Warn "Docker Engine 未运行,尝试启动 Docker Desktop..."
        $dockerDesktopPath = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
        if (Test-Path $dockerDesktopPath) {
            Start-Process $dockerDesktopPath
            Write-Info "等待 Docker Desktop 启动..."
            for ($i = 0; $i -lt 24; $i++) {
                Start-Sleep -Seconds 5
                $info = & $dockerCmd info --format "{{.ServerVersion}}" 2>&1
                if ($info -and $info -notmatch "error|Error|not|cannot") {
                    Write-Ok "Docker Engine 已就绪 (Server $info)"
                    $engineOk = $true
                    break
                }
                Write-Info "  等待中... ($($i+1)/24)"
            }
        }
        if (-not $engineOk) {
            Write-Err "Docker Engine 启动失败"
            Write-Err "请手动启动 Docker Desktop 后重新运行此脚本"
            exit 1
        }
    }
    Write-Host ""
} else {
    Write-Warn "未检测到 Docker Desktop"
    Write-Host ""

    # ============================================================
    # [2/7] 下载 Docker Desktop
    # ============================================================
    Write-Host "[2/7] 下载 Docker Desktop 安装包..." -ForegroundColor Blue
    Write-Host ""

    $dockerInstallerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
    $installerPath = "$env:TEMP\DockerDesktopInstaller.exe"

    if (Test-Path $installerPath) {
        Write-Info "安装包已存在: $installerPath"
    } else {
        Write-Info "正在下载 Docker Desktop..."
        Write-Info "URL: $dockerInstallerUrl"
        Write-Host ""

        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        try {
            Invoke-WebRequest -Uri $dockerInstallerUrl -OutFile $installerPath -UseBasicParsing
            Write-Ok "下载完成 ($(Get-Item $installerPath | Select-Object -ExpandProperty Length) bytes)"
        } catch {
            Write-Err "下载失败: $($_.Exception.Message)"
            Write-Err "请手动从 https://www.docker.com/products/docker-desktop/ 下载安装"
            exit 1
        }
    }
    Write-Host ""

    # ============================================================
    # [3/7] 安装 Docker Desktop
    # ============================================================
    Write-Host "[3/7] 安装 Docker Desktop..." -ForegroundColor Blue
    Write-Host ""

    # Win10 Home 需要指定 WSL2 后端
    $installArgs = @("install","--quiet","--accept-license")
    if ($isHome) {
        $installArgs += "--backend=wsl-2"
    }
    Write-Info "静默安装中(需要 1-3 分钟)..."
    Write-Info "安装参数: $($installArgs -join ' ')"
    Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -NoNewWindow

    # 刷新 PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

    # 检测安装结果
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        $dockerExe = "${env:ProgramFiles}\Docker\Docker\resources\bin\docker.exe"
        if (Test-Path $dockerExe) {
            $dockerCmd = $dockerExe
        } else {
            Write-Err "Docker 安装后未找到 docker 命令"
            Write-Err "请重启系统后重新运行此脚本"
            exit 1
        }
    }

    $dockerVer = & $dockerCmd --version 2>&1
    Write-Ok "安装完成: $dockerVer"
    Write-Host ""

    # ============================================================
    # [4/7] 检测系统重启需求
    # ============================================================
    Write-Host "[4/7] 检测系统重启需求..." -ForegroundColor Blue
    Write-Host ""

    # 检查挂起重启 (WSL2 功能启用后需要重启)
    $pendingRebootKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
        "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\PendingFileRenameOperations"
    )
    $rebootPending = $false
    foreach ($key in $pendingRebootKeys) {
        if (Test-Path $key) { $rebootPending = $true; break }
    }

    # 检查 WSL2 是否需要重启
    if ($needRestart) { $rebootPending = $true }

    if ($rebootPending -and -not $SkipRestartCheck) {
        Write-Warn "系统需要重启以完成 WSL2/Docker 配置"
        Write-Host ""
        Write-Host "  重启后请重新运行此脚本,将自动跳过安装步骤:" -ForegroundColor White
        Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  或使用 -SkipRestartCheck 跳过检查(不推荐):" -ForegroundColor DarkGray
        Write-Host "    -SkipRestartCheck" -ForegroundColor DarkGray
        Write-Host ""

        $choice = Read-Host "是否立即重启? (Y/N)"
        if ($choice -eq 'Y' -or $choice -eq 'y') {
            Write-Ok "10 秒后重启系统..."
            Start-Sleep -Seconds 10
            Restart-Computer -Force
        } else {
            Write-Warn "请稍后手动重启,重启后重新运行此脚本"
            exit 2
        }
    } else {
        Write-Ok "无需重启,继续配置"
    }
    Write-Host ""

    # 启动 Docker Desktop
    Write-Info "启动 Docker Desktop..."
    $dockerDesktopPath = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktopPath) {
        Start-Process $dockerDesktopPath
    }

    Write-Host "  等待 Docker Engine 启动(首次启动可能需要 30-60 秒)..."
    $engineOk = $false
    for ($i = 0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        $info = & $dockerCmd info --format "{{.ServerVersion}}" 2>&1
        if ($info -and $info -notmatch "error|Error|not|cannot") {
            Write-Ok "Docker Engine 已就绪 (Server $info)"
            $engineOk = $true
            break
        }
        if ($i % 4 -eq 3) {
            Write-Info "  等待中... ($($i+1)/36)"
        }
    }

    if (-not $engineOk) {
        Write-Err "Docker Engine 启动超时(3 分钟)"
        Write-Host ""
        Write-Err "可能原因:"
        Write-Err "  1. WSL2 未正确安装 -> 运行: wsl --install"
        Write-Err "  2. BIOS 虚拟化未启用 -> 重启进 BIOS 开启 VT-x/SVM"
        Write-Err "  3. 系统需要重启 -> 重启后重新运行此脚本"
        Write-Err "  4. Docker Desktop 首次启动慢 -> 手动启动 Docker Desktop,等待托盘图标稳定"
        Write-Host ""
        Write-Err "手动构建测试命令:"
        Write-Err "  $dockerCmd build -t ${ImageName}:${ImageTag} `"$BackendDir`""
        Write-Err "  $dockerCmd run --rm ${ImageName}:${ImageTag} pytest -v"
        exit 1
    }
    Write-Host ""

    if ($dockerCmd -isnot [string]) { $dockerCmd = "docker" }
}

# ============================================================
# [5/7] 构建镜像
# ============================================================
Write-Host "[5/7] 构建 Docker 镜像..." -ForegroundColor Blue
Write-Host ""

Write-Info "项目目录: $BackendDir"
Write-Info "镜像名称: ${ImageName}:${ImageTag}"
Write-Host ""

if (-not (Test-Path "$BackendDir\Dockerfile")) {
    Write-Err "Dockerfile 不存在: $BackendDir\Dockerfile"
    exit 1
}

$requiredFiles = @("Dockerfile", "requirements.txt", "main.py", "models.py", "test_risk_control.py", "pytest.ini")
foreach ($f in $requiredFiles) {
    if (Test-Path "$BackendDir\$f") {
        Write-Ok "  [OK] $f"
    } else {
        Write-Warn "  [missing] $f"
    }
}
Write-Host ""

Write-Info "开始构建..."
$imageFullName = "${ImageName}:${ImageTag}"

Push-Location $BackendDir
try {
    $buildOutput = & $dockerCmd build -t $imageFullName . 2>&1
    $buildExit = $LASTEXITCODE

    $buildLines = $buildOutput -split "`n"
    $startLine = [Math]::Max(0, $buildLines.Count - 20)
    for ($i = $startLine; $i -lt $buildLines.Count; $i++) {
        Write-Host "  $($buildLines[$i])" -ForegroundColor DarkGray
    }

    if ($buildExit -ne 0) {
        Write-Err "镜像构建失败 (exit code: $buildExit)"
        exit 1
    }
    Write-Ok "镜像构建成功: $imageFullName"
} finally {
    Pop-Location
}
Write-Host ""

# ============================================================
# [6/7] 运行测试
# ============================================================
Write-Host "[6/7] 运行容器内测试..." -ForegroundColor Blue
Write-Host ""

Write-Info "执行: docker run --rm $imageFullName pytest -v"
Write-Host ""

& $dockerCmd run --rm $imageFullName pytest -v
$testExit = $LASTEXITCODE

Write-Host ""
if ($testExit -eq 0) {
    Write-Ok "=========================================="
    Write-Ok "  All tests passed!"
    Write-Ok "=========================================="
} else {
    Write-Warn "=========================================="
    Write-Warn "  Some tests failed (exit code: $testExit)"
    Write-Warn "  Check output above"
    Write-Warn "=========================================="
}

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  Complete!" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""
Write-Host "  Image: $imageFullName" -ForegroundColor Cyan
Write-Host "  Start: $dockerCmd run --rm -p 8000:8000 $imageFullName" -ForegroundColor Cyan
Write-Host "  Swagger: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
