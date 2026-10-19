# 复核: ECS 本地 API 12 款 image 字段 + Redis product keys
$c = curl.exe -s -m 10 http://127.0.0.1:8000/api/product/list
[IO.File]::WriteAllText('C:\tmp8147\api3.json', $c)
& C:\Python312\python.exe -c "import json; d=json.load(open(r'C:\tmp8147\api3.json', encoding='utf-8')); [print(p.get('product_id'), '->', (p.get('image') or '')[:70]) for p in (d.get('products') or [])]"
Write-Host '--- redis product keys:'
& C:\Python312\python.exe -c "import redis; r=redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True); ks=[k for k in r.scan_iter(match='*product*') if r.type(k)=='hash']; print(len(ks), sorted(ks)[:5])"
Write-Host 'RECHECK-DONE'
