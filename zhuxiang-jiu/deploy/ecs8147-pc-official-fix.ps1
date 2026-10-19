# PC 首页 official.html 金刚区修复: 破图jpg -> 圆球+汉字(与手机版 v5 对齐), css 版本对齐 20261026
$ErrorActionPreference = 'Stop'
$F = 'C:\www\dist\official.html'

Write-Host '==> [1/4] 备份'
cmd /c "tar -czf C:\tmp8147\www-dist-bak-pc-official-20261019.tgz -C C:\www\dist official.html"
if (-not (Test-Path 'C:\tmp8147\www-dist-bak-pc-official-20261019.tgz')) { Copy-Item $F 'C:\tmp8147\official.html.bak-20261019'; Write-Host '    (tar 备份失败, Copy-Item 兜底)' }

Write-Host '==> [2/4] 原子替换(每步校验)'
$c = [IO.File]::ReadAllText($F)
$orig = $c
$utf8 = [Text.UTF8Encoding]::new($false)

# a) css 版本号 -> 20261026 (拉新缓存)
$c = $c.Replace('css/style.css?v=20261018', 'css/style.css?v=20261026')
if ($c -eq $orig) { Write-Host '!! css 版本替换未命中(非20261018?), 尝试通用正则'; $c = $orig; $c = [regex]::Replace($c, 'style\.css\?v=\d+', 'style.css?v=20261026'); if ($c -eq $orig) { Write-Host '!! css 版本替换彻底失败, 中止'; exit 1 } }

# b) 注入 KK_ICON 字符表(数组拼行, 规避 here-string 终止符问题)
$lines = @(
    'const KK_ICON = {',
    "            products: '全', alliance: '同', activity: '活', trace: '溯',",
    "            member: '会', cart: '购', service: '客', news: '品'",
    '        };',
    '        // 圆球+汉字(取标题首字)——原 kk-*.jpg 图标从未落盘, PC 端一直',
    '        // 是 404 破图(alt 文字挤球内左上角), 本次与手机版 v5 一并对齐',
    '        const KINGKONG = ['
)
$kkIconBlock = ($lines -join "`r`n")
$c = $c.Replace('const KINGKONG = [', $kkIconBlock)
if (-not $c.Contains("products: '全'")) { Write-Host '!! KK_ICON 注入失败, 中止'; exit 1 }

# c) 数组字段 img -> ico (8 条)
foreach ($p in @(
    @("{ img: 'kk-products.jpg',", "{ ico: 'products',"),
    @("{ img: 'kk-alliance.jpg',", "{ ico: 'alliance',"),
    @("{ img: 'kk-activity.jpg',", "{ ico: 'activity',"),
    @("{ img: 'kk-trace.jpg',", "{ ico: 'trace',"),
    @("{ img: 'kk-member.jpg',", "{ ico: 'member',"),
    @("{ img: 'kk-cart.jpg',", "{ ico: 'cart',"),
    @("{ img: 'kk-service.jpg',", "{ ico: 'service',"),
    @("{ img: 'kk-news.jpg',", "{ ico: 'news',")
)) { $c = $c.Replace($p[0], $p[1]) }
if ([regex]::Matches($c, "\{ ico: '").Count -ne 8) { Write-Host ('!! ico 条目数不为8: ' + [regex]::Matches($c, "\{ ico: '").Count + ', 中止'); exit 1 }

# d) 渲染模板: img -> 字符
$oldTpl = '<div class="icon"><img src="images/icons/${k.img}" alt="${k.title}" width="46" height="46" loading="lazy"></div>'
$newTpl = '<div class="icon">${KK_ICON[k.ico]}</div>'
$c = $c.Replace($oldTpl, $newTpl)
if ($c.Contains('images/icons/${k.img}')) { Write-Host '!! 渲染模板替换未命中, 中止'; exit 1 }

[IO.File]::WriteAllText($F, $c, $utf8)
Write-Host '    全部替换成功'

Write-Host '==> [3/4] 文件级校验'
$c2 = [IO.File]::ReadAllText($F)
Write-Host ('    css v: ' + ([regex]::Match($c2, 'style\.css\?v=(\d+)').Groups[1].Value))
Write-Host ('    KK_ICON 字表: ' + ($c2 -match "products: '全'") + '; ico 条目: ' + ([regex]::Matches($c2, "\{ ico: '").Count))
Write-Host ('    残留 kk-*.jpg(应0): ' + ([regex]::Matches($c2, 'kk-\w+\.jpg').Count))
Write-Host ('    残留 img 模板(应0): ' + ([regex]::Matches($c2, [regex]::Escape('images/icons/${k.img}')).Count))

Write-Host '==> [4/4] PC UA 直出验证'
$pc = curl.exe -s -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/
Write-Host ('    /: ' + $code + '  含KK_ICON: ' + ($pc | Select-String 'KK_ICON' -Quiet) + '  含kk jpg(应False): ' + ($pc | Select-String 'kk-products\.jpg' -Quiet))
if ($code -ne '200' -or -not ($pc | Select-String 'KK_ICON' -Quiet)) { Write-Host '!! 验证未过'; exit 1 }
Write-Host 'PC-OFFICIAL-FIX-DONE'
