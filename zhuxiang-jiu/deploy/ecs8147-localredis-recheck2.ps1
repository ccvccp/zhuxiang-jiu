# 复核v2: 输出落文件再 Write-Host 回传
$c = curl.exe -s -m 10 http://127.0.0.1:8000/api/product/list
[IO.File]::WriteAllText('C:\tmp8147\api3.json', $c)
$py = @'
import json, redis
out = []
d = json.load(open(r'C:\tmp8147\api3.json', encoding='utf-8'))
for p in (d.get('products') or []):
    out.append('%s -> %s' % (p.get('product_id'), (p.get('image') or '')[:70]))
r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
ks = [k for k in r.scan_iter(match='*product*') if r.type(k) == 'hash']
out.append('redis product hash: %d' % len(ks))
out.append('sample: %s' % sorted(ks)[:4])
open(r'C:\tmp8147\recheck.txt', 'w', encoding='utf-8').write('\n'.join(out))
'@
[IO.File]::WriteAllText('C:\tmp8147\recheck.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\recheck.py
Get-Content C:\tmp8147\recheck.txt -Encoding UTF8 | ForEach-Object { Write-Host $_ }
Write-Host 'RECHECK2-DONE'
