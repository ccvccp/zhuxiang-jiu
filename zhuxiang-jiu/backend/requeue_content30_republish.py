"""36号·内容#30 重发回置(2026-09-27 内容图标记治理)

背景: 抖音平台不支持编辑已发布图文的内容图——旧图含脚本标记
无法原位替换, 品牌方决策删旧发新。本脚本将 #30 回执由 rpa
置回 rpa_pending(重入 RPA 待发清单, 复用既有重试语义), 留
republishOf 审计字段。

运行(backend 容器内): python /tmp/requeue30.py APPLY
"""
import json
import os
import sys
from datetime import datetime, timezone

import redis

APPLY = len(sys.argv) > 1 and sys.argv[1] == "APPLY"
KEY = "zhuxiang:promo:promo_contents:30"
OLD_NOTE = "7689909181234629923"

r = redis.from_url(
    os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True)
raw = r.get(KEY)
assert raw, "content #30 not found"
c = json.loads(raw)
receipt = c.get("receipt") or {}
assert receipt.get("mode") == "rpa", \
    f"非 rpa 态(当前 {receipt.get('mode')}), 不可回置"

old_url = receipt.get("url", "")
receipt.update({
    "mode": "rpa_pending",
    "error": "内容图含脚本标记, 平台不支持换图, 删旧重发",
    "republishOf": OLD_NOTE,
    "republishQueuedAt": datetime.now(timezone.utc).isoformat(),
})
c["receipt"] = receipt
print(f"#{'APPLY' if APPLY else 'DRY-RUN'}: "
      f"rpa → rpa_pending (old url={old_url})")
print(f"body 首行: {str(c.get('body'))[:40]}")
if APPLY:
    r.set(KEY, json.dumps(c, ensure_ascii=False))
    print("written.")
