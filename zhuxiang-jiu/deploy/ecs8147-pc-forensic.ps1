# 取证: 线上 PC 首页(official.html)金刚区现状
$ErrorActionPreference = 'Stop'
$idx = [IO.File]::ReadAllText('C:\www\dist\official.html')
Write-Host ('css 引用: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value))
Write-Host ('含 img 图标(应 True 即病根): ' + ($idx -match 'images/icons/kk-'))
$m = [regex]::Match($idx, "\{ img: 'kk-[^']+'[^}]*\}")
if ($m.Success) { Write-Host ('KK行示例: ' + $m.Value) }
$pc = curl.exe -s -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/
Write-Host ('PC UA 根路径含 kk jpg 引用: ' + ($pc | Select-String 'images/icons/kk-' -Quiet))
