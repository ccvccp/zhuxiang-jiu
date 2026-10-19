# ECS 本机 Redis 商品图本地化 + seed 代码同步 + 重启 backend8147
$ErrorActionPreference = 'Stop'
$py = @'
import json, redis, sys
r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
keys = [k for k in r.scan_iter(match='*product*') if r.type(k) == 'hash']
print('product hash keys:', len(keys))
MAP = {
  'ZX42-2026L07': 'zz-classic42', 'ZX45-2026L05': 'zz-classic45',
  'ZX52-2026X01': 'zz-premium52', 'ZX53-2026Z01': 'zz-reserve53',
  'ZX52-2026X02': 'zz-premium52-750', 'ZX53-2026N10': 'zz-age10',
  'ZX52-2026L02': 'zz-giftbox', 'ZX53-2026N20': 'zz-age20',
  'ZX52-2026X03': 'zz-premium-gift', 'ZX50-2026D01': 'zz-collection50',
}
bak = {}
keyfmt = None
for k in keys[:3]:
    print('sample key:', k)
for pid, img in MAP.items():
    for pref in ('zhuxiang:product:', 'product:'):
        k = pref + pid
        if r.exists(k):
            keyfmt = pref
            old = r.hget(k, 'images')
            bak[k] = old
            r.hset(k, 'images', json.dumps({'main': '/images/products/%s.jpg' % img, 'gallery': []}))
            print(pid, '->', r.hget(k, 'images'))
            break
with open(r'C:\tmp8147\ecs-redis-img-bak.json', 'w', encoding='utf-8') as f:
    json.dump(bak, f, ensure_ascii=False, indent=1)
print('BACKUP-OK', len(bak))
'@
[IO.File]::WriteAllText('C:\tmp8147\fixredis.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\fixredis.py
if ($LASTEXITCODE -ne 0) { Write-Host '!! redis 修复失败'; exit 1 }

Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/backend/ecsseed-20261019.tgz' -OutFile C:\tmp8147\seed.tgz -UseBasicParsing
Copy-Item C:\backend\repositories\product_repository.py C:\backend\repositories\product_repository.py.bak-20261019 -Force
cmd /c "tar -xzf C:\tmp8147\seed.tgz -C C:\backend 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Host '!! seed 覆盖失败'; exit 1 }
Write-Host ('seed 已覆盖: ' + (([IO.File]::ReadAllText('C:\backend\repositories\product_repository.py') -match 'zz-classic42')))

cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 12
Write-Host ('服务状态: ' + (Get-Service backend8147).Status)
$c = curl.exe -s -m 10 http://127.0.0.1:8000/api/product/list
[IO.File]::WriteAllText('C:\tmp8147\api2.json', $c)
$t = [IO.File]::ReadAllText('C:\tmp8147\api2.json')
Write-Host ('API 含 /images/products 次数: ' + ([regex]::Matches($t, '"/images/products/')).Count)
Write-Host ('API 含外链 mchost: ' + ($t -match 'mchost'))
Write-Host 'ECSREDIS-FIX-DONE'
