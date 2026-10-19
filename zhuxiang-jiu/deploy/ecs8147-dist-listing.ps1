# ECS dist 文件清单与关键功能核对
$ErrorActionPreference = 'Continue'
Write-Host '== ECS dist html 列表 =='
$htmls = Get-ChildItem C:\www\dist -Filter *.html | Select-Object -ExpandProperty Name
Write-Host ('html总数: ' + $htmls.Count)
Write-Host ($htmls -join ' ')
Write-Host '== 子目录 =='
(Get-ChildItem C:\www\dist -Directory | Select-Object -ExpandProperty Name) -join ' ' | ForEach-Object { Write-Host $_ }
Write-Host '== 关键功能文件 =='
foreach ($f in @('my-card.html', 'xiaozhu-voice.html', 'alliance-mall.html', 'recycle.html', 'app.html', 'm.html', 'mall.html')) {
    Write-Host ($f + ': ' + (Test-Path "C:\www\dist\$f"))
}
Write-Host '== SPA 痕迹(app.*.js / taro.*.js) =='
$spa = Get-ChildItem C:\www\dist\js -Filter '*.js' -ErrorAction SilentlyContinue | Where-Object Name -match '^(app|taro|common)\.' | Select-Object -First 5 -ExpandProperty Name
Write-Host ($spa -join ' ')
Write-Host '== js 目录文件数 =='
Write-Host (@(Get-ChildItem C:\www\dist\js -Filter '*.js' -ErrorAction SilentlyContinue).Count)
