"""唤醒热词诊断: 容器内验证 _asr_hotwords() 三源注入是否完好"""
import subprocess

HOST = "root@47.236.61.117"

SCRIPT = r'''
cat > /tmp/diag_hotwords.py <<'PYEOF'
import asyncio
import sys
sys.path.insert(0, "/app")

async def main():
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()
    hw = await svc._asr_hotwords()
    print("hotwords count:", len(hw))
    print("hotwords:", hw[:30])
    key = [w for w in hw if "小竹" in w or "小主" in w or "小住" in w]
    print("唤醒词相关:", key)

asyncio.run(main())
PYEOF
docker cp /tmp/diag_hotwords.py zhuxiang-backend-1:/tmp/diag_hotwords.py
docker exec -w /app zhuxiang-backend-1 python3 /tmp/diag_hotwords.py
'''

r = subprocess.run(["ssh", HOST, SCRIPT],
                   capture_output=True, text=True, timeout=120)
print(r.stdout)
print(r.stderr[:500] if r.returncode else "")
