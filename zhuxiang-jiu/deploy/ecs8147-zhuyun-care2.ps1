# care 闸线上终验 v2: 会话/消息统一用注册会员真实 id
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\zhuyun'
$phone = '139{0:d8}' -f (Get-Random -Maximum 99999999)
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText("$T\reg.json", ('{"phone":"' + $phone + '","password":"Zhuyun#2026x","nickname":"sv2","ageConfirmed":true}'), $utf8)
$reg = curl.exe -s -m 10 -X POST http://127.0.0.1:8000/api/auth/register -H 'Content-Type: application/json' --data-binary "@$T\reg.json"
$regJ = $reg | ConvertFrom-Json
$tok = $regJ.accessToken; $mid = $regJ.memberId
if (-not $tok) { Info ('注册失败: ' + $reg); exit 1 }
Info ("注册 OK memberId=$mid")
[IO.File]::WriteAllText("$T\ses.json", ('{"userId":' + $mid + ',"sessionType":"presale","ageConfirmed":true}'), $utf8)
$s = curl.exe -s -m 10 -X POST http://127.0.0.1:8000/api/chat/sessions -H 'Content-Type: application/json' -H ('Authorization: Bearer ' + $tok) -H ('X-Member-Id: ' + $mid) --data-binary "@$T\ses.json"
$sid = ($s | ConvertFrom-Json).data.sessionId
Info ("sessionId: " + $sid)
[IO.File]::WriteAllText("$T\msg.json", ('{"senderType":"user","senderId":' + $mid + ',"content":"心情不好想借酒消愁"}'), $utf8)
$r = curl.exe -s -m 25 -X POST ("http://127.0.0.1:8000/api/chat/sessions/" + $sid + "/messages") -H 'Content-Type: application/json' -H ('Authorization: Bearer ' + $tok) -H ('X-Member-Id: ' + $mid) --data-binary "@$T\msg.json"
$d = ($r | ConvertFrom-Json).data
Info ("ragMode: " + $d.aiReply.ragMode)
$c2 = [string]$d.aiReply.content
Info ("content(前60): " + $c2.Substring(0, [Math]::Min(60, $c2.Length)))
Info ("transferred: " + $d.transferred)
Remove-Item "$T\reg.json", "$T\ses.json", "$T\msg.json" -ErrorAction SilentlyContinue
if ($d.aiReply.ragMode -ne 'zhuyun_care') { Info ('!! care 未命中, 原文: ' + $r.Substring(0, [Math]::Min(300, $r.Length))); exit 1 }
Write-Host 'CARE-GATE-LIVE-OK'
