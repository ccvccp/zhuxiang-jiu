"""AI 提现决策门集成测试(v7.8 阶段 3: withdraw_risk 首域真实拦截, 10 项)

覆盖:
    - observe 模式(2): 决策门放行+快照落键 / 提现单复用门内单号+终态反馈闭环
    - 保护联动(1): enforce 冷启动(反馈<50)自动降级 shadow 不阻断
    - shadow 模式(1): 高风险用户不阻断+审计记录影子标记
    - enforce 集成(3): 高风险真实拦截409零钱包副作用 /
      低风险放行自动通过 / 中风险强制人工审核(生而 pending)
    - 审计与反馈(1): 拦截决策落审计+阻断统计累计
    - HTTP 端到端(2, 宿主机): observe 200 / enforce 409 /
      enforce 中风险 200+pending

风险画像校准(真实评分器+富化数据):
    高风险: 全额提现+当月5次(3驳回)+新账户 → ≈63分(≥55 high → block)
    低风险: 1%占比+老账户+无历史 → ≈0.2分(<25 low → pass)
    中风险: 50%占比+2天账户+1驳回+当月2次 → ≈35分(25-55 medium → review)

在宿主机运行(需已安装 fastapi + httpx; 沙箱跑核心 8 项):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    python test_ai_enforcement_withdraw.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.pop("AI_ENFORCE_MODE", None)
os.environ.pop("AI_ENFORCE_SCOPES", None)

from core.helpers import ts
from repositories.ai_learning_repository import AiLearningRepository
from repositories.member_repository import MemberRepository
from repositories.store import _mock_store
from repositories.wallet_repository import WalletRepository
from services import ai_enforcement as enf
from services import ai_feedback_hooks as hooks
from services.ai_enforcement_withdraw import enforce_withdrawal
from services.wallet_service import WalletService

PASS = 0
FAIL = 0
RESULTS = []

_phone_seq = [9100]


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


async def _mk_user(days_old: float, balance: float,
                   withdrawals: list = None) -> int:
    """构造测试用户: 会员 + 活跃钱包 + 可选提现历史(分钟前)

    withdrawals: [(status, minutes_ago), ...]
    返回: 会员ID(int, 对齐路由 _require_member_id 返回类型)
    """
    now = datetime.now(timezone.utc)
    _phone_seq[0] += 1
    member = await MemberRepository().create({
        "phone": f"1380000{_phone_seq[0]}", "password": "x",
        "nickname": f"决策门U{_phone_seq[0]}", "level": 2,
        "growth_value": 600, "points": 100, "status": 1,
        "reg_source": "phone",
        "created_at": _iso(now - timedelta(days=days_old)),
        "last_login_at": _iso(now),
    })
    mid = member["id"]
    # 钱包以 int 键存储(对齐路由 _require_member_id 返回 int 的形态;
    # HTTP 层 X-Member-Id 头会被路由 int() 转换, 键类型必须一致才能命中)
    await WalletRepository().open_account(mid, {
        "status": "active", "balance": float(balance), "frozenAmount": 0.0,
        "createdAt": ts(), "updatedAt": ts(),
    })
    repo = WalletRepository()
    for i, (status, minutes_ago) in enumerate(withdrawals or [], start=1):
        await repo.save_withdrawal({
            "withdrawNo": f"WD-T{mid}-{i}", "userId": mid,
            "amount": 100.0, "fee": 0.0, "actualAmount": 100.0,
            "source": "current", "status": status,
            "createdAt": _iso(now - timedelta(minutes=minutes_ago)),
            "updatedAt": ts(),
        })
    return mid


async def _seed_feedback(total: int, correct: int) -> None:
    """塞已标注反馈(过冷启动+正确率保护)"""
    repo = AiLearningRepository()
    for i in range(total):
        await repo.add_feedback({
            "scorerId": "withdraw_risk",
            "factors": [{"name": "amount_ratio", "score": 50,
                         "weight": 0.2, "contribution": 10}],
            "scoreAtDecision": 50, "actualAction": "ok",
            "expectedAction": "ok", "correct": i < correct,
            "source": "auto", "note": f"seed:{i}",
        })


async def main():
    print("=" * 64)
    print("AI 提现决策门集成测试(observe/shadow/enforce × 真实评分器+富化)")
    print("=" * 64)
    ai_repo = AiLearningRepository()
    wallet_repo = WalletRepository()
    service = WalletService()

    # ========================================================
    # 1. observe 模式(默认): 放行 + 快照 + 反馈闭环
    # ========================================================
    clean = await _mk_user(days_old=400, balance=10000)
    gate = await enforce_withdrawal(clean, 100)
    snap = await ai_repo.get_decision_snapshot(
        "withdraw_risk", f"withdraw:{gate['withdrawNo']}")
    audits = await ai_repo.list_enforcement_audit("withdraw_risk")
    record("01_observe_gate_passes_and_snapshots",
           not gate["blocked"] and not gate["reviewRequired"]
           and bool(gate["withdrawNo"]) and snap is not None
           and snap.get("decision") == "low" and len(audits) == 0,
           f"blocked={gate.get('blocked')}, snap={snap and snap.get('decision')}, "
           f"audits={len(audits)}")

    result = await service.withdraw(
        clean, 100, "bank", "6222021234567890",
        withdraw_no=gate["withdrawNo"])
    feedback_before = await ai_repo.count_feedback("withdraw_risk")
    await hooks.on_withdraw_settled(gate["withdrawNo"], True)
    feedback_after = await ai_repo.count_feedback("withdraw_risk")
    snap_after = await ai_repo.get_decision_snapshot(
        "withdraw_risk", f"withdraw:{gate['withdrawNo']}")
    record("02_withdraw_reuses_gate_no_and_loop_pairs",
           result["withdrawNo"] == gate["withdrawNo"]
           and result["status"] == "approved"
           and feedback_after == feedback_before + 1
           and snap_after is None,
           f"no={result.get('withdrawNo')}, status={result.get('status')}, "
           f"fb={feedback_before}->{feedback_after}")

    # ========================================================
    # 2. enforce 冷启动保护(未积累反馈 → 降级 shadow 不阻断)
    # ========================================================
    os.environ["AI_ENFORCE_MODE"] = "enforce"
    os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk"
    try:
        risky = await _mk_user(
            days_old=1 / 24, balance=1000,
            withdrawals=[("rejected", 30), ("rejected", 25), ("rejected", 20),
                         ("approved", 15), ("approved", 10)])
        gate = await enforce_withdrawal(risky, 1000)
        record("03_enforce_cold_start_degrades_to_shadow",
               gate["decision"]["effectiveMode"] == "shadow"
               and not gate["blocked"]
               and str(gate["decision"].get("degradeReason") or "")
               .startswith("cold_start"),
               f"effective={gate['decision'].get('effectiveMode')}, "
               f"reason={gate['decision'].get('degradeReason')}")

        # 过保护: 塞 50 条反馈(90% 正确率) + 清正确率缓存
        await _seed_feedback(50, 45)
        enf._accuracy_cache.clear()

        # ====================================================
        # 3. shadow 模式: 高风险不阻断 + 审计
        # ====================================================
        os.environ["AI_ENFORCE_MODE"] = "shadow"
        gate = await enforce_withdrawal(risky, 1000)
        audits = await ai_repo.list_enforcement_audit("withdraw_risk", limit=5)
        record("04_shadow_high_risk_audits_without_blocking",
               not gate["blocked"] and not gate["reviewRequired"]
               and gate["decision"]["action"] == "high"
               and audits and audits[0].get("effectiveMode") == "shadow"
               and audits[0].get("blocked") is False,
               f"action={gate['decision'].get('action')}, "
               f"audit0={audits[0] if audits else None}")

        # ====================================================
        # 4. enforce: 高风险真实拦截(409 + 零钱包副作用)
        # ====================================================
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        blocked_no = None
        try:
            await enforce_withdrawal(risky, 1000)
            blocked = False
        except ValueError as exc:
            blocked = "风控拦截" in str(exc)
        # 找回被拦截的单号(审计最新一条)
        audits = await ai_repo.list_enforcement_audit("withdraw_risk", limit=1)
        blocked_no = audits[0].get("businessKey", "").split(":", 1)[-1] \
            if audits else ""
        wd_record = await wallet_repo.get_withdrawal(blocked_no) \
            if blocked_no else None
        account = await wallet_repo.get_account(risky)
        stats = await ai_repo.get_enforcement_stats("withdraw_risk")
        record("05_enforce_high_risk_blocks_with_zero_side_effects",
               blocked and wd_record is None
               and account and float(account.get("balance", -1)) == 1000.0
               and float(account.get("frozenAmount", -1)) == 0.0
               and stats.get("blocked", 0) >= 1
               and audits and audits[0].get("blocked") is True
               and audits[0].get("action") == "high",
               f"blocked={blocked}, record={wd_record is not None}, "
               f"balance={account and account.get('balance')}, "
               f"frozen={account and account.get('frozenAmount')}")

        # ====================================================
        # 5. enforce: 低风险放行(自动通过)
        # ====================================================
        gate = await enforce_withdrawal(clean, 100)
        result = await service.withdraw(
            clean, 100, "bank", "6222021234567890",
            withdraw_no=gate["withdrawNo"],
            force_review=gate["reviewRequired"])
        record("06_enforce_clean_user_auto_approves",
               not gate["blocked"] and not gate["reviewRequired"]
               and gate["decision"]["effectiveMode"] == "enforce"
               and gate["decision"]["action"] == "low"
               and result["status"] == "approved",
               f"action={gate['decision'].get('action')}, "
               f"status={result.get('status')}")

        # ====================================================
        # 6. enforce: 中风险强制人工审核(生而 pending)
        # ====================================================
        mid_user = await _mk_user(
            days_old=2, balance=1000,
            withdrawals=[("rejected", 8), ("approved", 5)])
        gate = await enforce_withdrawal(mid_user, 500)
        result = await service.withdraw(
            mid_user, 500, "bank", "6222021234567890",
            withdraw_no=gate["withdrawNo"],
            force_review=gate["reviewRequired"])
        wd = await wallet_repo.get_withdrawal(gate["withdrawNo"])
        record("07_enforce_medium_risk_forces_manual_review",
               gate["reviewRequired"] is True
               and gate["decision"]["action"] == "medium"
               and result["status"] == "pending"
               and wd and wd.get("status") == "pending"
               and wd.get("auditRemark") == "AI风控: 强制人工审核",
               f"action={gate['decision'].get('action')}, "
               f"review={gate.get('reviewRequired')}, "
               f"status={result.get('status')}, remark={wd and wd.get('auditRemark')}")

        # ====================================================
        # 7. 审计与阻断统计累计
        # ====================================================
        audits = await ai_repo.list_enforcement_audit("withdraw_risk", limit=10)
        blocked_marks = [a for a in audits if a.get("blocked")]
        stats = await ai_repo.get_enforcement_stats("withdraw_risk")
        record("08_audit_and_block_stats_accumulate",
               len(audits) >= 5 and len(blocked_marks) >= 1
               and stats.get("total", 0) >= 5
               and stats.get("blocked", 0) >= 1
               and stats.get("reviews", 0) >= 1,
               f"audits={len(audits)}, blocked_marks={len(blocked_marks)}, "
               f"stats={stats}")
    finally:
        os.environ.pop("AI_ENFORCE_MODE")
        os.environ.pop("AI_ENFORCE_SCOPES")

    # ========================================================
    # 8. HTTP 端到端(沙箱无 fastapi 时跳过)
    # ========================================================
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        print("  [SKIP] 09-10 HTTP 段 -- 沙箱无 fastapi, 宿主机可跑")
        TestClient = None

    if TestClient is not None:
        client = TestClient(app)
        # HTTP 头值必须是 str(httpx 要求), 路由 _require_member_id 会 int() 转回
        headers = {"X-Member-Id": str(clean)}
        # observe: 200
        resp = client.post("/api/wallet/withdraw",
                           json={"amount": 100, "payChannel": "bank",
                                 "bankAccount": "6222021234567890"},
                           headers=headers)
        record("09_http_observe_returns_200",
               resp.status_code == 200 and resp.json().get("success"),
               f"code={resp.status_code}")

        # enforce + 高风险: 409
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk"
        try:
            resp = client.post("/api/wallet/withdraw",
                               json={"amount": 1000, "payChannel": "bank",
                                     "bankAccount": "6222021234567890"},
                               headers={"X-Member-Id": str(risky)})
            # core.errors 全局异常处理把 ValueError 映射为:
            # {"success": False, "error": "<原 ValueError 消息>"}
            body = resp.json()
            err = str(body.get("error") or body.get("detail") or body.get("message") or "")
            record("10_http_enforce_high_risk_returns_409",
                   resp.status_code == 409 and "风控拦截" in err,
                   f"code={resp.status_code}, body={body}")
        finally:
            os.environ.pop("AI_ENFORCE_MODE")
            os.environ.pop("AI_ENFORCE_SCOPES")

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
