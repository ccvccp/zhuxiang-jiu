# [html no-store 升级] location ~ \.html$ 两处 no-cache -> no-store + reload
$ErrorActionPreference = 'Stop'
$conf = 'C:\nginx\conf\nginx.conf'
Copy-Item $conf "$conf.bak-store-20261021" -Force
$t = [IO.File]::ReadAllText($conf)
$old = 'location ~ \.html$ { add_header Cache-Control "no-cache, must-revalidate"; }'
$new = 'location ~ \.html$ { add_header Cache-Control "no-store, must-revalidate"; }'
$cnt = ([regex]::Matches($t, [regex]::Escape($old))).Count
if ($cnt -eq 0) {
    if ($t.Contains('no-store, must-revalidate"; }')) { Write-Host 'ALREADY-STORE'; }
    else { Write-Host 'PATTERN-NOT-FOUND'; }
    exit 0
}
$t = $t.Replace($old, $new)
[IO.File]::WriteAllText($conf, $t, [Text.UTF8Encoding]::new($false))
Set-Location C:\nginx
& C:\nginx\nginx.exe -t 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Copy-Item "$conf.bak-store-20261021" $conf -Force
    Write-Host 'NGINX-T-FAIL-ROLLED-BACK'; exit 1
}
& C:\nginx\nginx.exe -s reload 2>&1 | ForEach-Object { Write-Host $_ }
Start-Sleep -Seconds 2
Write-Host 'NGINX-STORE-DONE'