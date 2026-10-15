# -*- coding: utf-8 -*-
"""缺 JSON-LD 的 16 部详情页形态探测(帐中香样本)"""
import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

slug = "/zh-cn/dramas/zhang-zhong-xiang"
with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    r = c.get("https://shortdramacast.com" + slug)
    d = r.text
    print(f"status={r.status_code} final_url={r.url} len={len(d)/1024:.0f}KB")
    ttl = re.search(r"<title>(.*?)</title>", d, re.S)
    print("title:", ttl.group(1).strip()[:90] if ttl else "-")
    n_ld = len(re.findall(r'application/ld\+json', d))
    print("ld+json 块数:", n_ld)
    for m in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', d, re.S):
        try:
            obj = json.loads(m.group(1).strip())
        except json.JSONDecodeError as e:
            print("  [解析失败]", str(e)[:60], m.group(1)[:120])
            continue
        for o in (obj if isinstance(obj, list) else [obj]):
            print(f"  @type={o.get('@type')} name={str(o.get('name'))[:50]!r} "
                  f"keys_n={len(o)}")
    if not n_ld:
        # 找页面里的结构性线索
        for kw in ("TVSeason", "TVSeries", "genre", "season", "__NEXT_DATA__",
                   "Not Found", "404"):
            nn = d.count(kw)
            if nn:
                mm = re.search(kw, d)
                ctx = re.sub(r"\s+", " ", d[max(0, mm.start()-60):mm.start()+100])
                print(f"kw={kw!r} n={nn} ctx={ctx[:150]!r}")
