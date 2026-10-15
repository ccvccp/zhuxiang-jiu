# -*- coding: utf-8 -*-
"""批3投喂 21 链探针: 剧名对照文案 + loader_key(manju/duanju) + itemId 去重预判"""
import html as _h
import io
import json
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from sqlalchemy import select
from db import get_session
from models import Drama

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LINKS = [  # (码, 文案剧名或 None)
    ("wOPXWI2L6jU", None),
    ("BqWUIOnEdr8", "万妖图录传第一季"),
    ("X94f-oplEL8", "谁说没灵根不能修仙的？之无灵证道"),
    ("e9KmRW8QCl0", "聚宝仙盆之杂灵根才是真BOSS"),
    ("gS1FtCCms2w", "网游：毒帝无双，我的毒能屠神"),
    ("4yOoKpRA4fM", "重回八一从打猎开始发家"),
    ("7ZUV6t03vp4", "谁让这个悍匪修仙的！"),
    ("7d3rDbLpcsM", "山海藏墟，无眼窥天"),
    ("-4uDix-t6Fo", "风雪遇良缘"),
    ("mH0m-VWGals", "母凭子贵之我送两个孩子上清北"),
    ("PwRRXWP6Mbg", "这一次，女婿他绝不低头"),
    ("zbf-c3P0Tos", "掌生2：嫡女国师"),
    ("epUmp-LfRD8", "爱在风起燕舞时"),
    ("kjD42_x8X2I", "正在搬砖，被宋小姐拉去闪婚了"),
    ("LgU1nXB4hfU", "反相之地"),
    ("DNOv1ohjUFE", "归墟第一季"),
    ("ODOdC88Mq1Y", "我李长生重回八零，赶山狩猎养妻女"),
    ("xOcVWKHHfqY", "重生八五，靠购物返现系统赢麻了"),
    ("P9RY0qGW0FM", "装穷参加同学会，合照把我给裁了"),
    ("BNsFyJUpk7o", "重回1988，我从倒货开始养家致富"),
    ("SAgUvsmpiEE", "持械入宋：第一季"),
]
FN = re.compile(r'data-fn-args="([^"]+)"')

with get_session() as s:
    pool = set(s.execute(select(Drama.drama_name)).scalars())
    items = s.execute(select(Drama.item_id).where(Drama.item_id.isnot(None))).scalars().all()
pool_ids = set(items)
print(f"池 {len(pool)} 部 / itemId {len(pool_ids)} 个")

ok = dup_code = 0
results = []
with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for code, claim in LINKS:
        url = f"https://novelquickapp.com/s/{code}/"
        time.sleep(1.0)
        try:
            r = c.get(url)
            text = r.text
        except Exception as e:  # noqa: BLE001
            print(f"[失败] {code}: {type(e).__name__}")
            continue
        ttl = re.search(r"<title>(.*?)</title>", text, re.S)
        title = ttl.group(1).strip() if ttl else ""
        m_name = re.search(r"《([^》]+)》", title)
        name = m_name.group(1) if m_name else title[:30]
        kind, sid, iid = "", None, None
        for raw in FN.findall(text):
            try:
                arr = json.loads(_h.unescape(raw))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(arr, list) and arr and "share" in str(arr[0]):
                kind = "manju" if "animation" in str(arr[0]) else (
                    "duanju" if "video-list" in str(arr[0]) else str(arr[0])[:20])
                pd = arr[2] if len(arr) > 2 else {}
                sd = (pd or {}).get("series_data") or {}
                sid = sd.get("series_id")
                iid = (pd or {}).get("used_chapter_id")
                break
        in_pool = name in pool
        mark = "★池内" if in_pool else ""
        claim_ok = "?" if claim is None else ("✓" if claim in name or name in claim else "✗")
        results.append((code, name, kind, iid, in_pool, claim_ok))
        ok += 1
        print(f"[{code:>14}] {kind:<6} {name} | 文案{claim_ok} {mark} "
              f"iid={iid}")

print(f"\n探针 {ok}/21 | 池内同名: {sum(1 for r in results if r[4])} | "
      f"文案不符: {[r[0] for r in results if r[5] == '✗']}")
