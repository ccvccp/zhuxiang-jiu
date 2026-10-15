[Console]::OutputEncoding = [Text.Encoding]::UTF8
$d = Invoke-RestMethod -Uri 'https://zyjiu.com/api/v1/dramas' -TimeoutSec 15
@($d.data) | ConvertTo-Json -Depth 8 | Out-File 'drama-eps-full.json' -Encoding UTF8
Write-Host 'saved'
