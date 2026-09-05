"""54号·小竹AI智能登录引擎大模型 引擎看板+红队防御
(login54_dashboard_service, P5)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §六 P5):
    - 引擎大模型看板(版本/因子/回流/漂移四区)
    - 红队防御区: 权重投毒(伪造反馈操纵权重)防御
      ——护栏 [0.5,2.0] 倍状态核验+标注源集中度
      异常检测(单源占比>阈值告警)

四区结构(fail-soft——单区异常不阻断看板):
    ① 版本区: champion/challenger 版本对+模型
       事件统计(learning/promoted/rollback/
       drift_alert/regression_rollback)
    ② 因子区: 八因子当前权重 vs 默认雷达数据
       (含护栏区间与偏移幅度)
    ③ 回流区: 七类信号分布+44号池双写+延迟态
    ④ 漂移区: EMA 漂移统计+最近 drift_alert

红队防御区(计划 §七风险矩阵——权重投毒):
    - 护栏状态: 逐因子核验当前权重是否在
      [0.5,2.0] 倍默认区间(违规即标红)
    - 标注源集中度: 44号池反馈单 source 占比
      > SOURCE_CONCENTRATION_LIMIT → 告警
      (伪造反馈洪流特征)

零侵入红线: 44号/46号 零改动(纯读取聚合)。
"""

import logging

from core.helpers import ts

logger = logging.getLogger("login54_dashboard_service")

MODEL_VERSION = "v1-login54-dashboard"

SCORER_ID = "login_orchestration"

# 标注源集中度上限(投毒洪流特征——单源占比)
SOURCE_CONCENTRATION_LIMIT = 0.8

# 集中度检测最少样本(小样本不判)
CONCENTRATION_MIN_SAMPLES = 20

# 模型事件类型 → 看板标签
EVENT_LABELS = {
    "learning": "学习轮次",
    "promoted": "版本晋升",
    "rollback": "版本回滚",
    "drift_alert": "漂移告警",
    "regression_rollback": "回归自动回滚",
    "feedback_collect": "回流采集",
}


