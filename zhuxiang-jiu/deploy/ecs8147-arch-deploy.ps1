# 竹智 MVP 生产部署(后端 5 文件+前端 2 文件; 无 env 变更, 重启即加载)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\arch'; $BE = 'C:\backend'; $DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force $T | Out-Null

Write-Host '==> [1/5] 备份+下载+解压'
Copy-Item "$BE\routes\__init__.py", "$BE\main.py", "$BE\core\auth_middleware.py" $T -Force
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/arch/arch-prod.tgz' -OutFile "$T\p.tgz" -UseBasicParsing
tar -xzf "$T\p.tgz" -C $T
Info ('解包 7 文件: ' + ((Get-ChildItem $T -File | Measure-Object).Count))

Write-Host '==> [2/5] py_compile 预校验'
& C:\Python312\python.exe -m py_compile "$T\arch_agent_service.py" "$T\arch_routes.py" "$T\__init__.py" "$T\main.py" "$T\auth_middleware.py"
if ($LASTEXITCODE -ne 0) { Info '!! py_compile 失败中止'; exit 1 }
Info 'py_compile OK'

Write-Host '==> [3/5] 后端覆盖+重启'
Copy-Item "$T\arch_agent_service.py" "$BE\services\" -Force
Copy-Item "$T\arch_routes.py" "$BE\routes\" -Force
Copy-Item "$T\__init__.py" "$BE\routes\" -Force
Copy-Item "$T\main.py" "$BE\" -Force
Copy-Item "$T\auth_middleware.py" "$BE\core\" -Force
sc.exe stop backend8147 | Out-Null; Start-Sleep -Seconds 3
sc.exe start backend8147 | Out-Null; Start-Sleep -Seconds 9
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Info ('health: ' + $h.Substring(0, [Math]::Min(40, $h.Length)))
if (-not $h) { Get-Content C:\backend\logs\service-err.log -Tail 8 | ForEach-Object { Info $_ }; exit 1 }

Write-Host '==> [4/5] observe 生产验证'
$o = curl.exe -s -m 40 http://127.0.0.1:8000/api/arch/observe
Info ('observe(前200): ' + $o.Substring(0, [Math]::Min(200, $o.Length)))
if ($o -notmatch '"success":true') { Info '!! observe 失败'; exit 1 }

Write-Host '==> [5/5] 前端上线+验证'
Copy-Item "$T\arch-dashboard.html" "$DIST\" -Force
Copy-Item "$T\arch-dashboard.js" "$DIST\js\" -Force
$c1 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/arch-dashboard.html
$c2 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/js/arch-dashboard.js
Info ("dashboard:$c1 js:$c2")
if ($c1 -ne '200' -or $c2 -ne '200') { Info '!! 前端验证未过'; exit 1 }
Write-Host 'ARCH-PROD-DEPLOY-DONE'
