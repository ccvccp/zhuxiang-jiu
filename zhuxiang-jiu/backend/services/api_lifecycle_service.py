"""44号·P5 治理闭环(裁决回流 + 生命周期状态机 + 目录门户)

计划(docs/44号_API智能管理模块实施计划.md §八):
    ① 异常事件裁决回流(43号 P2 学习闭环范式):
        confirmed=检测对(正反馈) / false_positive=误报(负反馈)
        → Hedge 第27档案 ApiHealthScorer 权重调优
        (submit_feedback 基建复用, eventFed 幂等标记)
    ② 生命周期状态机(遵循规则——转换人工触发, 台账留痕):
        development → published → deprecated → offline
        - deprecated: 响应头 X-Api-Deprecated 注入(弃用预警)
        - offline: 410 Gone(Key 面)
        - offline 前置软护栏: 近 1 日该 API Key 面调用量 >0
          则阻断(可 force=true 强制, 留痕)
    ③ 对外目录(方便快捷的对外门面):
        GET /apis/catalog —— published 接口自助文档
"""

import logging

logger = logging.getLogger(__name__)

# 生命周期合法转换(计划 §八②: 人工触发单向推进 + 回退开发态)
LIFECYCLE_TRANSITIONS = {
    "development": {"published", "deprecated", "offline"},
    "published": {"deprecated", "development", "offline"},
    "deprecated": {"offline", "published", "development"},
    "offline": {"development"},   # 重新启用需显式回开发态
}

# 弃用期日落窗口(计划 §八②: 弃用期默认 30 天)
DEPRECATION_SUNSET_DAYS = 30


class ApiLearningService:
    """44号裁决真值回流(P5①; 43号 collect_event_feedback 范式)"""

    SCORER_ID = "api_health"

    def __init__(self, anomaly_service=None):
        if anomaly_service is None:
            from services.api_intelligence_service import (
                ApiAnomalyService,
            )
            anomaly_service = ApiAnomalyService()
        self._anomalies = anomaly_service

    async def collect_anomaly_feedback(self) -> dict:
        """批量回流: 已裁决且未回流的事件 → 第27档案反馈

        真值口径: confirmed=检测器判对(正反馈 +0.5) /
                  false_positive=误报(负反馈 -0.5)。
        单条失败不阻断批量; eventFed 幂等标记。
        """
        from services.ai_learning_service import submit_feedback

        events = (await self._anomalies.list_events(
            status=None)).get("events") or []
        submitted, skipped, results = 0, 0, []

        for event in events:
            if str(event.get("eventFed") or "") in ("1", "True",
                                                    "true"):
                skipped += 1
                continue
            status = event.get("status")
            if status not in ("confirmed", "false_positive"):
                skipped += 1   # pending 未裁决
                continue
            correct = status == "confirmed"
            kind = event.get("kind") or "unknown"
            try:
                result = await submit_feedback({
                    "scorerId": self.SCORER_ID,
                    "factors": self._kind_factors(kind, event),
                    "scoreAtDecision": 50.0,
                    "actualAction": "alert",
                    "expectedAction": ("alert" if correct
                                       else "silent"),
                    "correct": correct,
                    "reward": 0.5 if correct else -0.5,
                    "note": f"eventId={event.get('eventId')} "
                            f"kind={kind} status={status}",
                    "source": "api44",
                })
                submitted += 1
                results.append(result)
                # eventFed 幂等标记(直接回写事件存储)
                await self._mark_fed(event)
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("api44_feedback_skip event=%s: %s",
                               event.get("eventId"), exc)
        return {"success": True, "submitted": submitted,
                "skipped": skipped, "results": results}

    @staticmethod
    def _kind_factors(kind: str, event: dict) -> list[dict]:
        """检测结果 → 因子快照(五因子近似——kind 决定主因子)

        contribution=因子分(经验回放同约定), 供 Hedge
        影响度归一(无 contribution 则该反馈不参与权重更新)。
        """
        # 检测器类型映射到健康五因子的主要嫌疑因子
        main = {"spike": "stability",
                "drop": "stability",
                "error_burst": "success_rate"}.get(
            kind, "stability")
        return [
            {"name": main, "score": 60.0,
             "contribution": 60.0,
             "detail": f"{kind} {event.get('total', 0)}/"
                       f"μ={event.get('baselineMean', 0)}"},
            {"name": "change_freq", "score": 80.0,
             "contribution": 80.0,
             "detail": "baseline-window"},
        ]

    async def _mark_fed(self, event: dict) -> None:
        """eventFed 幂等标记回写"""
        event["eventFed"] = True
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k,
            get_in_memory_store,
        )
        from datetime import datetime, UTC
        day = event.get("day") or datetime.now(
            UTC).strftime("%Y-%m-%d")
        key = (f"{event.get('template')}|"
               f"{event.get('kind')}|{day}")
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("api44", "anomaly", key),
                              mapping={**event, "eventFed": 1})
            return
        store = get_in_memory_store()
        store.setdefault("api44_anomalies", {})[key] = event

    async def run_learning(self) -> dict:
        """触发第27档案一轮 Hedge 学习"""
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle(self.SCORER_ID)

    async def learning_status(self) -> dict:
        """第27档案学习状态"""
        from services.ai_learning_service import (
            SCORER_REGISTRY, get_weights_view,
        )
        events = (await self._anomalies.list_events(
            status=None)).get("events") or []
        decided = [e for e in events
                   if e.get("status") in ("confirmed",
                                         "false_positive")]
        fed = [e for e in decided
               if str(e.get("eventFed") or "") in
               ("1", "True", "true")]
        return {
            "success": True,
            "scorer": self.SCORER_ID,
            "registry": SCORER_REGISTRY.get(self.SCORER_ID),
            "decided": len(decided),
            "fed": len(fed),
            "pending": len([e for e in events
                            if e.get("status") == "pending"]),
            "weights": await get_weights_view(self.SCORER_ID),
        }


