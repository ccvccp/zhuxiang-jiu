# [证书部署] live(reparse)->nginx cert.pem/key.pem 备份先行 + reload + 双域名验证
# [进化 2026-10-27] IMP-15 修复：本文件原名 ecs8147-certbot.py，但全文 40 行
# 无一句 Python（$ErrorActionPreference / Copy-Item / Start-Process / SslStream
# 全为 PowerShell），扩展名 .py 属误标，导致全仓 ast 语法扫描在第 2 行报
# "invalid syntax"。属《架构修复复盘报告》§7 根因四「命名语义漂移」同族问题。
# 处置：改名为 .ps1，不改任何一行逻辑。安全性实测：Grep 全仓 9 处引用均在
# markdown/txt 文档中，零代码 import；该文件在 git 中为 untracked，改名即
# 文件系统 move，无历史追溯断裂。实锤：deploy\tmp\scan_b0_baseline.py。
# 执行方式：powershell -ExecutionPolicy Bypass -File ecs8147-certbot.ps1
$ErrorActionPreference = 'Continue'
$live = 'C:\Certbot\live\zyjiu.com'
# 真实文件校验(读内容长度)
$fc = [IO.File]::ReadAllText("$live\fullchain.pem").Length
$fk = [IO.File]::ReadAllText("$live\privkey.pem").Length
Write-Host ("fullchain_chars=" + $fc + " privkey_chars=" + $fk)
if ($fc -lt 1000 -or $fk -lt 200) { Write-Host 'PEM-EMPTY-ABORT'; exit 1 }

Copy-Item C:\nginx\cert.pem C:\nginx\cert.pem.bak-20261021 -Force
Copy-Item C:\nginx\key.pem C:\nginx\key.pem.bak-20261021 -Force
Copy-Item "$live\fullchain.pem" C:\nginx\cert.pem -Force
Copy-Item "$live\privkey.pem" C:\nginx\key.pem -Force
Write-Host 'copied'

Set-Location C:\nginx
$t = Start-Process -FilePath 'C:\nginx\nginx.exe' -ArgumentList '-t' -Wait -PassThru -NoNewWindow -RedirectStandardOutput C:\tmp8147\ngt.txt -RedirectStandardError C:\tmp8147\ngte.txt
Write-Host ("nginx_t_exit=" + $t.ExitCode)
if ($t.ExitCode -ne 0) {
    Copy-Item C:\nginx\cert.pem.bak-20261021 C:\nginx\cert.pem -Force
    Copy-Item C:\nginx\key.pem.bak-20261021 C:\nginx\key.pem -Force
    Write-Host 'T-FAIL-ROLLED-BACK'; Get-Content C:\tmp8147\ngte.txt -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    exit 1
}
$r = Start-Process -FilePath 'C:\nginx\nginx.exe' -ArgumentList '-s','reload' -Wait -PassThru -NoNewWindow
Write-Host ("reload_exit=" + $r.ExitCode)
Start-Sleep -Seconds 3

# 双域名 SNI 验证
foreach ($h in @('zyjiu.com', 'www.zyjiu.com')) {
    try {
        $tcp = New-Object Net.Sockets.TcpClient($h, 443)
        $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, { param($a,$b,$c,$d) $true })
        $ssl.AuthenticateAsClient($h)
        $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
        Write-Host ($h + ' -> subject=' + $cert.Subject + ' expire=' + $cert.NotAfter.ToString('yyyy-MM-dd'))
        $ssl.Dispose(); $tcp.Dispose()
    } catch { Write-Host ($h + ' ERR: ' + $_.Exception.Message.Substring(0, [Math]::Min(90, $_.Exception.Message.Length))) }
}
Write-Host 'CERT-DEPLOY-DONE'