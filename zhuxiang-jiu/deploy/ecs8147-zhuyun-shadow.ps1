﻿﻿﻿# 竹韵 V0.5 shadow 生产部署(国内 ECS, 云助手执行体)
# 范围: 后端 3 文件(persona/chat/knowledge) + CHAT_LLM_MODE=shadow + 前端挂件三件
# 红线: rule 轨零变化(shadow=留痕不呈现); 失败任一步即停
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS_B = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/zhuyun/zhuyun-backend.tgz'
$OSS_F = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/zhuyun/zhuyun-frontend.tgz'
$TMP = 'C:\tmp8147\zhuyun'; $BE = 'C:\backend'; $DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force $TMP | Out-Null

Step '[1/7] 备份'
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$bak = "C:\tmp8147\zhuyun-bak-$stamp.tgz"
tar -czf $bak -C $BE services/chat_service.py services/knowledge_service.py 2>$null
Copy-Item "$DIST\service.html" "$TMP\service.html.bak" -Force
Info ("备份: " + (Get-Item $bak).Length + "B + service.html.bak")

Step '[2/7] 后端 3 文件覆盖'
Invoke-WebRequest $OSS_B -OutFile "$TMP\b.tgz" -UseBasicParsing
tar -xzf "$TMP\b.tgz" -C "$TMP"
if (-not (Test-Path "$TMP\zhuyun_persona.py")) { Info '!! 解压缺 zhuyun_persona.py'; exit 1 }
& C:\Python312\python.exe -m py_compile "$TMP\zhuyun_persona.py" "$TMP\chat_service.py" "$TMP\knowledge_service.py"
if ($LASTEXITCODE -ne 0) { Info '!! py_compile 失败, 中止'; exit 1 }
Copy-Item "$TMP\zhuyun_persona.py", "$TMP\chat_service.py", "$TMP\knowledge_service.py" "$BE\services\" -Force
Info 'py_compile OK, 3 文件已覆盖'

Step '[3/7] nssm env 注入 CHAT_LLM_MODE=shadow'
& C:\nginx\nssm.exe set backend8147 AppEnvironmentExtra 'PYTHONUTF8=1 CHAT_LLM_MODE=shadow' 2>$null | Out-Null
$envReg = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147' -Name Environment -ErrorAction SilentlyContinue).Environment
Info ("nssm env(注册表): " + ($envReg -join ' | '))

Step '[4/7] 重启后端'
& C:\nginx\nssm.exe restart backend8147 | Out-Null
Start-Sleep -Seconds 6
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Info ("health: " + $h)
if (-not $h) { Info '!! 健康检查空, 查 service-err.log'; Get-Content C:\backend\logs\service-err.log -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }; exit 1 }

Step '[5/7] mode 确认(应 shadow/env)'
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode
Info ("mode: " + $m)
if ($m -notmatch '"mode"\s*:\s*"shadow"') { Info '!! mode 非 shadow(env/override/paused 冲突), 如实登记不阻断' }

Step '[6/7] 前端挂件上线'
Invoke-WebRequest $OSS_F -OutFile "$TMP\f.tgz" -UseBasicParsing
tar -xzf "$TMP\f.tgz" -C "$TMP"
Copy-Item "$TMP\ai-hub-widget.js" "$DIST\js\" -Force
Copy-Item "$TMP\service.html" "$DIST\service.html" -Force
# service.html v 号归一(与全站 v=20261017 一致, 防缓存)
$utf8 = [Text.UTF8Encoding]::new($false)
$c = [IO.File]::ReadAllText("$DIST\service.html")
$c = $c -replace '(css/style\.css)\?v=\d+', '$1?v=20261017'
$c = $c -replace 'css/style\.css(")', 'css/style.css?v=20261017$1'
$c = $c -replace '(js/main\.js)\?v=\d+', '$1?v=20261017'
$c = $c -replace 'js/main\.js(")', 'js/main.js?v=20261017$1'
[IO.File]::WriteAllText("$DIST\service.html", $c, $utf8)
# official.html 幂等插挂件行(在 </body> 前)
$off = "$DIST\official.html"
$oc = [IO.File]::ReadAllText($off)
if ($oc -notmatch 'ai-hub-widget') {
    $line = '  <script src="js/ai-hub-widget.js" defer onload="AIHubWidget.mount({ role: ''member'' })"></script>'
    $oc = $oc -replace '(?s)(.*</script>\s*)(</body>)', "`$1$line`r`n`$2"
    [IO.File]::WriteAllText($off, $oc, $utf8)
    Info 'official.html 已插挂件行'
} else { Info 'official.html 已含挂件(跳过)' }
Info ("ai-hub-widget.js: " + (Get-Item "$DIST\js\ai-hub-widget.js").Length + "B")

Step '[7/7] 本机验证'
$c1 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/service.html
$c2 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/js/ai-hub-widget.js
$c3 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/official.html
$hit = (curl.exe -s -m 8 http://127.0.0.1/official.html | Select-String 'ai-hub-widget' -Quiet)
$sh = (curl.exe -s -m 8 http://127.0.0.1/service.html | Select-String 'ai-hub-widget' -Quiet)
Info ("service:$c1 widget:$c2 official:$c3 official含挂件:$hit service含挂件:$sh")
if ($c1 -ne '200' -or $c2 -ne '200' -or $c3 -ne '200' -or -not $hit -or -not $sh) { Info '!! 验证未全过'; exit 1 }
Write-Host 'ZHUYUN-SHADOW-DEPLOY-DONE'
