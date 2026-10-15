$b = [IO.File]::ReadAllBytes('C:\tmp8147\wx_check_result.txt')
Write-Host ('file size=' + $b.Length)
$txt = [Text.Encoding]::UTF8.GetString($b)
foreach ($ln in $txt -split "`n") {
    $safe = ($ln.ToCharArray() | ForEach-Object { if ([int]$_ -ge 32 -and [int]$_ -lt 127) { $_ } else { '?' } }) -join ''
    if ($safe.Trim('?').Length -gt 0 -or $ln.Trim().Length -eq 0) { Write-Host $safe }
}
Write-Host 'READ-DONE'
