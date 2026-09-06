"""61号·AI智能系统升级决策 决策请求底座
(dm61_service, P0)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.1/§七 P0):
    P0 底座:
        ① 决策请求接收(三源——56号提案/
           44号信号/人工发起→统一请求域)
        ② 语义标签轨(确定性关键词规则
           ——LLM 不进判定链)
        ③ 环境感知(WINDOW_CHECK 窗口
           适宜性——高峰/故障/波动)
        ④ 影响面预测(DEPENDENCY_MAP
           封闭注册)
        ⑤ 状态机 received→tagged
           (九态首两态——P1 续)
        ⑥ registry/model_status 观测面
           (44号 get_weights_view 复用)

铁律(计划 §一/§九):
    - 默认零影响(DM61_MODE off——决策面
      关闭)
    - 三权分立: 61号仅参谋部——执行永远
      走 46号总线, 本模块不执行变更
    - 56号提案纯消费(零改动)
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_service")

MODEL_VERSION = "v1-dm61-service"

SCORER_ID = "decision_orchestration"

# 请求来源域(三源——计划 §五)
REQUEST_SOURCES = (
    "proposal",   # 56号升级提案
    "signal",     # 44号学习信号
    "manual",     # 人工发起
)

# 请求状态机九态(计划 §五——P0 落
# received/tagged 两态)
REQUEST_STATES = (
    "received",     # 已接收
    "tagged",       # 语义标注完成
    "assessed",     # 风险评估完成(P1)
    "simulated",    # 沙箱推演完成(P2)
    "recommended",  # 方案推荐完成(P1)
    "decided",      # 人类裁决完成(P1)
    "executed",     # 经46号总线回执(P1)
    "closed",       # 关闭归档
    "rejected",     # 拒绝(前置门槛)
)


def current_mode() -> str:
    """模块开关(DM61_MODE, 默认 off)"""
    return os.environ.get(
        "DM61_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"DM61_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Dm61Service:
    """61号决策请求底座+观测面(P0)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """决策注册表视图(观测面不受开关影响)"""
        from services.dm61_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "request": {
                "sources": list(
                    REQUEST_SOURCES),
                "states": list(
                    REQUEST_STATES),
            },
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe",
                               "optimize",
                               "urgent"),
            },
            "note": "P0 底座: 决策注册表"
                    "语义标签六类+依赖映射+"
                    "第36档案(P1 三级决策矩阵"
                    "完整交付)",
        })
        return view

    # ============================================================
    # 决策请求接收(三源——P0 核心)
    # ============================================================

    async def create_request(self,
                             title: str,
                             description: str = "",
                             source: str = "manual",
                             proposal_id: int = None,
                             signal_id: int = None,
                             requested_by: str = "admin",
                             hour: int = None,
                             recent_failure_rate:
                             float = None,
                             trust_volatility:
                             float = None) -> dict:
        """决策请求接收+语义标签+环境感知+
        影响面预测(P0 全链)

        状态机: received→tagged(P0 落两态
        ——一步完成语义标注)

        Args:
            title: 变更标题(语义解析主输入)
            description: 变更描述(辅输入)
            source: 请求来源(三源域)
            proposal_id: 56号提案 ID
                (source=proposal 时必填)
            signal_id: 44号信号 ID
                (source=signal 时必填)
            requested_by: 发起人
            hour: 评估时点(缺省取当前)
            recent_failure_rate: 近 7 日
                变更失败率(缺省环境感知读取)
            trust_volatility: 信值分布波动
                (缺省环境感知读取)

        Raises:
            ValueError: off 态/来源域外/
                proposal 源缺提案号
        """
        require_active_mode()
        source = str(source or "").strip()
        if source not in REQUEST_SOURCES:
            raise ValueError(
                f"来源 {source} 域外"
                f"(合法: {'/'.join(
                    REQUEST_SOURCES)})")
        if source == "proposal" \
                and not proposal_id:
            raise ValueError(
                "proposal 源必须携带 "
                "proposalId(56号提案号)")
        if source == "signal" \
                and not signal_id:
            raise ValueError(
                "signal 源必须携带 "
                "signalId(44号信号号)")

        title = str(title or "").strip()
        if not title:
            raise ValueError("变更标题不能为空")

        # ① 语义标签轨(确定性——LLM 不进
        #    判定链)
        from services.dm61_registry import (
            parse_semantic_tag,
        )
        semantic = parse_semantic_tag(
            title, description)

        # ② 影响面预测(DEPENDENCY_MAP)
        from services.dm61_registry import (
            predict_impact,
        )
        impact = predict_impact(
            semantic["tag"])

        # ③ 环境感知(WINDOW_CHECK——
        #    缺省因子自动感知)
        env_factors = {}
        if hour is None:
            import datetime
            hour = datetime.datetime.now().hour
        if recent_failure_rate is None:
            recent_failure_rate = \
                await self._recent_failure_rate()
        if trust_volatility is None:
            trust_volatility = \
                await self._trust_volatility()
        from services.dm61_registry import (
            check_window,
        )
        window = check_window(
            hour,
            recent_failure_rate=
                recent_failure_rate,
            trust_volatility=
                trust_volatility)
        env_factors.update(window)

        # ④ 落库(received→tagged 一步)
        request_id = await \
            self.repo.next_request_id()
        fingerprint = _fingerprint(
            request_id, title, source,
            semantic["tag"])
        record = {
            "requestId": request_id,
            "title": title,
            "description": str(
                description or ""),
            "source": source,
            "proposalId": int(proposal_id
                              or 0),
            "signalId": int(signal_id or 0),
            "requestedBy": str(
                requested_by or "admin"),
            "status": "tagged",
            "tag": semantic["tag"],
            "semantic": semantic,
            "impact": impact,
            "environment": env_factors,
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_request(record)

        await self._track(request_id, "request", {
            "action": "create",
            "source": source,
            "tag": semantic["tag"],
            "sensitivity":
                semantic["sensitivity"],
            "windowLevel":
                window.get("level"),
            "requestedBy": requested_by,
        })
        return {
            "success": True,
            "requestId": request_id,
            "status": "tagged",
            "source": source,
            "semantic": semantic,
            "impact": impact,
            "environment": env_factors,
            "fingerprint": fingerprint,
            "note": "决策请求已接收——语义标签"
                    "+影响面+环境感知完成"
                    "(P1 评估接管)",
            "createdAt": record["createdAt"],
        }

    # --------------------------------------------------------
    # 观测面(请求)
    # --------------------------------------------------------

    async def get_request(self,
                          request_id: int) -> dict:
        """请求详情(观测面——语义+影响面+
        环境快照+最新评估/决策联动)

        Raises:
            KeyError: 请求不存在
        """
        record = await self.repo.get_request(
            int(request_id))
        if not record:
            raise KeyError(
                f"决策请求 {request_id} 不存在")
        # 最新评估+决策联动(P1 观测面)
        assessment = None
        decision = None
        try:
            assessments = await (
                self.repo.list_assessments(
                    request_id=int(request_id)))
            assessment = assessments[0] \
                if assessments else None
        except Exception:  # noqa: BLE001
            assessment = None
        try:
            decisions = await (
                self.repo.list_decisions(
                    request_id=int(request_id)))
            decision = decisions[0] \
                if decisions else None
        except Exception:  # noqa: BLE001
            decision = None
        return {
            "success": True,
            "request": record,
            "latestAssessment": assessment,
            "latestDecision": decision,
            "note": "决策请求详情——语义标签+"
                    "影响面+环境感知快照"
                    "+最新评估/决策联动",
        }

    async def list_requests(self,
                            source: str = None,
                            tag: str = None,
                            status: str = None
                            ) -> dict:
        """请求列表(观测面——来源/标签/
        状态三过滤)"""
        records = await self.repo.list_requests(
            source=source, tag=tag,
            status=status)
        by_tag: dict = {}
        by_source: dict = {}
        for r in records:
            by_tag[r.get("tag")] = \
                by_tag.get(r.get("tag"), 0) + 1
            by_source[r.get("source")] = \
                by_source.get(
                    r.get("source"), 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byTag": by_tag,
            "bySource": by_source,
            "requests": records,
            "note": "决策请求列表——三源×六类"
                    "标签分布",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view
        复用——第36档案)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            SCORER_ID)
        view.update({
            "module": "dm61",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "decision_accuracy":
                    "决策准确率",
                "autonomous_ratio":
                    "自治占比",
                "simulation_hit_rate":
                    "预测命中",
                "dissent_effectiveness":
                    "预警有效",
                "member_trust": "会员信值",
                "rollback_success":
                    "回滚可靠",
                "latency_budget":
                    "决策时效",
                "coverage_breadth":
                    "场景覆盖",
            },
            "decisions": ["observe",
                          "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——"
                    "第36档案",
        })
        return {"success": True,
                "status": view}

    # --------------------------------------------------------
    # 内部(环境感知——纯读取 fail-soft)
    # --------------------------------------------------------

    @staticmethod
    async def _recent_failure_rate() -> float:
        """近期变更失败率感知(56号提案
        驱动记录纯读取 fail-soft 中性 0)

        P0 口径: 56号 proposal 无失败态
        终局时取 0(中性); 有 failed/
        rejected 提案时按占比计算。
        """
        try:
            from repositories.aiup56_repository import (
                Aiup56Repository,
            )
            proposals = await (
                Aiup56Repository()
                .list_proposals(limit=100))
            if not proposals:
                return 0.0
            failed = sum(
                1 for p in proposals
                if str(p.get("status") or "")
                in ("failed", "rejected",
                    "aborted"))
            return round(
                failed / len(proposals), 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_env_failure_failsoft: "
                "%s", exc)
            return 0.0

    @staticmethod
    async def _trust_volatility() -> float:
        """信值分布稳定性感知(45号入库存证
        事件纯读取 fail-soft 中性 0)

        P0 口径: 近 90 日 deposit 事件中
        负向事件(finalScore<0)占比——
        高频负向处置=分布波动。
        """
        try:
            from repositories.trust_value_repository import (
                TrustValue45Repository,
            )
            records = await (
                TrustValue45Repository()
                .list_deposit_events(days=90))
            if not records:
                return 0.0
            negative = sum(
                1 for r in records
                if float(r.get("finalScore")
                         or 0) < 0)
            return round(
                negative / len(records), 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_env_volatility_failsoft: "
                "%s", exc)
            return 0.0

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "requestId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_track_failed %s: %s",
                event_type, exc)
