# ECS 二轮: 无版本号 css 引用也升 20261027
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
$utf8 = [Text.UTF8Encoding]::new($false)
$n = 0
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName)
    $orig = $c
    $c = [regex]::Replace($c, 'style\.css\?v=\d+', 'style.css?v=20261027')
    $c = $c -replace 'css/style\.css"', 'css/style.css?v=20261027"'
    if ($c -ne $orig) { [IO.File]::WriteAllText($_.FullName, $c, $utf8); $n++ }
}
Write-Host ("二轮升号 html 数: $n")
$m = Get-ChildItem "$DIST\*.html" | Where-Object { [IO.File]::ReadAllText($_.FullName) -match 'css/style\.css"(?!\?)' }
Write-Host ('残留无版本引用: ' + @($m).Count)
Write-Host 'CSSV2-ECS-DONE'
