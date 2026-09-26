"""36号·存量内容脚本标记修正(2026-09-27 治理, douyin 轨 9 条)

对 promo_contents 中命中分镜时间标记/【热点借势】标签的正文做
确定性清洗: 标记剥除 + 裸热点标题行去除(与标题重复) + 空行规整;
其余字段(状态/回执/审核记录)不动, 每条加 copyHygieneAt 审计戳。
合规不变量: 清洗后必须仍含警示语(缺失则跳过该条不写)。

运行(backend 容器内):
    python /tmp/patch_copy_hygiene.py        # dry-run 仅打印
    python /tmp/patch_copy_hygiene.py APPLY  # 实际写回
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import redis

APPLY = len(sys.argv) > 1 and sys.argv[1] == "APPLY"
MARKER_RE = re.compile(r"【\d+(?:-\d+)?s[^】]*】")
KEY = "zhuxiang:promo:promo_contents:{cid}"
DISCLAIMER = "过量饮酒有害健康"

r = redis.from_url(
    os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True)

patched, skipped = [], []
for cid in range(1, 200):
    raw = r.get(KEY.format(cid=cid))
    if not raw:
        continue
    c = json.loads(raw)
    body = str(c.get("body") or "")
    if not (MARKER_RE.search(body) or "【热点借势】" in body):
        continue
    lines = MARKER_RE.sub(
        "", body.replace("【热点借势】", "")).split("\n")
    lines = [ln.strip() for ln in lines]
    title_head = str(c.get("title") or "").split("｜")[0].strip()
    if lines and lines[0] == title_head:
        lines = lines[1:]
    new_body = "\n".join(ln for ln in lines if ln)
    if DISCLAIMER not in new_body:
        skipped.append((cid, "警示语缺失, 跳过"))
        continue
    c["body"] = new_body
    c["copyHygieneAt"] = datetime.now(timezone.utc).isoformat()
    patched.append((cid, new_body.replace("\n", " ⏎ ")[:90],
                    json.dumps(c, ensure_ascii=False)))

print(f"{'APPLY' if APPLY else 'DRY-RUN'}: "
      f"patched={len(patched)} skipped={len(skipped)}")
for cid, preview, payload in patched:
    print(f"  #{cid}: {preview}")
    if APPLY:
        r.set(KEY.format(cid=cid), payload)
for cid, why in skipped:
    print(f"  #{cid} SKIP: {why}")
