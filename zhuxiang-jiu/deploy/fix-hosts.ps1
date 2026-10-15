# 临时 hosts 绑定: zyjiu.com → 海外生产服务器(绕过阿里云 DNS 同步故障)
# 用法: 管理员运行; 参数 -Revert 撤销绑定
param([switch]$Revert)

$hosts = "$env:WinDir\System32\drivers\etc\hosts"
$line = "47.236.61.117 zyjiu.com"

if ($Revert) {
    $c = Get-Content $hosts | Where-Object { $_.Trim() -ne $line }
    Set-Content -Path $hosts -Value $c -Encoding ASCII
    Write-Host "removed: $line"
} else {
    $raw = Get-Content $hosts -Raw -ErrorAction SilentlyContinue
    if ($raw -notmatch [regex]::Escape($line)) {
        Add-Content -Path $hosts -Value "`n$line"
        Write-Host "added: $line"
    } else {
        Write-Host "already present"
    }
}
ipconfig /flushdns | Out-Null
Write-Host "flushdns done"
