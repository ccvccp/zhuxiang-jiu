# ECS 本机经 /api 反代复核商品图全本地
$c = curl.exe -s -m 20 -H 'Host: zxjiu.com' 'http://127.0.0.1/api/product/list'
[IO.File]::WriteAllText('C:\tmp8147\api.json', $c)
$txt = [IO.File]::ReadAllText('C:\tmp8147\api.json')
$loc = ([regex]::Matches($txt, '"/images/products/')).Count
Write-Host ('/images/products 出现次数: ' + $loc)
Write-Host ('包含 zz-classic42: ' + ($txt -match 'zz-classic42'))
Write-Host ('包含外链 mchost: ' + ($txt -match 'mchost'))
Write-Host 'API-CHECK-DONE'
