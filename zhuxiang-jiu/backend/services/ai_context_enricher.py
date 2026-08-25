"""AI 评分输入富化层(v7.8 阶段 2: 挂钩输入从硬编码中性值 → 真实业务数据)

v7.6 的领域挂钩为「零侵入」全部使用硬编码中性输入(如 bambooScore=750),
观察模式下只影响反馈质量; 但决策阻断(enforce)模式下, 假输入的阻断 =
随机误杀。本模块在挂钩与评分器之间补上数据富化:

    路由层(业务事件) ──→ ai_context_enricher ──→ 评分器(真实画像)
                            │
                            ├─ 信用档案: 竹信分/账户冻结(credit_repository)
                            ├─ 会员档案: 注册时长(member_repository.created_at)
                            ├─ 订单历史: 历史订单数/取消率(order_repository)
                            ├─ 钱包档案: 提现频率/历史驳回(wallet_repository)
                            └─ 积分流水: 当日获取爆发(points_repository)

设计约定(延续「火后不管」哲学):
    - 永不抛异常: 任何数据源查询失败 → 该字段回退中性默认值, 只记日志
    - 部分富化: 有几条真实数据填几条, 无数据源的字段直接缺省
      (评分器对缺省字段的中性处理已内建, 置信度会相应下降)
    - 只读: 富化查询不产生任何写副作用(不创建信用账户/不补流水)
"""

import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-enricher"

# 列表查询「全量」上限(仓库层 limit=0 会返回空列表, 用大数表达全量)
_LIST_ALL = 10 ** 6

# 中性默认值(数据源查询失败时的回退, 与 v7.6 硬编码值保持一致)
_NEUTRAL = {
    "bambooScore": 750,       # 信用分中性
    "registerHours": 720,     # 注册 30 天
    "accountAgeDays": 365,    # 账户 1 年
    "historyOrders": 10,      # 少量历史订单
}


def _parse_iso(value) -> datetime | None:
    """ISO 时间字符串解析(失败返回 None)"""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _hours_since(created) -> float:
    """距创建时刻的小时数(无法解析返回中性默认)"""
    dt = _parse_iso(created)
    if dt is None:
        return _NEUTRAL["registerHours"]
    return max(0.0, (datetime.now(UTC) - dt).total_seconds() / 3600)


def _days_since(created) -> float:
    """距创建时刻的天数(无法解析返回中性默认)"""
    dt = _parse_iso(created)
    if dt is None:
        return _NEUTRAL["accountAgeDays"]
    return max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400)


def _normalize_uid(member_id):
    """会员/信用/积分档案按 int 键存储, 路由传入多为字符串 → 尽力转换"""
    try:
        return int(member_id)
    except (TypeError, ValueError):
        return member_id


async def _safe(coro_factory, *, fallback=None, label: str = ""):
    """安全执行异步查询: 失败返回 fallback + 日志(火后不管)"""
    try:
        return await coro_factory()
    except Exception as exc:
        logger.warning("输入富化查询失败(%s): %s", label or "unknown", exc)
        return fallback


# ============================================================
# 订单风控输入富化(order_risk, 模块 04)
# ============================================================

async def enrich_order_risk(member_id, items: list,
                            address: dict | None = None,
                            remark: str = "") -> dict:
    """订单风控评分输入: 会员信用画像 + 历史交易行为 + 本单要素

    Returns:
        OrderRiskScorer.score(ctx) 完整输入:
        bambooScore/registerHours/orderAmount/totalQuantity/
        historyOrders/historyCancels/addressComplete/remark/orderHour
    """
    ctx: dict = {}

    # --- 本单要素(调用方直接提供, 无需查询) ---
    ctx["orderAmount"] = round(sum(
        float(i.get("unitPrice") or 0) * int(i.get("quantity") or 1)
        for i in (items or [])), 2)
    ctx["totalQuantity"] = sum(int(i.get("quantity") or 1) for i in (items or []))
    ctx["remark"] = str(remark or "")
    ctx["orderHour"] = datetime.now(UTC).hour

    # 地址完整性: 关键字段全部非空才算完整(缺省中性=完整)
    if isinstance(address, dict) and address:
        keys = ("name", "phone", "province", "city", "district", "detail")
        ctx["addressComplete"] = all(str(address.get(k) or "").strip()
                                     for k in keys)

    # --- 会员信用画像(竹信分) ---
    async def _credit():
        from repositories.credit_repository import CreditRepository
        return await CreditRepository().get_score(_normalize_uid(member_id))
    score_acc = await _safe(_credit, fallback=None, label="credit")
    if score_acc:
        ctx["bambooScore"] = int(score_acc.get("bambooScore")
                                 or _NEUTRAL["bambooScore"])
    else:
        ctx["bambooScore"] = _NEUTRAL["bambooScore"]

    # --- 会员档案(注册时长) ---
    async def _member():
        from repositories.member_repository import MemberRepository
        return await MemberRepository().get_by_id(_normalize_uid(member_id))
    member = await _safe(_member, fallback=None, label="member")
    if member and member.get("created_at"):
        ctx["registerHours"] = round(_hours_since(member["created_at"]), 1)
    else:
        ctx["registerHours"] = _NEUTRAL["registerHours"]

    # --- 历史交易行为(订单数/取消数) ---
    async def _orders():
        from repositories.order_repository import OrderRepository
        return await OrderRepository().get_by_member(member_id)
    history = await _safe(_orders, fallback=[], label="orders") or []
    if history:
        ctx["historyOrders"] = len(history)
        ctx["historyCancels"] = sum(
            1 for o in history if o.get("status") == "CANCELLED")
    else:
        ctx["historyOrders"] = _NEUTRAL["historyOrders"]
        ctx["historyCancels"] = 0

    return ctx


