# -*- coding: utf-8 -*-
"""5 新链深挖: ①《》剧名提取→池内 297 部对照 ②djtj7 双域结构对照"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from sqlalchemy import select
from db import get_session
from models import Drama

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LINKS = {
    "aayow": "https://aayow.cn/",
    "bkdjtj": "https://www.bkdjtj.com.cn/",
    "aijee": "https://aijee.cn/",
    "dsdjtj": "https://www.dsdjtj.com.cn/",
    "djtj7cn": "https://www.djtj7.com.cn/",
}

# 池内全量剧名
with get_session() as s:
    pool = set(s.execute(select(Drama.drama_name)).scalars())
print(f"池内剧名 {len(pool)} 部(对照基准)")

names_all: dict[str, list[str]] = {}
with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for tag, url in LINKS.items():
        t = c.get(url).text
        names = []
        for m in re.finditer(r"《([^》]{2,20})》", t):
            n = m.group(1).strip()
            if n not in names:
                names.append(n)
        names_all[tag] = names
        hit = [n for n in names if n in pool]
        print(f"\n[{tag}] 《》剧名 {len(names)} 个, 池内命中 {len(hit)}: {hit[:5]}")
        print(f"  全部: {names[:15]}")

    # djtj7 双域对照: .com(已接入, rank-no 六平台榜) vs .com.cn
    print("\n== djtj7 双域对照")
    for tag, url in (("djtj7.com(已接入)", "https://www.djtj7.com/"),
                     ("djtj7.com.cn(新)", "https://www.djtj7.com.cn/")):
        t = c.get(url).text
        ttl = re.search(r"<title>(.*?)</title>", t, re.S)
        n_rankno = len(re.findall(r'class="rank-no"', t))
        n_li = len(re.findall(r"<li[^>]*>", t))
        body_hash = hash(re.sub(r"\s+", "", t)) % 10**8
        print(f"  {tag}: title={ttl.group(1).strip()[:40]!r} "
              f"rank-no={n_rankno} li={n_li} len={len(t)} bodyhash={body_hash}")

    # 双域剧名重合度
    t_com = c.get("https://www.djtj7.com/").text
    n_com = {m.group(1).strip() for m in re.finditer(r"《([^》]{2,20})》", t_com)}
    n_cn = set(names_all["djtj7cn"])
    inter = n_com & n_cn
    print(f"  双域《》剧名重合: {len(inter)}/{len(n_com | n_cn)} {sorted(inter)[:8]}")
