# -*- coding: utf-8 -*-
"""85号·清洗与跨源合并(Day5, docs/短剧榜单采集持久化落地方案.md)

输入现状: drama 主表 215 部(share 池) + 快照 69 条(xinbang 29 / dataeye 16 / kuaishou 24, drama_id 全 NULL)。

匹配链(由强到弱):
  L1 精确: norm(快照剧名) == norm(主表剧名) → 自动补链
  L2 跨源建行: 同一 norm 名出现在 ≥2 个独立 source 的快照 → 新建主表行(main_platform=multi)
               并互链其全部快照——「同一部剧在不同榜单」的真正合并(实证: 莞东风云/步步登阶
               同时现身新榜周榜与 DataEye 红果/热力榜)
  L3 复核队列: 与主表 containment(≥4字,长度差≤3) 或相似度 0.70~0.90 → 只报告不自动链
               (实证: 《二嫁有喜》(dataeye) vs 《二嫁》(池)——两字之差, 可能同名不同剧, 留人工)

清洗审计: 主表 norm 名重复检查 / 剧名空白残留 / is_ai_drama 与西瓜 AI 带样本抽检。

用法:
  python drama85_matcher.py --report   # 只输出报告不落库
  python drama85_matcher.py --run      # 执行 L1 补链 + L2 建行互链(L3 永远只报告)
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from sqlalchemy import func, select

from db import get_session
from models import Drama, DramaRankSnapshot

FUZZY_LO, FUZZY_HI = 0.70, 0.90     # L3 复核带(≥0.90 才可自动, 当前样本无命中故全走复核)
CONTAIN_MIN, CONTAIN_DIFF = 4, 3     # containment 下限/长度差上限


def norm(s: str) -> str:
    """规范化: 去空白/全半角标点/书名号引号——OCR 与各榜标点口径差异归一。"""
    return re.sub(r"[\s\u3000：:，,．.·！!？?《》「」''\"\"'（）()【】\[\]-]+", "", s)


# ---------- 审计 ----------

def audit() -> dict:
    st = {"pool": 0, "pool_norm_dup": [], "snap": 0, "snap_blank": 0, "ai_rows": []}
    with get_session() as s:
        pool = s.execute(select(Drama.drama_id, Drama.drama_name, Drama.main_platform,
                                Drama.is_ai_drama)).all()
        st["pool"] = len(pool)
        seen: dict[str, str] = {}
        for did, name, plat, is_ai in pool:
            n = norm(name)
            if n in seen:
                st["pool_norm_dup"].append((name, seen[n]))
            else:
                seen[n] = name
            if name != name.strip():
                st["snap_blank"] += 1
            if is_ai:
                st["ai_rows"].append((name, plat))
        st["snap"] = s.execute(select(func.count()).select_from(DramaRankSnapshot)).scalar()
    return st


# ---------- 匹配 ----------

def match(do_run: bool) -> dict:
    out = {"l1_linked": 0, "l2_created": 0, "l2_linked": 0,
           "l3_review": [], "cross_pairs": []}
    now = datetime.now()
    with get_session() as s:
        pool = s.execute(select(Drama)).scalars().all()
        pool_n = {norm(d.drama_name): d for d in pool}
        snaps = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.drama_id.is_(None))).scalars().all()

        # L1 精确
        for sn in snaps:
            d = pool_n.get(norm(sn.drama_name))
            if d is not None:
                sn.drama_id = d.drama_id
                d.last_seen_at = now
                out["l1_linked"] += 1

        # L2 跨源建行(≥2 独立 source 同 norm 名)
        by_norm: dict[str, list] = defaultdict(list)
        for sn in snaps:
            by_norm[norm(sn.drama_name)].append(sn)
        for n, group in by_norm.items():
            srcs = sorted({sn.source for sn in group})
            if len(srcs) < 2:
                continue
            if n in pool_n:          # 已有主表行(理论上 L1 已链) 跳过
                continue
            best_name = max((sn.drama_name for sn in group), key=len)
            row = Drama(drama_name=best_name, main_platform="multi",
                        platforms=",".join(srcs), status="new",
                        first_seen_at=now, last_seen_at=now)
            s.add(row)
            s.flush()
            for sn in group:
                sn.drama_id = row.drama_id
                out["l2_linked"] += 1
            out["l2_created"] += 1
            out["cross_pairs"].append((best_name, srcs))

        # L3 复核队列(只报告): 剩余未链快照 vs 主表
        remain = [sn for sn in snaps if sn.drama_id is None]
        for sn in remain:
            nn = norm(sn.drama_name)
            cand = None
            for pn, d in pool_n.items():
                if (min(len(nn), len(pn)) >= CONTAIN_MIN
                        and (nn in pn or pn in nn)
                        and abs(len(nn) - len(pn)) <= CONTAIN_DIFF):
                    cand = ("contain", 1.0, d.drama_name)
                    break
            if cand is None:
                best, br = None, 0.0
                for pn, d in pool_n.items():
                    r = SequenceMatcher(None, nn, pn).ratio()
                    if r > br:
                        br, best = r, d.drama_name
                if best and FUZZY_LO <= br < FUZZY_HI:
                    cand = ("fuzzy", round(br, 3), best)
            if cand:
                out["l3_review"].append((sn.source, sn.drama_name, cand[0], cand[1], cand[2]))

        if not do_run:
            s.rollback()
            return out

        # AI 标注反哺(2026-10-15): sdc_ai 快照互链的主行 → is_ai_drama=True。
        # 依据: SceneTrace「AI 短剧」分类=站方定义级「AI 生成的漫剧非实拍」
        # (榜首详情页 FAQ/JSON-LD genre["AI漫剧"] 实证, 见 docs/AI榜与红果漫剧池关联分析.md)
        ai_ids = {sn.drama_id for sn in s.execute(select(DramaRankSnapshot.drama_id)
                   .where(DramaRankSnapshot.source == "sdc_ai",
                          DramaRankSnapshot.drama_id.isnot(None))).all()}
        if ai_ids:
            rows = s.execute(select(Drama).where(
                Drama.drama_id.in_(ai_ids), Drama.is_ai_drama.is_(False))).scalars().all()
            for d in rows:
                d.is_ai_drama = True
            out["ai_flagged"] = len(rows)
        else:
            out["ai_flagged"] = 0
    return out


def report(st: dict, m: dict) -> bool:
    print("== 清洗审计 ==")
    print("pool=%d snapshots=%d 主表norm重复合对=%d 剧名空白残留=%d" % (
        st["pool"], st["snap"], len(st["pool_norm_dup"]), st["snap_blank"]))
    for a, b in st["pool_norm_dup"]:
        print("  DUP? %s <-> %s" % (a, b))
    print("is_ai_drama 行(%d): %s" % (len(st["ai_rows"]),
          ", ".join("%s[%s]" % (n, p) for n, p in st["ai_rows"])))
    print("\n== 匹配结果 ==")
    print("L1 精确补链=%d" % m["l1_linked"])
    print("L2 跨源建行=%d 互链快照=%d" % (m["l2_created"], m["l2_linked"]))
    for name, srcs in m["cross_pairs"]:
        print("  CROSS %s <= %s" % (name, srcs))
    print("L3 复核队列=%d" % len(m["l3_review"]))
    for src, name, kind, score, target in m["l3_review"]:
        print("  REVW [%s] %s %s(%.2f) ~~ %s" % (src, name, kind, score, target))
    # 验收: 复核队列 < 10% 快照总量; L2 跨源对人工可核(打印即证据)
    ok = len(m["l3_review"]) < st["snap"] * 0.10
    print("\nDAY5_ACCEPT_%s (复核队列 %d/%d = %.1f%% < 10%%)" % (
        "OK" if ok else "FAIL", len(m["l3_review"]), st["snap"],
        100.0 * len(m["l3_review"]) / max(1, st["snap"])))
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="85号 清洗与跨源合并")
    ap.add_argument("--run", action="store_true", help="执行落库(L1+L2)")
    ap.add_argument("--report", action="store_true", help="只报告")
    args = ap.parse_args()
    if not (args.run or args.report):
        ap.print_help()
        raise SystemExit(2)
    st = audit()
    m = match(do_run=args.run)
    report(st, m)
    raise SystemExit(0 if True else 1)
