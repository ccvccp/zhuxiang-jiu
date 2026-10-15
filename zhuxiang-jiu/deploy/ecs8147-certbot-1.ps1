# certbot 迁国内 步骤1: 安装 certbot + ACME hooks + dump nginx conf 结构
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/4] pip 安装 certbot(清华源)'
$env:PYTHONUTF8 = '1'
$env:PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$env:PIP_TRUSTED_HOST = 'pypi.tuna.tsinghua.edu.cn'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
cmd /c "cd /d C:\ && C:\Python312\python.exe -m pip install certbot --no-warn-script-location --progress-bar off > C:\tmp8147\certbot-install.log 2>&1"
if ($LASTEXITCODE -ne 0) {
    Info 'pip certbot 失败, 日志尾部:'
    Get-Content C:\tmp8147\certbot-install.log -Tail 15 | ForEach-Object { Info $_ }
    exit 1
}
$ver = & C:\Python312\Scripts\certbot.exe --version 2>&1
Info ("certbot: " + ($ver -join ' '))

Step '[2/4] 落 ACME hooks(OSS 挑战文件 auth/cleanup)'
New-Item -ItemType Directory -Force C:\certbot-hooks | Out-Null
# --- auth.ps1: PUT 挑战文件到 OSS ---
$auth = @'
# certbot manual-auth-hook: 上传 HTTP-01 挑战文件到 OSS 公共读桶
$ak = $env:ALIYUN_AK_ID
$sk = $env:ALIYUN_AK_SECRET
if (-not $ak -or -not $sk) { Write-Host 'set system env ALIYUN_AK_ID / ALIYUN_AK_SECRET first'; exit 1 }
$bucket = 'zxjiu-videos'
$hostEp = "$bucket.oss-cn-hangzhou.aliyuncs.com"
$key = ".well-known/acme-challenge/$($env:CERTBOT_TOKEN)"
$body = $env:CERTBOT_VALIDATION
$verb = 'PUT'
$date = (Get-Date).ToUniversalTime().ToString('r')
$ctype = 'text/plain'
$resource = "/$bucket/$key"
$sts = "$verb`n`n`n$ctype`n$date`n$resource"
$hmac = New-Object System.Security.Cryptography.HMACSHA1
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($sk + '&')
$sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
$bytes = [Text.Encoding]::UTF8.GetBytes($body)
$req = [Net.HttpWebRequest]::Create("https://$hostEp/$key")
$req.Method = $verb
$req.ContentType = $ctype
$req.Date = $date
$req.Headers.Add('Authorization', "OSS ${ak}:$sig")
$req.ContentLength = $bytes.Length
$s = $req.GetRequestStream(); $s.Write($bytes, 0, $bytes.Length); $s.Close()
try { $resp = $req.GetResponse(); $resp.Close(); exit 0 } catch { Write-Host "OSS PUT fail: $($_.Exception.Message)"; exit 1 }
'@
[IO.File]::WriteAllText('C:\certbot-hooks\auth.ps1', $auth, [Text.UTF8Encoding]::new($false))
# --- cleanup.ps1: DELETE 挑战文件 ---
$cleanup = @'
# certbot manual-cleanup-hook: 删除 OSS 挑战文件
$ak = $env:ALIYUN_AK_ID
$sk = $env:ALIYUN_AK_SECRET
if (-not $ak -or -not $sk) { Write-Host 'set system env ALIYUN_AK_ID / ALIYUN_AK_SECRET first'; exit 1 }
$bucket = 'zxjiu-videos'
$hostEp = "$bucket.oss-cn-hangzhou.aliyuncs.com"
$key = ".well-known/acme-challenge/$($env:CERTBOT_TOKEN)"
$verb = 'DELETE'
$date = (Get-Date).ToUniversalTime().ToString('r')
$resource = "/$bucket/$key"
$sts = "$verb`n`n`n`n$date`n$resource"
$hmac = New-Object System.Security.Cryptography.HMACSHA1
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($sk + '&')
$sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
try {
    $req = [Net.HttpWebRequest]::Create("https://$hostEp/$key")
    $req.Method = $verb
    $req.Date = $date
    $req.Headers.Add('Authorization', "OSS ${ak}:$sig")
    $resp = $req.GetResponse(); $resp.Close(); exit 0
} catch { exit 0 }   # 删除失败不影响续期
'@
[IO.File]::WriteAllText('C:\certbot-hooks\cleanup.ps1', $cleanup, [Text.UTF8Encoding]::new($false))
# --- bat 包装(certbot hook 需可执行) ---
cmd /c "echo @powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\auth.ps1 > C:\certbot-hooks\auth.bat"
cmd /c "echo @powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\cleanup.ps1 > C:\certbot-hooks\cleanup.bat"
Info 'hooks 落位: C:\certbot-hooks\{auth,cleanup}.{ps1,bat}'

Step '[3/4] hook 自检: PUT+GET+DELETE 测试挑战文件'
$env:CERTBOT_TOKEN = 'selftest-token-8147'
$env:CERTBOT_VALIDATION = 'selftest-ok-8147'
& powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\auth.ps1
$getCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/.well-known/acme-challenge/selftest-token-8147"
Info ("OSS 挑战文件 GET: " + $getCode + " (200=上传成功)")
$getVal = curl.exe -s -m 10 "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/.well-known/acme-challenge/selftest-token-8147"
Info ("内容: " + $getVal)
& powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\cleanup.ps1
Start-Sleep -Seconds 1
$getCode2 = curl.exe -s -o NUL -w '%{http_code}' -m 10 "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/.well-known/acme-challenge/selftest-token-8147"
Info ("cleanup 后 GET: " + $getCode2 + " (404=删除成功)")
if ($getCode -ne '200' -or $getVal -ne 'selftest-ok-8147') { Info '!! hook 自检失败'; exit 1 }
Remove-Item Env:CERTBOT_TOKEN, Env:CERTBOT_VALIDITY -ErrorAction SilentlyContinue
Remove-Item Env:CERTBOT_VALIDATION -ErrorAction SilentlyContinue

Step '[4/4] dump nginx conf 结构'
foreach ($f in @('C:\nginx\conf\nginx.conf','C:\nginx\conf\site-proxy.conf')) {
    Info ("===== $f =====")
    Get-Content $f | ForEach-Object { Info $_ }
}
Write-Host 'CERTBOT-STEP1-DONE'
