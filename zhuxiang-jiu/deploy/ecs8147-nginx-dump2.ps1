# nginx.conf 分段导出
$b = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\nginx\conf\nginx.conf'))
Write-Host ('LEN: ' + $b.Length)
Write-Host ('PART1: ' + $b.Substring(0, [Math]::Min(900, $b.Length)))
if ($b.Length -gt 900) { Write-Host ('PART2: ' + $b.Substring(900)) }