class Login54DashboardService:
    """54号引擎大模型看板(四区聚合+红队防御)"""

    def __init__(self):
        self._errors: list[str] = []

    # --------------------------------------------------------
    # 看板入口(fail-soft 四区聚合)
    # --------------------------------------------------------

    async def build(self) -> dict:
        """聚合看板(四区+红队防御区; 单区异常
        不阻断——错误入 zoneErrors)"""
        self._errors = []
        zones = {}
        zone_errors = {}

        for name, fn in (
                ("version", self._zone_version),
                ("factors", self._zone_factors),
                ("feedback", self._zone_feedback),
                ("drift", self._zone_drift),
                ("defense", self._zone_defense)):
            try:
                zones[name] = await fn()
            except Exception as exc:  # noqa: BLE001
                zone_errors[name] = str(exc)[:80]
                zones[name] = {"error":
                               str(exc)[:80]}
                logger.warning(
                    "login54_dash_zone_failed %s: %s",
                    name, exc)

        return {
            "success": True,
            "module": "login54",
            "scorerId": SCORER_ID,
            "zones": zones,
            "zoneErrors": zone_errors,
            "redlines": [
                "双模型 max 合成: auth_risk 独立并行"
                "不替换不侵入",
                "护栏 [0.5,2.0] 倍+归一化"
                "(权重投毒防御第一层)",
                "标注源集中度检测"
                "(投毒洪流防御第二层)",
                "滑动窗口回归检测→自动回滚+冻结",
                "LOGIN54_MODE/LEARN_MODE 双开关"
                "默认 off 零影响",
            ],
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # ① 版本区(champion/challenger+事件统计)
    # --------------------------------------------------------

    async def _zone_version(self) -> dict:
        """版本区: 在役版本对+模型事件统计"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        from repositories.login54_repository import (
            Login54Repository,
        )
        view = await get_weights_view(SCORER_ID)
        champion = view.get("champion") or {}
        challenger = view.get("challenger") or {}

        events = await Login54Repository(
        ).list_model_events(limit=200)
        by_type: dict = {}
        for e in events:
            et = str(e.get("eventType") or "unknown")
            by_type[et] = by_type.get(et, 0) + 1

        return {
            "champion": {
                "version": champion.get("version"),
                "source": champion.get("source"),
                "createdAt": champion.get("createdAt"),
            },
            "challenger": {
                "version": challenger.get("version"),
                "source": challenger.get("source"),
                "createdAt": challenger.get("createdAt"),
            } if challenger else None,
            "eventStats": {
                "total": len(events),
                "byType": by_type,
                "labels": EVENT_LABELS,
            },
            "recentEvents": [
                {"type": e.get("eventType"),
                 "label": EVENT_LABELS.get(
                     e.get("eventType"),
                     e.get("eventType")),
                 "at": e.get("createdAt")}
                for e in events[:5]],
        }

    # --------------------------------------------------------
    # ② 因子区(八因子权重 vs 默认雷达)
    # --------------------------------------------------------

    async def _zone_factors(self) -> dict:
        """因子区: 当前权重/默认/护栏区间/偏移幅度"""
        from services.ai_learning_service import (
            default_weights, get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        current = ((view.get("champion") or {})
                   .get("weights")) or {}
        defaults = default_weights(SCORER_ID)
        guardrail = float(
            (view.get("config") or {})
            .get("guardrail") or 2.0)

        radar = []
        for name, d in defaults.items():
            c = float(current.get(name) or 0)
            ratio = round(c / d, 3) if d else 0
            radar.append({
                "factor": name,
                "default": round(d, 4),
                "current": round(c, 4),
                "guardrailLo": round(d / guardrail, 4),
                "guardrailHi": round(d * guardrail, 4),
                "shiftRatio": ratio,
                "inGuardrail":
                    d / guardrail <= c
                    <= d * guardrail,
            })
        return {
            "weights": radar,
            "normalized": abs(
                sum(f["current"] for f in radar) - 1.0)
            < 1e-6,
            "guardrail": guardrail,
        }

    # --------------------------------------------------------
    # ③ 回流区(七类信号+池双写+延迟态)
    # --------------------------------------------------------

    async def _zone_feedback(self) -> dict:
        """回流区: 回流统计复用(P1 feedback_stats)"""
        from services.login54_feedback_service import (
            Login54FeedbackService,
        )
        stats = await Login54FeedbackService(
        ).feedback_stats()
        return {
            "total": stats.get("total"),
            "labeled": stats.get("labeled"),
            "pending": stats.get("pending"),
            "bySource": stats.get("bySource"),
            "rewardSplit": stats.get("rewardSplit"),
            "poolSubmitted":
                stats.get("poolSubmitted"),
            "signalRewards":
                stats.get("signalRewards"),
        }

    # --------------------------------------------------------
    # ④ 漂移区(EMA 统计+最近告警)
    # --------------------------------------------------------

    async def _zone_drift(self) -> dict:
        """漂移区: 漂移统计+最近 drift_alert"""
        from services.ai_learning_service import (
            get_drift_view,
        )
        from repositories.login54_repository import (
            Login54Repository,
        )
        view = await get_drift_view(SCORER_ID)
        drift = view.get("drift") or {}

        events = await Login54Repository(
        ).list_model_events(limit=100)
        alerts = [e for e in events
                  if e.get("eventType")
                  == "drift_alert"][:3]
        return {
            "driftLevel": drift.get("driftLevel"),
            "driftScore": drift.get("driftScore"),
            "count": drift.get("count"),
            "emaScore": drift.get("emaScore"),
            "baselineScore":
                drift.get("baselineScore"),
            "recentAlerts": [
                {"at": a.get("createdAt"),
                 "detail": a.get("detail")}
                for a in alerts],
        }

    # --------------------------------------------------------
    # 红队防御区(权重投毒双层防御状态)
    # --------------------------------------------------------

    async def _zone_defense(self) -> dict:
        """红队防御区: 护栏状态+标注源集中度检测
        (投毒洪流特征——单 source 占比异常)"""
        from services.ai_learning_service import (
            default_weights, get_weights_view,
        )
        from repositories.ai_learning_repository \
            import AiLearningRepository

        # 第一层: 护栏状态(逐因子核验)
        view = await get_weights_view(SCORER_ID)
        current = ((view.get("champion") or {})
                   .get("weights")) or {}
        defaults = default_weights(SCORER_ID)
        guardrail = float(
            (view.get("config") or {})
            .get("guardrail") or 2.0)
        violations = [
            {"factor": name,
             "current": round(float(
                 current.get(name) or 0), 4),
             "allowed": [
                 round(defaults[name]
                       / guardrail, 4),
                 round(defaults[name]
                       * guardrail, 4)]}
            for name in defaults
            if not (defaults[name] / guardrail
                    <= float(current.get(name) or 0)
                    <= defaults[name] * guardrail)
        ]

        # 第二层: 标注源集中度(44号池 recent)
        repo = AiLearningRepository()
        recent = await repo.list_feedback(
            SCORER_ID, limit=200)
        by_source: dict = {}
        for fb in recent:
            src = str(fb.get("source") or "unknown")
            by_source[src] = \
                by_source.get(src, 0) + 1
        total = len(recent)
        concentration = {
            "samples": total,
            "minSamples":
                CONCENTRATION_MIN_SAMPLES,
            "bySource": by_source,
            "topSource": None,
            "topRatio": 0.0,
            "alert": False,
        }
        if total >= CONCENTRATION_MIN_SAMPLES:
            top = max(by_source.items(),
                      key=lambda kv: kv[1])
            ratio = round(top[1] / total, 3)
            concentration.update({
                "topSource": top[0],
                "topRatio": ratio,
                "alert": ratio
                > SOURCE_CONCENTRATION_LIMIT,
            })

        return {
            "guardrail": {
                "range": f"[{1 / guardrail:.1f}, "
                         f"{guardrail:.1f}] 倍默认",
                "violations": violations,
                "healthy": not violations,
            },
            "sourceConcentration": concentration,
            "defenseNote": "双层防御: 护栏约束权重"
                           "边界+集中度检测投毒洪流"
                           "(>80% 单源告警)",
        }
