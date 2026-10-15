# -*- coding: utf-8 -*-
"""归因验证第一步(零成本): DataEye 原文全文精读复核
- 全文所有《》剧名 + 所在榜单段落归属(DE_BOARDS 分段)
- 所有名次句式变体(TOP\d+/前[十十五\d]+/位列/跻身/排名/第N位/登顶/上榜)
- 对照已入库 16 条快照: 找文中提及但未入快照的名次信息
- 重点: 莞东风云/好雨知时节是否在总榜段落出现; 第4-7/9-14名是否有剧名
"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from drama85_media_crawler import DE_BOARDS, fetch_text, de_parse

URL = "https://m.thepaper.cn/newsDetail_forward_34234356"
FOCUS = ("莞东风云", "好雨知时节", "东北话事人", "步步登阶",
         "意外中奖", "傅总别装了", "督军府", "二嫁有喜", "我隐藏身份")

text = fetch_text(URL)
print("text len:", len(text))

# 1) baseline: 现解析器结果
base = de_parse(text)
base_keys = {(it["rank_type"], it["name"]) for it in base}
print("\n== baseline de_parse:", len(base), "items ==")

# 2) 榜单分段归属
marks = []
for pat, rtype, label in DE_BOARDS:
    m = pat.search(text)
    if m:
        marks.append((m.start(), rtype, label))
marks.sort()
print("\n== board segments ==")
for i, (pos, rtype, label) in enumerate(marks):
    end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
    seg = text[pos:end]
    names = [re.sub(r"\s+", "", m.group(1))
             for m in re.finditer(r"《\s*([^《》]{2,25})\s*》", seg)]
    from collections import Counter
    uniq = Counter(names)
    print(f"[{label}] span={pos}..{end} len={len(seg)} "
          f"distinct_names={len(uniq)}: {dict(uniq)}")

# 3) 全文名次句式变体扫描(不限 de_parse 的 RANK_AFTER 集)
print("\n== rank-expression sweep (whole text) ==")
RANK_PATS = [
    (r"TOP\s*(\d+)", "TOPn"),
    (r"前\s*([一二三四五六七八九十\d]{1,3})\s*[强名位名次]*", "前N"),
    (r"第\s*([一二三四五六七八九十\d]{1,3})\s*(?:位|名)", "第N"),
    (r"(?:位列|跻身|排名|位列第)\s*([一二三四五六七八九十\d]{1,3})", "位列N"),
    (r"登顶", "登顶"),
    (r"(?:上榜|蝉联|霸榜|闯入|挺进)", "上榜类"),
]
for pat, tag in RANK_PATS:
    for m in re.finditer(pat, text):
        ctx = text[max(0, m.start() - 55):m.end() + 75].replace("\n", " ")
        print(f"[{tag}] {m.group(0)!r} @{m.start()} ctx={ctx!r}")

# 4) 焦点剧名全文出现位置(不限段落)
print("\n== focus names: all occurrences ==")
for kw in FOCUS:
    for m in re.finditer(re.escape(kw), text):
        ctx = text[max(0, m.start() - 70):m.end() + 90].replace("\n", " ")
        print(f"[{kw}] @{m.start()} ctx={ctx!r}")

# 5) 增量发现: 总榜段落内出现但 baseline 未收的剧名
print("\n== gap check (board seg names vs baseline) ==")
for i, (pos, rtype, label) in enumerate(marks):
    end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
    seg = text[pos:end]
    for m in re.finditer(r"《\s*([^《》]{2,25})\s*》", seg):
        name = re.sub(r"\s+", "", m.group(1))
        inbase = (rtype, name) in base_keys
        # 找该出现的子句
        tail = re.search(r"[。；]", seg[m.end():])
        after = seg[m.end():m.end() + (tail.start() if tail else 90)]
        before = seg[max(0, m.start() - 16):m.start()]
        if not inbase:
            print(f"  NEW [{label}] {name!r} clause={before}《》{after[:70]!r}")
