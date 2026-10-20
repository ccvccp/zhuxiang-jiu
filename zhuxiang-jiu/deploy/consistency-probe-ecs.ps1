# [双端一致性巡检-ECS探测] 输出全部走 Write-Host(云助手契约)
$ErrorActionPreference = 'Continue'
# API 全量指标(T2.3 升级: 全页遍历防第一页截断; 三指标+零库存单次请求同算)
$ap = @'
import json, urllib.request

def fetch_all():
    ps, page = [], 1
    while True:
        d = json.loads(urllib.request.urlopen(
            'http://127.0.0.1:8000/api/product/list?page=%d&page_size=50' % page,
            timeout=10).read())
        ps.extend(d['products'])
        tp = d.get('totalPages') or max(1, -(-int(d.get('total') or 0) // 50))
        if page >= tp:
            return ps
        page += 1

ps = fetch_all()
loc = sum(1 for p in ps if (p.get('image') or '').startswith('/images/'))
ext = sum(1 for p in ps if 'mchost' in (p.get('image') or ''))
zero = sum(1 for p in ps if (p.get('stock') or 0) <= 0)
open(r'C:\tmp8147\api_kv.txt', 'w').write('%d %d %d %d' % (len(ps), loc, ext, zero))
'@
[IO.File]::WriteAllText('C:\tmp8147\apk.py', $ap, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\apk.py
$p = [IO.File]::ReadAllText('C:\tmp8147\api_kv.txt').Split(' ')
Write-Host ("api_total=" + $p[0])
Write-Host ("api_local=" + $p[1])
Write-Host ("api_ext=" + $p[2])
Write-Host ("stock_zero=" + $p[3])
Write-Host ("imgs=" + (Get-ChildItem 'C:\www\dist\images\products' -File).Count)
$idx = [IO.File]::ReadAllText('C:\www\dist\index.html')
Write-Host ("idx_mark=" + ([regex]::Matches($idx, 'data-hadd')).Count)
$app = [IO.File]::ReadAllText('C:\www\dist\app.html')
Write-Host ("app_v=" + ([regex]::Matches($app, 'b984e24b\.js\?v=')).Count)
# Redis 商品键数(python 落文件回传)
$rp = @'
import redis
r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
ks = [k for k in r.scan_iter(match='zhuxiang:product:*')
      if 'review' not in k and 'categories' not in k]
open(r'C:\tmp8147\rp.txt', 'w').write(str(len(ks)))
'@
[IO.File]::WriteAllText('C:\tmp8147\rp.py', $rp, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\rp.py
Write-Host ("redis_products=" + [IO.File]::ReadAllText('C:\tmp8147\rp.txt'))
Write-Host 'ECS-PROBE-DONE'
