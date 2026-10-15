# migration audit: public endpoint matrix (ASCII-only output)
$fail = 0
function Check($n, $code, $expect) {
    $ok = $expect -contains $code
    if (-not $ok) { $script:fail++ }
    $tag = 'OK '; if (-not $ok) { $tag = '!! ' }
    Write-Host ("  [$tag] $n -> $code")
}

Write-Host '### https://zyjiu.com endpoint matrix'
Check 'home'            (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/) @('200')
Check 'drama.html'      (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/drama.html) @('200')
Check 'zyh.html'        (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/zyh.html) @('200')
Check 'cert.html'       (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/cert.html) @('200')
Check 'js asset'        (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/js/app.b1018f82.js) @('200')
Check 'product image'   (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/images/products/zx42-100ml.jpg) @('200')
Check 'dramas (public)' (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/api/v1/dramas) @('200')
Check 'plans (public)'  (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/api/v1/memberships/plans) @('200')
Check 'health (401 auth)' (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/api/health) @('401')
Check 'admin (401/403)' (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/api/activity/admin/list) @('401','403')
Check 'robots.txt'      (curl.exe -sk -o NUL -w '%{http_code}' -m 12 https://zyjiu.com/robots.txt) @('200')
Check 'CDN video HEAD'  (curl.exe -sk -o NUL -w '%{http_code}' -m 15 -I https://v.zyjiu.com/d84_4c2d8186ccee_ep1.mp4) @('200','206')
Check 'WS handshake'    (curl.exe -sk -o NUL -w '%{http_code}' -m 10 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://zyjiu.com/api/xiaozhu/ws/voice) @('101','400','401','404','403')

Write-Host '### certificates'
foreach ($h in @('zyjiu.com','v.zyjiu.com')) {
    $tcp = New-Object Net.Sockets.TcpClient($h, 443)
    $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, { $true })
    $ssl.AuthenticateAsClient($h)
    $c = New-Object Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
    $days = ($c.NotAfter.ToUniversalTime() - (Get-Date).ToUniversalTime()).Days
    Write-Host ("  $h : $($c.Subject) / $($c.Issuer.Split(',')[0]) / ${days}d left")
    $ssl.Dispose(); $tcp.Dispose()
}
Write-Host ("### RESULT: " + $(if ($fail -eq 0) {'ALL PASS'} else {"$fail FAILED"}))
