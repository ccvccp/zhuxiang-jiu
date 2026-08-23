"""AI 自动反馈挂钩层测试(v7.6: 业务事件 → 决策快照 → 自动反馈, 30 项)

覆盖:
    - 开关与配对引擎(5): 默认开启 / AI_FEEDBACK_HOOKS=off / 评分+快照 /
      消费即删(业务键去重) / 无快照静默跳过
    - 路由类因子合成(2): 支付(5因子 snake_case 对齐权重档案) /
      物流(speed/cost 从最优候选项合成)
    - 订单域闭环(5): 创建→快照 / 完成·退款·退货→自动反馈(语义映射) /
      决策×终态映射表
    - 其余领域闭环(7): 支付路由 / 物流路由 / 登录 / 积分 /
      提现(申请+终态) / 推广奖励 / 流量佣金
    - 火后不管(2): 挂钩关闭不留痕 / 重复终态不重复反馈
    - 调度器(5): 默认开启 / off 关闭 / 反馈不足跳过 /
      满阈值自动学习 / 启动-停止生命周期
    - HTTP 端到端(3): 登录→自动反馈 / lifespan 启停调度器 / overview 汇总
    - 报表(1): learning_report 结构(版本正确率+曲线+趋势+auto占比)

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_ai_feedback_hooks.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from repositories.ai_learning_repository import AiLearningRepository
from services import ai_feedback_hooks as hooks
from services import ai_learning_scheduler as sched
from services.ai_learning_service import (
    default_weights, learning_report, overview, update_learning_config,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


async def _auto_feedback(scorer_id: str, *, note_prefix: str = None) -> list:
    """读取指定评分器的自动反馈(source=auto), 可按备注前缀过滤"""
    repo = AiLearningRepository()
    out = []
    for fb in await repo.list_feedback(scorer_id, limit=0):
        if fb.get("source") != "auto":
            continue
        if note_prefix is None or str(fb.get("note") or "").startswith(note_prefix):
            out.append(fb)
    return out


def _items(unit_price=199.0, qty=1):
    return [{"productId": "ZX42-2026L07", "quantity": qty,
             "unitPrice": unit_price}]


async def main():
    print("=" * 64)
    print("AI 自动反馈挂钩层测试(业务事件 → 决策快照 → 自动反馈 → 定时学习)")
    print("=" * 64)
    repo = AiLearningRepository()

    # ========================================================
    # 1. 开关与配对引擎
    # ========================================================
    record("01_hooks_enabled_by_default", hooks.hooks_enabled())

    os.environ["AI_FEEDBACK_HOOKS"] = "off"
    off_ok = not hooks.hooks_enabled()
    os.environ.pop("AI_FEEDBACK_HOOKS")
    record("02_hooks_env_off_disables", off_ok and hooks.hooks_enabled())

    result = await hooks.score_and_snapshot(
        "order_risk", "order:SNAP-1",
        {"bambooScore": 750, "registerHours": 720, "orderAmount": 199.0,
         "historyOrders": 10})
    snap = await repo.get_decision_snapshot("order_risk", "order:SNAP-1")
    record("03_score_and_snapshot_stores",
           result is not None and snap is not None
           and snap.get("decision") in ("pass", "review", "block")
           and isinstance(snap.get("factors"), list) and snap["factors"]
           and bool(snap.get("weightVersion")),
           f"snap={snap and {k: snap.get(k) for k in ('decision', 'weightVersion')}}")

    first = await repo.consume_decision_snapshot("order_risk", "order:SNAP-1")
    second = await repo.consume_decision_snapshot("order_risk", "order:SNAP-1")
    record("04_snapshot_consume_once_only",
           first is not None and second is None)

    silent = await hooks.record_outcome("order_risk", "order:NOPE-404",
                                        "completed")
    record("05_record_outcome_without_snapshot_is_silent", silent is None)

    # ========================================================
    # 2. 路由类评分器因子合成(camelCase → snake_case 对齐权重档案)
    # ========================================================
    from services.ai_scoring_service import (
        LogisticsRoutingScorer, PaymentRoutingScorer,
    )

    pay_res = await PaymentRoutingScorer().score(
        {"amount": 299.0, "sceneType": "order_pay"})
    pay_factors = hooks._factors_from_result("payment_routing", pay_res)
    pay_names = {f["name"] for f in pay_factors or []}
    record("06_payment_factors_snake_aligned",
           pay_factors is not None
           and pay_names == {"availability", "limit_fit", "cost",
                             "scene_fit", "capacity"}
           and pay_names <= set(default_weights("payment_routing")),
           f"names={sorted(pay_names or [])}")

    lg_res = await LogisticsRoutingScorer().score(
        {"budget": "balanced", "weight": 1.0})
    lg_factors = hooks._factors_from_result("logistics_routing:balanced",
                                            lg_res)
    lg_names = {f["name"] for f in lg_factors or []}
    record("07_logistics_factors_from_best_candidate",
           lg_factors is not None
           and lg_names == {"speed", "cost"}
           and lg_names <= set(default_weights("logistics_routing:balanced")),
           f"names={sorted(lg_names or [])}")

    # ========================================================
    # 3. 订单域闭环(创建→快照→终态→自动反馈)
    # ========================================================
    await hooks.on_order_created("ORD-T1", 901, _items())
    snap = await repo.get_decision_snapshot("order_risk", "order:ORD-T1")
    record("08_on_order_created_stores_snapshot",
           snap is not None
           and snap.get("decision") in ("pass", "review", "block"),
           f"snap={snap and snap.get('decision')}")

    for tag, outcome in (("completed", "completed"),
                         ("refunded", "refunded"),
                         ("returning", "returning")):
        order_id = f"ORD-{tag.upper()}"
        await hooks.on_order_created(order_id, 901, _items())
        snap = await repo.get_decision_snapshot("order_risk",
                                                f"order:{order_id}")
        decision = (snap or {}).get("decision")
        await hooks.on_order_outcome(order_id, outcome)
        fbs = await _auto_feedback("order_risk",
                                   note_prefix=f"order {outcome}")
        ok = (len(fbs) == 1 and decision is not None
              and fbs[0].get("correct")
              == hooks._ORDER_OUTCOME_CORRECT.get((decision, outcome)))
        record(f"09_on_order_outcome_{tag}", ok,
               f"decision={decision}, fb={fbs[0].get('correct') if fbs else None}")

    tbl = hooks._ORDER_OUTCOME_CORRECT
    record("10_order_outcome_semantics_table",
           tbl.get(("pass", "completed")) is True
           and tbl.get(("pass", "refunded")) is False
           and tbl.get(("review", "refunded")) is True
           and tbl.get(("review", "completed")) is False
           and tbl.get(("block", "completed")) is False)

    # ========================================================
    # 4. 其余领域闭环
    # ========================================================
    # 11 支付路由: 推荐渠道 vs 实际渠道
    await hooks.on_payment("ORD-PAY1", "wechat", 299.0)
    recommended = pay_res["recommendation"]["channelCode"]
    fbs = await _auto_feedback("payment_routing", note_prefix="pay ")
    record("11_on_payment_pairs_recommendation",
           len(fbs) == 1 and fbs[0].get("actualAction") == "wechat"
           and fbs[0].get("correct") == (recommended == "wechat"),
           f"recommended={recommended}")

    # 12 物流路由: 推荐承运商 vs 实际承运商
    await hooks.on_shipped("ORD-SHIP1", "SF")
    recommended_carrier = lg_res["recommendation"]["carrier"]
    fbs = await _auto_feedback("logistics_routing:balanced",
                               note_prefix="ship ")
    record("12_on_shipped_pairs_recommendation",
           len(fbs) == 1 and fbs[0].get("actualAction") == "SF"
           and fbs[0].get("correct") == (recommended_carrier == "SF"),
           f"recommended={recommended_carrier}")

    # 13 登录成功: 凭证有效期望 allow
    from services.ai_scoring_auth_service import AuthRiskScorer
    await hooks.on_login_success("13800009999")
    auth_res = await AuthRiskScorer().score(
        {"failedAttempts": 0, "newDevice": False, "ipRiskType": "clean"})
    decision = hooks._extract_decision("auth_risk", auth_res)
    fbs = await _auto_feedback("auth_risk", note_prefix="login ")
    record("13_on_login_success_pairs_auth_risk",
           len(fbs) == 1 and fbs[0].get("actualAction") == "logged_in"
           and fbs[0].get("correct") == (decision == "allow"),
           f"decision={decision}")

    # 14 积分发放: 正常发放期望 low
    from services.ai_scoring_ext_service import PointsRiskScorer
    await hooks.on_points_earned("901", 10.0)
    pts_res = await PointsRiskScorer().score(
        {"todayEarned": 10.0, "sameDeviceAccounts": 1})
    decision = hooks._extract_decision("points_risk", pts_res)
    fbs = await _auto_feedback("points_risk", note_prefix="points ")
    record("14_on_points_earned_pairs_points_risk",
           len(fbs) == 1 and fbs[0].get("correct") == (decision == "low"),
           f"decision={decision}")

    # 15 提现: 申请→快照, 终态→反馈(通过期望 low)
    await hooks.on_withdraw_requested("WD-T1", 300.0, 1200.0)
    snap = await repo.get_decision_snapshot("withdraw_risk", "withdraw:WD-T1")
    decision = (snap or {}).get("decision")
    await hooks.on_withdraw_settled("WD-T1", True)
    fbs = await _auto_feedback("withdraw_risk", note_prefix="withdraw ")
    record("15_on_withdraw_lifecycle_pairs",
           snap is not None and len(fbs) == 1
           and fbs[0].get("actualAction") == "approved"
           and fbs[0].get("correct") == (decision == "low"),
           f"decision={decision}")

    # 16 推广奖励: 正常发放期望 pay
    from services.ai_scoring_service import PromotionAntiFraudScorer
    await hooks.on_promotion_reward("GRANT-T1")
    promo_res = await PromotionAntiFraudScorer().score(
        {"relationCount": 5, "avgBindToRewardHours": 48,
         "inactiveInviteeRatio": 0.2})
    decision = hooks._extract_decision("promotion_antifraud", promo_res)
    fbs = await _auto_feedback("promotion_antifraud",
                               note_prefix="promo reward ")
    record("16_on_promotion_reward_pairs",
           len(fbs) == 1 and fbs[0].get("correct") == (decision == "pay"),
           f"decision={decision}")

    # 17 流量佣金: 正常计佣期望 pass
    from services.ai_scoring_service import TrafficAntiFraudScorer
    await hooks.on_traffic_commission("COMM-T1", 100)
    trf_res = await TrafficAntiFraudScorer().score(
        {"recentCount": 10, "totalRecords": 100, "newAccountRatio": 0.1})
    decision = hooks._extract_decision("traffic_antifraud", trf_res)
    fbs = await _auto_feedback("traffic_antifraud", note_prefix="commission ")
    record("17_on_traffic_commission_pairs",
           len(fbs) == 1 and fbs[0].get("correct") == (decision == "pass"),
           f"decision={decision}")

    # ========================================================
    # 5. 火后不管(挂钩关闭不留痕 / 重复终态去重)
    # ========================================================
    os.environ["AI_FEEDBACK_HOOKS"] = "off"
    await hooks.on_order_created("ORD-OFF", 901, _items(100.0))
    snap_off = await repo.get_decision_snapshot("order_risk", "order:ORD-OFF")
    silent = await hooks.record_outcome("order_risk", "order:ORD-OFF",
                                        "completed")
    os.environ.pop("AI_FEEDBACK_HOOKS")
    record("18_hooks_off_leaves_no_trace",
           snap_off is None and silent is None)

    await hooks.on_order_created("ORD-DUP", 901, _items(100.0))
    await hooks.on_order_outcome("ORD-DUP", "completed")
    n1 = len(await _auto_feedback("order_risk"))
    await hooks.on_order_outcome("ORD-DUP", "completed")
    n2 = len(await _auto_feedback("order_risk"))
    record("19_duplicate_outcome_no_double_feedback", n2 == n1,
           f"n1={n1}, n2={n2}")

    # ========================================================
    # 6. 定时学习调度器
    # ========================================================
    record("20_scheduler_enabled_by_default", sched.scheduler_enabled())

    os.environ["AI_LEARNING_AUTO"] = "off"
    off_ok = not sched.scheduler_enabled()
    os.environ.pop("AI_LEARNING_AUTO")
    record("21_scheduler_env_off_disables", off_ok and sched.scheduler_enabled())

    before = (await repo.get_scheduler_stats() or {}).get("runs", 0)
    stats = await sched.run_scheduled_learning()
    record("22_scheduler_skips_insufficient_feedback",
           stats.get("runs") == before + 1
           and stats.get("lastLearnedScorers") == 0,
           f"runs={stats.get('runs')}, learned={stats.get('lastLearnedScorers')}")

    await update_learning_config("traffic_antifraud", {"min_feedback": 1})
    stats = await sched.run_scheduled_learning()
    learned = [r for r in stats.get("lastResults", [])
               if r.get("scorerId") == "traffic_antifraud"]
    record("23_scheduler_auto_learns_when_threshold_met",
           len(learned) == 1 and learned[0].get("learnedFrom", 0) >= 1
           and bool(learned[0].get("newVersion")),
           f"learned={learned}")

    async def _cycle():
        started = sched.start_scheduler()
        running = sched.scheduler_running()
        sched.stop_scheduler()
        return started, running

    started, running_in = await _cycle()
    record("24_scheduler_start_stop_cycle",
           started and running_in and not sched.scheduler_running(),
           f"started={started}, running_in={running_in}")

    # ========================================================
    # 7. HTTP 端到端(沙箱无 fastapi 时跳过本段)
    # ========================================================
    try:
        from fastapi.testclient import TestClient
        from main import app
        from services.ai_learning_scheduler import scheduler_running
    except ImportError:
        print("  [SKIP] 25-27 HTTP 段 -- 沙箱无 fastapi, 宿主机可跑")
        TestClient = None

    if TestClient is not None:
        client = TestClient(app)
        phone = "13911112222"
        resp = client.post("/api/auth/register",
                           json={"phone": phone, "password": "pass123456"})
        reg_ok = resp.status_code in (200, 201)
        resp = client.post("/api/auth/login",
                           json={"phone": phone, "password": "pass123456"})
        fbs = await _auto_feedback("auth_risk", note_prefix=f"login {phone}")
        record("25_http_login_triggers_auto_feedback",
               reg_ok and resp.status_code == 200 and len(fbs) >= 1
               and fbs[-1].get("source") == "auto",
               f"reg={reg_ok}, login={resp.status_code}, n={len(fbs)}")

        with TestClient(app) as client:
            resp = client.get("/api/health")
            running_inside = scheduler_running()
        record("26_http_lifespan_manages_scheduler",
               resp.status_code == 200 and running_inside
               and not scheduler_running(),
               f"health={resp.status_code}, inside={running_inside}")

        ov = await overview()
        auth_entry = next((s for s in ov.get("scorers", [])
                           if s.get("scorerId") == "auth_risk"), {})
        record("27_overview_reports_auto_feedback",
               ov.get("scorerCount") == 16
               and auth_entry.get("autoFeedback24h", 0) >= 1
               and isinstance(ov.get("scheduler", {}).get("runs"), int),
               f"auto24h={auth_entry.get('autoFeedback24h')}")

    # ========================================================
    # 8. 学习效果报表
    # ========================================================
    rep = await learning_report("order_risk")
    record("28_learning_report_structure",
           rep.get("success") and rep.get("autoFeedback", 0) >= 1
           and isinstance(rep.get("versions"), list) and rep["versions"]
           and isinstance(rep.get("curve"), list)
           and "last10CorrectRate" in rep.get("recentTrend", {})
           and "driftScore" in rep.get("drift", {}),
           f"auto={rep.get('autoFeedback')}, "
           f"versions={len(rep.get('versions') or [])}")

    # ========================================================
    # 汇总
    # ========================================================
    print("-" * 64)
    for line in RESULTS:
        print(line)
    print("-" * 64)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
