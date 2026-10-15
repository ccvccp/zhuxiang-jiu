# -*- coding: utf-8 -*-
"""novelquickapp.com(红果新域) 页面形态解剖探针
用法:
  python probe_nqa.py fetch   # 抓首页样本存 nqa_sample.html(带 302 跟随)
  python probe_nqa.py anatomy # 本地解剖: script 块归属 + loaderData/pageData 容器定位
"""
import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

SAMPLE = "nqa_sample.html"
SHARE = "https://novelquickapp.com/s/FxZyqLh7jrw/"
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Mobile Safari/537.36")


def fetch():
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True,
                      timeout=15.0) as c:
        r = c.get(SHARE)
    print("final_url:", r.url)
    print("status:", r.status_code, "len:", len(r.text))
    with open(SAMPLE, "w", encoding="utf-8") as f:
        f.write(r.text)
    print("saved ->", SAMPLE)


def anatomy():
    text = open(SAMPLE, encoding="utf-8").read()
    print("total_len:", len(text))

    # 1) 所有 script 块归属
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>",
                         text, re.S | re.I)
    print("\n== script blocks:", len(scripts))
    for idx, s in enumerate(scripts):
        marks = []
        for kw in ("loaderData", "pageData", "series_data",
                   "series_title", "recommend_series", "&quot;"):
            if kw in s:
                marks.append(kw)
        head = s.strip()[:100].replace("\n", " ")
        print(f"[{idx:02d}] len={len(s):7d} marks={marks} head={head!r}")

    # 2) 非脚本区出现 loaderData/pageData(DOM 属性形态?)
    script_spans = [(m.start(), m.end()) for m in
                    re.finditer(r"<script\b[^>]*>.*?</script>", text, re.S | re.I)]

    def in_script(pos):
        return any(a <= pos < b for a, b in script_spans)

    print("\n== keyword occurrences outside <script>:")
    for kw in ("loaderData", "pageData"):
        for m in re.finditer(re.escape(kw), text):
            if not in_script(m.start()):
                ctx = text[max(0, m.start() - 150):m.start() + 150]
                print(f"[{kw}] @{m.start()} ctx={ctx!r}")

    # 3) script 块内 loaderData 出现处上下文(找容器前缀: window.xxx = 或对象键)
    print("\n== loaderData context inside scripts:")
    for idx, s in enumerate(scripts):
        for m in re.finditer("loaderData", s):
            ctx = s[max(0, m.start() - 120):m.start() + 160]
            print(f"[blk{idx}] @{m.start()} ctx={ctx!r}")

    # 4) 含 &quot; 的块: unescape 后试 json 提取
    print("\n== &quot; blocks unescape + json probe:")
    for idx, s in enumerate(scripts):
        if "&quot;" not in s:
            continue
        u = s.replace("&quot;", '"').replace("&amp;", "&") \
             .replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">")
        found = []
        for kw in ("series_data", "series_title", "recommend_series"):
            if kw in u:
                found.append(kw)
        print(f"[blk{idx}] len={len(s)} unescape_found={found}")
        # 找 unescape 后最大的 {...} JSON 候选
        for m in re.finditer(r'"(?:loaderData|pageData)"\s*:', u):
            ctx = u[max(0, m.start() - 100):m.start() + 120]
            print(f"   key@{m.start()} ctx={ctx!r}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "anatomy"
    if mode == "fetch":
        fetch()
    else:
        anatomy()
