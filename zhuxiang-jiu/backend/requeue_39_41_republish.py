"""36号·视频号 #39/#41 重发回置(2026-09-27 标注修复夜)

#41: 首发视频标注「含AI生成内容」选择器点到容器未选中, 编辑器查无标注项,
     品牌决策删旧重发(新版选中标注)。
#39: 误删(两条视频文案均以中秋开头混淆), 同款补发(带AI标注)。
回执 rpa → rpa_pending(重入待发, 复用重试语义), 留 republishOf 审计字段。

运行(backend 容器内): python /tmp/requeue_39_41.py APPLY
"""
import json
import os
import sys
from datetime import datetime, timezone

import redis

APPLY = len(sys.argv) > 1 and sys.argv[1] == "APPLY"
r = redis.from_url(
    os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True)

PLANS = [
    (39, "AonC54eLb", "原发误删(两条文案均中秋开头混淆), 补发带AI标注"),
    (41, "Ak0oWlldxf", "首发视频标注未选中且编辑器无标注项, 删旧重发带含AI生成内容"),
]

for cid, old_note, reason in PLANS:
    KEY = f"zhuxiang:promo:promo_contents:{cid}"
    raw = r.get(KEY)
    assert raw, f"content #{cid} not found"
    c = json.loads(raw)
    receipt = c.get("receipt") or {}
    if receipt.get("mode") != "rpa":
        print(f"#{cid}: 非 rpa 态({receipt.get('mode')}), 跳过")
        continue
    old_url = receipt.get("url", "")
    receipt.update({
        "mode": "rpa_pending",
        "error": reason,
        "republishOf": old_note,
        "republishQueuedAt": datetime.now(timezone.utc).isoformat(),
    })
    c["receipt"] = receipt
    print(f"{'APPLY' if APPLY else 'DRY-RUN'} #{cid}: rpa → rpa_pending "
          f"(republishOf={old_note}, old url={old_url})")
    if APPLY:
        r.set(KEY, json.dumps(c, ensure_ascii=False))
        print(f"#{cid} written.")
