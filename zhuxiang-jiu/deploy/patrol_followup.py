"""巡检跟进: 错误日志定位 + 留痕键名重扫 + 容器内健康对照"""
import subprocess

HOST = "root@47.236.61.117"

SCRIPT = r'''
echo "=== A. 错误日志上下文(最近2条 Traceback 各带12行) ==="
docker logs zhuxiang-backend-1 --since 300m 2>&1 | grep -A 12 "Traceback" | tail -40
echo ""
echo "=== B. 错误类型分布(300m) ==="
docker logs zhuxiang-backend-1 --since 300m 2>&1 | grep -E "ERROR|Exception" | sed 's/[0-9]//g' | sort | uniq -c | sort -rn | head -8
echo ""
echo "=== C. 容器内健康对照(排除 nginx 因素) ==="
docker exec zhuxiang-backend-1 python -c "
import urllib.request
for p in ('/api/monitor/health', '/api/maintenance/health', '/api/decision/health'):
    try:
        with urllib.request.urlopen('http://localhost:8000' + p, timeout=5) as r:
            print(p, r.status)
    except Exception as e:
        print(p, 'ERR', e)
"
echo ""
echo "=== D. 三模型留痕键名重扫(命名空间 zhuxiang:<模块>:) ==="
echo "-- zy --"
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "zhuxiang:zy:zy_logs*" | head -3
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "zhuxiang:zy:zy_logs*" | wc -l
echo "-- zd(checkups/anomalies) --"
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "zhuxiang:zd:*" | sed 's/:[0-9]*$//' | sort | uniq -c | head -6
echo "-- zk(churns/wakeups) --"
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "zhuxiang:zk:*" | sed 's/:[0-9]*$//' | sort | uniq -c | head -6
'''

r = subprocess.run(["ssh", HOST, SCRIPT],
                   capture_output=True, text=True, timeout=180)
print(r.stdout)
if r.stderr.strip():
    print("[stderr]", r.stderr[:300])
