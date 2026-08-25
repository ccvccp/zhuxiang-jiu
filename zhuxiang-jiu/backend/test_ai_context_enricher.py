"""AI 评分输入富化层测试(v7.8 阶段 2: 硬编码中性值 → 真实业务数据, 15 项)

覆盖:
    - 订单风控富化(6): 未知会员中性回退 / 注册时长(真实 created_at) /
      竹信分(信用档案) / 历史订单数+取消数 / 本单要素(金额/件数/时段) /
      地址完整性判定
    - 提现风控富化(5): 金额与提现前余额透传 / 未知会员账户年龄回退 /
      当月提现次数(跨月剔除) / 历史驳回数 / 钱包冻结状态
    - 积分富化(2): 当日正数流水聚合(负数/昨日剔除, str→int 键转换) /
      无流水回退本次发放值
    - 挂钩集成(1): on_withdraw_requested(member_id) 富化后快照含真实因子
    - HTTP 接线(1): 提现路由触发 withdraw_risk 快照(v7.6 缺失挂钩修复)

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    python test_ai_context_enricher.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone, UTC

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from core.helpers import ts
from repositories.credit_repository import CreditRepository
from repositories.member_repository import MemberRepository
from repositories.order_repository import OrderRepository
from repositories.points_repository import PointsRepository
from repositories.store import _mock_store
from repositories.wallet_repository import WalletRepository
from services import ai_feedback_hooks as hooks
from services.ai_context_enricher import (
    enrich_order_risk, enrich_points_risk, enrich_withdraw_risk,
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


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _setup():
    """构造测试画像: 会员 9001(2小时前注册) + 信用分 + 历史订单 + 提现记录"""
    for k in list(_mock_store.keys()):
        if k.startswith(("wallet", "_wallet", "orders_v2", "_order")):
            del _mock_store[k]
    _mock_store.pop("members", None)
    _mock_store["_member_seq"] = 9000

    now = datetime.now(UTC)
    # 会员: 2 小时前注册(真实 created_at)
    member_repo = MemberRepository()
    await member_repo.create({
        "phone": "13800009001", "password": "x", "nickname": "富化测试",
        "level": 2, "growth_value": 600, "points": 100, "status": 1,
        "reg_source": "phone", "created_at": _iso(now - timedelta(hours=2)),
        "last_login_at": _iso(now),
    })   # → id 9001

    # 信用档案: 竹信分 880
    credit_repo = CreditRepository()
    await credit_repo.save_score({
        "userId": 9001, "bambooScore": 880, "creditLevel": "L4",
        "payLaterQuota": 0.0, "status": "normal", "updatedAt": ts(),
    })

    # 历史订单: 3 单(1 完成 + 1 取消 + 1 待付款)
    order_repo = OrderRepository()
    for oid, status in (("ORD-E1", "COMPLETED"), ("ORD-E2", "CANCELLED"),
                        ("ORD-E3", "PENDING")):
        await order_repo.create({
            "orderId": oid, "memberId": "9001", "status": status,
            "createdAt": _iso(now - timedelta(days=1)),
        })

    # 提现记录: 当月 2 单(1 驳回) + 上月 1 单
    wallet_repo = WalletRepository()
    for wn, (status, created) in enumerate([
            ("rejected", now - timedelta(days=2)),
            ("approved", now - timedelta(days=1)),
            ("paid", now - timedelta(days=40))], start=1):
        await wallet_repo.save_withdrawal({
            "withdrawNo": f"WD-E{wn}", "userId": "9001", "amount": 100.0,
            "fee": 0.0, "actualAmount": 100.0, "source": "current",
            "status": status, "createdAt": _iso(created), "updatedAt": ts(),
        })

    # 积分流水: 今日 +50/+30/-20, 昨日 +99
    # (当日两条正流水均用 now: 聚合只按日期过滤, 用相对时刻在 UTC 午夜
    #  后第一小时会跨界误判 —— now-1h 属昨日, 修复边界 flake)
    points_repo = PointsRepository()
    for points, created in ((50, now), (30, now),
                            (-20, now), (99, now - timedelta(days=1))):
        await points_repo.add_log({
            "userId": 9001, "source": "signin", "type": "earn",
            "points": points, "status": 1, "createdAt": _iso(created),
        })


async def main():
    print("=" * 64)
    print("AI 评分输入富化层测试(真实业务数据 → 评分器画像)")
    print("=" * 64)
    await _setup()
    repo_ai = None
    from repositories.ai_learning_repository import AiLearningRepository
    repo_ai = AiLearningRepository()

    items = [{"productId": "ZX42-2026L07", "quantity": 2, "unitPrice": 268.0}]

    # ========================================================
    # 1. 订单风控富化
    # ========================================================
    ctx = await enrich_order_risk(
        999999, items, address={"name": "张三", "phone": "138...",
                                "province": "川", "city": "成都",
                                "district": "xx", "detail": "yy"},
        remark="尽快发货")
    record("01_unknown_member_neutral_fallback",
           ctx["bambooScore"] == 750 and ctx["registerHours"] == 720
           and ctx["historyOrders"] == 10 and ctx["historyCancels"] == 0,
           f"ctx={ctx.get('bambooScore')}/{ctx.get('registerHours')}/"
           f"{ctx.get('historyOrders')}")

    ctx = await enrich_order_risk("9001", items, remark="")
    record("02_register_hours_from_real_created_at",
           1.5 <= ctx["registerHours"] <= 3.0,
           f"registerHours={ctx.get('registerHours')}")
    record("03_bamboo_score_from_credit_profile",
           ctx["bambooScore"] == 880, f"bamboo={ctx.get('bambooScore')}")
    record("04_order_history_counts",
           ctx["historyOrders"] == 3 and ctx["historyCancels"] == 1,
           f"orders={ctx.get('historyOrders')}, "
           f"cancels={ctx.get('historyCancels')}")
    record("05_current_order_elements",
           ctx["orderAmount"] == 536.0 and ctx["totalQuantity"] == 2
           and ctx["orderHour"] == datetime.now(UTC).hour,
           f"amount={ctx.get('orderAmount')}, qty={ctx.get('totalQuantity')}, "
           f"hour={ctx.get('orderHour')}")

    ctx = await enrich_order_risk("9001", items,
                                  address={"name": "张三", "phone": "",
                                           "province": "川", "city": "",
                                           "district": "", "detail": ""})
    record("06_address_completeness_detected",
           ctx.get("addressComplete") is False,
           f"addressComplete={ctx.get('addressComplete')}")

    # ========================================================
    # 2. 提现风控富化
    # ========================================================
    ctx = await enrich_withdraw_risk("9001", 300.0, 1200.0)
    record("07_amount_balance_passthrough",
           ctx["amount"] == 300.0 and ctx["balance"] == 1200.0,
           f"amount={ctx.get('amount')}, balance={ctx.get('balance')}")
    record("08_monthly_withdraw_count_excludes_last_month",
           ctx.get("monthlyWithdrawCount") == 2
           and ctx.get("rejectedCount") == 1,
           f"monthly={ctx.get('monthlyWithdrawCount')}, "
           f"rejected={ctx.get('rejectedCount')}")
    record("09_account_age_days_real",
           0.04 <= ctx.get("accountAgeDays", 0) <= 0.15,
           f"ageDays={ctx.get('accountAgeDays')}")
    record("10_not_frozen_when_active",
           ctx.get("accountFrozen") is False,
           f"frozen={ctx.get('accountFrozen')}")

    # 冻结场景: 钱包账户状态非 active
    wallet_repo = WalletRepository()
    await wallet_repo.open_account("9001", {
        "status": "frozen", "balance": 1200.0, "frozenAmount": 0.0,
        "createdAt": ts(), "updatedAt": ts(),
    })
    ctx = await enrich_withdraw_risk("9001", 300.0, 1200.0)
    record("11_frozen_wallet_detected",
           ctx.get("accountFrozen") is True,
           f"frozen={ctx.get('accountFrozen')}")

    # 未知会员: accountAgeDays 回退中性 365
    ctx = await enrich_withdraw_risk(888888, 100.0, 100.0)
    record("12_unknown_member_account_age_fallback",
           ctx.get("accountAgeDays") == 365,
           f"ageDays={ctx.get('accountAgeDays')}")

    # ========================================================
    # 3. 积分富化
    # ========================================================
    ctx = await enrich_points_risk("9001", 30)
    record("13_today_earned_aggregates_positive_logs",
           ctx.get("todayEarned") == 80.0,
           f"todayEarned={ctx.get('todayEarned')}(期望 50+30, 剔除-20/昨日99)")

    ctx = await enrich_points_risk(777777, 40)
    record("14_no_logs_fallback_to_current_grant",
           ctx.get("todayEarned") == 40.0,
           f"todayEarned={ctx.get('todayEarned')}")

    # ========================================================
    # 4. 挂钩集成: 富化后快照含真实因子
    # ========================================================
    await hooks.on_withdraw_requested("WD-ENRICH-1", 300.0, 1200.0,
                                      member_id="9001")
    snap = await repo_ai.get_decision_snapshot("withdraw_risk",
                                               "withdraw:WD-ENRICH-1")
    factors = {f.get("name"): f for f in (snap or {}).get("factors", [])}
    record("15_hook_snapshot_with_enriched_factors",
           snap is not None and factors.get("amount_ratio") is not None
           and factors.get("frequency") is not None
           and snap.get("decision") in ("low", "medium", "high"),
           f"factor_names={list(factors)}, decision={snap and snap.get('decision')}")

    # ========================================================
    # 5. HTTP 接线(沙箱无 fastapi 时跳过)
    # ========================================================
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        print("  [SKIP] 16_http_withdraw_triggers_snapshot -- 沙箱无 fastapi")
    else:
        client = TestClient(app)
        # 钱包已冻结 → 用新会员走通提现
        member_repo = MemberRepository()
        m = await member_repo.create({
            "phone": "13800009003", "password": "x", "nickname": "HTTP提现",
            "level": 2, "growth_value": 600, "points": 100, "status": 1,
            "reg_source": "phone", "created_at": ts(), "last_login_at": ts(),
        })
        mid = m["id"]
        from services.wallet_service import WalletService
        ws = WalletService(wallet_repo=WalletRepository(),
                           member_repo=member_repo)
        # WalletService 的开通方法名为 open(非 open_account)
        await ws.open(mid)
        await WalletRepository().add_balance(mid, 10000.0)
        resp = client.post(
            "/api/wallet/withdraw",
            json={"amount": 1000.0, "payChannel": "bank",
                  "bankAccount": "6222021234567890"},
            headers={"X-Member-Id": str(mid)})
        wd_no = (resp.json().get("withdrawNo")
                 if resp.status_code == 200 else "")
        snap = await repo_ai.get_decision_snapshot(
            "withdraw_risk", f"withdraw:{wd_no}") if wd_no else None
        record("16_http_withdraw_triggers_snapshot",
               resp.status_code == 200 and snap is not None
               and bool(snap.get("factors")),
               f"code={resp.status_code}, wd={wd_no}, "
               f"has_snap={snap is not None}")

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("=" * 64)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
