# -*- coding: utf-8 -*-
"""竹韵话术入库后冲突检查器

三段检查:
  A 内部互查: 27 条 question(+keywords×2) 两两 cosine, 同口径 build_vector
      ≥0.85 = HARD-DUP(入库应已被挡, 出现即异常), 0.50-0.85 = NEAR(检索竞争风险)
  B 对阵存量: 27 条 vs 生产 published 全量(排除自身), ≥0.50 列出(RAG direct 阈值)
      另做 answer 全等检测(防整条拷贝式重复)
  C 事实矛盾: 对 published 全量做口径锚点正则扫描, 同主题不同口径列出供研判
报告 base64 输出(规避云助手通道 GBK 乱码), 本地解码研判。
用法: env 对齐 STORE_MODE=redis 后 python check_zhuyun_scripts.py
"""
import asyncio
import base64
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

from repositories.knowledge_repository import (  # noqa: E402
    KnowledgeRepository, build_vector, cosine)
from seed_zhuyun_scripts import SCRIPTS  # noqa: E402

DUP = 0.85     # service 层硬挡阈值
RAG = 0.50     # n-gram 轨 direct 命中阈值

ANCHORS = {
    "发货时效": r"(下午[一二三四五六\d]+点|当[天日]发|次日|\d+\s*小时[内前]|\d+\s*天[内前]发)",
    "快递品牌": r"(顺丰|京东|圆通|中通|韵达|邮政|EMS)",
    "度数口径": r"(\d+\s*[-到至~]\s*\d+\s*度|[四五]\d+度|\d+度)",
    "退货期限": r"([七7]天|7\s*天|十五天|15\s*天|三十天|30\s*天|无理由)",
    "退款时效": r"([一二三四五七八\d]+\s*(?:至|-|到)?\s*[一二三四五七八\d]*个?工作日)",
    "保质存放": r"(越陈越香|两年[内前]|多年|[一二三五十\d]+\s*年[内前](?:饮用|喝完))",
    "运费承担": r"(您承担|买家承担|自己承担|我们承担|卖家承担|来回运费|免?邮|包邮)",
}
COMPLIANCE_HINT = r"(未成年人|未满十八|适度|适量|贪杯|微醺)"


def scan_anchors(question: str, answer: str) -> dict:
    hits = {}
    for name, pat in ANCHORS.items():
        m = re.findall(pat, answer + " " + question)
        if m:
            hits[name] = sorted(set(str(x) for x in m))
    return hits


async def main() -> None:
    lines = []
    new_vecs = [build_vector(q, kw) for _, q, _, kw in SCRIPTS]

    # ---- A 段: 27 条内部两两 ----
    lines.append("== A 内部互查 (27x26/2=351 对) ==")
    a_near = a_hard = 0
    for i in range(len(SCRIPTS)):
        for j in range(i + 1, len(SCRIPTS)):
            sim = cosine(new_vecs[i], new_vecs[j])
            if sim >= RAG:
                tag = "HARD-DUP" if sim >= DUP else "NEAR"
                if sim >= DUP:
                    a_hard += 1
                else:
                    a_near += 1
                lines.append("  [%s] %.3f  #%d<->#%d  %s | %s" % (
                    tag, sim, i + 1, j + 1,
                    SCRIPTS[i][1][:16], SCRIPTS[j][1][:16]))
    lines.append("  A 汇总: HARD-DUP=%d NEAR=%d" % (a_hard, a_near))

    # ---- B 段: vs 生产 published 全量 ----
    repo = KnowledgeRepository()
    pub = await repo.list_entries(status="published", limit=2000)
    lines.append("== B 对阵存量 (published=%d, 新批含其中, 自身已排除) ==" % len(pub))
    b_hits = ans_dups = 0
    for i, (_, q, _, kw) in enumerate(SCRIPTS):
        vec_q = build_vector(q, kw)
        for e in pub:
            if e.get("question") == q:
                continue  # 自身(幂等重跑命中的同条)
            sim = cosine(vec_q, e.get("vector") or {})
            if sim >= RAG:
                b_hits += 1
                lines.append("  [RISK] %.3f  new(%s) <-> #%s(%s/%s) %s" % (
                    sim, q[:16], e.get("id"), e.get("category"),
                    e.get("source"), (e.get("question") or "")[:20]))
    for _, q, a, _ in SCRIPTS:  # answer 全等(整条拷贝式重复)
        for e in pub:
            if e.get("question") != q and (e.get("answer") or "") == a:
                ans_dups += 1
                lines.append("  [ANS-DUP] %s <-> #%s %s" % (
                    q[:16], e.get("id"), e.get("question", "")[:20]))
    lines.append("  B 汇总: RISK=%d ANS-DUP=%d" % (b_hits, ans_dups))

    # ---- C 段: 事实口径扫描(全量 published, 同主题不同口径并列) ----
    lines.append("== C 事实口径扫描 ==")
    buckets: dict[str, list] = {k: [] for k in ANCHORS}
    for e in pub:
        hits = scan_anchors(e.get("question") or "", e.get("answer") or "")
        for name, vals in hits.items():
            for v in vals:
                buckets[name].append((str(e.get("id")), v))
    for name, items in buckets.items():
        vals = sorted(set(v for _, v in items))
        flag = " <-- 口径不一致, 需研判" if len(vals) > 1 else ""
        lines.append("  %s: %s%s" % (name, " / ".join(vals), flag))
        if len(vals) > 1:
            for v in vals:
                ids = sorted(set(i for i, x in items if x == v))
                lines.append("    %s <- %s" % (v, ",".join(ids)))
    comp = [str(e.get("id")) for e in pub
            if re.search(COMPLIANCE_HINT, e.get("answer") or "")]
    lines.append("  合规话术(未成年人/适量)条目: %s" % ",".join(comp))

    report = "\n".join(lines)
    print("CHECK-DONE published=%d" % len(pub))
    print("REPORT-B64:")
    for k in range(0, len(report), 1200):
        print(base64.b64encode(report[k:k + 1200].encode("utf-8")).decode())


if __name__ == "__main__":
    asyncio.run(main())
