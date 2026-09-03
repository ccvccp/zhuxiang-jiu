"""43号·P5-2 Redis 体检告警 → 管理员站内信服务
        + P6-2 三信号化(Redis 体检/情报订阅降级/调度器基线异常)

P5-2 原始口径(docs/43号P5-2_Redis告警消息通道实施计划.md):
    - 采集: RedisHealthService.collect() 的 alerts
    - 级别过滤: 仅 critical/warn 触达(info 碎片率仅面板可见)
    - 规则级 24h 去重 + 聚合单封 + 管理员逐一触达
    - CATEGORY_SECURITY 强制投递 + P1 优先级

P6-2 增强(docs/43号P6_聚合规模化与就绪度实施计划.md §三):
    - S1 Redis 体检告警(P5-2 既有, 原样)
    - S2 情报订阅降级: auto 状态 consecutiveFailures ≥3 →
      warn(rule=threatintel_degraded; P6-1 多源化后按源细化)
    - S3 调度器异常: lastErrors 含 baseline_anomaly → warn
      (自检口径: 读上一轮调度留痕——本轮基线异常下轮触达,
      滞后一轮可接受)
    - notify_security_alerts: 三信号统一采集→过滤→去重→触达
      (单信号采集异常不阻断其余 fail-soft)
    - notify_redis_alerts: 仅 Redis 信号(向后兼容,
      旧端点 POST /admin/redis/alert/test 保留)

消息通道口径(调研确认):
    - CATEGORY_SECURITY ∈ MANDATORY_CATEGORIES(不可退订强制
      投递)——天然绕过订阅/静默/频率四重调控, 零改造直通
    - priority=P1(高优先非紧急; P0 留给全站级故障)

触发时机:
    - 日度调度轨: security_scheduler ④ 步骤(三信号)
    - 手动轨: POST /admin/alerts/collect(三信号) /
      POST /admin/redis/alert/test(仅 Redis, 兼容)
"""

import hashlib
import logging

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger(__name__)

# 触达级别门槛: info 仅面板可见(防噪声), critical/warn 才发消息
ALERT_LEVELS = ("critical", "warn")
# 同规则去重窗口(秒)——防日度体检重复轰炸
DEDUPE_TTL = 86400
# S2 订阅降级阈值(与 P5-3 stats().auto.degraded 口径一致)
DEGRADED_THRESHOLD = 3

# 信号分组展示名(面板/站内信分组渲染)
SIGNAL_NAMES = {"redis": "Redis 体检", "intel": "情报订阅",
                "scheduler": "调度器"}


