# -*- coding: utf-8 -*-
"""批5 缺失4链: worker parse_page 本地复现"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from drama85_hongguo_worker import HongguoCollector

w = HongguoCollector()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
for code in ("VNqwTNR5Br0", "sM6wBaxyDuQ", "Ly-mATadcG4", "g5d0McpSZfQ"):
    url = f"https://novelquickapp.com/s/{code}/"
    try:
        text = w.fetch_page(url, force=True)
        main, recs = w.parse_page(text)
        print(f"[{code}] OK title={main.get('title')} ep={main.get('episodes')} "
              f"recs={len(recs)}")
    except Exception as e:  # noqa: BLE001
        print(f"[{code}] FAIL {type(e).__name__}: {e}")
