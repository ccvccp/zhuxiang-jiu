# -*- coding: utf-8 -*-
"""8511 批2验证: 53 链采集 → 17 新剧入契约 + 近身保镖不双入"""
import io
import sys
import time
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

NEW = [
    "归来：亏欠妻儿二十五年", "将门枭虎", "近身保镖", "合租凶宅",
    "被讹后，全村的麦子烂在地里了", "分手四年，傅机长失了控", "逍遥镇北王",
    "开局百年内力，我带师妹横扫武林", "都重生了，这富二代我不装了",
    "冰山美女的超级保镖", "盛律师的离婚前规则", "东北婆婆，笑护全家",
    "亿万斯年", "知许不许", "京夜诱温", "顾先生我不要你了", "好雨知时节",
]

t0 = time.time()
items, offset = [], 0
with httpx.Client(timeout=600.0) as c:
    while True:
        r = c.get("http://127.0.0.1:8511/dramas",
                  params={"n": 500, "offset": offset})
        r.raise_for_status()
        d = r.json()
        items.extend(d.get("dramas") or [])
        if not d.get("hasMore"):
            break
        offset += len(d.get("dramas") or [])
print(f"fetched {len(items)} items in {time.time() - t0:.0f}s")

titles = [str(it.get("title") or "") for it in items]
found = [t for t in NEW if t in titles]
missing = [t for t in NEW if t not in titles]
print(f"new_found: {len(found)}/{len(NEW)}")
if missing:
    print("MISSING:", missing)

dup = [(t, n) for t, n in Counter(titles).items() if n > 1 and t in NEW]
print("dup_in_new:", dup or "none")

for t in ("好雨知时节", "近身保镖"):
    for it in items:
        if it.get("title") == t:
            print(f"  {t!r} ep={it.get('episodes')} itemId={it.get('itemId')}")
            break
