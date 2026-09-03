"""43号·P5-2 Redis 体检告警 → 管理员站内信服务

计划(docs/43号P5-2_Redis告警消息通道实施计划.md):
    - 采集: 复用 P4-4 RedisHealthService.collect() 的 alerts
    - 级别过滤: 仅 critical/warn 触达(info 碎片率提示仅面板
      可见——防收件箱噪声)
    - 规则级 24h 去重: 同 rule(如"单键 >100KB")24h 一条——
      大 key 昨天未处理今天还在 → 是提醒不是新事件; 处理掉后
      次日再出现则重新触达(去重锁过期)
    - 聚合单封: 一轮体检 N 条告警 → 单封站内信(逐条列明)
    - 管理员触达: 会员表 role=admin 逐一发送(单人失败不阻断)

消息通道口径(调研确认):
    - CATEGORY_SECURITY ∈ MANDATORY_CATEGORIES(不可退订强制
      投递)——天然绕过订阅/静默/频率四重调控, 零改造直通
    - priority=P1(高优先非紧急; P0 留给全站级故障)

触发时机(仅两处, 与 P4-4"体检不进自动刷新"口径一致):
    - 日度调度轨: security_scheduler ④ 步骤
    - 手动轨: POST /admin/redis/alert/test(演练/通道验证)
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


class SecurityAlertService:
    """Redis 体检告警 → 管理员站内信(P5-2)"""

    async def notify_redis_alerts(self, force: bool = False) -> dict:
        """体检 → 过滤 → 去重 → 管理员触达

        Args:
            force: True 跳过 24h 去重(手动轨演练/通道验证用)

        Returns:
            {success, alerts(总数), eligible, deduped, fresh,
             admins, sent, failed, collectedAt}
        """
        from services.redis_health_service import RedisHealthService

        # ① 采集(复用 P4-4; 异常上抛由调用方兜底)
        report = await RedisHealthService().collect()
        alerts = report.get("alerts") or []

        # ② 级别过滤: critical/warn 触达, info 不发
        eligible = [a for a in alerts
                    if a.get("level") in ALERT_LEVELS]

        # ③ 规则级 24h 去重(force 跳过)
        fresh = []
        deduped = 0
        for alert in eligible:
            rule = str(alert.get("rule") or "")
            if not force and not await self._claim(rule):
                deduped += 1
                continue
            fresh.append(alert)

        # ④ 管理员触达(聚合单封; 逐一发送, 单人失败不阻断)
        admins = await self._list_admin_ids()
        sent = failed = 0
        if fresh and admins:
            title, content = self._compose(fresh)
            sent, failed = await self._broadcast(
                admins, title, content)

        if fresh:
            logger.warning(
                "security_alert_dispatched eligible=%s deduped=%s "
                "admins=%s sent=%s failed=%s", len(eligible),
                deduped, len(admins), sent, failed)
        return {
            "success": True,
            "alerts": len(alerts),
            "eligible": len(eligible),
            "deduped": deduped,
            "fresh": len(fresh),
            "admins": len(admins),
            "sent": sent,
            "failed": failed,
            "collectedAt": report.get("collectedAt"),
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
    # 聚合单封站内信
    # --------------------------------------------------------

    def _compose(self, alerts: list[dict]) -> tuple[str, str]:
        """N 条告警 → (标题, 正文): 逐条列明+处置建议+入口指引"""
        lines = []
        for a in alerts:
            level = str(a.get("level") or "warn")
            lines.append(
                f"[{level.upper()}] {a.get('rule')}\n"
                f"  {a.get('message')}")
        title = f"[安全运维] Redis 体检告警({len(alerts)} 项)"
        content = (
            "Redis 实况体检发现以下风险:\n\n"
            + "\n\n".join(lines)
            + "\n\n处置建议见操作指南 §八; 详情查看安全管理面板"
              "⑦区「Redis 实况体检」。\n"
              "(日度自动巡检/手动触发)")
        return title, content

    async def _broadcast(self, admins: list[int], title: str,
                         content: str) -> tuple[int, int]:
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
