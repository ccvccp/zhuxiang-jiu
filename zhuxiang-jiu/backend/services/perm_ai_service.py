"""权限AI智能管理模块 P1: AI 监控引擎 + 权责信用分奖惩引擎

AI 监控引擎(行为风控, 规则引擎 B 级):
    - 行为基线: 权限使用/越权尝试全部留痕(perm_audit_logs)
    - 风险因子:
        · 异常时段   02:00-06:00(北京时间)管理操作 → +20
        · 异常频率   单会员 1h 内使用 ≥50 次 → +30
        · 敏感批量   单次批量导出/查询 >100 条 → +40
        · 越权尝试   1h 内 ≥3 次无权限访问 → 触发冻结(计 75 分)
    - 风险评分 4 级处置:
        low(<40) 仅记录 / medium(40-69) 记录+通知本人 /
        high(70-89) 冻结该权限+超管复核 / extreme(≥90) 吊销全部权限+告警

权责信用分引擎(月度考核, 奖惩闭环):
    - 因子: 使用合规率 40 + 责任履行度 30 + 审批尽责度 20 + 无异常基础 10
    - 奖惩: ≥90 奖金200元(钱包收益)+500竹叶 / 80-89 奖金100元+200竹叶 /
            40-59 高危权限降权(核心权限限期缩至7天) /
            <40 全部权限冻结+通知超管追责
    - 奖惩自动执行, 钱包/积分异常不阻断考核(降级为日志)

异常约定:
    - KeyError → 404(权限/日志不存在)
    - ValueError → 409(参数非法)
    - PermissionError → 403(无权限)
"""

import logging
from datetime import datetime, timedelta, UTC

from core.locks import get_lock
from repositories.perm_repository import PermRepository
from repositories.member_repository import MemberRepository
from services.perm_service import PermService

logger = logging.getLogger(__name__)

# ============================================================
# 风险规则常量
# ============================================================

RISK_BASE = 10                     # 基础分(正常使用)
RISK_ODD_HOUR = 20                 # 异常时段(02:00-06:00 北京时间)
RISK_FREQ_THRESHOLD = 50           # 1h 使用次数阈值
RISK_FREQUENT = 30                 # 异常频率加分
RISK_BULK_THRESHOLD = 100          # 批量导出条数阈值
RISK_BULK = 40                     # 敏感批量加分
RISK_DENY_FREEZE = 3               # 1h 越权次数冻结阈值
RISK_DENY_SCORE = 75               # 越权触发冻结的风险分

LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"
LEVEL_EXTREME = "extreme"

# ============================================================
# 信用分与奖惩常量
# ============================================================

# 因子权重
W_COMPLIANCE = 40      # 使用合规率
W_DUTY = 30            # 责任履行度(签署率)
W_APPROVAL = 20        # 审批尽责度(48h 内办结率)
W_BASE = 10            # 无风险事件基础分

# 风险事件扣减权重(medium=1, high=3, extreme=6, 归一化除数)
_EVENT_WEIGHT = {LEVEL_LOW: 0, LEVEL_MEDIUM: 1, LEVEL_HIGH: 3,
                 LEVEL_EXTREME: 6}
_EVENT_WEIGHT_NORM = 6

# 奖惩档位
REWARD_TIERS = [
    # (min_score, max_score, 奖金元, 竹叶积分, 处置类型)
    (90, 100, 200.0, 500, "bonus"),
    (80, 89, 100.0, 200, "bonus"),
    (60, 79, 0.0, 0, "none"),
    (40, 59, 0.0, 0, "demote"),     # 高危权限降权
    (0, 39, 0.0, 0, "freeze"),      # 全权限冻结+追责
]

APPROVAL_SLA_HOURS = 48            # 审批时限(超时计误批风险)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _beijing_hour(dt: datetime) -> int:
    """北京时间小时(UTC+8)"""
    return (dt.hour + 8) % 24


def _score_to_level(score: int) -> str:
    if score >= 90:
        return LEVEL_EXTREME
    if score >= 70:
        return LEVEL_HIGH
    if score >= 40:
        return LEVEL_MEDIUM
    return LEVEL_LOW


