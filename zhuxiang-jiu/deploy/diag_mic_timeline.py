"""唤醒独占诊断: 3 小时时序对照——语音页 session 活动 vs widget WS"""
import subprocess

HOST = "root@47.236.61.117"

SCRIPT = r'''
echo "=== A. voice48_session_open / timing (语音页面板活动) ==="
docker logs zhuxiang-backend-1 --since 180m 2>&1 | grep -E "voice48_session_open|voice48_timing" | tail -30
echo ""
echo "=== B. WS accepted 时序(widget 唤醒与面板对话共用) ==="
docker logs zhuxiang-backend-1 --since 180m 2>&1 | grep "ws/asr" | tail -30
echo ""
echo "=== C. auth_failed / asr_exception ==="
docker logs zhuxiang-backend-1 --since 180m 2>&1 | grep -E "auth_failed|hub_asr_exception" | tail -10
echo ""
echo "=== D. TTS 请求时序(面板播报活动) ==="
docker logs zhuxiang-backend-1 --since 180m 2>&1 | grep "GET /api/xiaozhu/tts" | tail -15
'''

r = subprocess.run(["ssh", HOST, SCRIPT],
                   capture_output=True, text=True, timeout=120)
print(r.stdout)
