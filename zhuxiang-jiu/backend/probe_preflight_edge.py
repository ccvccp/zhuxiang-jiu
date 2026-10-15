# -*- coding: utf-8 -*-
"""预检层边路单测: ①启发式(空域名库测 bkdjtj) ②robots 根级禁(v.m.chenzhongtech.com)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from drama85_preflight import load_farm_fp, load_pool, preflight

farm = load_farm_fp()
pool = load_pool()

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}) as c:
    # ① 启发式: 清空 domains/names 模拟"指纹库没收录的新农场"
    farm_empty = {"domains": [], "names": []}
    r = preflight("https://www.bkdjtj.com.cn/", farm_empty, pool, c)
    print(f"① 启发式(bkdjtj, 空指纹库): [{r['verdict']}] {r['tag']}")
    for x in r["reasons"]:
        print(f"   {x}")

    # ② robots 根级禁(快手落地域, 已知 UA:* Disallow: /)
    r2 = preflight("https://v.m.chenzhongtech.com/", farm, pool, c)
    print(f"\n② robots 红线(v.m.chenzhongtech.com): [{r2['verdict']}] {r2['tag']}")
    for x in r2["reasons"]:
        print(f"   {x}")
