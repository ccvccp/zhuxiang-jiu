# 后端迁移 Phase 2 v3: Python + 代码 + 依赖 + 启动测试
# v2 教训: ①残留进程锁 python-installer.exe 导致写文件失败(改新文件名)
#          ②下载进度流塞爆云助手24KB输出窗($ProgressPreference=SilentlyContinue)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/backend-migrate'

function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/6] 安装 Python 3.12'
# 判定标准用 Lib\os.py(标准库 landmark): python.exe 存在不代表装全(v2 损坏后 bundle 只补了 core+exe)
$pyExe = 'C:\Python312\python.exe'
$pyReady = Test-Path 'C:\Python312\Lib\os.py'
if (-not $pyReady) {
    $installer = 'C:\tmp8147\python-3.12.7.exe'
    if (-not (Test-Path $installer)) {
        Info '下载华为云镜像...'
        Invoke-WebRequest 'https://mirrors.huaweicloud.com/python/3.12.7/python-3.12.7-amd64.exe' -OutFile $installer -UseBasicParsing
        Info ("下载完成 " + [math]::Round((Get-Item $installer).Length/1MB,1) + " MB")
    }
    # 半成品状态先彻底卸载(bundle 状态损坏会让重装跳过 lib/pip 组件)
    if (Test-Path $pyExe) {
        Info '检测到半成品安装, 卸载重建...'
        $u = Start-Process $installer -ArgumentList '/uninstall','/quiet' -PassThru -Wait
        Info ("卸载退出码: " + $u.ExitCode)
        Start-Sleep -Seconds 3
    }
    cmd /c "taskkill /f /im python-installer.exe 2>nul"
    Info '静默安装...'
    $p = Start-Process $installer -ArgumentList '/quiet','InstallAllUsers=1','TargetDir=C:\Python312','PrependPath=1','Include_pip=1','/log','C:\tmp8147\py-install.log' -PassThru -Wait
    Info ("安装器退出码: " + $p.ExitCode)
    Start-Sleep -Seconds 2
    if (-not (Test-Path 'C:\Python312\Lib\os.py')) {
        Info ('!! os.py 仍缺失. python.exe:' + (Test-Path $pyExe) + ' Lib:' + (Test-Path C:\Python312\Lib))
        Get-Content C:\tmp8147\py-install.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
        exit 1
    }
}
Info ("Python: " + (& $pyExe -c "import sys; print(sys.version.split()[0] + ' prefix=' + sys.prefix)"))

