"""81号 HRM·EMA 观察期监控脚本(watch)

用法(HRM81_EMA=on 后观察期, 每日跑一次或随时; 配套检查清单 §三节奏):
    Get-Content .\\watch_hrm81_ema.py -Raw |
        ssh root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"

输出六节观察报告(S1-S6) + 观察记录表一行(复制入清单 §六):
    S1 采样健康   总量/覆盖天数/近24h条数(决策轮停摆检测)
    S2 基线覆盖   24 桶 valid 数 / memEMA 区间
    S3 决策统计   近 600 轮 level 分布 / tracks ema 命中占比
                  (shadow 下 amber+red 轮占比 = "若 MODE=on 会拦批"的预估)
    S4 amber 时段 按小时聚合的紧张时段画像(业务高峰合理/凌晨异常)
    S5 偏离与外推 当前水位 vs 基线偏离度 / 磁盘剩余天数
    S6 结论建议   自动三档: 可评估转 on / 继续观察 / 暂缓+核查指引

判据(检查清单 §二-4 同口径):
    amber+red 占比 ≤5%  → 可评估 HRM81_MODE=on
    5%~30%             → 继续 shadow 观察, 关注 S4 时段分布
    >30% 或 red 出现   → 暂缓转 on, 核查基线合理性(清单 §二-3)
幂等可重复执行; 纯只读监控, 不改任何状态。
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta


def _hour_of(ts: str) -> int:
    try:
        return int(str(ts)[11:13])
    except (ValueError, IndexError):
        return -1


async def main():
    from services import hrm81_service as hrm

    print("=" * 60)
    print("81号 HRM·EMA 观察期监控报告  ",
          datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 60)

    # ---------------- S1 采样健康 ----------------
    samples = await hrm.list_water_samples()
    days = sorted({str(s["ts"])[:10] for s in samples})
    day_ago = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    recent = [s for s in samples if str(s["ts"]) >= day_ago]
    print(f"\n[S1 采样健康] samples={len(samples)} 覆盖{len(days)}天 "
          f"({days[0] if days else '-'}~{days[-1] if days else '-'})")
    print(f"    近24h条数={len(recent)} (期望≈24, <18 提示决策轮停摆"
          "或重启频繁, 见清单§二-1)")
    s1_ok = len(recent) >= 18

    # ---------------- S2 基线覆盖 ----------------
    baselines = hrm._build_ema_baselines(samples)
    valid = {h: v for h, v in baselines.items() if v["valid"]}
    emas = [v["memEMA"] for v in valid.values()]
    print(f"\n[S2 基线覆盖] 桶valid={len(valid)}/24 "
          f"(invalid 桶走静态轨兜底)")
    if emas:
        print(f"    memEMA 区间: {min(emas):.0f}~{max(emas):.0f}MB "
              f"(异常低值查清单§二-3)")
    s2_ok = len(valid) >= 20

    # ---------------- S3 决策统计 ----------------
    rows = await hrm.decision_history(600)
    levels = Counter(r.get("level") for r in rows)
    ema_hits = sum(1 for r in rows
                   if (r.get("tracks") or {}).get("mem") == "ema")
    n = len(rows)
    amber_red = levels.get("amber", 0) + levels.get("red", 0)
    pct = (amber_red / n * 100) if n else 0.0
    print(f"\n[S3 决策统计] 近{n}轮 "
          f"(约{round(n * 5 / 60)}h): "
          f"green={levels.get('green', 0)} amber={levels.get('amber', 0)} "
          f"red={levels.get('red', 0)} unknown={levels.get('unknown', 0)}")
    pct_ema = (ema_hits / n * 100) if n else 0.0
    print(f"    tracks ema 命中={ema_hits} 轮({pct_ema:.0f}%)")
    print(f"    amber+red 占比={pct:.1f}%  "
          f"(shadow 下即'MODE=on 会暂缓批任务'的预估频率)")
    s3_note = pct

    # ---------------- S4 amber 时段分布 ----------------
    hour_hist = Counter(_hour_of(r["decidedAt"])
                        for r in rows
                        if r.get("level") in ("amber", "red"))
    print(f"\n[S4 紧张时段分布(UTC)] {dict(sorted(hour_hist.items()))}")
    if hour_hist:
        print("    解读: 11-15 UTC(19-23 北京)集中=业务高峰合理错峰; "
              "凌晨集中=异常, 核查该时段基线(清单§二-3)")

    # ---------------- S5 偏离与外推 ----------------
    w = await hrm.assess_water_level(refresh=True)
    hour_now = _hour_of(w["assessedAt"])
    b = baselines.get(hour_now) or {}
    mem = (w.get("metrics") or {}).get("memAvailableMB")
    if b.get("valid") and b.get("memEMA") and mem:
        print(f"\n[S5 偏离度] 当前 mem={mem}MB vs 基线"
              f"{b['memEMA']}MB → dev={mem / b['memEMA']:.0%} "
              f"(动态 amber 线=dev 65%, red 线=45%)")
    else:
        print(f"\n[S5 偏离度] 当前 mem={mem}MB, "
              "当前小时桶无有效基线(冷启动/该桶缺样本)")
    print(f"    磁盘外推: 距 amber 线剩余 "
          f"{w.get('diskDaysRemaining')} 天(None=趋势不足/未降)")

    # ---------------- S6 结论建议 ----------------
    red = levels.get("red", 0)
    print("\n[S6 结论建议]")
    if n == 0:
        verdict = "无留痕数据(决策轮未跑?), 核查 HRM81_AUTO/调度日志"
        action = "暂缓"
    elif not (s1_ok and s2_ok):
        verdict = (f"采样/基线未达标(近24h {len(recent)}条, "
                   f"valid {len(valid)}/24)——继续积累")
        action = "继续观察"
    elif red > 0:
        verdict = (f"近{n}轮出现 red×{red}——核查该时段水位与基线"
                   "(清单§二-3), red 由静态线或深度偏离触发")
        action = "暂缓转 on"
    elif pct > 30:
        verdict = (f"amber 占比 {pct:.0f}%>30%——动态线贴近日常水位, "
                   "先查基线或评估调 ratio(0.65→0.55)")
        action = "暂缓转 on"
    elif pct > 5:
        verdict = (f"amber 占比 {pct:.0f}%——批任务偶发错峰符合设计, "
                   "关注 S4 时段是否集中业务高峰")
        action = "继续观察"
    else:
        verdict = (f"amber 占比 {pct:.1f}%≤5%, 采样与基线健康——"
                   "EMA 判定稳定, 可评估 HRM81_MODE=on")
        action = "可评估转 on"
    print(f"    [{action}] {verdict}")

    # ---------------- 观察记录表一行(复制入清单 §六) ----------------
    today = datetime.now(UTC).strftime("%m-%d")
    print(f"\n观察记录: | {today} | {len(samples)} | "
          f"{len(valid)}/24 | {pct:.0f}% | "
          f"{'-' if not hour_hist else dict(sorted(hour_hist.items()))} "
          f"| {action} |")

asyncio.run(main())
