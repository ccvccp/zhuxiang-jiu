# -*- coding: utf-8 -*-
"""解析 data-fn-args 属性 → 还原 pageData 结构(Modern.js streaming hydration)"""
import html
import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

text = open("nqa_sample.html", encoding="utf-8").read()

# 所有 data-fn-args script 标签
tags = re.findall(
    r'<script\b[^>]*data-fn-name="r"[^>]*data-fn-args="([^"]*)"[^>]*>',
    text)
print("data-fn-args tags:", len(tags))
for i, raw in enumerate(tags):
    u = html.unescape(raw)
    print(f"\n== tag[{i}] unescape_len={len(u)} head200={u[:200]!r}")
    try:
        arr = json.loads(u)
    except json.JSONDecodeError as e:
        print("   json error:", e)
        continue
    print("   outer type:", type(arr).__name__, "len:", len(arr) if isinstance(arr, list) else "-")
    if isinstance(arr, list):
        print("   arg0:", arr[0])
        arg1 = arr[1] if len(arr) > 1 else None
        print("   arg1 type:", type(arg1).__name__)
        if isinstance(arg1, str):
            try:
                inner = json.loads(arg1)
                print("   arg1(str) -> json OK, type:", type(inner).__name__)
                if isinstance(inner, dict):
                    print("   inner keys:", list(inner.keys()))
                    pd = inner.get("pageData") if "pageData" in inner else inner
                    if isinstance(pd, dict):
                        print("   pageData keys:", list(pd.keys())[:20])
                        sd = pd.get("series_data")
                        if isinstance(sd, dict):
                            print("   series_data keys:", list(sd.keys())[:25])
                            for k in ("title", "serial_count", "series_play_cnt",
                                      "like_count", "category"):
                                print(f"      {k} = {sd.get(k)!r}")
                        rs = pd.get("recommend_series")
                        if isinstance(rs, list):
                            print("   recommend_series n =", len(rs))
                            if rs:
                                r0 = rs[0]
                                print("   rec0 keys:", list(r0.keys())[:20])
                                for k in ("series_title", "episode_cnt",
                                          "series_play_cnt", "like_cnt"):
                                    print(f"      {k} = {r0.get(k)!r}")
            except json.JSONDecodeError as e:
                print("   arg1 inner json error:", e)
        elif isinstance(arg1, dict):
            print("   arg1 keys:", list(arg1.keys()))
