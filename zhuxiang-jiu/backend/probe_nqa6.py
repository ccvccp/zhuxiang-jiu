# -*- coding: utf-8 -*-
"""第 1 链偶发失败复查: 连抓 3 次看形态稳定性"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from drama85_hongguo_worker import HongguoCollector, _extract_router_data, _extract_fn_args_page

url = "https://novelquickapp.com/s/FxZyqLh7jrw/"
col = HongguoCollector()
for i in range(3):
    try:
        text = col.fetch_page(url, force=True)
        r = _extract_router_data(text)
        f = _extract_fn_args_page(text)
        print(f"try{i}: len={len(text)} router={'Y' if r else 'N'} "
              f"fn_args={'Y' if f else 'N'}")
        if f:
            main, recs = col.parse_page(text)
            print("   title:", (main or {}).get("title"),
                  "ep:", (main or {}).get("episodes"),
                  "plays:", (main or {}).get("plays"))
            break
        # 看 S:1 hidden div 是否存在
        print("   has S:1 div:", '<div hidden id="S:1">' in text,
              "has data-fn-args:", 'data-fn-args=' in text)
    except Exception as e:
        print(f"try{i}: EXC {type(e).__name__}: {e}")
