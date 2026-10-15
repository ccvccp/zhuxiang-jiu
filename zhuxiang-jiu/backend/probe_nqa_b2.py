# -*- coding: utf-8 -*-
"""批2投喂: novelquickapp 17 独立链探针 + toutiaoapi 搜索链形态判定"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from urllib.parse import urlparse

import httpx

from drama85_hongguo_worker import HongguoCollector
from drama85_wukong_worker import _robots_allowed, DEFAULT_UA

LINKS = [
    ("https://novelquickapp.com/s/XnY4fzHwMko/", "归来：亏欠妻儿二十五年"),
    ("https://novelquickapp.com/s/LPtS1uqVwNw/", "将门枭虎"),
    ("https://novelquickapp.com/s/DZPxK0HG4JY/", "近身保镖"),  # 与上批 Y4o_7d-lPL0 同剧双入口
    ("https://novelquickapp.com/s/uwLijPVAZCY/", "合租凶宅"),
    ("https://novelquickapp.com/s/l-Xfm4DqpgE/", "被讹后，全村的麦子烂在地里了"),
    ("https://novelquickapp.com/s/JiIuCM3zH4s/", "分手四年，傅机长失了控"),
    ("https://novelquickapp.com/s/e-ganLWD9Ac/", "逍遥镇北王"),
    ("https://novelquickapp.com/s/MuRYAlENc6s/", "开局百年内力，我带师妹横扫武林"),
    ("https://novelquickapp.com/s/HzjHTSmW9FU/", "都重生了，这富二代我不装了"),
    ("https://novelquickapp.com/s/SB1-KmaDXIc/", "冰山美女的超级保镖"),
    ("https://novelquickapp.com/s/i46lNXPwAKs/", "盛律师的离婚前规则"),
    ("https://novelquickapp.com/s/NdFQa99qymA/", "东北婆婆，笑护全家"),
    ("https://novelquickapp.com/s/NXX8dtdOwpM/", "亿万斯年"),
    ("https://novelquickapp.com/s/ZU53Ld0xSSY/", "知许不许"),
    ("https://novelquickapp.com/s/RZNINH30WGY/", "京夜诱温"),
    ("https://novelquickapp.com/s/1osb4j-kocg/", "顾先生我不要你了"),
    ("https://novelquickapp.com/s/KJYSQfYNmls/", "好雨知时节"),  # DataEye 红果端 no=3
]

print("== part1: novelquickapp 17 links ==")
col = HongguoCollector()
ok = fail = 0
for url, expect in LINKS:
    try:
        main, recs = col.parse_page(col.fetch_page(url))
        t = (main or {}).get("title") or ""
        m = "==" if t == expect else ("~" if expect in t or t in expect else "!!")
        print(f"[OK {m}] {t!r} ep={main.get('episodes')} "
              f"plays={main.get('plays')} likes={main.get('likes')} "
              f"itemId={main.get('itemId')!r} recs={len(recs)}")
        ok += 1
    except Exception as e:
        print(f"[FAIL] {url} -> {type(e).__name__}: {e}")
        fail += 1
print(f"== nqa: total={len(LINKS)} ok={ok} fail={fail}\n")

print("== part2: toutiaoapi search link ==")
url = ("https://search5-search-hl.toutiaoapi.com/search"
       "?keyword=%E5%A5%BD%E9%9B%A8%E7%9F%A5%E6%97%B6%E8%8A%82%20%E7%9F%AD%E5%89%A7&aid=777821")
with httpx.Client(timeout=10.0, follow_redirects=True,
                  headers={"User-Agent": DEFAULT_UA}) as c:
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    r = c.get(robots_url)
    print("robots:", robots_url, r.status_code)
    print(r.text[:600] if r.status_code == 200 else "(no robots)")
    allowed = _robots_allowed(url, r.text if r.status_code == 200 else None)
    print("robots_allowed:", allowed)
    if allowed:
        r2 = c.get(url)
        print("page status:", r2.status_code, "len:", len(r2.text))
        print("head:", repr(r2.text[:300]))
