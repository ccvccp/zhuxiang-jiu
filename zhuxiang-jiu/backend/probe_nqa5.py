# -*- coding: utf-8 -*-
"""novelquickapp 22 链全量探针: 直调 HongguoCollector.fetch_page+parse_page
(robots 检查 + 1 QPS 限速均走 worker 既有纪律)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from drama85_hongguo_worker import HongguoCollector

LINKS = [
    ("https://novelquickapp.com/s/FxZyqLh7jrw/", "游戏入侵：普攻加血一箭亿万伤屠戮神明"),
    ("https://novelquickapp.com/s/TKo_WMThIvM/", "归墟第一季"),
    ("https://novelquickapp.com/s/9Cjn8Yn6bQ0/", "反相之地"),
    ("https://novelquickapp.com/s/vFJMh3V2nuI/", "持械入宋：第一季"),
    ("https://novelquickapp.com/s/eeJCX8VA8QA/", "垫底漫画家：靠考古一战成名"),
    ("https://novelquickapp.com/s/LSRBiZh77RU/", "困兽之战"),
    ("https://novelquickapp.com/s/T8e4bSBhNwk/", "万妖图录传第一季"),
    ("https://novelquickapp.com/s/D1uQtraqC0g/", "聚宝仙盆之杂灵根才是真BOSS"),
    ("https://novelquickapp.com/s/oW0bERcBdNg/", "网游：毒帝无双，我的毒能屠神"),
    ("https://novelquickapp.com/s/-9BCJAcmtjY/", "公司扣我年终奖，我不忍了"),
    ("https://novelquickapp.com/s/PlJnQ5trCiQ/", "母凭子贵之我送两个孩子上清北"),
    ("https://novelquickapp.com/s/Lrdd-mlM-w0/", "重生之嫡女归来"),
    ("https://novelquickapp.com/s/iK7ZcK9CEhQ/", "从东北来的她"),
    ("https://novelquickapp.com/s/uMzsTzUXnJA/", "爱在风起燕舞时"),
    ("https://novelquickapp.com/s/CigcS96PufM/", "弟媳表演的孝心，我笑了"),
    ("https://novelquickapp.com/s/BiPyOxNmBmo/", "风雪遇良缘"),
    ("https://novelquickapp.com/s/8MIx_MLTUnU/", "我隐藏身份，找到了新妈妈"),
    ("https://novelquickapp.com/s/_n53ljdkm38/", "傅总别装了，你老婆要掀桌"),
    ("https://novelquickapp.com/s/uYCDr2lHA_M/", "奔头儿"),
    ("https://novelquickapp.com/s/9EB9Jkj3vFM/", "东北话事人"),
    ("https://novelquickapp.com/s/ZzD1i4T6pA0/", "影后的剧本出了错"),
    ("https://novelquickapp.com/s/Y4o_7d-lPL0/", "近身保镖"),
]

col = HongguoCollector()
ok = fail = title_mismatch = 0
for url, expect in LINKS:
    try:
        text = col.fetch_page(url)
        main, recs = col.parse_page(text)
        t = (main or {}).get("title") or ""
        match = "==" if t == expect else ("~" if expect in t or t in expect else "!!")
        if t == expect or expect in t or t in expect:
            title_mismatch_ok = True
        else:
            title_mismatch += 1
        print(f"[OK {match}] {t!r} ep={main.get('episodes')} "
              f"plays={main.get('plays')} likes={main.get('likes')} "
              f"itemId={main.get('itemId')!r} recs={len(recs)}")
        ok += 1
    except Exception as e:
        print(f"[FAIL] {url} -> {type(e).__name__}: {e}")
        fail += 1

print(f"\n== total={len(LINKS)} ok={ok} fail={fail} title_mismatch={title_mismatch}")
