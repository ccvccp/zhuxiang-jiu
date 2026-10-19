# 修正: 在 C:\nginx 目录下执行 -t(相对路径 logs/), 通过后 reload + 验证
$ErrorActionPreference = 'Continue'
Set-Location C:\nginx
Write-Host '==> nginx -t(在 C:\nginx 下)'
$out = & C:\nginx\nginx.exe -t 2>&1
$out | ForEach-Object { Write-Host ('    ' + $_) }
Write-Host ('    exit=' + $LASTEXITCODE)
if ($LASTEXITCODE -ne 0) {
    Write-Host '!! 语法未过, 回滚'
    Copy-Item 'C:\tmp8147\nginx.conf.bak-20261019' C:\nginx\conf\nginx.conf -Force
    & C:\nginx\nginx.exe -s reload 2>&1 | Out-Null
    exit 1
}
Write-Host '==> reload'
& C:\nginx\nginx.exe -s reload 2>&1 | ForEach-Object { Write-Host ('    ' + $_) }
Start-Sleep -Seconds 2
Write-Host '==> 验证响应头'
$h1 = curl.exe -s -D - -o NUL -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
$h1 | Select-String 'HTTP|Cache-Control' | ForEach-Object { Write-Host ('    official: ' + $_.Line.Trim()) }
$h2 = curl.exe -s -D - -o NUL -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/index.html
$h2 | Select-String 'HTTP|Cache-Control' | ForEach-Object { Write-Host ('    index:    ' + $_.Line.Trim()) }
Write-Host 'NGINX-NOCACHE-PATCH2-DONE'
