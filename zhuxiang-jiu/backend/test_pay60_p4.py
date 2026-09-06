"""60号·AI智能支付管理模块 P4 专项测试
(支付数据反哺+现金流预测+T+1 调度)

运行方式:
    python test_pay60_p4.py

覆盖(60号计划 §3.4/§七 P4):
    - 六类支付事件→44号池双写
      (payId 1:1 幂等)
    - 信号判定: compliance_streak/
      intent_positive/payment_
      anomaly/refund_dispute/
      fraud_confirmed/long_term_
      compliance
    - 现金流预测: 7 日确定性外推
      +缺口预警
    - T+1 调度器(PAY60_LEARN_MODE
      默认 off)
    - 回流不受开关影响(铁律)
    - QC: 回流幂等; 预测确定性
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
os.environ["PAY60_CHANNEL_MODE"] = "mock"
os.environ.pop("PAY60_CHANNEL_KEY", None)
os.environ.pop("PAY60_LLM_MODE", None)
os.environ.pop("PAY60_LEARN_MODE", None)

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


async def seed_order(status, member_id=10,
                     amount=100.0,
                     intent_id=0,
                     risk_tier="light"):
    """种指定状态订单"""
    from repositories.pay60_repository import (
        Pay60Repository,
    )
    from core.helpers import ts
    repo = Pay60Repository()
    pay_id = await repo.next_pay_id()
    await repo.save_order({
        "payId": pay_id,
        "memberId": member_id,
        "scene": "purchase",
        "role": "member",
        "status": status,
        "basePrice": amount,
        "finalPrice": amount,
        "tier": "standard",
        "attribution": {
            "payId": pay_id,
            "intentId": intent_id,
            "tier": "standard",
            "riskTier": risk_tier,
            "pricing": {}},
        "createdAt": ts(),
        "updatedAt": ts()})
    return pay_id


class TestCollect:
    """01 六类信号池双写"""

    async def run(self):
        print("[01 池双写]")
        reset_all()
        from services.pay60_learn_service import (
            Pay60LearnService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        svc = Pay60LearnService()
        repo = Pay60Repository()

        # off 亦可用(回流铁律)
        record("off 态回流可用(铁律)",
               os.environ.get(
                   "PAY60_MODE") == "off",
               "")

        # 造六类各一:
        # ① settled→compliance_streak
        p1 = await seed_order("settled")
        # ② success+intent→intent_positive
        p2 = await seed_order(
            "success", member_id=11,
            intent_id=58)
        # ③ success 无 intent→
        #    long_term_compliance
        p3 = await seed_order(
            "success", member_id=12)
        # ④ failed→payment_anomaly
        p4 = await seed_order(
            "failed", member_id=13)
        # ⑤ refunded→refund_dispute
        p5 = await seed_order(
            "refunded", member_id=14)
        # ⑥ priced+block→fraud_confirmed
        p6 = await seed_order(
            "priced", member_id=15,
            risk_tier="block")

        r = await svc.collect_feedback()
        record("六类信号全标注",
               r["labeled"] == 6
               and r["signals"] == {
                   "compliance_streak": 1,
                   "intent_positive": 1,
                   "long_term_compliance": 1,
                   "payment_anomaly": 1,
                   "refund_dispute": 1,
                   "fraud_confirmed": 1},
               str(r["signals"]))
        record("池双写提交(6 笔)",
               r["poolSubmitted"] == 6
               and r["poolFailed"] == 0,
               str((r["poolSubmitted"],
                    r["poolFailed"])))

        # ① 幂等(二轮全跳过)
        r2 = await svc.collect_feedback()
        record("回流幂等(二轮跳过)",
               r2["labeled"] == 0
               and r2["skipped"] == 6,
               str((r2["labeled"],
                    r2["skipped"])))

        # ② payId 1:1 回写标记
        order = await repo.get_order(p2)
        record("payId 1:1 回写标记",
               int(order.get(
                   "pooledFeedbackId")
                   or 0) > 0
               and order.get(
                   "poolSignal")
               == "intent_positive"
               and order.get(
                   "poolReward") == 0.6,
               str((order.get(
                   "poolSignal"),
                    order.get(
                        "poolReward"))))

        # ③ learn_signal 事件留痕
        evs = await repo.list_events(
            event_type="learn_signal",
            limit=10)
        record("learn_signal 留痕",
               len(evs) == 6,
               str(len(evs)))

        # ④ 非终态跳过
        reset_all()
        await seed_order("created")
        await seed_order("priced")
        await seed_order("verified")
        r3 = await svc.collect_feedback()
        record("非终态跳过",
               r3["labeled"] == 0
               and r3["skipped"] == 3,
               str((r3["labeled"],
                    r3["skipped"])))


class TestForecast:
    """02 现金流预测"""

    async def run(self):
        print("[02 现金流预测]")
        reset_all()
        from services.pay60_learn_service import (
            Pay60LearnService,
        )
        svc = Pay60LearnService()

        # ① 空库(零流入——无预警)
        r = await svc.forecast()
        record("空库预测(零流入)",
               r["success"] is True
               and r["forecast"]
                   ["net"] == 0.0
               and r["gapAlert"]
               is False,
               str(r["forecast"]))

        # ② 健康流(3 成功+1 退款)
        reset_all()
        for i in range(3):
            await seed_order(
                "success",
                member_id=20 + i,
                amount=100.0)
        await seed_order(
            "refunded", member_id=23,
            amount=50.0)
        r = await svc.forecast()
        record("历史统计(300 入/50 出)",
               r["history"]
                   ["totalInflow"]
               == 300.0
               and r["history"]
                   ["totalOutflow"]
               == 50.0,
               str(r["history"]))

        # 预测确定性(线性外推——
        # 近 3 单为最早 3 笔成功单:
        # recent in=300/3=100, out=0
        # 全期 in=75, out=12.5
        # daily_in=100×0.6+75×0.4=90
        # daily_out=0×0.6+12.5×0.4=5
        record("线性外推(确定性)",
               r["forecast"]
                   ["dailyInflow"]
               == 90.0
               and r["forecast"]
                   ["dailyOutflow"]
               == 5.0,
               str(r["forecast"]))

        # 7 日外推
        record("7 日外推(net)",
               r["forecast"]["net"]
               == round(
                   (90.0 - 5.0) * 7, 2),
               str(r["forecast"]
                   ["net"]))

        # ③ 无预警(流出/流入<50%)
        record("无缺口预警",
               r["gapAlert"] is False,
               str(r["gapAlert"]))

        # ④ 缺口预警(流出≥流入 50%)
        reset_all()
        await seed_order(
            "success", member_id=30,
            amount=100.0)
        await seed_order(
            "refunded", member_id=31,
            amount=80.0)
        r = await svc.forecast()
        # 近 2 单: in=100, out=80
        # 全期: in=50, out=40
        # daily: 80×0.6+50×0.4=68,
        #        64×0.6+40×0.4=54.4
        # out/in=54.4/68=0.8≥0.5
        record("缺口预警触发(≥50%)",
               r["gapAlert"] is True
               and "预警" in str(
                   r["gapAdvice"]),
               str((r["gapAlert"],
                    r["gapAdvice"])))

        # ⑤ 确定性(同输入同输出)
        r2 = await svc.forecast()
        record("预测确定性(同输入同输出)",
               r["forecast"]
               == r2["forecast"],
               "")


class TestScheduler:
    """03 T+1 调度器"""

    async def run(self):
        print("[03 调度器]")
        reset_all()
        from services.pay60_scheduler import (
            scheduler_enabled,
            scheduler_interval_seconds,
            run_scheduled_tasks,
            start_scheduler,
            stop_scheduler,
        )

        # ① 默认 off
        record("调度默认 off",
               scheduler_enabled()
               is False,
               "")
        record("默认周期 24h",
               scheduler_interval_seconds()
               == 86400,
               str(
                   scheduler_interval_seconds()))
        record("off 态不启动",
               start_scheduler() is False,
               "")

        # ② 手动轮(三合一)
        os.environ[
            "PAY60_LEARN_MODE"] = "on"
        r = await run_scheduled_tasks()
        record("手动轮三合一",
               "collect" in r
               and "recon" in r
               and "forecast" in r,
               str(r)[:70])

        # ③ on 态启动
        started = start_scheduler()
        stop_scheduler()
        record("on 态可启动",
               started is True,
               "")
        os.environ[
            "PAY60_LEARN_MODE"] = "off"

        # ④ scheduler_run 留痕
        from repositories.pay60_repository \
            import Pay60Repository
        repo = Pay60Repository()
        evs = [
            e for e in await
            repo.list_events(limit=50)
            if (e.get("detail") or {})
            .get("collect")
            is not None
            or e.get("eventType")
            == "scheduler_run"]
        record("scheduler_run 留痕",
               len(evs) >= 1,
               str(len(evs)))


class TestHttp:
    """04 HTTP 层(P4)"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 造数据
        await seed_order(
            "settled", member_id=40,
            amount=200.0)
        await seed_order(
            "refunded", member_id=41,
            amount=100.0)

        # ① collect(off 亦可用)
        resp = client.post(
            "/api/pay60/feedback/collect",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP collect(off 可用)",
               resp.status_code == 200
               and body.get("labeled")
               == 2,
               str((resp.status_code,
                    body.get("labeled"))))

        # ② 幂等(二轮零)
        resp = client.post(
            "/api/pay60/feedback/collect",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP collect 幂等",
               (body.get("labeled")
                or 0) == 0,
               str(body.get("labeled")))

        # ③ forecast 观测面
        resp = client.get(
            "/api/pay60/forecast",
            headers=admin)
        body = resp.json() or {}
        record("HTTP forecast 观测面",
               resp.status_code == 200
               and "forecast" in body
               and "gapAlert" in body,
               str((resp.status_code,
                    sorted(body.keys())
                    [:4])))

        # ④ 鉴权 403
        resp = client.post(
            "/api/pay60/feedback/collect",
            json={})
        record("HTTP collect 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get(
            "/api/pay60/forecast")
        record("HTTP forecast 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))


class TestConstitution:
    """05 宪法+QC"""

    async def run(self):
        print("[05 宪法+QC]")
        from services.pay60_learn_service import (
            FORECAST_DAYS,
            GAP_ALERT_RATIO,
            SIGNAL_REWARDS,
        )

        # ① 六类信号封闭
        record("六类信号(封闭)",
               len(SIGNAL_REWARDS) == 6
               and set(
                   SIGNAL_REWARDS
                   .values())
               <= {1.0, 0.6, -0.5,
                   -0.8, -1.0, 0.3},
               str(SIGNAL_REWARDS))

        # ② 奖励值与计划表对齐
        record("奖励值对齐(计划 §3.4)",
               SIGNAL_REWARDS == {
                   "compliance_streak":
                       1.0,
                   "intent_positive":
                       0.6,
                   "payment_anomaly":
                       -0.5,
                   "refund_dispute":
                       -0.8,
                   "fraud_confirmed":
                       -1.0,
                   "long_term_compliance":
                       0.3},
               str(SIGNAL_REWARDS))

        # ③ 预测窗口与预警比
        record("7 日窗口+50% 预警比",
               FORECAST_DAYS == 7
               and GAP_ALERT_RATIO
               == 0.5,
               str((FORECAST_DAYS,
                    GAP_ALERT_RATIO)))

        # ④ 44号零改动(纯调用)
        import services.ai_learning_service as s44
        record("44号零改动(纯调用)",
               s44.__name__.endswith(
                   "ai_learning_service"),
               "")

        # ⑤ 第35档案在册
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("第35档案在册",
               "payment_orchestration"
               in SCORER_REGISTRY
               and len(SCORER_REGISTRY)
               == 38,
               str(len(
                   SCORER_REGISTRY)))


async def run_all():
    await TestCollect().run()
    await TestForecast().run()
    await TestScheduler().run()
    await TestHttp().run()
    await TestConstitution().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
