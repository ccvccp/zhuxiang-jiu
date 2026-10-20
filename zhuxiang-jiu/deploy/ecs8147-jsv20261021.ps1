# [B1 缓存升位] dist html 裸引用 js 统一加 ?v=20261021(打破 immutable 旧缓存)
$ErrorActionPreference = 'Continue'
$files = @('cart.html','checkout.html','module-test.html','news-detail.html',
            'news.html','product-detail.html','products.html','service.html')
foreach ($f in $files) {
    $p = "C:\www\dist\$f"
    if (-not (Test-Path $p)) { Write-Host "MISS $f"; continue }
    Copy-Item $p "$p.bak-v20261021" -Force
    $t = [IO.File]::ReadAllText($p)
    $new = $t -replace 'src="js/(data|products-live|main)\.js"', 'src="js/$1.js?v=20261021"'
    $n = ([regex]::Matches($t, 'src="js/(data|products-live|main)\.js"')).Count
    [IO.File]::WriteAllText($p, $new, [Text.UTF8Encoding]::new($false))
    Write-Host "$f replaced=$n"
}
# 验证: products.html 引用行
Get-Content C:\www\dist\products.html -Encoding UTF8 | Select-String 'script src' | ForEach-Object { Write-Host ("REF: " + $_.Line.Trim()) }
Write-Host 'JSV-DEPLOY-DONE'