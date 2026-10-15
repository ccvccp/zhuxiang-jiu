# -*- coding: utf-8 -*-
"""百度AI投喂 5 新链探针: robots + 首页形态 + 榜单结构线索"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LINKS = [
    "https://aayow.cn/",
    "https://www.bkdjtj.com.cn/",
    "https://aijee.cn/",
    "https://www.dsdjtj.com.cn/",
    "https://www.djtj7.com.cn/",
]

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for url in LINKS:
        print("=" * 70)
        print(url)
        try:
            r = c.get(url)
            final = str(r.url)
            redir = f" → {final}" if final.rstrip("/") != url.rstrip("/") else ""
            print(f"  GET {r.status_code} ({len(r.text)/1024:.0f}KB){redir}")
        except Exception as e:  # noqa: BLE001
            print(f"  GET 失败: {type(e).__name__}: {str(e)[:120]}")
            continue
        try:
            rb = c.get(url.rstrip("/") + "/robots.txt")
            if rb.status_code == 200:
                lines = [l for l in rb.text.splitlines()
                         if l.strip() and not l.startswith("#")][:8]
                print(f"  robots 200: {lines}")
            else:
                print(f"  robots {rb.status_code} (404 按允许)")
        except Exception as e:  # noqa: BLE001
            print(f"  robots 失败: {type(e).__name__}")
        t = r.text
        ttl = re.search(r"<title>(.*?)</title>", t, re.S)
        print(f"  title: {ttl.group(1).strip()[:80] if ttl else '-'}")
        n_ju = len(re.findall(r"《[^》]{2,20}》", t))
        n_rank = t.count("rank") + t.count("排名") + t.count("热度")
        print(f"  《》剧名 {n_ju} 处 / rank|排名|热度 {n_rank} 处")
        # 结构样例: 首个含剧名的上下文
        m = re.search(r"《[^》]{2,20}》", t)
        if m:
            ctx = re.sub(r"\s+", " ", t[max(0, m.start()-100):m.start()+120])
            print(f"  剧名上下文: {ctx[:200]!r}")
        icp = re.search(r"[京粤沪浙苏鲁湘鄂闽豫]ICP[备案证]?\s*号?\s*[：:]*\s*\d+号", t)
        print(f"  ICP备案: {'有 ' + icp.group(0) if icp else '无'}")
