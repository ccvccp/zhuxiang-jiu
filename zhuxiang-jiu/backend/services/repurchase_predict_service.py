# -*- coding: utf-8 -*-
"""无感复购预测·蓝图近期项②(repurchase_predict_service)

IMTM f(Q_user) × 消耗周期 → 建议书留痕 + 人工确认后推送:
    - 消耗周期: 有效订单(状态 ∈ PAID/SHIPPED/RECEIVED/COMPLETED)按支付
      时间排序, 相邻间隔天数的中位数; ≥2 笔才可预测(<2 不可预测)
    - 预测下次购买日 = 末单支付时间 + 周期; dueInDays = 今日距预测日
      天数(负数 = 已过期未回购)
    - qFactor(IMTM f(Q_user) hint, observe only 留痕, 不接定价链):
        周期稳定性 CV = 间隔标准差/均值(总体口径, 确定性)
        间隔样本 <4 → 1.0(小样本跳过, 对齐 demand_factor MIN_SAMPLE 精神)
        CV≤0.3 → 1.2(规律型) / CV≤0.6 → 1.0 / >0.6 → 0.8(波动型)
    - 两类建议书: upcoming(0≤dueInDays≤window, 临近复购→提醒+竹叶
      余额提示) / overdue(dueInDays<-7, 已逾期→关怀+权益建议)

模式闸 REPURCHASE_PUSH_MODE=off|observe|enforce(默认 observe,
写法对齐 growth80_pricing.multiplier_mode):
    off      不扫描
    observe  扫描+建议书留痕+结构化日志 repurchase_predict_snapshot,
             绝不发送
    enforce  解锁(且仅解锁)管理员确认后的 dispatch 走 message_service
             ——enforce 也不是自动发送, 只是解锁 dispatch 端点权限

铁律(沿用 zk wakeup 范式): 建议书留痕; 触达永不自动发送, 须管理员
逐条确认(suggestion_id + operator)后由消息模块执行; 全部确定性公式
与模板拼装, LLM 禁入; fail-soft(单会员异常不中断整扫); 幂等(同会员
同 tier 当日已存在 pending 建议则跳过)。
"""

import logging
import os
from datetime import datetime, timedelta, UTC

from services.zk_fabric_service import (
    _ZkStore, ZkFabricService, _round2, _now_iso,
    VALID_ORDER_STATUSES, order_paid_at, status_of)

logger = logging.getLogger(__name__)

# ============================================================
# 常量(确定性)
# ============================================================

REPURCHASE_PUSH_ENV = "REPURCHASE_PUSH_MODE"

# 可预测的最小有效订单数(<2 无间隔可言)
PREDICT_MIN_ORDERS = 2

# qFactor 三档(周期稳定性 CV 分档)
QF_REGULAR = 1.2     # 规律型: CV ≤ 0.3
QF_NORMAL = 1.0      # 常规型: CV ≤ 0.6
QF_VOLATILE = 0.8    # 波动型: CV > 0.6
CV_REGULAR_LINE = 0.3
CV_VOLATILE_LINE = 0.6
QF_MIN_INTERVALS = 4   # 间隔样本 <4 跳过(小样本防抖)

# 两类建议书阈值
OVERDUE_LINE_DAYS = -7   # dueInDays < -7 视为已逾期(流失风险)

# 建议书留痕表(已登记进 ZK_TABLES)
SUGGESTION_TABLE = "repurchase_suggestions"

# 建议书统一口径(对齐 zk_operation.DISPOSITION 精神)
DISPOSITION = ("建议书; 触达永不自动发送, 须管理员逐条确认"
               "(suggestionId+operator)后由消息模块执行")


def repurchase_push_mode() -> str:
    """模式闸(默认 observe——留痕无副作用; 写法对齐 multiplier_mode)"""
    mode = os.environ.get(REPURCHASE_PUSH_ENV, "observe")
    mode = mode.strip().lower()
    return mode if mode in ("off", "observe", "enforce") else "observe"


