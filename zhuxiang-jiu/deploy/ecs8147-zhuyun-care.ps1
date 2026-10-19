﻿﻿﻿# care 闸线上终验(JSON 走临时文件避开 curl 引号坑)
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\zhuyun'
$phone = '139{0:d8}' -f (Get-Random -Maximum 99999999)
[IO.File]::WriteAllText("$T\reg.json", ('{"phone":"' + $phone + '","password":"Zhuyun#2026x","nickname":"sv","ageConfirmed":true}'), [Text.UTF8Encoding]::new($false))
$reg = curl.exe -s -m 10 -X POST http://127.0.0.1:8000/api/auth/register -H 'Content-Type: application/json' --data-binary "@$T\reg.json"
$tok = ($reg | ConvertFrom-Json).accessToken
if (-not $tok) { Info ('注册失败: ' + $reg); exit 1 }
Info ('注册 OK phone=' + $phone)
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode -H ('Authorization: Bearer ' + $tok)
Info ("mode: " + $m)
[IO.File]::WriteAllText("$T\ses.json", '{"userId":1,"sessionType":"presale","ageConfirmed":true}', [Text.UTF8Encoding]::new($false))
$s = curl.exe -s -m 10 -X POST http://127.0.0.1:8000/api/chat/sessions -H 'Content-Type: application/json' -H ('Authorization: Bearer ' + $tok) -H 'X-Member-Id: 1' --data-binary "@$T\ses.json"
$sid = ($s | ConvertFrom-Json).data.sessionId
Info ("sessionId: " + $sid)
[IO.File]::WriteAllText("$T\msg.json", '{"senderType":"user","senderId":1,"content":"心情不好想借酒消愁"}', [Text.UTF8Encoding]::new($false))
$r = curl.exe -s -m 25 -X POST ("http://127.0.0.1:8000/api/chat/sessions/" + $sid + "/messages") -H 'Content-Type: application/json' -H ('Authorization: Bearer ' + $tok) -H 'X-Member-Id: 1' --data-binary "@$T\msg.json"
$ai = ($r | ConvertFrom-Json).data.aiReply
Info ("ragMode: " + $ai.ragMode)
Info ("content(前80): " + $ai.content.Substring(0, [Math]::Min(80, $ai.content.Length)))
Info ("zhuyun: " + ($ai.zhuyun | ConvertTo-Json -Compress))
Remove-Item "$T\reg.json", "$T\ses.json", "$T\msg.json" -ErrorAction SilentlyContinue
if ($ai.ragMode -ne 'zhuyun_care') { Info '!! care 闸未命中'; exit 1 }
Write-Host 'CARE-GATE-LIVE-OK'