# ============================================================
# 提现风控输入富化(withdraw_risk, 模块 12)
# ============================================================

async def enrich_withdraw_risk(member_id, amount: float,
                               balance: float) -> dict:
    """提现风控评分输入: 提现行为画像 + 账户状态

    Args:
        amount: 提现金额; balance: 提现前可用余额(路由侧传入)

    Returns:
        WithdrawRiskScorer.score(ctx) 输入:
        amount/balance/monthlyWithdrawCount/accountAgeDays/rejectedCount/
        accountFrozen(identityVerified/abnormalIncomeRatio 暂无数据源, 缺省)
    """
    ctx: dict = {
        "amount": float(amount or 0),
        "balance": float(balance or 0),
    }
    if ctx["balance"] <= 0:
        ctx["balance"] = ctx["amount"]   # 防御: 余额缺省按全额提现计

    # --- 提现行为画像(当月次数/历史驳回) ---
    async def _withdrawals():
        from repositories.wallet_repository import WalletRepository
        repo = WalletRepository()
        all_wd = await repo.list_withdrawals(
            member_id, limit=_LIST_ALL) or []
        rejected = await repo.list_withdrawals(
            member_id, status="rejected", limit=_LIST_ALL) or []
        return all_wd, rejected
    result = await _safe(_withdrawals, fallback=None, label="withdrawals")
    if result is not None:
        all_wd, rejected = result
        now = datetime.now(UTC)
        ctx["monthlyWithdrawCount"] = sum(
            1 for w in all_wd
            if (dt := _parse_iso(w.get("createdAt")))
            and dt.year == now.year and dt.month == now.month)
        ctx["rejectedCount"] = len(rejected)

    # --- 会员档案(账户年龄) ---
    async def _member():
        from repositories.member_repository import MemberRepository
        return await MemberRepository().get_by_id(_normalize_uid(member_id))
    member = await _safe(_member, fallback=None, label="member")
    if member and member.get("created_at"):
        ctx["accountAgeDays"] = round(_days_since(member["created_at"]), 1)
    else:
        ctx["accountAgeDays"] = _NEUTRAL["accountAgeDays"]

    # --- 账户状态(钱包冻结/信用黑名单 任一命中即视为冻结) ---
    async def _frozen():
        from repositories.credit_repository import CreditRepository
        from repositories.wallet_repository import WalletRepository
        wallet = await WalletRepository().get_account(member_id)
        if wallet and str(wallet.get("status") or "") != "active":
            return True
        credit = await CreditRepository().get_score(
            _normalize_uid(member_id))
        return credit and str(credit.get("status") or "") in ("frozen", "blacklist")
    frozen = await _safe(_frozen, fallback=False, label="frozen_check")
    ctx["accountFrozen"] = bool(frozen)

    return ctx


# ============================================================
# 积分防薅羊毛输入富化(points_risk, 模块 03)
# ============================================================

async def enrich_points_risk(member_id, points_now: float) -> dict:
    """积分防薅羊毛评分输入: 当日获取爆发(含本次)

    Args:
        points_now: 本次发放积分数(已计入当日流水, 函数按流水聚合, 不再叠加)

    Returns:
        PointsRiskScorer.score(ctx) 输入: todayEarned
        (sameDeviceAccounts 等暂无数据源, 缺省)
    """
    ctx: dict = {}

    # --- 当日积分获取(正数流水聚合, 挂钩在发放后触发, 已含本次) ---
    async def _today_earned():
        from repositories.points_repository import PointsRepository
        logs = await PointsRepository().list_logs(
            _normalize_uid(member_id), limit=_LIST_ALL) or []
        now = datetime.now(UTC)
        return sum(float(l.get("points") or 0)
                   for l in logs
                   if float(l.get("points") or 0) > 0
                   and (dt := _parse_iso(l.get("createdAt")))
                   and dt.year == now.year
                   and dt.month == now.month and dt.day == now.day)
    earned = await _safe(_today_earned, fallback=None, label="points_logs")
    # 无当日流水(含查询失败) → 至少计入本次发放, 不低估爆发量
    ctx["todayEarned"] = round(earned or float(points_now or 0), 1)

    return ctx
