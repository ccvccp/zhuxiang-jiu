"""带时间戳的唤醒链路时序还原"""
import subprocess

HOST = "root@47.236.61.117"

SCRIPT = r'''
echo "=== WS accepted (带时间戳) ==="
docker logs zhuxiang-backend-1 --since 180m --timestamps 2>&1 | grep "ws/asr" | sed 's/.*INFO: *//' | awk '{print $1, $NF}'
echo ""
echo "=== auth_failed (带时间戳) ==="
docker logs zhuxiang-backend-1 --since 180m --timestamps 2>&1 | grep "auth_failed"
echo ""
echo "=== xiaozhu 相关 HTTP 访问日志(sessions/turns 401 等) ==="
docker logs zhuxiang-backend-1 --since 180m --timestamps 2>&1 | grep -E "api/xiaozhu" | grep -vE "ws/asr" | sed 's/.*INFO: *//' | tail -25
echo ""
echo "=== auth 相关 401(refresh 失败线索) ==="
docker logs zhuxiang-backend-1 --since 180m --timestamps 2>&1 | grep -E "api/auth/refresh" | sed 's/.*INFO: *//' | tail -10
'''

r = subprocess.run(["ssh", HOST, SCRIPT],
                   capture_output=True, text=True, timeout=120)
print(r.stdout)
