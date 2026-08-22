# 竹香酒项目 - FastAPI 路由测试一键运行脚本
# 用途: 运行 9 个依赖 fastapi 的 pytest 测试文件
# 使用: 在宿主机 PowerShell 中执行  .\run-fastapi-tests.ps1

#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# ============================================================
# 1. 切换到脚本所在目录(backend)
# ============================================================
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptRoot
Write-Host "[1/5] 工作目录: $scriptRoot" -ForegroundColor Cyan

# ============================================================
# 2. 检查 Python 环境
# ============================================================
Write-Host "[2/5] 检查 Python 环境..." -ForegroundColor Cyan
try {
    $pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
    Write-Host "  Python 版本: $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "  错误: 未找到 python, 请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# ============================================================
# 3. 检查 fastapi 依赖
# ============================================================
Write-Host "[3/5] 检查 fastapi 依赖..." -ForegroundColor Cyan
$fastapiOk = $false
try {
    $fastapiVersion = python -c "import fastapi; print(fastapi.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  fastapi 版本: $fastapiVersion" -ForegroundColor Green
        $fastapiOk = $true
    }
} catch {}

if (-not $fastapiOk) {
    Write-Host "  fastapi 未安装, 尝试自动安装..." -ForegroundColor Yellow
    pip install --upgrade typing_extensions 2>&1 | Out-Null
    pip install fastapi httpx pytest pytest-asyncio 2>&1 | Out-Null

    # 再次验证
    try {
        $fastapiVersion = python -c "import fastapi; print(fastapi.__version__)" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  fastapi 安装成功: $fastapiVersion" -ForegroundColor Green
            $fastapiOk = $true
        }
    } catch {}
}

if (-not $fastapiOk) {
    Write-Host ""
    Write-Host "  错误: fastapi 安装失败!" -ForegroundColor Red
    Write-Host "  请手动执行以下命令:" -ForegroundColor Yellow
    Write-Host "    pip install --upgrade typing_extensions" -ForegroundColor White
    Write-Host "    pip install fastapi httpx pytest pytest-asyncio" -ForegroundColor White
    Write-Host ""
    Write-Host "  如果 typing_extensions 版本冲突, 请先执行:" -ForegroundColor Yellow
    Write-Host "    pip uninstall typing_extensions -y" -ForegroundColor White
    Write-Host "    pip install typing_extensions" -ForegroundColor White
    exit 1
}

# ============================================================
# 4. 设置环境变量
# ============================================================
Write-Host "[4/5] 设置环境变量..." -ForegroundColor Cyan
$env:LOCK_MODE = "asyncio"
$env:STORE_MODE = "asyncio"
$env:PYTHONPATH = "."
Write-Host "  LOCK_MODE=$env:LOCK_MODE" -ForegroundColor Green
Write-Host "  STORE_MODE=$env:STORE_MODE" -ForegroundColor Green
Write-Host "  PYTHONPATH=$env:PYTHONPATH" -ForegroundColor Green

# ============================================================
# 5. 运行 9 个 fastapi 测试文件
# ============================================================
Write-Host "[5/5] 运行 9 个 fastapi 测试文件..." -ForegroundColor Cyan
Write-Host ""

$testFiles = @(
    "test_agent_rebate_risk.py",
    "test_agent_routes.py",
    "test_business_routes.py",
    "test_decision_endpoints.py",
    "test_finance_routes.py",
    "test_member_routes.py",
    "test_order_routes.py",
    "test_product_routes.py",
    "test_risk_control.py"
)

Write-Host "测试文件列表:" -ForegroundColor White
for ($i = 0; $i -lt $testFiles.Count; $i++) {
    Write-Host "  $($i+1). $($testFiles[$i])" -ForegroundColor Gray
}
Write-Host ""

# 运行 pytest
$testArgs = $testFiles + @("--tb=short", "-q")
& python -m pytest @testArgs

$exitCode = $LASTEXITCODE

# ============================================================
# 结果汇总
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
if ($exitCode -eq 0) {
    Write-Host "  结果: 全部通过" -ForegroundColor Green
} else {
    Write-Host "  结果: 存在失败 (退出码: $exitCode)" -ForegroundColor Yellow
    Write-Host "  请查看上方输出中的 FAILED 行" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  查看详细失败信息:" -ForegroundColor White
    Write-Host "    python -m pytest <失败文件> --tb=long -v" -ForegroundColor Gray
}
Write-Host "============================================================" -ForegroundColor Cyan

exit $exitCode
