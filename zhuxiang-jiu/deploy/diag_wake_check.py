"""小竹唤醒故障诊断: 服务器侧全链路体检"""
import subprocess

HOST = "root@47.236.61.117"


def run(cmd, timeout=60):
    r = subprocess.run(["ssh", HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


print("=== 1. 容器健康 ===")
print(run("docker ps --filter name=zhuxiang-backend-1 --format "
          "'{{.Status}}' | head -1"))

print("\n=== 2. 最近 60 分钟 WS ASR 连接与鉴权 ===")
log = run("docker logs zhuxiang-backend-1 --since 60m 2>&1 | "
          "grep -E 'ws/asr|asr_failed|auth_failed' | tail -20")
print(log if log else "(60 分钟内无 WS 相关日志——用户未尝试或连接未到达)")

print("\n=== 3. 最近 60 分钟后端错误 ===")
err = run("docker logs zhuxiang-backend-1 --since 60m 2>&1 | "
          "grep -iE 'error|exception|traceback' | grep -v "
          "'ws_asr_auth_failed' | tail -10")
print(err if err else "(无错误日志)")

print("\n=== 4. 线上 widget 完整性(?v=22): 特征 + 首 3 字节 BOM ===")
print(run("curl -s 'https://zxjiu.com/js/voice-wake-widget.js?v=22' "
          "| head -c 3 | xxd | head -1"))
print(run("curl -s 'https://zxjiu.com/js/voice-wake-widget.js?v=22' "
          "| grep -c 'Audio Health Monitor'"))
print(run("curl -s https://zxjiu.com/ | "
          "grep -o 'voice-wake-widget.js?v=[0-9]*' | head -1"))

print("\n=== 5. 语音页在线(v3 特征 + BOM) ===")
print(run("curl -s https://zxjiu.com/xiaozhu-voice.html | "
          "head -c 3 | xxd | head -1"))
print(run("curl -s https://zxjiu.com/xiaozhu-voice.html | "
          "grep -c 'pumpTts'"))
print(run("curl -s https://zxjiu.com/xiaozhu-voice.html | "
          "grep -c 'endsAsk'"))

print("\n=== 6. TTS 接口快测(生产链路) ===")
print(run("curl -s -o /dev/null -w '%{http_code} %{size_download}B "
          "%{time_total}s' "
          "'https://zxjiu.com/api/xiaozhu/tts?text=%E5%9C%A8%E5%91%A2'"))

print("\n=== 7. WS 端点握手可达性(无鉴权 403/404 检查) ===")
print(run("curl -s -o /dev/null -w '%{http_code}' "
          "-H 'Upgrade: websocket' -H 'Connection: Upgrade' "
          "-H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "
          "-H 'Sec-WebSocket-Version: 13' "
          "https://zxjiu.com/api/xiaozhu/ws/asr"))
