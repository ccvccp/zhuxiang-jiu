"""64号·信值兑换管理模块 P3 专项测试
(动态风控层)

运行方式:
    python test_xx64_p3.py

覆盖(64号 P3 设计 §九):
    - ARB-HF: 高频小额(命中/不命中
      /边界=恰quota×3/取消态不计/
      指标可溯源/确定性)
    - ARB-MA: 多账号集中(同商品
      命中/4号不命中/同卖方弱信号
      /窗口外不计/阻断riskId)
    - PTS-SHOCK: 积分冲击(量级/
      999不命中/持续3日/3日×2次
      不命中/探测5次/拦截当笔)
    - PRICE-MANIP: 价格操纵(20%
      涨幅/19%不涨/样本不足/
      无信值叠加/口径/建议不自动)
    - LIQ-CRUNCH: 流动性推演(触线/
      未触线/40%边界/仅建议/
      数字可溯源)
    - 分级处置: 三档/tier 摩擦/
      tier 不豁免 high/叠加加权/
      复核 dismissed
    - HTTP 层+宪法断言
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ["AV62_MODE"] = "off"
os.environ["XX64_MODE"] = "off"
os.environ["XX64_LLM_MODE"] = "off"
os.environ["XX64_LEARN_MODE"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_profile(trust_id, score=500.0):
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    await TrustValue45Repository() \
        .save_profile({
            "trustId": int(trust_id),
            "role": "person",
            "name": f"P{trust_id}",
            "idDigest": f"d-{trust_id}",
            "factors": {},
            "score": float(score),
            "rawScore": float(score),
            "grade": "A",
            "fused": False,
            "frozen": False,
            "createdAt":
                "2026-01-01T00:00:00",
            "updatedAt":
                "2026-01-01T00:00:00"})


async def add_order(repo, buyer, seller,
                    trust, product, price,
                    tv, status="paid",
                    paid_at=None,
                    created_at=None):
    from core.helpers import ts
    from datetime import datetime, UTC
    now = paid_at or created_at or \
        datetime.now(UTC).isoformat()
    oid = await repo.next_order_id()
    await repo.save_order({
        "orderId": oid,
        "buyerId": buyer,
        "sellerId": seller,
        "trustId": trust,
        "product": product,
        "price": float(price),
        "trustValue": float(tv),
        "cashValue": round(float(price)
                           - float(tv), 2),
        "balanceSnapshot": 500.0,
        "status": status,
        "paidAt": now if status == "paid"
        else "",
        "createdAt": created_at
        or datetime.now(UTC).isoformat(),
    })
    return oid


async def add_exchange(repo, user, trust,
                       points, value,
                       status="pending",
                       created=None):
    from datetime import datetime, UTC
    from core.helpers import ts
    eid = await repo.next_exchange_id()
    await repo.save_exchange({
        "exchangeId": eid,
        "buyerId": user,
        "trustId": trust,
        "points": points,
        "pointsValue": float(value),
        "exchangeRate": 0.01,
        "status": status,
        "frozenHours": 24,
        "releaseAt": ts(),
        "createdAt": created
        or datetime.now(UTC).isoformat(),
    })
    return eid


class TestArbHf:
    """01 ARB-HF 高频小额"""

    async def run(self):
        print("[01 ARB-HF 高频小额]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        reset_all()
        await seed_profile(1, 500.0)
        repo = Xx64Repository()
        svc = Xx64RiskService()

        # 命中: 12 笔(≥10)+总额 360
        # > 单次限额×3=300
        for _ in range(12):
            await add_order(repo, 1, 2, 1,
                            "gA", 100, 30)
        f = await svc.detect_arb_hf(1, 1)
        record("高频命中(12 笔+360>300)",
               f is not None
               and f["severity"] == "medium"
               and f["detail"]
               ["orderCount"] == 12
               and f["detail"]
               ["trustTotal"] == 360.0,
               str(f and f["detail"]))

        # 不命中: 9 笔(<10)
        reset_all()
        await seed_profile(1, 500.0)
        for _ in range(9):
            await add_order(repo, 1, 2, 1,
                            "gA", 100, 30)
        f = await svc.detect_arb_hf(1, 1)
        record("9 笔不命中(<10)",
               f is None, str(f))

        # 边界: 10 笔但总额恰=quota×3
        reset_all()
        await seed_profile(1, 500.0)
        for _ in range(10):
            await add_order(repo, 1, 2, 1,
                            "gA", 100, 30)
        f = await svc.detect_arb_hf(1, 1)
        record("边界恰等 quota×3 不命中",
               f is None,
               str(f and f["detail"]))

        # cancelled 态不计入
        reset_all()
        await seed_profile(1, 500.0)
        for _ in range(12):
            await add_order(repo, 1, 2, 1,
                            "gA", 100, 30,
                            status="cancelled")
        f = await svc.detect_arb_hf(1, 1)
        record("cancelled 态不计入",
               f is None, str(f))

        # 指标可溯源
        reset_all()
        await seed_profile(1, 500.0)
        for _ in range(11):
            await add_order(repo, 1, 2, 1,
                            "gA", 100, 30)
        f = await svc.detect_arb_hf(1, 1)
        d = (f or {}).get("detail") or {}
        record("指标可溯源"
               "(count/total/quotaX3)",
               d.get("orderCount") == 11
               and d.get("trustTotal")
               == 330.0
               and d.get("quotaX3") == 300.0
               and d.get("balance")
               == 500.0,
               str(d))

        # 确定性: 同输入同输出
        f2 = await svc.detect_arb_hf(1, 1)
        record("确定性(同输入同输出)",
               f == f2, "")


class TestArbMa:
    """02 ARB-MA 多账号集中"""

    async def run(self):
        print("[02 ARB-MA 多账号集中]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        reset_all()
        await seed_profile(1, 500.0)
        repo = Xx64Repository()
        svc = Xx64RiskService()

        # 同商品 5 账号命中(high)
        for b in (11, 12, 13, 14, 15):
            await add_order(repo, b, 2, 1,
                            "gM", 100, 30)
        f = await svc.detect_arb_ma(
            product="gM")
        record("同商品 5 账号命中(high)",
               f is not None
               and f["severity"] == "high"
               and f["detail"]
               ["accountCount"] == 5
               and sorted(f["detail"]
                          ["buyers"])
               == [11, 12, 13, 14, 15],
               str(f and f["detail"]))

        # 4 账号不命中
        reset_all()
        for b in (11, 12, 13, 14):
            await add_order(repo, b, 2, 1,
                            "gM", 100, 30)
        f = await svc.detect_arb_ma(
            product="gM")
        record("4 账号不命中",
               f is None, str(f))

        # 同卖方跨 2 商品 5 账号
        # (弱信号 medium)
        reset_all()
        for b in (11, 12, 13, 14, 15):
            await add_order(repo, b, 2, 1,
                            f"gS{b}", 100, 30)
        f = await svc.detect_arb_ma(
            seller_id=2)
        record("同卖方弱信号"
               "(5 账号跨 5 商品)",
               f is not None
               and f["severity"] == "medium"
               and f["detail"]
               ["accountCount"] == 5
               and f["detail"]
               ["productCount"] == 5,
               str(f and f["detail"]))

        # 窗口外(2h 前)不计
        reset_all()
        from datetime import datetime, \
            UTC, timedelta
        old = (datetime.now(UTC)
               - timedelta(hours=2)
               ).isoformat()
        for b in (11, 12, 13, 14, 15):
            await add_order(repo, b, 2, 1,
                            "gM", 100, 30,
                            paid_at=old)
        f = await svc.detect_arb_ma(
            product="gM")
        record("1h 窗口外不计",
               f is None, str(f))

        # 阻断产生 riskId(sync gate)
        reset_all()
        await seed_profile(1, 500.0)
        await seed_profile(2, 500.0)
        for b in (11, 12, 13, 14, 15):
            await add_order(repo, b, 2, 1,
                            "gM", 100, 30)
        order = await repo.get_order(5)
        os.environ["XX64_MODE"] = "assist"
        gate = await svc.sync_gate_pay(
            dict(order))
        os.environ["XX64_MODE"] = "off"
        record("assist 态阻断+riskId",
               gate["blocked"] is True
               and (gate["riskId"]
                    or 0) > 0,
               str(gate))

        # shadow 态仅观察不阻断
        os.environ["XX64_MODE"] = "shadow"
        gate2 = await svc.sync_gate_pay(
            dict(order))
        os.environ["XX64_MODE"] = "off"
        record("shadow 态不阻断"
               "(仅观察留痕)",
               gate2["blocked"] is False
               and len(gate2["findings"])
               > 0,
               str(gate2.get("blocked")))


class TestPtsShock:
    """03 PTS-SHOCK 积分冲击"""

    async def run(self):
        print("[03 PTS-SHOCK 积分冲击]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        repo = Xx64Repository()
        svc = Xx64RiskService()

        # 量级命中(24h ≥1000 信值)
        reset_all()
        await seed_profile(1, 500.0)
        await add_exchange(repo, 1, 1,
                           50000, 500.0)
        await add_exchange(repo, 1, 1,
                           60000, 600.0)
        f = await svc.detect_pts_shock(1)
        record("量级命中(1100≥1000)",
               f is not None
               and f["severity"] == "high"
               and f["detail"]
               ["trustValue24h"]
               == 1100.0
               and f["detail"]
               ["signal"] == "magnitude",
               str(f and f["detail"]))

        # 999 不命中
        reset_all()
        await add_exchange(repo, 1, 1,
                           99900, 999.0)
        f = await svc.detect_pts_shock(1)
        record("999 不命中(<1000)",
               f is None, str(f))

        # 持续: 连续 3 日每日满频
        reset_all()
        from datetime import datetime, \
            UTC, timedelta
        base = datetime.now(UTC)
        for d in range(3):
            day = (base
                   - timedelta(days=d)
                   ).strftime("%Y-%m-%d")
            for _ in range(3):
                await add_exchange(
                    repo, 1, 1, 100, 1.0,
                    created=f"{day}T10:00:00"
                            "+00:00")
        f = await svc.detect_pts_shock(1)
        record("持续命中"
               "(3 日每日 3 次)",
               f is not None
               and f["severity"]
               == "medium"
               and f["detail"]
               ["consecutiveDays"] == 3,
               str(f and f["detail"]))

        # 3 日×2 次不命中
        reset_all()
        for d in range(3):
            day = (base
                   - timedelta(days=d)
                   ).strftime("%Y-%m-%d")
            for _ in range(2):
                await add_exchange(
                    repo, 1, 1, 100, 1.0,
                    created=f"{day}T10:00:00"
                            "+00:00")
        f = await svc.detect_pts_shock(1)
        record("3 日×2 次不命中",
               f is None, str(f))

        # 探测: 当日 ≥5 次(含取消)
        reset_all()
        today = base.strftime(
            "%Y-%m-%d")
        for _ in range(3):
            await add_exchange(
                repo, 1, 1, 100, 1.0,
                created=f"{today}T10:00:00"
                        "+00:00")
        for _ in range(2):
            await add_exchange(
                repo, 1, 1, 100, 1.0,
                status="cancelled",
                created=f"{today}T11:00:00"
                        "+00:00")
        f = await svc.detect_pts_shock(1)
        record("探测命中(5 次含取消)",
               f is not None
               and f["severity"]
               == "medium"
               and f["detail"]
               ["signal"] == "probe"
               and f["detail"]
               ["todayAttempts"] == 5,
               str(f and f["detail"]))

        # 拦截当笔(assist 态 exchange)
        reset_all()
        await seed_profile(1, 500.0)
        await add_exchange(repo, 1, 1,
                           50000, 500.0)
        await add_exchange(repo, 1, 1,
                           60000, 600.0)
        os.environ["XX64_MODE"] = "assist"
        gate = await svc \
            .sync_gate_exchange(1, 1)
        os.environ["XX64_MODE"] = "off"
        record("量级拦截当笔(assist)",
               gate["blocked"] is True
               and (gate["riskId"]
                    or 0) > 0,
               str(gate))

        # 事件留痕
        from repositories.xx64_repository import (
            Xx64Repository as R,
        )
        risks = await R().list_risks(
            limit=10)
        record("风险事件留痕"
               "(xx64_risk 落库)",
               len(risks) >= 1
               and risks[0]
               ["detectorCode"]
               == "PTS-SHOCK"
               and risks[0]["matched"]
               is True,
               str(len(risks)))


class TestPriceManip:
    """04 PRICE-MANIP 价格操纵"""

    async def run(self):
        print("[04 PRICE-MANIP 价格操纵]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        repo = Xx64Repository()
        svc = Xx64RiskService()
        from datetime import datetime, \
            UTC, timedelta
        now = datetime.now(UTC)

        # 涨幅 50% 命中(prior 100→
        # recent 150)
        reset_all()
        old = (now - timedelta(days=10)
               ).isoformat()
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=old)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 150, 45)
        fs = await svc.detect_price_manip()
        record("涨幅 50% 命中",
               len(fs) == 1
               and fs[0]["severity"]
               == "medium"
               and fs[0]["detail"]
               ["avgRecent"] == 150.0
               and fs[0]["detail"]
               ["avgPrior"] == 100.0
               and fs[0]["detail"]
               ["drift"] == 0.5,
               str(fs))

        # 19% 不命中
        reset_all()
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=old)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 119, 35.7)
        fs = await svc.detect_price_manip()
        record("涨幅 19% 不命中",
               len(fs) == 0, str(fs))

        # 样本不足(前窗仅 2 笔)
        reset_all()
        for _ in range(2):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=old)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 150, 45)
        fs = await svc.detect_price_manip()
        record("样本不足降级"
               "(前窗 <3)",
               len(fs) == 0, str(fs))

        # 无信值支付叠加不命中
        reset_all()
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=old)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 150, 0)
        fs = await svc.detect_price_manip()
        record("无信值叠加不命中",
               len(fs) == 0, str(fs))

        # 口径: 近 7 日 vs 前 7 日均价
        reset_all()
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=old)
        mid = (now - timedelta(days=5)
               ).isoformat()
        for _ in range(2):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=mid)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 160, 48)
        fs = await svc.detect_price_manip()
        record("口径(前窗=8-14 日"
               "不含近 7 日)",
               len(fs) == 1
               and fs[0]["detail"]
               ["avgPrior"] == 100.0
               and fs[0]["detail"]
               ["samplesPrior"] == 3,
               str(fs))

        # 建议书不自动下架
        reset_all()
        await seed_profile(1, 500.0)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 100, 30,
                            paid_at=old)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gP", 150, 45)
        fs = await svc.detect_price_manip()
        rec = await svc._save_finding(
            fs[0])
        record("建议书不自动执行",
               rec["suggested"]
               ["autoExecute"] is False
               and "executor"
               in rec["suggested"],
               str(rec["suggested"]))


class TestLiqCrunch:
    """05 LIQ-CRUNCH 流动性"""

    async def run(self):
        print("[05 LIQ-CRUNCH 流动性推演]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        repo = Xx64Repository()
        svc = Xx64RiskService()

        # 触线: 消耗 500/供给 1000=50%
        async def add_debits(n, amt=100.0):
            for k in range(n):
                eid = await \
                    repo.next_entry_id()
                await repo.save_ledger({
                    "entryId": eid,
                    "orderId": k + 1,
                    "trustId": 1,
                    "direction": "debit",
                    "transferType":
                        "pay",
                    "amount": -amt,
                    "source":
                        "consumption_"
                        "transfer",
                    "createdAt":
                        __import__(
                            "core.helpers",
                            fromlist=[
                                "ts"]).ts(),
                })

        reset_all()
        await seed_profile(1, 600.0)
        await seed_profile(2, 400.0)
        await add_debits(5)
        f = await svc.detect_liq_crunch()
        record("推演触线(50%≥40%)",
               f is not None
               and f["severity"] == "high"
               and f["detail"]
               ["consumed24h"] == 500.0
               and f["detail"]
               ["totalSupply"] == 1000.0
               and f["detail"]
               ["projectedRatio"] == 0.5,
               str(f and f["detail"]))

        # 未触线: 供给 2000
        reset_all()
        await seed_profile(1, 1600.0)
        await seed_profile(2, 400.0)
        await add_debits(5)
        f = await svc.detect_liq_crunch()
        record("未触线不命中(25%)",
               f is None, str(f))

        # 边界: 恰 40% 命中
        reset_all()
        await seed_profile(1, 1250.0)
        await add_debits(5)
        f = await svc.detect_liq_crunch()
        record("边界恰 40% 命中(≥)",
               f is not None
               and (f["detail"]
                    ["projectedRatio"])
               == 0.4,
               str(f and f["detail"]))

        # 仅建议(建议书 executor)
        rec = await svc._save_finding(f)
        record("仅建议不自动冷却",
               f["severity"] == "high"
               and rec["suggested"]
               ["autoExecute"] is False,
               str(rec["suggested"]))

        # 数字可溯源(消耗/供给/比率)
        record("数字可溯源"
               "(consumed/supply/ratio)",
               "consumed24h"
               in f["detail"]
               and "totalSupply"
               in f["detail"]
               and "projectedRatio"
               in f["detail"],
               str(f["detail"].keys()))


class TestDisposition:
    """06 分级处置"""

    async def run(self):
        print("[06 分级处置]")
        from services.xx64_risk_service import (
            disposition, risk_score,
        )

        # 三档阈值
        record("低档直通(<40)",
               disposition(39)["level"]
               == "low"
               and disposition(0)
               ["action"] == "pass",
               str(disposition(39)))
        record("中档增强(40-69)",
               disposition(40)["action"]
               == "enhanced_verify"
               and disposition(69)
               ["action"]
               == "enhanced_verify",
               "")
        record("高档冻结(≥70)",
               disposition(70)["action"]
               == "freeze_review"
               and disposition(100)
               ["action"]
               == "freeze_review",
               "")

        # tier 摩擦修正
        medium = [{"severity": "medium"}]
        record("tier 摩擦"
               "(trusted 45→27)",
               risk_score(medium,
                          "trusted") == 27
               and risk_score(medium,
                              "standard")
               == 36
               and risk_score(medium,
                              "restricted")
               == 54,
               str(risk_score(medium,
                              "trusted")))

        # tier 不豁免 high 阻断
        # (high finding 无论 tier
        # severity 权重不变)
        high = [{"severity": "high"}]
        record("tier 不豁免 high"
               "(75×0.6=45 摩擦仅"
               "修正风险分)",
               risk_score(high,
                          "trusted") == 45,
               str(risk_score(high,
                              "trusted")))

        # 叠加加权(2 medium:
        # 45+45+10=100 封顶)
        two = [{"severity": "medium"},
               {"severity": "medium"}]
        record("叠加加权+封顶",
               risk_score(two, None)
               == 100,
               str(risk_score(two,
                              None)))

        # 复核 dismissed(人工复核态)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        rid = await repo.next_risk_id()
        await repo.save_risk({
            "riskId": rid,
            "detectedAt": "2026-01-01",
            "detectorCode": "ARB-HF",
            "entityKey": "user:1",
            "severity": "medium",
            "riskScore": 45,
            "matched": True,
            "detail": {},
            "action": "enhanced_verify",
            "status": "dismissed",
        })
        r = await repo.list_risks(
            limit=5)
        record("人工复核 dismissed 态",
               any(x.get("status")
                   == "dismissed"
                   for x in r),
               str(r))


class TestHttp:
    """07 HTTP 端点"""

    async def run(self):
        print("[07 HTTP 端点+鉴权]")
        from httpx import ASGITransport, \
            AsyncClient
        from main import app

        reset_all()
        await seed_profile(1, 500.0)
        await seed_profile(2, 500.0)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}

        # 制造风险数据(高频买家)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        for _ in range(12):
            await add_order(repo, 1, 2, 1,
                            "gH", 100, 30)
        os.environ["XX64_MODE"] = "shadow"

        async with AsyncClient(
                transport=ASGITransport(
                    app=app),
                base_url="http://t"
        ) as client:
            # scan(shadow 态 200)
            resp = await client.post(
                "/api/xx64/risk/scan",
                headers=admin)
            body = resp.json() or {}
            record("HTTP scan 200"
                   "(shadow 管理面)",
                   resp.status_code == 200
                   and body.get(
                       "detectors",
                       {}).get(
                       "ARB-HF") == 1,
                   str((resp.status_code,
                        body.get(
                            "detectors"))))

            # status(观测面 off 可用)
            os.environ["XX64_MODE"] = "off"
            resp = await client.get(
                "/api/xx64/risk/status"
                "?trust_id=1",
                headers=member)
            body = resp.json() or {}
            record("HTTP status 200 画像",
                   resp.status_code == 200
                   and body.get("tier")
                   in ("trusted",
                       "standard",
                       "watched",
                       "restricted")
                   and "riskScore"
                   in body,
                   str((resp.status_code,
                        body.get("tier"))))

            # scan off 409(决策面)
            resp = await client.post(
                "/api/xx64/risk/scan",
                headers=admin)
            record("HTTP scan off 409",
                   resp.status_code == 409,
                   str(resp.status_code))

            # scan member 403
            os.environ["XX64_MODE"] \
                = "shadow"
            resp = await client.post(
                "/api/xx64/risk/scan",
                headers=member)
            record("HTTP scan member 403",
                   resp.status_code == 403,
                   str(resp.status_code))

            # 无 Role 403
            resp = await client.get(
                "/api/xx64/risk/status"
                "?trust_id=1")
            record("HTTP 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        os.environ["XX64_MODE"] = "off"


class TestConstitution:
    """08 宪法断言"""

    async def run(self):
        print("[08 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 39 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))

        from routes.xx64_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("64号路由 P3 16 端点",
               count == 16, str(count))

        # 三开关铁律
        import services.xx64_risk_service \
            as risk_mod
        record("三开关铁律"
               "(XX64/LLM/LEARN off)",
               risk_mod.current_mode()
               == "off"
               and os.environ.get(
                   "XX64_LLM_MODE")
               == "off"
               and os.environ.get(
                   "XX64_LEARN_MODE")
               == "off",
               "")

        # 预警仅建议(建议书执行人)
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        finding = {
            "detector": "LIQ-CRUNCH",
            "severity": "high",
            "entityType": "global",
            "entityId": "site",
            "detail": {},
        }
        sug = Xx64RiskService \
            ._suggestion(finding,
                         {"level": "high",
                          "action":
                              "freeze_review"})
        record("惩罚处置永不自动"
               "(executor=人工/46号)",
               sug["autoExecute"]
               is False
               and "46号" in sug.get(
                   "executor", ""),
               str(sug.get("executor")))

        # LLM 不进判定链(纯函数
        # ——判定模块无 LLM 导入)
        import inspect
        src = inspect.getsource(
            risk_mod)
        imports = [
            l.strip() for l in
            src.splitlines()
            if l.strip().startswith(
                ("import ", "from "))]
        record("LLM 不进判定链"
               "(无 LLM 依赖)",
               all("llm" not in l.lower()
                   for l in imports),
               str(imports))


async def main():
    suites = [
        TestArbHf(), TestArbMa(),
        TestPtsShock(), TestPriceManip(),
        TestLiqCrunch(), TestDisposition(),
        TestHttp(), TestConstitution(),
    ]
    for s in suites:
        await s.run()
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main())
             else 0)
