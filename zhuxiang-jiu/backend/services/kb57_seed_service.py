"""57号·AI智能知识库 知识种子工坊
(kb57_seed_service, P2)

计划(docs/57号_AI智能知识库模块实施计划.md §五):
    - 种子工坊: compliant 资源→结构化认知种子
      (多模态 content 封装+KNOWLEDGE_REASON
      注释即证据+多模态对齐元数据)
    - 版本化+A/B: 同知识点新版发布旧版不删除
      自动降权(abTest.variantOf 关联对照)
    - 有效期元数据: validUntil 过期自动降权+
      触发更新流程

种子结构规范(计划 §5.1):
    SEED_TYPES 封闭枚举——text/image/video/
    qa_pair/workflow; 首期 text+qa_pair 全落地,
    image/video 带 mediaRef 引用+多模态对齐
    元数据(摘要/关键帧/转写稿/alt 无障碍)。

状态机(八态——计划 §5.2):
    sandbox → review → published → boosted/
    downgraded → retired; rejected; recalled

铁律: 无合规指纹不入库——craft 仅消费
compliant 态资源(quarantined 待人工复审资源
须先人工放行)。
"""

import logging
import os

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_seed_service")

MODEL_VERSION = "v1-kb57-seed"

# 种子类型封闭枚举(计划 §5.1)
SEED_TYPES = (
    "text", "image", "video", "qa_pair", "workflow")

# 种子有效期(天)——过期自动降权+触发更新
DEFAULT_VALID_DAYS = 365

