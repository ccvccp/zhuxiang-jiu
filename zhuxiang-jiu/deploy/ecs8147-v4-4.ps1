# certbot V4 步骤4: 双 lineage dry-run 终验(zyjiu 静态 hook + v.zyjiu OSS V4 hook)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Info($m) { Write-Host ("    " + $m) }

Write-Host '==> certbot renew --dry-run(全部 lineage)'
cmd /c "cd /d C:\ && C:\Python312\Scripts\certbot.exe renew --dry-run > C:\tmp8147\certbot-dryrun2.log 2>&1"
$rc = $LASTEXITCODE
Info ("dry-run 退出码: " + $rc)
Get-Content C:\tmp8147\certbot-dryrun2.log -Tail 20 | ForEach-Object { Info $_ }
Write-Host '==> lineage 清单'
Get-ChildItem C:\Certbot\renewal | ForEach-Object { Info $_.Name }
Write-Host '==> 证书有效期'
foreach ($d in @('zyjiu.com','v.zyjiu.com')) {
    $pem = "C:\Certbot\live\$d\cert.pem"
    if (Test-Path $pem) {
        $info = & C:\Python312\python.exe -c "from cryptography import x509; c=x509.load_pem_x509_certificate(open(r'$pem','rb').read()); print(c.not_valid_after_utc)"
        Info ($d + " -> " + $info)
    }
}
if ($rc -ne 0) { exit 1 }
Write-Host 'V4-STEP4-DONE'
