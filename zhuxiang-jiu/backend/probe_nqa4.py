# -*- coding: utf-8 -*-
"""验证 arg2(value) 结构 + 短剧链(非漫剧)落地页形态是否同构"""
import html
import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Mobile Safari/537.36")


def parse_fn_args(text: str):
    """返回 [(loader_key, prop_key, value), ...]"""
    out = []
    for raw in re.findall(
            r'<script\b[^>]*data-fn-name="r"[^>]*data-fn-args="([^"]*)"[^>]*>', text):
        try:
            arr = json.loads(html.unescape(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(arr, list) and len(arr) >= 3:
            out.append((arr[0], arr[1], arr[2]))
    return out


def report(url: str):
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True,
                      timeout=15.0) as c:
        r = c.get(url)
    print("url:", url)
    print("final:", str(r.url)[:120])
    print("status:", r.status_code, "len:", len(r.text))
    triples = parse_fn_args(r.text)
    print("fn-args triples:", len(triples))
    for lk, pk, val in triples:
        print(f"  loader_key={lk!r} prop_key={pk!r} val_type={type(val).__name__}")
        if isinstance(val, dict):
            print("    val keys:", list(val.keys())[:15])
            sd = val.get("series_data")
            if isinstance(sd, dict):
                print("    series_data keys:", list(sd.keys()))
                for k in ("title", "series_id", "serial_count",
                          "series_play_cnt", "like_count", "category"):
                    print(f"      {k} = {sd.get(k)!r}")
            rs = val.get("recommend_series")
            if isinstance(rs, list):
                print("    recommend_series n =", len(rs))
                if rs:
                    print("    rec0:", {k: rs[0].get(k) for k in
                                       ("series_title", "episode_cnt", "like_cnt")})
    print()


if __name__ == "__main__":
    # 1) 漫剧样本(本地)
    local = open("nqa_sample.html", encoding="utf-8").read()
    print("== local (manju sample)")
    for lk, pk, val in parse_fn_args(local):
        print(f"  loader_key={lk!r} prop_key={pk!r}")
        if isinstance(val, dict):
            print("    val keys:", list(val.keys()))
            sd = val.get("series_data")
            if isinstance(sd, dict):
                print("    series_data keys:", list(sd.keys()))
                for k in ("title", "series_id", "serial_count",
                          "series_play_cnt", "like_count"):
                    print(f"      {k} = {sd.get(k)!r}")
            rs = val.get("recommend_series")
            if isinstance(rs, list):
                print("    recommend_series n =", len(rs))

    # 2) 短剧链验证(远程)
    print("\n== remote duanju sample")
    report("https://novelquickapp.com/s/-9BCJAcmtjY/")