class ApiLifecycleService:
    """生命周期状态机(P5②; 转换人工触发, 台账留痕)"""

    def __init__(self, registry_service=None):
        if registry_service is None:
            from services.api_registry_service import (
                ApiRegistryService,
            )
            registry_service = ApiRegistryService()
        self._registry = registry_service

    async def transition(self, api_id: int, status: str,
                         force: bool = False) -> dict:
        """状态转换(软护栏: offline 前检查近 1 日存量调用)

        Raises:
            KeyError: apiId 不存在
            ValueError: 非法转换/存量阻断
        """
        rec = await self._registry.repo.find_by_id(api_id)
        if rec is None:
            raise KeyError(f"apiId {api_id} 不存在")
        current = rec.get("status") or "development"
        if status == current:
            raise ValueError(f"状态已是 {status}(无转换)")

        allowed = LIFECYCLE_TRANSITIONS.get(current, set())
        if status not in allowed:
            raise ValueError(
                f"非法转换: {current} → {status}"
                f"(合法目标: {'/'.join(sorted(allowed)) or '无'})")

        # offline 软护栏: 近 7 日 Key 面调用量 >0 阻断
        if status == "offline" and not force:
            recent = await self._recent_calls(rec["method"],
                                              rec["path"])
            if recent > 0:
                raise ValueError(
                    f"该 API 近 7 日仍有 {recent} 次 Key 面调用, "
                    f"禁止下线(确认无误请传 force=true)")

        updated = await self._registry.patch_entry(
            api_id, status=status)
        logger.info("api44_lifecycle apiId=%s %s → %s "
                    "(force=%s)", api_id, current, status, force)
        return updated

    async def _recent_calls(self, method: str, path: str) -> int:
        """近 7 日该模板 Key 面调用量(软护栏数据源)"""
        from services.api_intelligence_service import (
            load_history_days,
        )
        history = await load_history_days(7)
        days_map = history.get(path) or {}
        return sum(int(v.get("total") or 0)
                   for v in days_map.values())

    async def catalog(self) -> dict:
        """对外目录(published 自助文档门面 + deprecated 迁移窗口)

        published: 正常开放; deprecated: 带弃用预警与日落时间
        (30 天迁移窗口); offline/development 不展示。
        """
        reg = await self._registry.list_registry(
            status="published")
        entries = []
        for e in reg.get("entries") or []:
            entries.append({
                "method": e.get("method"),
                "path": e.get("path"),
                "module": e.get("module"),
                "summary": e.get("summary"),
                "status": "published",
                "deprecated": False,
                "auth": "X-Api-Key + X-App-Code 双头凭证",
                "minTier": "free",
            })
        # deprecated 但未下线的也展示(带预警标记——消费方迁移
        # 窗口; offline 前的最后缓冲)
        dep = await self._registry.list_registry(
            status="deprecated")
        for e in dep.get("entries") or []:
            entries.append({
                "method": e.get("method"),
                "path": e.get("path"),
                "module": e.get("module"),
                "summary": e.get("summary"),
                "status": "deprecated",
                "deprecated": True,
                "auth": "X-Api-Key + X-App-Code 双头凭证",
                "minTier": "free",
                "deprecatedAt": e.get("deprecatedAt") or "",
                "sunsetAt": self._sunset(e.get("deprecatedAt")),
            })
        return {"success": True, "total": len(entries),
                "apis": entries}

    @staticmethod
    def _sunset(deprecated_at) -> str:
        """日落时间(deprecatedAt + 30 天; 解析失败返回空)"""
        if not deprecated_at:
            return ""
        from datetime import datetime, timedelta
        try:
            return (datetime.fromisoformat(
                str(deprecated_at))
                + timedelta(days=DEPRECATION_SUNSET_DAYS)
            ).isoformat()
        except (TypeError, ValueError):
            return ""
