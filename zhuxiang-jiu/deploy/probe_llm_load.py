"""长文负载实测: 两模型真实生成耗时(决定 LLM_TIMEOUT 取值)"""
import subprocess

INNER = r'''
import time
from services.llm_client import provider_client

SYS = ("你是小红书营销文案专家, 为竹香型白酒写借势笔记。")
USER = ("热点: 非遗文化体验馆走红。写一篇 300 字左右的小红书笔记, "
        "含标题/正文/话题标签, 语气自然, 须含'未成年人禁止饮酒'提示, "
        "直接输出文案。")

for m in ("glm-5.3", "glm-4-flash"):
    t0 = time.time()
    try:
        r = provider_client.chat(SYS, USER, model=m)
        ok = bool(r) and len(str(r)) > 100
        print(f"{m}: {'OK' if ok else 'EMPTY'} "
              f"len={len(str(r))} ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"{m}: FAIL {str(e)[:60]} ({time.time()-t0:.1f}s)")
'''

import base64
b64 = base64.b64encode(INNER.encode()).decode()
cmd = (f"echo {b64} | base64 -d | "
       f"docker exec -i zhuxiang-backend-1 python -")
r = subprocess.run(["ssh", "root@47.236.61.117", cmd],
                   capture_output=True, text=True, timeout=240)
print(r.stdout)
print(r.stderr[:300])