class SecurityAlertService:
    """安全告警 → 管理员站内信(P5-2 建, P6-2 三信号化)"""

    # --------------------------------------------------------
    # 信号采集
    # --------------------------------------------------------

    async def _collect_redis_alerts(self) -> tuple:
        """S1: Redis 体检告警(P5-2 既有逻辑)

        Returns:
            (alerts(已标 signal=redis), collectedAt)
        """
        from services.redis_health_service import RedisHealthService
        report = await RedisHealthService().collect()
        alerts = [{**a, "signal": "redis"}
                  for a in (report.get("alerts") or [])]
        return alerts, report.get("collectedAt")

    async def _collect_intel_degraded(self) -> list[dict]:
        """S2: 情报订阅降级(consecutiveFailures ≥3 → warn)

        P6-1 多源化后此方法按源循环输出(rule 含 source
        维度); 当前单源口径 rule=threatintel_degraded。
        """
        try:
            from repositories.security_repository import (
                Security43Repository,
            )
            state = await Security43Repository(
            ).get_threatintel_auto_state() or {}
            failures = int(state.get("consecutiveFailures") or 0)
            if failures >= DEGRADED_THRESHOLD:
                last_error = str(
                    state.get("lastError") or "")[:120]
                return [{
                    "level": "warn", "signal": "intel",
                    "rule": "threatintel_degraded",
                    "message": f"威胁情报订阅连续失败 "
                               f"{failures} 次: {last_error}",
                }]
        except Exception as exc:
            logger.warning("security_alert_intel_skip: %s", exc)
        return []

    async def _collect_scheduler_anomalies(self) -> list[dict]:
        """S3: 调度器基线重建异常(lastErrors 含 baseline_anomaly)

        读上一轮调度留痕(自检口径——本轮异常下轮触达)。
        """
        try:
            from repositories.security_repository import (
                Security43Repository,
            )
            stats = await Security43Repository(
            ).get_scheduler_stats() or {}
            errors = stats.get("lastErrors") or []
            if any("baseline_anomaly" in str(e) for e in errors):
                return [{
                    "level": "warn", "signal": "scheduler",
                    "rule": "baseline_anomaly",
                    "message": "UEBA 基线重建异常: 有行为计数"
                               "但 0 基线(采集/重建链路异常)",
                }]
        except Exception as exc:
            logger.warning("security_alert_sched_skip: %s", exc)
        return []

    # --------------------------------------------------------
    # 统一入口
    # --------------------------------------------------------

    async def notify_security_alerts(self,
                                      force: bool = False) -> dict:
        """P6-2: 三信号统一采集 → 过滤 → 去重 → 管理员触达

        单信号采集异常不阻断其余(fail-soft);
        无 P1 级告警零发送(不发"一切正常"骚扰信)。

        Returns:
            {success, alerts, eligible, deduped, fresh, admins,
             sent, failed, signals{redis/intel/scheduler},
             collectedAt}
        """
        alerts = []
        collected_at = None
        # S1(fail-soft: 采集异常不阻断 S2/S3)
        try:
            redis_alerts, collected_at = (
                await self._collect_redis_alerts())
            alerts.extend(redis_alerts)
        except Exception as exc:
            logger.warning("security_alert_redis_skip: %s", exc)
        # S2 + S3(内部各自 fail-soft)
        alerts.extend(await self._collect_intel_degraded())
        alerts.extend(await self._collect_scheduler_anomalies())
        return await self._dispatch(alerts, force,
                                     collected_at=collected_at)

    async def notify_redis_alerts(self, force: bool = False) -> dict:
        """(P5-2 既有口径: 仅 Redis 信号——旧端点向后兼容)"""
        alerts, collected_at = await self._collect_redis_alerts()
        return await self._dispatch(alerts, force,
                                     collected_at=collected_at)

    # --------------------------------------------------------
    # 共享分发(过滤→去重→聚合单封→触达)
    # --------------------------------------------------------

    async def _dispatch(self, alerts: list, force: bool,
                        collected_at: str = None) -> dict:
        """级别过滤 → 规则级 24h 去重 → 聚合单封 → 触达"""
        eligible = [a for a in alerts
                    if a.get("level") in ALERT_LEVELS]
        # 信号分组计数(P6-2 面板/返回结构展示)
        signals = {}
        for a in eligible:
            sig = str(a.get("signal") or "redis")
            signals[sig] = signals.get(sig, 0) + 1

        # 规则级 24h 去重(force 跳过)
        fresh = []
        deduped = 0
        for alert in eligible:
            rule = str(alert.get("rule") or "")
            if not force and not await self._claim(rule):
                deduped += 1
                continue
            fresh.append(alert)

        # 管理员触达(聚合单封; 逐一发送, 单人失败不阻断)
        admins = await self._list_admin_ids()
        sent = failed = 0
        if fresh and admins:
            title, content = self._compose(fresh)
            sent, failed = await self._broadcast(
                admins, title, content)

        if fresh:
            logger.warning(
                "security_alert_dispatched eligible=%s deduped=%s "
                "signals=%s admins=%s sent=%s failed=%s",
                len(eligible), deduped, signals,
                len(admins), sent, failed)
        return {
            "success": True,
            "alerts": len(alerts),
            "eligible": len(eligible),
            "deduped": deduped,
            "fresh": len(fresh),
            "admins": len(admins),
            "sent": sent,
            "failed": failed,
            "signals": signals,
            "collectedAt": collected_at,
        }

    # --------------------------------------------------------
    # 规则级 24h 去重锁
    # --------------------------------------------------------

    async def _claim(self, rule: str) -> bool:
        """同规则 24h 内首次调用 True, 之后 False(去重锁)

        Redis: SETNX security43:alert:dedupe:{rule_hash} TTL 86400
        内存: bucket _security43_alert_dedupe {rule_hash: expiry_ts}
        """
        import time as _time
        rule_hash = hashlib.sha1(
            rule.encode("utf-8")).hexdigest()[:16]
        now = _time.time()
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "alert", "dedupe", rule_hash)
            got = await client.set(key, "1", nx=True,
                                   ex=DEDUPE_TTL)
            return bool(got)
        store = get_in_memory_store()
        bucket = store.setdefault("_security43_alert_dedupe", {})
        expiry = float(bucket.get(rule_hash) or 0)
        if expiry > now:
            return False
        bucket[rule_hash] = now + DEDUPE_TTL
        return True

    # --------------------------------------------------------
    # 管理员收件人(运行时查询, 双模式通用)
    # --------------------------------------------------------

    async def _list_admin_ids(self) -> list[int]:
        """会员表 role=admin 的全部会员 ID"""
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            members = await MemberRepository().list_all()
            return sorted(int(m["id"]) for m in members
                          if m.get("role") == "admin" and m.get("id"))
        except Exception as exc:
            logger.warning("security_alert_admins_failed: %s", exc)
            return []

    # --------------------------------------------------------
    # 聚合单封站内信(按信号分组渲染, 超 10 条截断)
    # --------------------------------------------------------

    def _compose(self, alerts: list) -> tuple:
        """N 条告警 → (标题, 正文): 信号分组+逐条列明+入口指引

        标题口径: 仅 Redis 信号时保持 P5-2 兼容标题
        ("Redis 体检告警"); 多信号用通用标题("安全告警")。
        """
        groups = {}
        for a in alerts:
            sig = str(a.get("signal") or "redis")
            groups.setdefault(sig, []).append(a)

        sections = []
        for sig, items in groups.items():
            name = SIGNAL_NAMES.get(sig, sig)
            lines = []
            for a in items[:10]:   # 超 10 条截断防单封过长
                level = str(a.get("level") or "warn")
                lines.append(
                    f"[{level.upper()}] {a.get('rule')}\n"
                    f"  {a.get('message')}")
            if len(items) > 10:
                lines.append(f"(另有 {len(items) - 10} 条略)")
            sections.append(f"■ {name}({len(items)} 项)\n"
                            + "\n".join(lines))
        total = len(alerts)
        if set(groups) == {"redis"}:
            title = f"[安全运维] Redis 体检告警({total} 项)"
        else:
            title = f"[安全运维] 安全告警({total} 项)"
        content = (
            "安全告警汇总:\n\n"
            + "\n\n".join(sections)
            + "\n\n处置建议见操作指南; 详情查看安全管理面板。\n"
              "(日度自动巡检/手动触发)")
        return title, content

    async def _broadcast(self, admins: list, title: str,
                         content: str) -> tuple:
        """逐一发送(单人失败不阻断), 返回 (sent, failed)"""
        from services.message_service import (
            MessageService, CHANNEL_INMAIL, CATEGORY_SECURITY,
            PRIORITY_P1,
        )
        svc = MessageService()
        sent = failed = 0
        for admin_id in admins:
            try:
                await svc.send_message(
                    admin_id, CHANNEL_INMAIL, title, content,
                    category=CATEGORY_SECURITY,
                    priority=PRIORITY_P1)
                sent += 1
            except Exception as exc:
                # 单人订阅状态异常不影响其余触达
                failed += 1
                logger.warning("security_alert_send_failed "
                               "admin=%s: %s", admin_id, exc)
        return sent, failed
