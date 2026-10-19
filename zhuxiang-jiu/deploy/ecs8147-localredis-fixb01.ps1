# ECS 本地 Redis 便携两款改相对路径(与海外一致)
$py = @'
import json, redis, traceback
out = []
try:
    r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
    fix = {
        'ZX42-2026B01': {'main': '/images/products/zx42-100ml.jpg', 'gallery': ['/images/products/zx42-100ml-desc.jpg']},
        'ZX52-2026B01': {'main': '/images/products/zx52-100ml.jpg', 'gallery': []},
    }
    bak = {}
    for pid, imgs in fix.items():
        k = 'zhuxiang:product:' + pid
        bak[k] = r.hget(k, 'images')
        r.hset(k, 'images', json.dumps(imgs))
        out.append('%s -> %s' % (pid, r.hget(k, 'images')))
    open(r'C:\tmp8147\ecs-redis-b01-bak.json', 'w', encoding='utf-8').write(json.dumps(bak, ensure_ascii=False, indent=1))
    out.append('BAK-OK')
except Exception:
    out.append(traceback.format_exc())
open(r'C:\tmp8147\fixb01.txt', 'w', encoding='utf-8').write('\n'.join(out))
'@
[IO.File]::WriteAllText('C:\tmp8147\fixb01.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\fixb01.py
Get-Content C:\tmp8147\fixb01.txt -Encoding UTF8 | ForEach-Object { Write-Host $_ }
Write-Host '--- API 终验:'
$py2 = @'
import json, urllib.request
raw = urllib.request.urlopen('http://127.0.0.1:8000/api/product/list', timeout=10).read().decode('utf-8')
d = json.loads(raw)
ps = d.get('products') or []
loc = [p for p in ps if (p.get('image') or '').startswith('/images/')]
ext = [(p.get('product_id'), p.get('image')) for p in ps if not (p.get('image') or '').startswith('/images/')]
open(r'C:\tmp8147\final.txt', 'w', encoding='utf-8').write('local %d/%d ext=%s' % (len(loc), len(ps), ext))
'@
[IO.File]::WriteAllText('C:\tmp8147\final.py', $py2, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\final.py
Get-Content C:\tmp8147\final.txt -Encoding UTF8 | ForEach-Object { Write-Host $_ }
Write-Host 'FIXB01-DONE'
