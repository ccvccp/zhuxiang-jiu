"""81号 HRM·EMA 开启后验证脚本(live)

用法(HRM81_EMA=on 且重建容器后执行; 配套《EMA开启前检查清单》§四):
    Get-Content .\\verify_hrm81_ema_live.py -Raw |
        ssh root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"

验证项(检查清单 §四 + 只紧不松语义实证):
    V1  开关生效: HRM81_EMA=on(EMA) + MODE/AUTO 环境三值
    V2  观测面: ema.enabled / samples 积累 / 当前小时桶基线 / 磁盘外推
    V3  双轨判定: 当前生效阈值线合成展示(只紧不松) + tracks 字段
    V4  emaWould 消失: 手动决策轮新留痕无该字段 + 三新字段在位
    V5  闸门语义回归: amber 下 25 项全拦/critical+移出项放行
        (EMA 开启不改闸门——进程内覆盖 MODE 验证后即还原)
    V6  只紧不松实证: 高基线收紧早警 / 低基线永不放松(合成基线)

输出: 逐项 PASS/FAIL + 总结; 全 PASS 即 EMA 开启验证通过。
脚本幂等可重复执行(V4 会新增一条决策留痕, 属正常积累)。
"""

import asyncio
import os

RESULTS = []


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))


async def main():
    from services import hrm81_service as hrm

    # ---------------- V1 开关生效 ----------------
    check("V1 HRM81_EMA=on", hrm.ema_enabled(),
          f"env={os.environ.get('HRM81_EMA')}")
    check("V1b HRM81_MODE", os.environ.get("HRM81_MODE", "off"))
    check("V1c HRM81_AUTO", os.environ.get("HRM81_AUTO", "off"))

    # ---------------- V2 观测面 ----------------
    es = await hrm.ema_status()
    check("V2 ema.enabled=true", es.get("enabled") is True,
          f"samples={es.get('samples')}")
    check("V2b 采样持续积累", es.get("samples", 0) > 0)
    hb = es.get("hourBaseline") or {}
    check("V2c 当前小时桶基线在位", bool(hb),
          f"days={hb.get('days')} valid={hb.get('valid')} "
          f"memEMA={hb.get('memEMA')}MB")
    check("V2d 磁盘外推字段", "diskDaysRemaining" in es,
          f"diskDaysRemaining={es.get('diskDaysRemaining')}")

    # ---------------- V3 双轨判定(生效线合成展示) ----------------
    w = await hrm.assess_water_level(refresh=True)
    hour = int(w["assessedAt"][11:13])
    static_amber = hrm.MEM_AMBER_BYTES // 1048576
    static_red = hrm.MEM_RED_BYTES // 1048576
    if hb.get("valid") and hb.get("memEMA"):
        amber_line = max(static_amber,
                         round(hb["memEMA"] * hrm.MEM_EMA_AMBER_RATIO))
        red_line = max(static_red,
                       round(hb["memEMA"] * hrm.MEM_EMA_RED_RATIO))
        src = "EMA轨" if amber_line > static_amber else "静态轨"
    else:
        amber_line, red_line, src = static_amber, static_red, "静态轨(冷启动)"
    check("V3 水位判定", w.get("level") in ("green", "amber", "red"),
          f"level={w.get('level')} 当前生效 amber线={amber_line}MB "
          f"red线={red_line}MB ({src})")
    check("V3b tracks 字段在位", isinstance(w.get("tracks"), dict),
          f"tracks={w.get('tracks')}")

    # ---------------- V4 emaWould 消失(手动决策轮) ----------------
    try:
        d = await hrm.run_hrm_decision()
        check("V4 新留痕无 emaWould", "emaWould" not in d,
              f"level={d.get('level')} mode={d.get('mode')}")
        check("V4b 留痕新字段在位",
              all(k in d for k in ("tracks", "diskDaysRemaining")),
              f"tracks={d.get('tracks')}")
    except ValueError as exc:
        check("V4 手动决策轮", False,
              f"MODE=off 不可跑({exc})——请在 shadow/on 下执行")

    # ---------------- V5 闸门语义回归(EMA 不改闸门) ----------------
    # 进程内覆盖 MODE 验证后即还原, 不碰生产进程环境
    prev_mode = os.environ.get("HRM81_MODE")
    os.environ["HRM81_MODE"] = "on"
    import time
    hrm._level_cache.update({
        "at": time.time(),
        "value": {"level": "amber", "metrics": {}, "reasons": ["验证"],
                  "assessedAt": w["assessedAt"]}})
    blocked = {m: await hrm.acquire_slot(m) for m in hrm.BATCH_GATED}
    check("V5 amber 全拦截", all(v is False for v in blocked.values()),
          f"{len(blocked)} 项")
    check("V5b critical 豁免",
          await hrm.acquire_slot("trade_main") is True)
    check("V5c 移出项放行",
          await hrm.acquire_slot("ai_gov_health") is True
          and await hrm.acquire_slot("attract72_health") is True)
    hrm._level_cache.update({"at": 0, "value": None})
    if prev_mode is None:
        os.environ.pop("HRM81_MODE", None)
    else:
        os.environ["HRM81_MODE"] = prev_mode

    # ---------------- V6 只紧不松实证(合成基线, 不碰生产状态) ----------------
    metrics = {"memAvailableMB": 500, "diskAvailRatio": 0.5,
               "load1": 0.5, "cpuCount": 2}
    hi = {7: {"valid": True, "memEMA": 900.0, "loadEMA": 0.5}}
    lo = {7: {"valid": True, "memEMA": 300.0, "loadEMA": 0.5}}
    lv_hi, _, tr_hi = hrm._judge_level(metrics, hi, True, hour=7)
    check("V6a 高基线收紧(900→线585, 500 判 amber)",
          lv_hi == "amber" and tr_hi["mem"] == "ema")
    lv_lo, _, tr_lo = hrm._judge_level(metrics, lo, True, hour=7)
    check("V6b 低基线不放松(300→线仍450, 500 判 green)",
          lv_lo == "green" and tr_lo["mem"] == "static")

    # ---------------- 总结 ----------------
    fails = [r[0] for r in RESULTS if not r[1]]
    print("\n" + "=" * 50)
    print(f"=== {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS ===")
    if fails:
        print("失败项:", "; ".join(fails))
        print("处置: 参见《81号_HRM_EMA开启前检查清单》§五 应急速查")
    else:
        print("EMA 开启验证全部通过——观察 tracks 与决策留痕 1-3 天后"
              "可评估 HRM81_MODE=on")


asyncio.run(main())
