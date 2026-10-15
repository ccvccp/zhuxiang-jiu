# -*- coding: utf-8 -*-
"""批3 补测: ODOdC88Mq1Y 重试 + 21 码对照现有 .env 池去重"""
import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ① 重试超时链
for attempt in range(3):
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True,
                          headers={"User-Agent": UA}) as c:
            r = c.get("https://novelquickapp.com/s/ODOdC88Mq1Y/")
        ttl = re.search(r"<title>(.*?)</title>", r.text, re.S)
        print(f"重试{attempt+1} OK: title={ttl.group(1).strip()[:60] if ttl else '-'} "
              f"len={len(r.text)/1024:.0f}KB "
              f"剧名={re.findall(r'《([^》]+)》', r.text)[:1]}")
        break
    except Exception as e:  # noqa: BLE001
        print(f"重试{attempt+1} 失败: {type(e).__name__}")
        time.sleep(2.0)

# ② 分享码去重对照
env = open(".env.example", encoding="utf-8").read()
m = re.search(r"HONGGUO_SHARE_URLS=(.*)", env)
existing = m.group(1) if m else ""
codes = ["wOPXWI2L6jU", "BqWUIOnEdr8", "X94f-oplEL8", "e9KmRW8QCl0",
         "gS1FtCCms2w", "4yOoKpRA4fM", "7ZUV6t03vp4", "7d3rDbLpcsM",
         "-4uDix-t6Fo", "mH0m-VWGals", "PwRRXWP6Mbg", "zbf-c3P0Tos",
         "epUmp-LfRD8", "kjD42_x8X2I", "LgU1nXB4hfU", "DNOv1ohjUFE",
         "ODOdC88Mq1Y", "xOcVWKHHfqY", "P9RY0qGW0FM", "BNsFyJUpk7o",
         "SAgUvsmpiEE"]
dup = [c for c in codes if f"/s/{c}/" in existing or f"/s/{c}" in existing]
print(f"现有池链接数: {len(existing.split(','))} | 21 码中已存在: {dup or '无'}")