# 信值标签候选域(建议集——写入不强制封闭,
# 推荐池检索用)
VALUE_TAG_SUGGESTIONS = (
    "elderly_service", "policy", "sop",
    "high_trust", "accessibility", "tutorial")


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = os.environ.get("KB57_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"KB57_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


class Kb57SeedService:
    """57号知识种子工坊(P2)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 种子锻造主链
    # ============================================================

    async def craft(self, gap_id: int,
                    resource_id: int,
                    seed_type: str = "text",
                    value_tags: list = None
                    ) -> dict:
        """compliant 资源→结构化认知种子(sandbox 态)

        Args:
            gap_id: 归属缺口
            resource_id: 合规资源(compliant 态)
            seed_type: SEED_TYPES 封闭枚举
            value_tags: 信值标签

        Raises:
            KeyError: 资源不存在
            ValueError: off 态/资源状态机非法
                (非 compliant——无指纹不入库铁律)
        """
        _require_active_mode()

        if seed_type not in SEED_TYPES:
            raise ValueError(
                f"非法种子类型 {seed_type}"
                f"(合法值: {list(SEED_TYPES)})")

        resource = await self.repo.get_resource(
            int(resource_id))
        if resource is None:
            raise KeyError(
                f"资源 {resource_id} 不存在")
        if resource.get("status") != "compliant":
            raise ValueError(
                f"资源状态 {resource.get('status')}"
                f"(需 compliant 方可锻造——无合规"
                f"指纹不入库铁律)")
        fingerprint = str(
            resource.get("fingerprint") or "")
        if not fingerprint.startswith("sha256:"):
            raise ValueError(
                "资源无合规指纹(铁律: 无指纹不入库)")

        gap = await self.repo.get_gap(int(gap_id))
        if gap is None:
            raise KeyError(f"缺口 {gap_id} 不存在")

        # ① 种子内容封装(多模态结构)
        content = self._build_content(
            resource, seed_type)

        # ② KNOWLEDGE_REASON 注释即证据
        knowledge_reason = self._knowledge_reason(
            gap, resource, seed_type)

        # ③ 版本化(同缺口同知识点旧版定位)
        seed_id = await self.repo.next_seed_id()
        existing = await self.repo.list_seeds(limit=1000)
        prior = [
            s for s in existing
            if int(s.get("gapId") or 0)
            == int(gap_id)
            and s.get("type") == seed_type
            and s.get("status") in (
                "published", "boosted", "downgraded",
                "retired", "sandbox", "review")]

        seed = {
            "seedId": seed_id,
            "seedVersion": len(prior) + 1,
            "type": seed_type,
            "title": str(resource.get("title")
                         or "知识种子")[:128],
            "content": content,
            "contentHash": str(
                resource.get("contentHash") or ""),
            "complianceFingerprint": fingerprint,
            "valueTags": list(value_tags or
                              ["policy"]),
            "sourceId": str(
                resource.get("sourceId") or ""),
            "sourceCredibility": float(
                resource.get("sourceCredibility")
                or 0),
            "privacyCost": 0.002,
            "knowledgeReason": knowledge_reason,
            "humanVerified": False,
            "validUntil": self._valid_until(),
            "abTest": {
                "active": False,
                "variantOf": None,
            },
            "status": "sandbox",
            "gapId": int(gap_id),
            "resourceId": int(resource_id),
            "viewCount": 0,
            "positiveCount": 0,
            "negativeCount": 0,
            "pooledFeedbackId": 0,
            "llmCalls": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_seed(seed)

        # ④ seed_craft 事件留痕
        await self._track(gap_id, "seed_craft", {
            "seedId": seed_id,
            "seedVersion": seed["seedVersion"],
            "type": seed_type,
            "resourceId": int(resource_id),
        })

        return {
            "success": True,
            "seedId": seed_id,
            "seedVersion": seed["seedVersion"],
            "status": "sandbox",
            "type": seed_type,
            "knowledgeReason": knowledge_reason,
            "note": "种子锻造完成(sandbox 态)——"
                    "人类终审 review 后发布",
            "craftedAt": ts(),
        }

    # ============================================================
    # 多模态内容封装
    # ============================================================

    @staticmethod
    def _build_content(resource: dict,
                       seed_type: str) -> dict:
        """种子内容结构(按类型分形态——计划 §5.1)

        text/qa_pair 全落地; image/video 带
        mediaRef 引用+多模态对齐元数据。
        """
        text = str(
            resource.get("maskedText")
            or resource.get("contentText") or "")
        content = {
            "text": text,
            "mediaRef": None,
            "transcript": None,
            "keyframes": None,
            "alt": None,
        }
        if seed_type == "qa_pair":
            title = str(
                resource.get("title")
                or "知识问答")[:64]
            content["text"] = (
                f"Q: {title}\nA: {text}")
        elif seed_type == "image":
            content["mediaRef"] = (
                "/media/image/kb57-placeholder.png")
            content["alt"] = (
                f"[无障碍替代文本] {text[:80]}")
        elif seed_type == "video":
            content["mediaRef"] = (
                "/media/voice/kb57-placeholder.mp3")
            content["transcript"] = text
            content["keyframes"] = [0.0, 15.0, 30.0]
        elif seed_type == "workflow":
            content["text"] = (
                "步骤一: " + text[:64]
                + "\n步骤二: 提交审核\n步骤三: 反馈结果")
        return content

    @staticmethod
    def _knowledge_reason(gap: dict,
                           resource: dict,
                           seed_type: str) -> str:
        """KNOWLEDGE_REASON 注释即证据
        (56号 VALUE_REASON 范式——入库依据+
        预期用途, 人类审核与 AI 推理时引用)"""
        snap = gap.get("signalSnapshot") or {}
        hits = snap.get("hits") or []
        signals = ",".join(
            h.get("signalId") or "?" for h in hits[:3]
        ) or "manual"
        topic = str(gap.get("topic") or "")
        source_id = str(
            resource.get("sourceId") or "")
        credibility = resource.get(
            "sourceCredibility")
        return (
            f"覆盖缺口 {topic}(信号: {signals}); "
            f"来源 {source_id}"
            f"(可信度 {credibility}); "
            f"类型 {seed_type}——供角色植入"
            f"学习与检索场景使用")

    @staticmethod
    def _valid_until() -> str:
        """有效期(365 日后——过期自动降权)"""
        from datetime import datetime, timedelta
        return (
            datetime.utcnow() + timedelta(
                days=DEFAULT_VALID_DAYS)
        ).strftime("%Y-%m-%d")

    # ============================================================
    # 版本化+A/B(新版发布旧版降权)
    # ============================================================

    async def _demote_prior_versions(
            self, gap_id: int, seed_type: str,
            new_seed_id: int) -> int:
        """旧版降权不删除(published/boosted→
        downgraded; A/B variantOf 关联)"""
        existing = await self.repo.list_seeds(
            limit=1000)
        demoted = 0
        for s in existing:
            if int(s.get("seedId") or 0) \
                    == int(new_seed_id):
                continue
            if int(s.get("gapId") or 0) != int(gap_id) \
                    or s.get("type") != seed_type:
                continue
            if s.get("status") in ("published",
                                   "boosted"):
                s["status"] = "downgraded"
                s["abTest"] = {
                    "active": True,
                    "variantOf": int(new_seed_id),
                }
                s["updatedAt"] = ts()
                await self.repo.save_seed(
                    s, create=False)
                demoted += 1
        return demoted

    # ============================================================
    # 有效期健康检查
    # ============================================================

    async def freshness_check(self) -> dict:
        """有效期健康检查(过期种子自动降权+
        触发更新流程——计划 §五 版本化+有效期)"""
        from datetime import datetime
        today = datetime.utcnow().strftime(
            "%Y-%m-%d")
        seeds = await self.repo.list_seeds(
            limit=1000)
        expired = 0
        demoted = 0
        for s in seeds:
            valid_until = str(
                s.get("validUntil") or "")
            if not valid_until:
                continue
            if valid_until >= today:
                continue
            expired += 1
            if s.get("status") in ("published",
                                   "boosted"):
                s["status"] = "downgraded"
                s["updatedAt"] = ts()
                await self.repo.save_seed(
                    s, create=False)
                demoted += 1
                await self._track(
                    int(s.get("gapId") or 0),
                    "seed_expire", {
                        "seedId": s.get("seedId"),
                        "validUntil": valid_until,
                    })
        return {
            "success": True,
            "scanned": len(seeds),
            "expired": expired,
            "demoted": demoted,
            "note": "有效期健康检查——过期种子自动"
                    "降权+触发更新流程",
            "checkedAt": ts(),
        }

    # ============================================================
    # 种子查询(观测面)
    # ============================================================

    async def list_seeds(self, status: str = None,
                         seed_type: str = None
                         ) -> dict:
        """种子列表(观测面)"""
        records = await self.repo.list_seeds(
            status=status, seed_type=seed_type,
            limit=200)
        return {
            "success": True,
            "total": len(records),
            "seeds": records,
            "seedTypes": list(SEED_TYPES),
            "note": "知识种子库——版本化+八态状态机",
        }

    async def get_seed(self, seed_id: int) -> dict:
        """种子详情(观测面)"""
        seed = await self.repo.get_seed(
            int(seed_id))
        if seed is None:
            raise KeyError(
                f"种子 {seed_id} 不存在")
        return {
            "success": True,
            "seed": seed,
            "note": "知识种子详情——合规指纹+"
                    "KNOWLEDGE_REASON+多模态 content",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, gap_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "gapId": int(gap_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_seed_track_failed: %s", exc)
