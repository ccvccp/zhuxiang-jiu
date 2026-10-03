"""LLM_TIMEOUT 15→60(长文生成负载实测双模型均超时——E2E 发现)

依据: 短 prompt 2.4s OK; 300 字营销文案 5.3/4-flash 双双 15s
读超时(llm_client._TIMEOUT 模块级, 须重建生效)。
"""
import subprocess
import time

HOST = "root@47.236.61.117"

CHANGE = """
cd /opt/zhuxiang
cp .env .env.bak-llm-timeout-20261003
grep -q '^LLM_TIMEOUT=' .env 2>/dev/null && sed -i 's/^LLM_TIMEOUT=.*/LLM_TIMEOUT=60/' .env || echo 'LLM_TIMEOUT=60' >> .env
grep -n '^LLM_TIMEOUT=' .env
docker compose up -d --force-recreate backend 2>&1 | tail -2
"""

VERIFY = r'''
sleep 8
docker exec zhuxiang-backend-1 python -c "
import time
from services.llm_client import provider_client, _TIMEOUT
print('LLM_TIMEOUT =', _TIMEOUT)
assert _TIMEOUT == 60, _TIMEOUT
SYS = '你是小红书营销文案专家, 为竹香型白酒写借势笔记。'
USER = ('热点: 非遗文化体验馆走红。写一篇 300 字左右的小红书笔记, '
        '含标题/正文/话题标签, 语气自然, 须含未成年人禁止饮酒提示, '
        '直接输出文案。')
for m in ('glm-5.3', 'glm-4-flash'):
    t0 = time.time()
    try:
        r = provider_client.chat(SYS, USER, model=m)
        print(f'{m}:', 'OK len=%d' % len(str(r)),
              '({:.1f}s)'.format(time.time() - t0))
    except Exception as e:
        print(f'{m}: FAIL {str(e)[:60]}')
" 2>&1 | grep -v WARNING
docker ps --format '{{.Names}} {{.Status}}' | grep zhuxiang-backend
'''


def run(script, timeout=900):
    r = subprocess.run(["ssh", HOST, script],
                       capture_output=True, text=True,
                       timeout=timeout)
    print(r.stdout)
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:500])
        raise SystemExit("step failed")
    return r


def main():
    run(CHANGE)
    time.sleep(3)
    run(VERIFY, timeout=300)
    print("LLM_TIMEOUT 调整完成: 15 → 60")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
