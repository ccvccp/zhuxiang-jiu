# -*- coding: utf-8 -*-
"""批5 缺失4链复探: 页面形态确认"""
import html
import io
import json
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
for code in ("VNqwTNR5Br0", "sM6wBaxyDuQ", "Ly-mATadcG4", "g5d0McpSZfQ"):
    time.sleep(1)
    try:
        r = httpx.get(f"https://novelquickapp.com/s/{code}/", timeout=20,
                      follow_redirects=True, headers={"User-Agent": UA})
        t = r.text
        ttl = re.search(r"<title>(.*?)</title>", t, re.S)
        fn = re.findall(r'data-fn-args="([^"]+)"', t)
        kinds = [str(json.loads(html.unescape(x))[0])[:30] for x in fn if x][:2]
        print(code, "status", r.status_code, "len", len(t),
              "title", (ttl.group(1)[:40] if ttl else None), "fn", kinds)
    except Exception as e:  # noqa: BLE001
        print(code, "ERR", e)