Step '[2/6] 下载后端代码(128MB)'
if (-not (Test-Path C:\backend\main.py)) {
    $tgz = 'C:\tmp8147\backend-code-v3.tgz'
    Remove-Item $tgz -Force -ErrorAction SilentlyContinue
    Info '下载中...'
    Invoke-WebRequest "$OSS/backend-code.tgz" -OutFile $tgz -UseBasicParsing
    Info ("下载完成 " + [math]::Round((Get-Item $tgz).Length/1MB,1) + " MB")
    New-Item -ItemType Directory -Force C:\backend | Out-Null
    cmd /c "tar -xzf `"$tgz`" -C C:\backend 2>nul"
    if (-not (Test-Path C:\backend\main.py)) { Info '!! 解压后 main.py 不存在'; exit 1 }
}
$count = (Get-ChildItem C:\backend -Recurse -Filter *.py | Measure-Object).Count
Info ("py 文件: " + $count + " 个")

Step '[3/6] 下载 .env + Redis RDB'
Invoke-WebRequest "$OSS/env.production" -OutFile C:\backend\.env -UseBasicParsing
Invoke-WebRequest "$OSS/redis-dump.rdb" -OutFile C:\tmp8147\redis-dump.rdb -UseBasicParsing
Info ("env: " + (Get-Item C:\backend\.env).Length + "B  rdb: " + [math]::Round((Get-Item C:\tmp8147\redis-dump.rdb).Length/1MB,1) + "MB")

Step '[4/6] pip 安装依赖(清华源)'
# 雷: C:\backend(海外打包)内含 pyvenv.cfg, python 以 CWD 误判 venv home -> No module 'encodings'
if (Test-Path C:\backend\pyvenv.cfg) {
    Info '发现 pyvenv.cfg(海外 venv 残留), 删除'
    Remove-Item C:\backend\pyvenv.cfg -Force
}
$env:PYTHONUTF8 = '1'
$reqBytes = [IO.File]::ReadAllBytes('C:\backend\requirements.txt')
if ($reqBytes[0] -ne 0xEF) {
    $reqText = [Text.Encoding]::UTF8.GetString($reqBytes)
    [IO.File]::WriteAllText('C:\backend\requirements.txt', $reqText, [Text.UTF8Encoding]::new($true))
    Info 'requirements.txt 已补 UTF-8 BOM'
}
$env:PYTHONUTF8 = '1'
# 阿里镜像缺 pydantic-2.9.0 whl(404), 换清华源
$env:PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$env:PIP_TRUSTED_HOST = 'pypi.tuna.tsinghua.edu.cn'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
# PS5 stderr 雷: pip stderr 经 2>&1 变 ErrorRecord 会触发 EAP=Stop, 用 cmd 内吞并落日志; CWD 用 C:\ 防误判
cmd /c "cd /d C:\ && C:\Python312\python.exe -m pip install -r C:\backend\requirements.txt --no-warn-script-location --progress-bar off > C:\tmp8147\pip-install.log 2>&1"
$pipRc = $LASTEXITCODE
Info ("pip 退出码: " + $pipRc)
if ($pipRc -ne 0) {
    Info '!! pip 失败, 日志尾部:'
    Get-Content C:\tmp8147\pip-install.log -Tail 30
    exit 1
}
$pkgs = & $pyExe -m pip list | Select-String 'fastapi|uvicorn|^redis|httpx|Pillow'
Info ("核心包: " + ($pkgs -join ' | '))
Info ("C:\backend 根: " + ((Get-ChildItem C:\backend | Select-Object -First 15 -ExpandProperty Name) -join ', '))

Step '[5/6] 修改 .env (asyncio 模式)'
$envContent = Get-Content C:\backend\.env -Raw
$envContent = $envContent -replace '(?m)^#?\s*STORE_MODE=.*', 'STORE_MODE=asyncio'
$envContent = $envContent -replace '(?m)^#?\s*LOCK_MODE=.*', 'LOCK_MODE=asyncio'
Set-Content C:\backend\.env -Value $envContent -Encoding UTF8
Info 'STORE_MODE/LOCK_MODE=asyncio 已写入'

Step '[6/6] 后端启动测试'
cmd /c "taskkill /f /im python.exe 2>nul"
Start-Sleep -Seconds 1
$proc = Start-Process $pyExe -ArgumentList '-m','uvicorn','main:app','--host','0.0.0.0','--port','8000' -PassThru -WindowStyle Hidden -RedirectStandardOutput C:\tmp8147\backend-out.log -RedirectStandardError C:\tmp8147\backend-err.log -WorkingDirectory C:\backend
Start-Sleep -Seconds 10
$alive = -not $proc.HasExited
Info ("进程存活: " + $alive + " (PID " + $proc.Id + ")")
if ($alive) {
    $resp = curl.exe -s -o NUL -w '%{http_code}' -m 5 http://127.0.0.1:8000/api/promo/plans
    Info ("API 测试: " + $resp)
    if ($resp -eq '401') {
        Info 'OK 后端启动成功 (401=正确鉴权语义)'
        Stop-Process $proc -Force
        Write-Host 'ALL-STEPS-DONE'
        exit 0
    } else {
        Info '端口响应异常, stderr 尾部:'
        Get-Content C:\tmp8147\backend-err.log -Tail 15 -ErrorAction SilentlyContinue
        Stop-Process $proc -Force -ErrorAction SilentlyContinue
        exit 1
    }
} else {
    Info '启动失败, stderr 尾部:'
    Get-Content C:\tmp8147\backend-err.log -Tail 20 -ErrorAction SilentlyContinue
    Get-Content C:\tmp8147\backend-out.log -Tail 10 -ErrorAction SilentlyContinue
    exit 1
}
