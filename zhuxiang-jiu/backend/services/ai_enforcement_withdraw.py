"""AI 提现决策门集成(v7.8 阶段 3: withdraw_risk 首域真实拦截)

将 v7.8 阶段 1 的通用决策门(enforce_decision)落地到提现业务流:

    POST /api/wallet/withdraw
        │
        ├─ 1. 预生成提现单号(与创建复用同一单号, 保证快照配对键一致)
        ├─ 2. 输入富化(v7.8 阶段 2: 当月提现/历史驳回/账户冻结/真实余额)
        ├─ 3. enforce_decision("withdraw_risk", "withdraw:{单号}", ctx)
        │       observe: 评分+快照(v7.6 行为前置, 反馈闭环不变)
        │       shadow : + 决策审计(真实流量验证命中率, 不阻断)
        │       enforce: blocked → ValueError(409); review → 强制人工
        └─ 4. 路由按结果调用 wallet_service.withdraw(force_review=...)

关键设计(对比「创建后驳回」方案):
    - 决策前置到冻结金额之前: blocked 时零钱包变更、零流水、零回滚
      (approve_withdrawal 仅能驳回 pending 单, 无法撤销已自动通过的单)
    - 预生成单号贯穿决策与创建: 决策快照与终态事件(approve 路由的
      on_withdraw_settled)配对键天然一致, 反馈闭环零改动
    - blocked 的提现不产生业务记录: 决策依据落 AI 审计
      (enforcement audit), 后续人工复核误杀走审计记录
    - 决策门自身永不抛业务异常以外的错误(引擎 fail-open 已兜底)

 Raises:
     ValueError: AI 阻断(路由映射 409, 对用户不暴露内部评分细节)
"""

import logging

from services.ai_context_enricher import enrich_withdraw_risk
from services.ai_enforcement import enforce_decision

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-withdraw-gate"

SCORER_ID = "withdraw_risk"


async def enforce_withdrawal(user_id, amount: float) -> dict:
    """提现前置决策门(路由在调用 wallet_service.withdraw 之前执行)

    Args:
        user_id: 会员标识(富化画像用)
        amount: 提现金额

    Returns:
        {
            "withdrawNo": 预生成提现单号(传入 withdraw() 复用),
            "reviewRequired": 是否强制人工审核(enforce 模式 medium 档),
            "blocked": 是否阻断(恒为 False, 阻断直接抛 ValueError),
            "decision": enforce_decision 完整结果(含模式/评分/降级原因),
        }

    Raises:
        ValueError: enforce 模式下 AI 判定高风险(block) → 路由映射 409
    """
    from repositories.wallet_repository import WalletRepository
    repo = WalletRepository()

    # 提现前余额(决策在冻结前执行; 查询失败按全额提现防御性计分)
    balance = 0.0
    try:
        account = await repo.get_account(user_id)
        balance = float((account or {}).get("balance") or 0)
    except Exception as exc:
        logger.warning("决策门余额查询失败(user=%r): %s", user_id, exc)
    if balance <= 0:
        balance = float(amount or 0)

    # 输入富化 + 预生成单号 + 决策门
    ctx = await enrich_withdraw_risk(user_id, amount, balance)
    withdraw_no = await repo.next_withdraw_no()
    decision = await enforce_decision(
        SCORER_ID, f"withdraw:{withdraw_no}", ctx)

    if decision.get("blocked"):
        logger.info("ai_withdraw_blocked user=%r amount=%.2f score=%s "
                    "version=%s action=%s",
                    user_id, float(amount or 0), decision.get("score"),
                    decision.get("weightVersion"), decision.get("action"))
        # 用户侧不暴露内部评分细节, 复核依据见 enforcement 审计
        raise ValueError("提现申请被风控拦截, 请稍后重试或联系客服")

    return {
        "withdrawNo": withdraw_no,
        "reviewRequired": bool(decision.get("reviewRequired")),
        "blocked": False,
        "decision": decision,
    }
