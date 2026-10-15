# -*- coding: utf-8 -*-
"""8511 重启后端点验证: 36 链采集 → 22 新剧主剧全入契约核对"""
import io
import json
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

NEW = [
    "游戏入侵：普攻加血一箭亿万伤屠戮神明", "归墟第一季", "反相之地",
    "持械入宋：第一季", "垫底漫画家：靠考古一战成名", "困兽之战",
    "万妖图录传第一季", "聚宝仙盆之杂灵根才是真BOSS",
    "网游：毒帝无双，我的毒能屠神", "公司扣我年终奖，我不忍了",
    "母凭子贵之我送两个孩子上清北", "重生之嫡女归来", "从东北来的她",
    "爱在风起燕舞时", "弟媳表演的孝心，我笑了", "风雪遇良缘",
    "我隐藏身份，找到了新妈妈", "傅总别装了，你老婆要掀桌", "奔头儿",
    "东北话事人", "影后的剧本出了错", "近身保镖",
]

t0 = time.time()
items, offset = [], 0
with httpx.Client(timeout=600.0) as c:
    while True:
        r = c.get("http://127.0.0.1:8511/dramas",
                  params={"n": 500, "offset": offset})
        r.raise_for_status()
        d = r.json()
        batch = d.get("dramas") or []
        items.extend(batch)
        if not d.get("hasMore"):
            break
        offset += len(batch)
print(f"fetched {len(items)} items in {time.time() - t0:.0f}s")

titles = {str(it.get("title") or "") for it in items}
missing = [t for t in NEW if t not in titles]
print("new_dramas_found:", len(NEW) - len(missing), "/", len(NEW))
if missing:
    print("MISSING:", missing)

# 主剧指标抽检(漫剧3 + DataEye 三剧)
for t in ("游戏入侵：普攻加血一箭亿万伤屠戮神明", "归墟第一季",
          "我隐藏身份，找到了新妈妈", "傅总别装了，你老婆要掀桌", "东北话事人"):
    for it in items:
        if it.get("title") == t:
            m = it.get("metrics") or {}
            print(f"  {t!r} ep={it.get('episodes')} "
                  f"plays={m.get('plays')} likes={m.get('likes')} "
                  f"itemId={str(it.get('itemId') or '')[:24]}")
            break

# 去重核对
from collections import Counter
dup = [t for t, n in Counter(str(it.get("title") or "") for it in items).items()
       if n > 1 and t in NEW]
print("dup_in_new:", dup or "none")
