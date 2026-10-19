# 复核v4: python urllib 直拉 API + redis 扫描, 输出落文件
$py = @'
import json, redis, traceback, urllib.request
out = []
try:
    raw = urllib.request.urlopen('http://127.0.0.1:8000/api/product/list', timeout=10).read().decode('utf-8')
    out.append('raw len: %d' % len(raw))
    d = json.loads(raw)
    for p in (d.get('products') or []):
        out.append('%s -> %s' % (p.get('product_id'), (p.get('image') or '')[:70]))
    r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
    ks = [k for k in r.scan_iter(match='*product*') if r.type(k) == 'hash']
    out.append('redis product hash: %d' % len(ks))
    out.append('sample: %s' % sorted(ks)[:4])
except Exception:
    out.append(traceback.format_exc())
open(r'C:\tmp8147\recheck.txt', 'w', encoding='utf-8').write('\n'.join(out))
'@
[IO.File]::WriteAllText('C:\tmp8147\recheck4.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\recheck4.py
Get-Content C:\tmp8147\recheck.txt -Encoding UTF8 | ForEach-Object { Write-Host $_ }
Write-Host 'RECHECK4-DONE'
