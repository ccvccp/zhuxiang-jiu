"""v92-P0b 基线报告: 全量(≤1000)评分流 + 失败挖掘统计
分界: 2026-09-26T02:32Z(埋点三修复 rebuild 完成时刻)——
前=虚高期(对照), 后=真实分层燃料(Bandit 基线)
"""
import asyncio

# 修复部署时刻(UTC ISO8601——ts() 即此格式)
CUTOVER = "2026-09-26T02:32"
# 复测注入会话(container 直调, 非真机)
INJECTED_SIDS = (340, 341)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1,
            int(round(p / 100.0 * (len(s) - 1))))
    return round(s[i], 1)


def _avg(vals):
    return round(sum(vals) / len(vals), 3) \
        if vals else 0.0


def _stats(scores):
    n = len(scores)
    if not n:
        return {"n": 0}
    by = {}
    for c in scores:
        t = str(c.get("track") or "") or "-"
        by[t] = by.get(t, 0) + 1
    lats = [_f(c.get("latencyMs"))
            for c in scores]
    rewards = [_f(c.get("reward"))
               for c in scores]
    per_track = {}
    for t in by:
        sub = [c for c in scores
               if (str(c.get("track") or "") or "-") == t]
        per_track[t] = {
            "n": len(sub),
            "rewardAvg": _avg([_f(c.get("reward"))
                               for c in sub]),
            "latP50": _pct([_f(c.get("latencyMs"))
                            for c in sub], 50),
        }
    return {
        "n": n,
        "rewardAvg": _avg(rewards),
        "taskAvg": _avg([_f(c.get("task"))
                         for c in scores]),
        "effAvg": _avg([_f(c.get("eff"))
                        for c in scores]),
        "smoothAvg": _avg([_f(c.get("smooth"))
                           for c in scores]),
        "byTrack": by,
        "latP50": _pct(lats, 50),
        "latP95": _pct(lats, 95),
        "perTrack": per_track,
    }


async def main():
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    recs = await repo.list_records(
        repo.TABLE_FAILURES, limit=1000)
    scores = [c for c in recs
              if c.get("kind") == "turn_score"]
    fails = [c for c in recs
             if c.get("kind") != "turn_score"]

    print("== 总量 ==")
    print("记录总数:", len(recs),
          "| 评分流:", len(scores),
          "| 失败流:", len(fails))

    old = [c for c in scores
           if str(c.get("ts") or "") < CUTOVER]
    new = [c for c in scores
           if str(c.get("ts") or "") >= CUTOVER]
    injected = [c for c in new
                if c.get("sessionId")
                in INJECTED_SIDS]
    organic = [c for c in new
               if c.get("sessionId")
               not in INJECTED_SIDS]

    print("\n== 修复前(虚高期·对照) ==")
    o = _stats(old)
    print("n:", o.get("n"),
          "rewardAvg:", o.get("rewardAvg"),
          "byTrack:", o.get("byTrack"))

    print("\n== 修复后·真实燃料(全部) ==")
    s = _stats(new)
    for k in ("n", "rewardAvg", "taskAvg",
              "effAvg", "smoothAvg", "byTrack",
              "latP50", "latP95"):
        print(f"{k}: {s.get(k)}")
    print("perTrack:")
    for t, v in (s.get("perTrack")
                 or {}).items():
        print(f"  {t}: {v}")

    print("\n== 修复后·真机轮(剔除注入 sid=%s) =="
          % (INJECTED_SIDS,))
    g = _stats(organic)
    for k in ("n", "rewardAvg", "byTrack"):
        print(f"{k}: {g.get(k)}")

    print("\n== 注入复测轮(sid=%s) =="
          % (INJECTED_SIDS,))
    j = _stats(injected)
    print("n:", j.get("n"),
          "rewardAvg:", j.get("rewardAvg"),
          "byTrack:", j.get("byTrack"))

    print("\n== 失败挖掘(清洗后) ==")
    kinds = {}
    phrases = {}
    for c in fails:
        k = c.get("kind") or "unknown"
        kinds[k] = kinds.get(k, 0) + 1
        p = str(c.get("rawText") or "").strip()[:40]
        if not p or set(p) <= {"#"}:
            continue
        phrases[p] = phrases.get(p, 0) + 1
    print("byKind:", kinds)
    for p, n in sorted(phrases.items(),
                       key=lambda kv: -kv[1])[:10]:
        print(f"  top: {p} ×{n}")


asyncio.run(main())