class PermAiService:
    """权限 AI 监控引擎 + 权责信用分引擎"""

    def __init__(self, perm_repo: PermRepository = None,
                 member_repo: MemberRepository = None,
                 perm_service: PermService = None):
        self.repo = perm_repo or PermRepository()
        self.member_repo = member_repo or MemberRepository()
        self.perm = perm_service or PermService()

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _logs_since(self, member_id: int, hours: float,
                          action: str = None) -> list[dict]:
        """取会员近 N 小时内指定行为的审计日志"""
        since = _now() - timedelta(hours=hours)
        logs = await self.repo.list_logs(member_id=member_id, limit=1000)
        result = []
        for lg in reversed(logs):  # 时间升序遍历
            ts = _parse_iso(lg.get("createdAt", ""))
            if not ts or ts < since:
                continue
            if action and lg.get("action") != action:
                continue
            result.append(lg)
        return result

    async def _log(self, member_id: int, action: str, node_code: str,
                   risk_level: str, detail: dict, handled: str) -> dict:
        log_id = await self.repo.next_id("log")
        log = {
            "logId": log_id, "memberId": member_id, "action": action,
            "nodeCode": node_code, "riskLevel": risk_level,
            "riskScore": {"low": 10, "medium": 50, "high": 75,
                          "extreme": 95}[risk_level],
            "detail": detail, "handled": handled,
            "createdAt": _now().isoformat(),
        }
        await self.repo.save_log(log)
        return log

    async def _freeze_grant(self, grant_id: int, risk_log_id: int) -> None:
        await self.repo.update_grant(grant_id, {
            "status": "frozen", "frozenAt": _now().isoformat(),
            "frozenByLogId": risk_log_id,
        })

    async def _revoke_all_grants(self, member_id: int,
                                 risk_log_id: int) -> list[int]:
        revoked = []
        for g in await self.repo.list_grants(member_id=member_id,
                                             status="active"):
            await self.repo.update_grant(g["grantId"], {
                "status": "revoked", "revokedBy": 0,
                "revokedAt": _now().isoformat(),
                "revokedByLogId": risk_log_id,
            })
            revoked.append(g["grantId"])
        return revoked

    def _handled_by_level(self, level: str) -> str:
        return {LEVEL_LOW: "none", LEVEL_MEDIUM: "notify",
                LEVEL_HIGH: "freeze", LEVEL_EXTREME: "revoke"}[level]

    # ============================================================
    # 权限使用记录(供各业务模块调用, AI 风险评分)
    # ============================================================

    async def record_use(self, member_id: int, node_code: str,
                         bulk_count: int = 0) -> dict:
        """记录权限使用并做 AI 风险评分与 4 级处置

        Raises:
            KeyError: 权限点不存在
            PermissionError: 无权限/未签责任书/已冻结(先走权限校验)
        """
        # 1. 权限校验(超管直通; 未签/冻结/过期在此拦截)
        check = await self.perm.check_permission(member_id, node_code)

        # 2. 风险因子评分
        score = RISK_BASE
        factors = []
        hour = _beijing_hour(_now())
        if 2 <= hour < 6:
            score += RISK_ODD_HOUR
            factors.append(f"异常时段({hour}点) +{RISK_ODD_HOUR}")
        recent_uses = await self._logs_since(member_id, 1, action="use")
        if len(recent_uses) + 1 >= RISK_FREQ_THRESHOLD:
            score += RISK_FREQUENT
            factors.append(f"1h使用{len(recent_uses) + 1}次超基线 "
                           f"+{RISK_FREQUENT}")
        if bulk_count > RISK_BULK_THRESHOLD:
            score += RISK_BULK
            factors.append(f"批量导出{bulk_count}条 +{RISK_BULK}")
        score = min(100, score)
        level = _score_to_level(score)
        handled = self._handled_by_level(level)

        # 3. 留痕
        log = await self._log(
            member_id, "use", node_code, level,
            {"factors": factors or ["正常使用"], "bulkCount": bulk_count,
             "via": check.get("via", "")},
            handled)

        # 4. 分级处置
        if level == LEVEL_HIGH:
            # 冻结本次使用的授权
            grants = await self.repo.list_grants(
                member_id=member_id, node_code=node_code, status="active")
            for g in grants:
                await self._freeze_grant(g["grantId"], log["logId"])
        elif level == LEVEL_EXTREME:
            revoked = await self._revoke_all_grants(member_id, log["logId"])
            log["detail"]["revokedGrantIds"] = revoked

        logger.info("perm_use member=%r node=%s score=%d level=%s "
                    "handled=%s", member_id, node_code, score, level,
                    handled)
        return {
            "recorded": True, "riskScore": score, "riskLevel": level,
            "handled": handled, "factors": factors or ["正常使用"],
            "logId": log["logId"],
        }

    # ============================================================
    # 越权尝试升级(供 check_permission 拒绝路径调用)
    # ============================================================

    async def escalate_denials(self, member_id: int,
                               node_code: str) -> None:
        """越权尝试 1h 内 ≥3 次 → 冻结全部权限(high 级处置)"""
        denies = await self._logs_since(member_id, 1, action="deny_access")
        if len(denies) < RISK_DENY_FREEZE:
            return
        log = await self._log(
            member_id, "risk_escalation", node_code, LEVEL_HIGH,
            {"reason": f"1h内越权尝试{len(denies)}次",
             "riskScore": RISK_DENY_SCORE},
            "freeze")
        frozen = []
        for g in await self.repo.list_grants(member_id=member_id,
                                             status="active"):
            await self._freeze_grant(g["grantId"], log["logId"])
            frozen.append(g["grantId"])
        logger.warning("perm_deny_escalation member=%r frozen=%s",
                       member_id, frozen)

    # ============================================================
    # 高危复核(超管)
    # ============================================================

    async def review_risk(self, admin_id: int, log_id: int,
                          action: str, opinion: str = "") -> dict:
        """超管复核高危处置: unfreeze(解冻)/revoke(维持吊销)

        Raises:
            PermissionError: 非超管
            KeyError: 日志不存在
            ValueError: action 非法/日志非高危
        """
        if action not in ("unfreeze", "revoke"):
            raise ValueError("action 非法(须为 unfreeze/revoke)")
        log = await self.repo.get_log(log_id)
        if not log:
            raise KeyError(f"风险日志不存在(id={log_id})")
        if log.get("riskLevel") not in (LEVEL_HIGH, LEVEL_EXTREME):
            raise ValueError("仅 high/extreme 级风险事件需复核")

        if action == "unfreeze":
            # 解冻该风险事件冻结的全部授权
            unfrozen = []
            for g in await self.repo.list_grants(member_id=log["memberId"],
                                                 status="frozen"):
                if g.get("frozenByLogId") == log_id:
                    await self.repo.update_grant(g["grantId"], {
                        "status": "active",
                        "unfrozenAt": _now().isoformat()})
                    unfrozen.append(g["grantId"])
            await self.repo.update_log(log_id, {
                "reviewed": True, "reviewBy": admin_id,
                "reviewAction": "unfreeze", "reviewOpinion": opinion[:200],
                "reviewedAt": _now().isoformat()})
            result = {"success": True, "action": "unfreeze",
                      "unfrozenGrantIds": unfrozen}
        else:
            await self.repo.update_log(log_id, {
                "reviewed": True, "reviewBy": admin_id,
                "reviewAction": "revoke", "reviewOpinion": opinion[:200],
                "reviewedAt": _now().isoformat()})
            revoked = await self._revoke_all_grants(log["memberId"], log_id)
            result = {"success": True, "action": "revoke",
                      "revokedGrantIds": revoked}

        await self._log(admin_id, "risk_review", log.get("nodeCode", ""),
                        LEVEL_LOW, {"targetLogId": log_id, "action": action},
                        "none")
        return result

    # ============================================================
    # 风险概览(超管)
    # ============================================================

    async def risk_summary(self, admin_id: int) -> dict:
        """风险事件概览: 各级数量 + 待复核列表"""
        logs = await self.repo.list_logs(limit=500)
        events = [l for l in logs
                  if l.get("riskLevel") in (LEVEL_HIGH, LEVEL_EXTREME)]
        pending = [l for l in events if not l.get("reviewed")]
        return {
            "totalEvents": len(logs),
            "byLevel": {
                LEVEL_LOW: sum(1 for l in logs
                               if l.get("riskLevel") == LEVEL_LOW),
                LEVEL_MEDIUM: sum(1 for l in logs
                                  if l.get("riskLevel") == LEVEL_MEDIUM),
                LEVEL_HIGH: sum(1 for l in logs
                                if l.get("riskLevel") == LEVEL_HIGH),
                LEVEL_EXTREME: sum(1 for l in logs
                                   if l.get("riskLevel") == LEVEL_EXTREME),
            },
            "pendingReview": pending,
        }

    # ============================================================
    # 权责信用分月度考核
    # ============================================================

    def _tier_for(self, score: int) -> tuple:
        for tier in REWARD_TIERS:
            lo, hi, amount, points, rtype = tier
            if lo <= score <= hi:
                return tier
        return REWARD_TIERS[-1]

    async def _assess_member(self, member_id: int,
                             period: str) -> dict:
        """单会员考核计分(数据源: 审计日志 + 授权 + 申请单)"""
        logs = await self.repo.list_logs(member_id=member_id, limit=1000)
        period_logs = [l for l in logs
                       if (l.get("createdAt", "")[:7]) == period]

        # 因子1: 使用合规率(40) — 按风险事件加权扣减(含越权/升级事件)
        weight = sum(_EVENT_WEIGHT.get(l.get("riskLevel"), 0)
                     for l in period_logs
                     if l.get("action") != "risk_review")
        compliance = int(W_COMPLIANCE * max(
            0.0, 1 - weight / _EVENT_WEIGHT_NORM))

        # 因子2: 责任履行度(30) — 生效授权责任书签署率
        grants = await self.repo.list_grants(member_id=member_id)
        active = [g for g in grants if g.get("status") in
                  ("active", "frozen", "expired", "revoked")]
        if active:
            signed = sum(1 for g in active if g.get("dutySigned"))
            duty = int(W_DUTY * signed / len(active))
        else:
            duty = W_DUTY

        # 因子3: 审批尽责度(20) — 作为审批人 48h 内办结率
        approvals_decided = 0
        approvals_timely = 0
        for r in await self.repo.list_requests(limit=1000):
            if r.get("applicantId") == member_id:
                continue
            for step in (r.get("approvals") or []):
                if step.get("approvedBy") != member_id:
                    continue
                approvals_decided += 1
                created = _parse_iso(r.get("createdAt", ""))
                decided = _parse_iso(step.get("decidedAt", ""))
                if created and decided and (
                        decided - created) <= timedelta(
                        hours=APPROVAL_SLA_HOURS):
                    approvals_timely += 1
        approval = (W_APPROVAL if approvals_decided == 0
                    else int(W_APPROVAL * approvals_timely
                             / approvals_decided))

        # 因子4: 无异常基础分(10) — 本周期无 medium+ 事件
        has_events = any(l.get("riskLevel") in (LEVEL_MEDIUM, LEVEL_HIGH,
                                                LEVEL_EXTREME)
                         for l in period_logs)
        base = 0 if has_events else W_BASE

        return {
            "complianceScore": compliance,
            "dutyScore": duty,
            "approvalScore": approval,
            "reportScore": base,
            "creditScore": max(0, min(100,
                                       compliance + duty + approval + base)),
        }

    async def _execute_reward(self, member_id: int, credit_score: int,
                              tier: tuple) -> dict:
        """执行奖惩(钱包/积分异常不阻断, 降级为日志)"""
        _, _, amount, points, rtype = tier
        executed = {"rewardType": rtype, "rewardAmount": amount,
                    "rewardPoints": points, "executed": []}
        try:
            if rtype == "bonus":
                # 奖金入钱包收益(仅购物不可提现)
                from services.wallet_service import WalletService
                from services.points_service import PointsService
                wallet = WalletService()
                try:
                    await wallet.deposit_reward(
                        member_id, amount, description="权责信用分奖励")
                except KeyError:
                    await wallet.open(member_id)
                    await wallet.deposit_reward(
                        member_id, amount, description="权责信用分奖励")
                # 竹叶积分
                pts = PointsService()
                async with get_lock(f"points:account:{member_id}"):
                    await pts._earn_points(
                        member_id, points, source="perm_credit",
                        ref_desc=f"权责考核{credit_score}分奖励")
                executed["executed"].append(
                    f"奖金¥{amount:.0f}+{points}竹叶已发放")
            elif rtype == "demote":
                # 高危降权: 核心权限有效期缩至 7 天
                shortened = []
                for g in await self.repo.list_grants(
                        member_id=member_id, status="active"):
                    node = await self.repo.get_node_by_code(g["nodeCode"])
                    if node and node.get("sensitivity") == "core":
                        new_exp = (_now() + timedelta(days=7)).isoformat()
                        if g.get("expiresAt", "") > new_exp:
                            await self.repo.update_grant(
                                g["grantId"], {"expiresAt": new_exp})
                            shortened.append(g["nodeCode"])
                executed["executed"].append(
                    f"核心权限限期缩短至7天: {shortened or '无'}")
            elif rtype == "freeze":
                frozen = []
                for g in await self.repo.list_grants(
                        member_id=member_id, status="active"):
                    await self._freeze_grant(g["grantId"], 0)
                    frozen.append(g["grantId"])
                executed["executed"].append(
                    f"全部权限冻结({len(frozen)}项), 已通知超管追责")
        except Exception as exc:  # noqa: BLE001
            logger.warning("perm_reward_execute_failed member=%r: %s",
                           member_id, exc)
            executed["executed"].append(f"奖惩执行异常: {exc}")
        return executed

    async def run_assessment(self, admin_id: int,
                             period: str = None,
                             force: bool = False) -> dict:
        """月度考核(全部有权会员; 同周期幂等, force 可重跑)

        Raises:
            PermissionError: 非超管
        """
        perm_svc = self.perm
        if not await perm_svc._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可触发考核")
        period = period or _now().strftime("%Y-%m")

        # 考核对象: 持有/持有过授权的会员(超管除外)
        member_ids = list({g["memberId"] for g
                           in await self.repo.list_grants()})
        member_ids = [m for m in member_ids
                      if not await perm_svc._is_super_admin(m)]

        results = []
        for member_id in member_ids:
            existing = await self.repo.list_scores(
                member_id=member_id, period=period)
            if existing and not force:
                results.append({"memberId": member_id,
                                "skipped": "本周期已考核"})
                continue
            scores = await self._assess_member(member_id, period)
            tier = self._tier_for(scores["creditScore"])
            reward = await self._execute_reward(
                member_id, scores["creditScore"], tier)
            score_id = await self.repo.next_id("score")
            record = {
                "scoreId": score_id, "memberId": member_id,
                "period": period, **scores,
                "rewardType": reward["rewardType"],
                "rewardAmount": reward["rewardAmount"],
                "rewardPoints": reward["rewardPoints"],
                "executed": reward["executed"],
                "aiReport": (f"合规{scores['complianceScore']}/{W_COMPLIANCE}+"
                             f"履责{scores['dutyScore']}/{W_DUTY}+"
                             f"审批{scores['approvalScore']}/{W_APPROVAL}+"
                             f"基础{scores['reportScore']}/{W_BASE}"),
                "createdAt": _now().isoformat(),
            }
            await self.repo.save_score(record)
            await self._log(admin_id, "assessment_run", "",
                            LEVEL_LOW,
                            {"memberId": member_id,
                             "creditScore": scores["creditScore"]},
                            "none")
            results.append(record)

        logger.info("perm_assessment_done period=%s members=%d by=%r",
                    period, len(member_ids), admin_id)
        return {"period": period, "assessed": len(results), "results":
                results}

    async def my_scores(self, member_id: int,
                        limit: int = 12) -> list[dict]:
        """我的考核记录(近 N 期)"""
        return await self.repo.list_scores(member_id=member_id,
                                           limit=limit)

    async def admin_list_scores(self, admin_id: int,
                                period: str = None) -> list[dict]:
        """全部考核记录(仅超管, 附昵称)"""
        if not await self.perm._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可查看全部考核")
        scores = await self.repo.list_scores(period=period)
        members = {m["id"]: m for m in await self.member_repo.list_all()}
        for s in scores:
            m = members.get(s.get("memberId"))
            s["memberNickname"] = m.get("nickname", "") if m else ""
        return scores