def _parse_ts(iso: str) -> datetime:
    """解析时间戳(空/非法 → epoch, 保证确定性不抛错, fail-soft)"""
    if not (iso or "").strip():
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        dt = datetime.fromisoformat(str(iso))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=UTC)


def _median(values: list[float]) -> float:
    """中位数(偶数取中间两值均值, 确定性)"""
    vs = sorted(float(v) for v in values)
    n = len(vs)
    if n == 0:
        return 0.0
    mid = n // 2
    return vs[mid] if n % 2 else (vs[mid - 1] + vs[mid]) / 2.0


def _pstdev(values: list[float], mean: float) -> float:
    """总体标准差(确定性, 样本量由调用方保证)"""
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _qfactor_of(intervals: list[float]) -> tuple[float, float]:
    """qFactor(IMTM f(Q_user) hint): 间隔 CV 分档

    Returns: (qFactor, cv) —— 小样本/退化样本 → (1.0, None)
    """
    if len(intervals) < QF_MIN_INTERVALS:
        return QF_NORMAL, None
    mean = sum(intervals) / len(intervals)
    if mean <= 0:
        return QF_NORMAL, None
    cv = _pstdev(intervals, mean) / mean
    if cv <= CV_REGULAR_LINE:
        return QF_REGULAR, cv
    if cv <= CV_VOLATILE_LINE:
        return QF_NORMAL, cv
    return QF_VOLATILE, cv


