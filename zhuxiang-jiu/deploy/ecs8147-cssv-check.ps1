# ECS 核实 css v 号分布
$ErrorActionPreference = 'Continue'
$DIST = 'C:\www\dist'
$groups = @{}
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName)
    $m = [regex]::Match($c, 'style\.css\?v=(\d+)')
    $k = if ($m.Success) { $m.Groups[1].Value } else { 'NO-REF' }
    if (-not $groups[$k]) { $groups[$k] = 0 }
    $groups[$k]++
}
$groups.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { Write-Host ("v=" + $_.Key + " : " + $_.Value + " 个") }
