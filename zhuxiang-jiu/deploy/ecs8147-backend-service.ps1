# Phase 3: 后端 nssm 常驻服务 + nginx /api 切本地后端(可回滚)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/5] nssm 注册后端服务'
$svc = Get-Service backend8147 -ErrorAction SilentlyContinue
if ($svc) {
    cmd /c "C:\nginx\nssm.exe stop backend8147"
    cmd /c "C:\nginx\nssm.exe remove backend8147 confirm"
}
cmd /c "C:\nginx\nssm.exe install backend8147 C:\Python312\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
cmd /c "C:\nginx\nssm.exe set backend8147 AppDirectory C:\backend"
cmd /c "C:\nginx\nssm.exe set backend8147 AppEnvironmentExtra PYTHONUTF8=1"
cmd /c "C:\nginx\nssm.exe set backend8147 AppStdout C:\backend\logs\service-out.log"
cmd /c "C:\nginx\nssm.exe set backend8147 AppStderr C:\backend\logs\service-err.log"
New-Item -ItemType Directory -Force C:\backend\logs | Out-Null
cmd /c "C:\nginx\nssm.exe set backend8147 AppRotateFiles 1"
cmd /c "C:\nginx\nssm.exe start backend8147"
Start-Sleep -Seconds 10
$svcState = (Get-Service backend8147 -ErrorAction SilentlyContinue).Status
Info ("服务状态: " + $svcState)
$code = curl.exe -s -o NUL -w '%{http_code}' -m 5 http://127.0.0.1:8000/api/health
Info ("本地 /api/health: " + $code)
if ($code -ne '200') {
    Info '!! 后端服务未就绪, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[2/5] 检查 nginx 当前反代配置'
$confPath = 'C:\nginx\conf\site-proxy.conf'
$conf = Get-Content $confPath -Raw
Info ("conf 大小: " + $conf.Length + "B")
$proxyLines = ($conf -split "`n" | Select-String 'proxy_pass|server_name|listen|upstream')
$proxyLines | ForEach-Object { Info $_.ToString().Trim() }

Step '[3/5] 切换 /api 反代到本地'
Copy-Item $confPath "$confPath.bak-overseas" -Force
Info '已备份 .bak-overseas'
$newConf = $conf -replace '(?m)^(.*proxy_pass\s+)https?://[^;]*(47\.236[^;]*)?;(.*)$', '$1http://127.0.0.1:8000;$3'
if ($newConf -eq $conf) {
    # 宽松兜底: 所有 proxy_pass 指到非本地的都换
    $newConf = $conf -replace '(?m)^(.*proxy_pass\s+)https?://(?!127\.0\.0\.1)[^;]+;(.*)$', '$1http://127.0.0.1:8000;$2'
}
# PS5 Set-Content UTF8 带 BOM, nginx 不认(BOM=锘?), 用无 BOM 写入
[IO.File]::WriteAllText($confPath, $newConf, [Text.UTF8Encoding]::new($false))
Info '已写入新配置(无BOM)'

Step '[4/5] nginx -t 校验并平滑重载'
$test = cmd /c "cd /d C:\nginx && nginx.exe -t 2>&1"
Info ("nginx -t: " + ($test -join ' '))
if ($LASTEXITCODE -ne 0) {
    Info '!! nginx -t 失败, 回滚配置'
    Copy-Item "$confPath.bak-overseas" $confPath -Force
    cmd /c "cd /d C:\nginx && nginx.exe -s reload"
    exit 1
}
cmd /c "cd /d C:\nginx && nginx.exe -s reload"
Start-Sleep -Seconds 3
Info 'reload 完成'

Step '[5/5] 全链验证'
$tests = @(
    @{n='本地后端';   u='http://127.0.0.1:8000/api/health'},
    @{n='本机nginx';  u='http://127.0.0.1/api/health'},
    @{n='https域名';  u='https://zxjiu.com/api/health'}
)
foreach ($t in $tests) {
    $c = curl.exe -sk -o NUL -w '%{http_code}' -m 8 $t.u
    Info ($t.n + " -> " + $c)
}
$c2 = curl.exe -sk -o NUL -w '%{http_code}' -m 8 https://zxjiu.com/
Info ("首页静态 -> " + $c2)
Write-Host 'PHASE3-DONE'