class RepurchasePredictService:
    """无感复购预测(消耗周期中位数 × IMTM f(Q_user) hint, 建议书引擎)"""

    def __init__(self, fabric: ZkFabricService = None,
                 store: _ZkStore = None, message_service=None):
        self.fabric = fabric or ZkFabricService()
        self.store = store or self.fabric.store
        # 消息服务懒注入(dispatch 专用; 测试可打桩, 运行时才实例化)
        self._message_service = message_service

    # ============================================================
    # 单会员预测(确定性)
    # ============================================================

    async def predict_member(self, member: dict, orders: list) -> dict:
        """单会员消耗周期预测

        口径: 有效订单按支付时间(paidAt 优先, createdAt 兜底)升序,
        消耗周期 = 相邻间隔天数的中位数; 预测日 = 末单 + 周期;
        dueInDays = 今日距预测日(负数 = 已过期未回购)。
        """
        mid = member["id"]
        valid = [o for o in (orders or [])
                 if o.get("status") in VALID_ORDER_STATUSES]
        paid = sorted(
            (_parse_ts(order_paid_at(o)) for o in valid),
            key=lambda dt: dt)
        if len(paid) < PREDICT_MIN_ORDERS:
            return {
                "memberId": mid,
                "predictable": False,
                "orderCount": len(paid),
                "reason": f"有效订单 {len(paid)} 笔(<{PREDICT_MIN_ORDERS}"
                          " 笔, 无间隔可测)",
                "computedAt": _now_iso(),
            }

        now = datetime.now(UTC)
        intervals = [
            (paid[i] - paid[i - 1]).total_seconds() / 86400.0
            for i in range(1, len(paid))]
        cycle_days = _round2(_median(intervals))
        q_factor, cv = _qfactor_of(intervals)
        predicted_at = paid[-1] + timedelta(days=cycle_days)
        due_in_days = _round2(
            (predicted_at - now).total_seconds() / 86400.0)
        return {
            "memberId": mid,
            "predictable": True,
            "orderCount": len(paid),
            "cycleDays": cycle_days,
            "lastPaidAt": paid[-1].isoformat(),
            "predictedAt": predicted_at.isoformat(),
            "dueInDays": due_in_days,
            "qFactor": q_factor,          # IMTM f(Q_user) hint(observe only)
            "cv": _round2(cv) if cv is not None else None,
            "formula": (f"消耗周期=相邻间隔天数中位数({len(intervals)} 个"
                        f"间隔)={cycle_days} 天; 预测日=末单+周期; "
                        f"dueInDays={due_in_days}(负=已逾期); qFactor 按"
                        f"CV 分档(≤{CV_REGULAR_LINE}→{QF_REGULAR}, "
                        f"≤{CV_VOLATILE_LINE}→{QF_NORMAL}, "
                        f">{CV_VOLATILE_LINE}→{QF_VOLATILE}, 间隔样本"
                        f"<{QF_MIN_INTERVALS}→{QF_NORMAL})"),
            "computedAt": _now_iso(),
        }

    # ============================================================
    # 全量扫描(建议书留痕, 幂等, fail-soft, 永不自动发送)
    # ============================================================

    async def scan_due(self, window_days: int = 3) -> dict:
        """全量会员无感复购扫描(两类建议书: upcoming/overdue)

        筛选: 0 ≤ dueInDays ≤ window_days(临近) 或
              dueInDays < OVERDUE_LINE_DAYS(已逾期, 流失风险)
        幂等: 同会员同 tier 当日已存在 pending 建议则跳过;
        observe 模式逐条结构化日志 repurchase_predict_snapshot。
        """
        mode = repurchase_push_mode()
        if mode == "off":
            return {
                "mode": "off", "scanned": 0, "predictable": 0,
                "upcoming": 0, "overdue": 0, "skipped": 0,
                "note": "REPURCHASE_PUSH_MODE=off, 不扫描",
                "scannedAt": _now_iso(),
            }

        window_days = max(0, int(window_days))
        members = await self.fabric.members()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        predictable = upcoming = overdue = skipped = 0

        for m in members:
            try:
                # 禁用账号不触达(合规口径, 对齐 wakeup 范式)
                if status_of(m) == 0:
                    skipped += 1
                    continue
                pred = await self.predict_member(
                    m, await self.fabric.member_orders(m["id"]))
                if not pred.get("predictable"):
                    skipped += 1
                    continue
                predictable += 1
                due = float(pred["dueInDays"])
                if 0 <= due <= window_days:
                    tier = "upcoming"
                elif due < OVERDUE_LINE_DAYS:
                    tier = "overdue"
                else:
                    continue
                if await self._pending_of_today(m["id"], tier, today):
                    skipped += 1   # 幂等: 当日同 tier 已留痕
                    continue
                record = self._build_suggestion(m, pred, tier, due)
                record["suggestionId"] = await self.store.next_id(
                    SUGGESTION_TABLE)
                await self.store.save(
                    SUGGESTION_TABLE, record["suggestionId"], record)
                if tier == "upcoming":
                    upcoming += 1
                else:
                    overdue += 1
                if mode == "observe":
                    logger.info(
                        "repurchase_predict_snapshot suggestion=%s "
                        "member=%s tier=%s cycle=%s dueInDays=%s "
                        "qFactor=%s mode=observe",
                        record["suggestionId"], record["memberId"], tier,
                        record["cycleDays"], record["dueInDays"],
                        record["qFactor"])
            except Exception as exc:            # fail-soft: 单会员异常不中断
                logger.warning("repurchase_predict_member_skip: %s", exc)
                skipped += 1

        return {
            "scanned": len(members),
            "predictable": predictable,
            "upcoming": upcoming,
            "overdue": overdue,
            "skipped": skipped,
            "mode": mode,
            "windowDays": window_days,
            "disposition": DISPOSITION,
            "scannedAt": _now_iso(),
        }

    def _build_suggestion(self, member: dict, pred: dict,
                          tier: str, due: float) -> dict:
        """建议书拼装(确定性模板, 无 LLM)"""
        mid = member["id"]
        nickname = member.get("nickname", "") or ""
        cycle = pred["cycleDays"]
        if tier == "upcoming":
            suggestion = (
                f"复购提醒: 预计 {due} 天后进入下次复购窗口"
                f"(消耗周期约 {cycle} 天), 建议推送复购提醒"
                "并提示竹叶余额可抵扣(须管理员确认后发送)")
        else:
            suggestion = (
                f"复购关怀: 已逾期 {abs(due)} 天未回购"
                f"(超过消耗周期 {cycle} 天 7 天以上), 存在流失风险, "
                "建议推送关怀+权益建议(参考沉睡唤醒权益矩阵, "
                "须管理员确认后发送)")
        return {
            "memberId": mid,
            "nickname": nickname,
            "tier": tier,
            "cycleDays": cycle,
            "predictedAt": pred.get("predictedAt", ""),
            "dueInDays": _round2(due),
            "qFactor": pred.get("qFactor", QF_NORMAL),
            "cv": pred.get("cv"),
            "suggestion": suggestion,
            "status": "pending",
            "disposition": DISPOSITION,
            "createdAt": _now_iso(),
        }

    async def _pending_of_today(self, member_id, tier: str,
                                today: str) -> bool:
        """幂等检查: 同会员同 tier 当日是否已有 pending 建议书"""
        rows = await self.store.list(SUGGESTION_TABLE)
        return any(r.get("memberId") == member_id
                   and r.get("tier") == tier
                   and r.get("status") == "pending"
                   and str(r.get("createdAt", ""))[:10] == today
                   for r in rows)

    # ============================================================
    # 建议书查询
    # ============================================================

    async def suggestions(self, limit: int = 50) -> list[dict]:
        """已留痕建议书列表(按 suggestionId 倒序=最近优先)"""
        rows = await self.store.list(SUGGESTION_TABLE)
        rows.sort(key=lambda r: int(r.get("suggestionId", 0)), reverse=True)
        return rows[:max(1, int(limit))]

    # ============================================================
    # 人工确认派发(唯一发送闸: enforce + 显式 suggestion_id + operator)
    # ============================================================

    async def dispatch(self, suggestion_id: int, operator: str) -> dict:
        """管理员确认后派发单条建议书(站内信, 会员类营销)

        人工确认闸: 必须显式 suggestion_id + operator, 永无自动调用路径;
        仅 enforce 模式解锁; 仅 pending 可派发; 发送失败不吞
        (异常上抛, 建议书保持 pending 待重试)。

        Raises:
            ValueError: 非 enforce 模式 / 建议书非 pending
            KeyError: 建议书不存在
        """
        mode = repurchase_push_mode()
        if mode != "enforce":
            raise ValueError(
                f"当前 REPURCHASE_PUSH_MODE={mode}, dispatch 仅 enforce "
                "模式可用; enforce 也只是解锁人工派发, 永不自动发送")
        record = await self.store.get(SUGGESTION_TABLE, suggestion_id)
        if record is None:
            raise KeyError(f"建议书不存在(suggestionId={suggestion_id})")
        if record.get("status") != "pending":
            raise ValueError(
                f"建议书状态为 {record.get('status')}, 仅 pending 可派发")

        tier = record.get("tier", "upcoming")
        cycle = record.get("cycleDays", 0)
        due = float(record.get("dueInDays", 0))
        if tier == "upcoming":
            title = "竹香酒复购提醒"
            content = (f"按您的消耗周期(约 {cycle} 天), 预计 {due} 天后"
                       "进入下次复购窗口; 提醒您关注竹叶余额, 下单可"
                       "抵扣, 期待再次为您服务。")
        else:
            title = "竹香酒会员关怀"
            content = (f"距您上次购买已超过消耗周期(约 {cycle} 天)"
                       f" {abs(due)} 天, 特为您整理专属权益建议, "
                       "欢迎回归品鉴。")

        if self._message_service is None:
            from services.message_service import MessageService
            self._message_service = MessageService()
        from repositories.message_repository import (
            CATEGORY_MEMBER, CHANNEL_INMAIL)
        # 发送失败不吞: 异常直接上抛(路由转 409/404), 记录保持 pending
        message = await self._message_service.send_message(
            record["memberId"], CHANNEL_INMAIL, title, content,
            category=CATEGORY_MEMBER)

        record["status"] = "sent"
        record["sentAt"] = _now_iso()
        record["sentBy"] = operator
        record["messageId"] = (message or {}).get("id")
        await self.store.save(SUGGESTION_TABLE, suggestion_id, record)
        return {
            "suggestionId": suggestion_id,
            "memberId": record["memberId"],
            "tier": tier,
            "status": "sent",
            "sentBy": operator,
            "sentAt": record["sentAt"],
            "messageId": record["messageId"],
            "disposition": DISPOSITION,
        }
