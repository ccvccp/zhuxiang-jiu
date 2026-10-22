# ECS 本地 Redis 导入同盟目录(商家/商品/分类)与海外对齐
$ErrorActionPreference = 'Stop'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/backend/alliance-export-20261019.json' -OutFile C:\tmp8147\alliance-export.json -UseBasicParsing
$py = @'
import json, redis, traceback, urllib.request
out = []
try:
    data = json.load(open(r'C:\tmp8147\alliance-export.json', encoding='utf-8'))
    r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
    tables = ['alliance_merchants', 'alliance_products', 'alliance_categories']
    # 备份现状
    bak = {}
    for t in tables:
        for k in r.scan_iter(match='zhuxiang:alliance:%s:*' % t):
            if not k.endswith(':seq'):
                bak[k] = r.get(k)
    json.dump(bak, open(r'C:\tmp8147\ecs-alliance-bak.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    out.append('backup keys: %d' % len(bak))
    # 覆盖导入
    n = 0
    for t in tables:
        for k, v in data.get(t, {}).items():
            r.set(k, v)
            n += 1
    out.append('imported: %d' % n)
    # 删除海外没有的多余目录键(ECS 独有旧键)
    extra = []
    for t in tables:
        want = set(data.get(t, {}).keys())
        for k in r.scan_iter(match='zhuxiang:alliance:%s:*' % t):
            if k.endswith(':seq') or k in want:
                continue
            extra.append(k)
            r.delete(k)
    out.append('removed extra: %d %s' % (len(extra), extra[:6]))
    # API 终验
    raw = urllib.request.urlopen('http://127.0.0.1:8000/api/alliance/products?status=active', timeout=10).read().decode('utf-8')
    d = json.loads(raw)
    names = [(i.get('productId'), i.get('name')) for i in (d.get('data') or [])]
    out.append('api products: %d' % len(names))
    out.append('has zhuliqingjing: %s' % any('竹里清境' in (nm or '') for _, nm in names))
except Exception:
    out.append(traceback.format_exc())
open(r'C:\tmp8147\import-result.txt', 'w', encoding='utf-8').write('\n'.join(out))
'@
[IO.File]::WriteAllText('C:\tmp8147\alimport.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\alimport.py
Get-Content C:\tmp8147\import-result.txt -Encoding UTF8 | ForEach-Object { Write-Host $_ }
Write-Host 'ALIMPORT-DONE'
