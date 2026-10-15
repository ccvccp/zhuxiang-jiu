# -*- coding: utf-8 -*-
"""定位 series_title / &quot; 转义数据的真实容器(非 script 区域)"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

text = open("nqa_sample.html", encoding="utf-8").read()
print("total_len:", len(text))

# 1) series_title 所有出现位置 + 上下文
print("\n== series_title occurrences:")
for m in re.finditer("series_title", text):
    ctx = text[max(0, m.start() - 200):m.start() + 200]
    print(f"@{m.start()} ctx={ctx!r}\n")

# 2) 含 &quot; 的非 script 区段: 按 HTML 结构切
script_spans = [(m.start(), m.end()) for m in
                re.finditer(r"<script\b[^>]*>.*?</script>", text, re.S | re.I)]


def in_script(pos):
    return any(a <= pos < b for a, b in script_spans)


print("\n== &quot; regions outside scripts (merged spans):")
hits = [m.start() for m in re.finditer("&quot;", text)
        if not in_script(m.start())]
print("count:", len(hits))
if hits:
    # 合并邻近位置(<2000 间隔)成区段
    spans = []
    s = p = hits[0]
    for h in hits[1:]:
        if h - p > 2000:
            spans.append((s, p))
            s = h
        p = h
    spans.append((s, p))
    for a, b in spans:
        print(f"\n-- span @{a}..{b} len={b - a}")
        # 向前找最近的标签开头
        head = text[max(0, a - 300):a]
        tail = text[b:b + 200]
        print("HEAD:", repr(head[-200:]))
        print("TAIL:", repr(tail[:150]))

# 3) hidden template / streaming 标记
print("\n== streaming markers:")
for pat in (r'<div[^>]*hidden[^>]*>', r"<template", r'id="S:\d"', r'id="B:\d"'):
    for m in list(re.finditer(pat, text))[:5]:
        ctx = text[m.start():m.start() + 150]
        print(f"[{pat}] @{m.start()} {ctx!r}")
