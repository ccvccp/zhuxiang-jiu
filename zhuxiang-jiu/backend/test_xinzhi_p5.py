"""68号·信值·臻选·P5 灰度上线与白皮书收官专项测试

覆盖(《68号 创新规划方案》§三 P5, ~20 断言):
    1. 三态灰度: 默认 off/决策面 off 拒绝/assist 放行/
       override 清除回落 env/非法态拒绝
    2. A/B 护栏: 正常指标不暂停/恶化>3% 自动暂停/
       暂停后决策面关闭/暂停留痕/恢复需人工/
       未暂停恢复拒绝/指标留痕滚动
    3. 年度白皮书: 四章节结构/雷达分布聚合/
       样本门(<3 不出数)/PII 扫描零命中/
       67号互助联动/发布责任口径

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_p5.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("XINZHI_MODE", None)

from services.xinzhi_mode_service import (
    XinzhiModeService, MODE_VALUES, GUARD_DETERIORATION,
)
from services.xinzhi_whitepaper_service import (
    XinzhiWhitepaperService, MIN_SAMPLE_GATE,
)
from repositories.xinzhi_repository import (
    XinzhiRepository, DIMENSIONS,
)
from repositories.member_repository import MemberRepository

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()
    os.environ.pop("XINZHI_MODE", None)


async def seed_member(member_id: int) -> None:
    from datetime import datetime, UTC, timedelta
    repo = MemberRepository()
    await repo.save(member_id, {
        "id": member_id,
        "phone": f"1380000000{member_id}",
        "password": "x",
        "nickname": f"测试会员{member_id}",
        "level": 2, "points": 100, "status": 1,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=200)).isoformat(),
        "last_login_at":
            datetime.now(UTC).isoformat(),
    })


async def seed_radar(member_id: int, grade: str,
                     total: float) -> None:
    from datetime import datetime, UTC
    repo = XinzhiRepository()
    sid = await repo.next_id("snapshot")
    await repo.save_snapshot({
        "snapshotId": sid, "memberId": member_id,
        **{d: 80 for d in DIMENSIONS},
        "totalScore": total, "grade": grade,
        "weights": {}, "recentFactors": {},
        "circuitBroken": False, "coldStart": False,
        "bonusApplied": False,
        "computedAt": datetime.now(UTC).isoformat(),
    })


# ============================================================
# 1. 三态灰度(5 断言)
# ============================================================

class TestMode:
    async def run(self):
        reset_store()
        svc = XinzhiModeService()

        # 1) 默认 off(env 未设/无 override)
        m0 = await svc.current_mode()
        record("灰度-默认off",
               m0["mode"] == "off"
               and m0["source"] == "env",
               f"{m0['mode']}/{m0['source']}")

        # 2) 决策面 off 拒绝(409 语义)
        ok = False
        try:
            await svc.require_decision_mode()
        except ValueError:
            ok = True
        record("灰度-决策面off拒绝", ok)

        # 3) override=assist 放行(读取链最高效档)
        await svc.set_override("assist", operator="admin")
        st = await svc.require_decision_mode()
        m1 = await svc.current_mode()
        record("灰度-assist放行",
               st["mode"] == "assist"
               and m1["source"] == "runtime_override",
               f"{st['mode']}/{m1['source']}")

        # 4) override 清除回落 env(shadow)
        os.environ["XINZHI_MODE"] = "shadow"
        await svc.set_override("", operator="admin")
        m2 = await svc.current_mode()
        st2 = await svc.require_decision_mode()
        record("灰度-清除回落env",
               m2["mode"] == "shadow"
               and m2["source"] == "env"
               and st2["mode"] == "shadow",
               f"{m2['mode']}/{m2['source']}")
        os.environ.pop("XINZHI_MODE", None)

        # 5) 非法态拒绝(override 与 env 双口径)
        e1 = False
        try:
            await svc.set_override("hot", operator="a")
        except ValueError:
            e1 = True
        os.environ["XINZHI_MODE"] = "xyz"
        m3 = await svc.current_mode()
        os.environ.pop("XINZHI_MODE", None)
        record("灰度-非法态回落off",
               e1 and m3["mode"] == "off"
               and set(MODE_VALUES) == {
                   "off", "shadow", "assist"},
               f"{m3['mode']}")


# ============================================================
# 2. A/B 护栏(7 断言)
# ============================================================

class TestGuard:
    async def run(self):
        reset_store()
        svc = XinzhiModeService()

        # 6) 正常指标不暂停(基线±3% 内)
        g0 = await svc.guard_check(
            refund_rate=0.05, complaint_rate=0.02,
            uninstall_rate=0.01)
        record("护栏-正常指标不暂停",
               g0["breached"] is False
               and g0["pausedNow"] is False
               and g0["breaches"] == [],
               str(g0["breaches"]))

        # 7) 退款率恶化>3% 自动暂停(0.05→0.10=+100%)
        g1 = await svc.guard_check(
            refund_rate=0.10, complaint_rate=0.02,
            uninstall_rate=0.01)
        record("护栏-退款恶化暂停",
               g1["breached"] is True
               and g1["pausedNow"] is True
               and g1["breaches"][0]["metric"]
               == "refundRate"
               and g1["breaches"][0][
                   "deterioration"] > 0.03,
               str(g1["breaches"]))

        # 8) 暂停后决策面关闭(读取链最高效: guard_pause)
        m = await svc.current_mode()
        ok = False
        try:
            await svc.require_decision_mode()
        except ValueError:
            ok = True
        record("护栏-暂停后决策面关闭",
               m["mode"] == "off"
               and m["source"] == "guard_pause"
               and ok,
               f"{m['mode']}/{m['source']}")

        # 9) 暂停留痕(指标名+恶化幅度)
        view = await svc.status_view()
        reason = view["guard"]["pausedReason"]
        record("护栏-暂停留痕",
               "退款率" in reason
               and view["guard"]["breachCount"] == 1
               and view["guard"]["checkCount"] == 2,
               reason[:40])

        # 10) 多指标同时恶化(全列出; 卸载率保持基线不恶化)
        g2 = await svc.guard_check(
            refund_rate=0.10, complaint_rate=0.05,
            uninstall_rate=0.01)
        record("护栏-多指标恶化",
               len(g2["breaches"]) == 2
               and {b["metric"] for b in g2["breaches"]}
               == {"refundRate", "complaintRate"},
               str([b["metric"]
                    for b in g2["breaches"]]))

        # 11) 恢复需人工(resume 后回 env 档)
        r = await svc.resume(operator="admin",
                             note="指标已回落")
        record("护栏-人工恢复",
               r["mode"] == "off"
               and r["paused"] is False,
               f"{r['mode']}/paused={r['paused']}")

        # 12) 未暂停时 resume 拒绝(幂等)
        ok = False
        try:
            await svc.resume(operator="admin")
        except ValueError:
            ok = True
        record("护栏-未暂停恢复拒绝", ok)

        # 13) 指标留痕滚动(4 次检查全留痕)
        view2 = await svc.status_view()
        record("护栏-指标留痕",
               view2["guard"]["checkCount"] == 3
               and view2["guard"]["breachCount"] == 2,
               f"chk={view2['guard']['checkCount']} "
               f"br={view2['guard']['breachCount']}")


# ============================================================
# 3. 年度信值白皮书(8 断言)
# ============================================================

class TestWhitepaper:
    async def run(self):
        reset_store()
        # 播种: 5 个 D 级(过样本门) + 2 个 S 级(不过门)
        for i in range(1, 6):
            await seed_member(i)
            await seed_radar(i, "D", 40)
        for i in range(6, 8):
            await seed_member(i)
            await seed_radar(i, "S", 95)
        svc = XinzhiWhitepaperService()

        # 14) 四章节固定结构
        wp = await svc.build_whitepaper()
        secs = wp["sections"]
        record("白皮书-四章节结构",
               set(secs.keys()) == {
                   "framework", "annual_data",
                   "redline_cases", "initiative"}
               and secs["annual_data"]["title"].endswith(
                   "年度数据"),
               str(list(secs.keys())))

        # 15) 雷达分布聚合(按会员最新快照)
        users = secs["annual_data"]["data"]["users"]
        dist = {d["grade"]: d["count"]
                for d in users["gradeDist"]}
        record("白皮书-雷达分布聚合",
               users["withRadar"] == 7
               and dist["D"] == 5
               and dist["S"] is None,
               f"n={users['withRadar']} d={dist}")

        # 16) 样本门(<3 不出数)
        record("白皮书-样本门",
               dist["S"] is None
               and MIN_SAMPLE_GATE == 3,
               f"S={dist['S']}")

        # 17) PII 扫描零命中(全量文本化扫描)
        record("白皮书-PII零命中",
               wp["piiScanned"] is True
               and wp["piiHits"] == 0,
               f"hits={wp['piiHits']}")

        # 18) 红线案例公示(宪法域)
        cases = secs["redline_cases"]["cases"]
        record("白皮书-红线案例",
               len(cases) >= 6
               and any("杀熟" in c["name"]
                       for c in cases)
               and any("永不自动" in c["name"]
                       for c in cases),
               f"n={len(cases)}")

        # 19) 67号互助联动(mutualAid 域)
        mutual = secs["annual_data"]["data"][
            "mutualAid"]
        record("白皮书-67号互助联动",
               isinstance(mutual, dict)
               and "orders" in mutual
               and mutual["orders"]["total"] == 0,
               str(type(mutual)))

        # 20) 发布责任口径(AI 仅展示)
        record("白皮书-发布责任口径",
               "admin" in wp["publishNote"]
               and secs["initiative"]["license"]
               == "CC BY-NC-SA 4.0",
               wp["publishNote"][:30])

        # 21) 空库不抛错(冷启动全量聚合)
        reset_store()
        wp2 = await svc.build_whitepaper(year=2026)
        d2 = wp2["sections"]["annual_data"]["data"]
        record("白皮书-空库冷启动",
               wp2["year"] == 2026
               and d2["users"]["withRadar"] == 0
               and d2["pricing"]["breakdowns"] == 0,
               f"u={d2['users']['withRadar']}")


async def main():
    tests = [TestMode(), TestGuard(), TestWhitepaper()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("68号 P5 信值·臻选 灰度上线与白皮书收官专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
