# 竹韵 shadow 收尾: 前端挂件三件 + Redis mode state 检查 + shadow 终验(会员JWT)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$TMP = 'C:\tmp8147\zhuyun'; $DIST = 'C:\www\dist'

Write-Host '==> [1/5] 前端挂件三件'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/zhuyun/zhuyun-frontend.tgz' -OutFile "$TMP\f.tgz" -UseBasicParsing
tar -xzf "$TMP\f.tgz" -C $TMP
Copy-Item "$TMP\ai-hub-widget.js" "$DIST\js\" -Force
Copy-Item "$TMP\service.html" "$DIST\service.html" -Force
$utf8 = [Text.UTF8Encoding]::new($false)
$c = [IO.File]::ReadAllText("$DIST\service.html")
$c = $c -replace '(css/style\.css)\?v=\d+', '$1?v=20261017'
$c = $c -replace 'css/style\.css(")', 'css/style.css?v=20261017$1'
$c = $c -replace '(js/main\.js)\?v=\d+', '$1?v=20261017'
$c = $c -replace 'js/main\.js(")', 'js/main.js?v=20261017$1'
[IO.File]::WriteAllText("$DIST\service.html", $c, $utf8)
$off = "$DIST\official.html"
$oc = [IO.File]::ReadAllText($off)
if ($oc -notmatch 'ai-hub-widget') {
    $line = '  <script src="js/ai-hub-widget.js" defer onload="AIHubWidget.mount({ role: ''member'' })"></script>'
    if ($oc -match '(?s)(.*</script>\s*)(</body>)') {
        $oc = $oc -replace '(?s)(.*</script>\s*)(</body>)', ('$1' + $line + "`r`n" + '$2')
        [IO.File]::WriteAllText($off, $oc, $utf8)
        Info 'official.html 插挂件行 OK'
    } else { Info '!! official.html 未匹配 </body> 前脚本, 人工查'; exit 1 }
} else { Info 'official.html 已含挂件(跳过)' }

Write-Host '==> [2/5] 前端本机验证'
$c1 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/service.html
$c2 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/js/ai-hub-widget.js
$hit = (curl.exe -s -m 8 http://127.0.0.1/official.html | Select-String 'ai-hub-widget' -Quiet)
$sh = (curl.exe -s -m 8 http://127.0.0.1/service.html | Select-String 'ai-hub-widget' -Quiet)
Info ("service:$c1 widget:$c2 official含挂件:$hit service含挂件:$sh")
if ($c1 -ne '200' -or $c2 -ne '200' -or -not $hit -or -not $sh) { Info '!! 验证未全过'; exit 1 }

Write-Host '==> [3/5] Redis chat:mode:state 检查(override/paused 压 env?)'
$keys = & C:\redis\redis-cli.exe --scan --pattern '*chat*mode*' 2>$null
Info ("匹配键: " + ($keys -join ', '))
foreach ($k in $keys) { Info ("$k => " + (& C:\redis\redis-cli.exe GET $k 2>$null)) }

Write-Host '==> [4/5] 注册会员取 JWT 验 mode'
$phone = '139{0:d8}' -f (Get-Random -Maximum 99999999)
$reg = curl.exe -s -m 10 -X POST http://127.0.0.1:8000/api/auth/register -H 'Content-Type: application/json' -d ('{"phone":"' + $phone + '","password":"Zhuyun#2026x","nickname":"shadow-verify","ageConfirmed":true}')
$tok = ($reg | ConvertFrom-Json).accessToken
if (-not $tok) { Info ('注册失败: ' + $reg); exit 1 }
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode -H ('Authorization: Bearer ' + $tok)
Info ("mode: " + $m)

Write-Host '==> [5/5] care 闸线上验证'
$s = curl.exe -s -m 10 -X POST http://127.0.0.1:8000/api/chat/sessions -H 'Content-Type: application/json' -H ('Authorization: Bearer ' + $tok) -H 'X-Member-Id: 1' -d '{"userId":1,"sessionType":"presale","ageConfirmed":true}'
$sid = ($s | ConvertFrom-Json).data.sessionId
Info ("sessionId: " + $sid)
if ($sid) {
    $uid = 1
    $r = curl.exe -s -m 20 -X POST ("http://127.0.0.1:8000/api/chat/sessions/" + $sid + "/messages") -H 'Content-Type: application/json' -H ('Authorization: Bearer ' + $tok) -H 'X-Member-Id: 1' -d '{"senderType":"user","senderId":1,"content":"心情不好想借酒消愁"}'
    $care = ($r | ConvertFrom-Json).data.aiReply.ragMode
    Info ("care ragMode: " + $care)
}
Write-Host 'ZHUYUN-SHADOW-FINAL-DONE'
