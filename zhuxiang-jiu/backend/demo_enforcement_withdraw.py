"""AI 提现决策门实况演示(v7.8 阶段 3)
==================================================

一键演示 enforce 模式下三种风险画像的提现请求真实处置,
全部请求走真实 FastAPI 路由(TestClient, 不占端口, 不影响运行中的后端):

    [1] 高风险(约63分) → HTTP 409 拦截 + 零钱包副作用
    [2] 中风险(约35分) → HTTP 200 + 强制人工审核(提现单生而 pending)
    [3] 低风险(约0.2分) → HTTP 200 + 自动通过(approved)

在宿主机运行(CMD, 需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    python demo_enforcement_withdraw.py

真实服务器开启拦截只需启动前设两个环境变量:
    set AI_ENFORCE_MODE=enforce
    set AI_ENFORCE_SCOPES=withdraw_risk
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

# 环境须在导入 app 前设置(演示跑独立内存态, 与其他进程互不影响)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")
os.environ["AI_ENFORCE_MODE"] = "enforce"          # 真实拦截模式
os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk"  # 提现风险域生效

from core.helpers import ts
from repositories.ai_learning_repository import AiLearningRepository
from repositories.member_repository import MemberRepository
from repositories.wallet_repository import WalletRepository
from services import ai_enforcement as enf

_seq = [0]


def _phone() -> str:
    """11 位演示手机号(时间戳低位+序号, 可重复运行不冲突)"""
    _seq[0] += 1
    return "139" + f"{int(time.time()) % 10 ** 7:07d}"[:7] + str(_seq[0])


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _mk_user(days_old: float, balance: float,
                   withdrawals: list) -> str:
    """构造演示用户: 会员 + 活跃钱包 + 提现历史 [(status, minutes_ago), ...]"""
    now = datetime.now(timezone.utc)
    member = await MemberRepository().create({
        "phone": _phone(), "password": "demo-pass", "nickname": "演示用户",
        "level": 2, "growth_value": 600, "points": 100, "status": 1,
        "reg_source": "phone",
        "created_at": _iso(now - timedelta(days=days_old)),
        "last_login_at": _iso(now),
    })
    mid = str(member["id"])
    await WalletRepository().open_account(mid, {
        "status": "active", "balance": float(balance), "frozenAmount": 0.0,
        "createdAt": ts(), "updatedAt": ts(),
    })
    repo = WalletRepository()
    for i, (status, minutes_ago) in enumerate(withdrawals, start=1):
        await repo.save_withdrawal({
            "withdrawNo": f"WD-DEMO-{member['id']}-{i}", "userId": mid,
            "amount": 100.0, "fee": 0.0, "actualAmount": 100.0,
            "source": "current", "status": status,
            "createdAt": _iso(now - timedelta(minutes=minutes_ago)),
            "updatedAt": ts(),
        })
    return mid


async def _seed_feedback() -> None:
    """塞 50 条已标注反馈(90% 正确率) → 通过冷启动/正确率两重保护"""
    repo = AiLearningRepository()
    for i in range(50):
        await repo.add_feedback({
            "scorerId": "withdraw_risk",
            "factors": [{"name": "amount_ratio", "score": 50,
                         "weight": 0.2, "contribution": 10}],
            "scoreAtDecision": 50, "actualAction": "ok",
            "expectedAction": "ok", "correct": i < 45,
            "source": "auto", "note": f"demo-seed:{i}",
        })
    enf._accuracy_cache.clear()


def _fmt(resp) -> str:
    try:
        return json.dumps(resp.json(), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return resp.text[:200]


def _curl_hint(mid: str, amount) -> str:
    return (f'curl -X POST http://localhost:8000/api/wallet/withdraw '
            f'-H "Content-Type: application/json" -H "X-Member-Id: {mid}" '
            f'-d "{{\\"amount\\":{amount},\\"payChannel\\":\\"bank\\",'
            f'\\"bankAccount\\":\\"6222021234567890\\"}}"')


def _withdraw(client, mid: str, amount):
    return client.post(
        "/api/wallet/withdraw",
        json={"amount": amount, "payChannel": "bank",
              "bankAccount": "6222021234567890"},
        headers={"X-Member-Id": str(mid)})


async def main() -> int:
    print("=" * 72)
    print("AI 提现决策门实况演示  (AI_ENFORCE_MODE=enforce, SCOPES=withdraw_risk)")
    print("=" * 72)

    await _seed_feedback()
    risky = await _mk_user(1 / 24, 1000, [
        ("rejected", 30), ("rejected", 25), ("rejected", 20),
        ("approved", 15), ("approved", 10)])
    mid_risk = await _mk_user(2, 1000, [("rejected", 8), ("approved", 5)])
    clean = await _mk_user(400, 10000, [])
    print("前置数据就绪: 保护检查已通过(50 条反馈, 正确率 90%)")
    print(f"  高风险用户 {risky}: 1小时新账户, 余额 ¥1000, 当月 5 次提现(3 次驳回)")
    print(f"  中风险用户 {mid_risk}: 2天账户, 余额 ¥1000, 2 次提现(1 次驳回)")
    print(f"  低风险用户 {clean}: 400天老账户, 余额 ¥10000, 无提现历史")
    print()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # ---------------- [1] 高风险 → 409 ----------------
    print("-" * 72)
    print(f"[1/3] 高风险提现: 用户 {risky} 申请 ¥1000(全额)")
    print("      等价命令: " + _curl_hint(risky, 1000))
    resp = _withdraw(client, risky, 1000)
    print(f"      <- HTTP {resp.status_code}  {_fmt(resp)}")

    acc = await WalletRepository().get_account(risky)
    audits = await AiLearningRepository().list_enforcement_audit(
        "withdraw_risk", limit=10)
    blocked_rec = next((a for a in audits if a.get("blocked")), None)
    wd_blocked = None
    if blocked_rec:
        no = str(blocked_rec.get("businessKey") or "").split(":", 1)[-1]
        wd_blocked = await WalletRepository().get_withdrawal(no)
    v1 = (resp.status_code == 409
          and "风控拦截" in str((resp.json() or {}).get("detail") or "")
          and acc and float(acc.get("balance", -1)) == 1000.0
          and float(acc.get("frozenAmount", -1)) == 0.0
          and wd_blocked is None)
    print(f"      钱包状态: 余额 ¥{float((acc or {}).get('balance', 0)):.2f} / "
          f"冻结 ¥{float((acc or {}).get('frozenAmount', 0)):.2f} / "
          f"被拦截单号无提现记录={wd_blocked is None}")
    print(f"      [{'PASS' if v1 else 'FAIL'}] blocked -> 409 + 零钱包副作用\n")

    # ---------------- [2] 中风险 → 强制人工 ----------------
    print("-" * 72)
    print(f"[2/3] 中风险提现: 用户 {mid_risk} 申请 ¥500")
    print("      等价命令: " + _curl_hint(mid_risk, 500))
    resp = _withdraw(client, mid_risk, 500)
    print(f"      <- HTTP {resp.status_code}  {_fmt(resp)}")
    body = resp.json() if resp.status_code == 200 else {}
    wd_no = body.get("withdrawNo")
    wd_rec = await WalletRepository().get_withdrawal(wd_no) if wd_no else None
    v2 = (resp.status_code == 200 and body.get("status") == "pending"
          and wd_rec and wd_rec.get("status") == "pending"
          and wd_rec.get("auditRemark") == "AI风控: 强制人工审核")
    if wd_rec:
        print(f"      提现单 {wd_no}: status={wd_rec.get('status')} "
              f"auditRemark=\"{wd_rec.get('auditRemark')}\""
              f"(本应自动通过, 被 AI 改判人工审核)")
    print(f"      [{'PASS' if v2 else 'FAIL'}] reviewRequired -> 生而 pending\n")

    # ---------------- [3] 低风险 → 自动通过 ----------------
    print("-" * 72)
    print(f"[3/3] 低风险提现: 用户 {clean} 申请 ¥100")
    print("      等价命令: " + _curl_hint(clean, 100))
    resp = _withdraw(client, clean, 100)
    print(f"      <- HTTP {resp.status_code}  {_fmt(resp)}")
    body = resp.json() if resp.status_code == 200 else {}
    v3 = (resp.status_code == 200 and body.get("status") == "approved"
          and body.get("autoApproved") is True)
    print(f"      [{'PASS' if v3 else 'FAIL'}] 低风险不受影响, 照常自动通过\n")

    # ---------------- 审计与统计(HTTP) ----------------
    print("-" * 72)
    print("[附加] 决策审计流(GET /api/ai-learning/enforcement/withdraw_risk/audit)")
    resp = client.get("/api/ai-learning/enforcement/withdraw_risk/audit?limit=5",
                      headers={"X-Role": "admin"})
    for r in (resp.json().get("records") or []):
        print(f"      {str(r.get('decidedAt'))[:19]}  "
              f"mode={r.get('effectiveMode'):8s} score={r.get('score')!s:5.5s} "
              f"blocked={r.get('blocked')!s:5s} review={r.get('reviewRequired')!s:5s} "
              f"{r.get('businessKey')}")

    print()
    print("[附加] 阻断概览(GET /api/ai-learning/enforcement/withdraw_risk/overview)")
    resp = client.get("/api/ai-learning/enforcement/withdraw_risk/overview",
                      headers={"X-Role": "admin"})
    ov = resp.json() or {}
    st = ov.get("stats") or {}
    print(f"      模式={ov.get('mode')}  累计决策={st.get('total')}  "
          f"真实阻断={st.get('blocked')}  强制人工={st.get('reviews')}  "
          f"阻断率={float(st.get('blockRate') or 0) * 100:.1f}%")

    # ---------------- 汇总 ----------------
    print()
    print("=" * 72)
    verdicts = [("高风险 blocked -> HTTP 409 + 零钱包副作用", v1),
                ("中风险 reviewRequired -> 强制人工审核(生而 pending)", v2),
                ("低风险照常自动通过(approved)", v3)]
    for name, v in verdicts:
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
    print("=" * 72)
    print("真实服务器开启拦截(CMD):")
    print("  set AI_ENFORCE_MODE=enforce")
    print("  set AI_ENFORCE_SCOPES=withdraw_risk")
    print("  python -m uvicorn main:app --port 8000")
    return 0 if all(v for _, v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
