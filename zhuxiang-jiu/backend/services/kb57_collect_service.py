"""57号·AI智能知识库 定向采集运行器
(kb57_collect_service, P1)

计划(docs/57号_AI智能知识库模块实施计划.md §十一 P1):
    - 采集运行器(高优先级缺口→源白名单定向采集;
      低优先级观察队列)
    - 原始资源沙箱隔离态(quarantined——唯一铁律
      入口: 未经合规鉴别的资源严禁暴露终端用户)

设计(56号 mock 轨范式——确定性模板产出, LLM 不进
采集链):
    run_collect 主链:
        ① 缺口选取(open 态按优先级+必要性排序)
        ② 源白名单定向采集(建议源映射——内置
           SOURCE_REGISTRY+动态注册域)
        ③ mock 确定性内容生成(按源类型分发模板,
           以缺口主题为锚)
        ④ 资源落库 quarantined 隔离态
           (contentHash 指纹留痕)
        ⑤ 缺口状态翻转 open→collecting
        ⑥ collect 事件留痕

预算口径: 采集本身 mock 轨零成本; PII 扫描
成本在鉴别中心(P1 compliance)经 49号计量。
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_collect_service")

MODEL_VERSION = "v1-kb57-collect"

# 每缺口单轮采集的资源数上限(防信号风暴
# ——单缺口一次最多采 3 源)
MAX_SOURCES_PER_GAP = 3


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = os.environ.get("KB57_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"KB57_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


def _content_hash(text: str) -> str:
    """内容指纹(版权关去重锚点)"""
    return "sha256:" + hashlib.sha256(
        text.encode("utf-8")).hexdigest()[:32]


# ============================================================
# 采集内容模板(mock 轨确定性——按源类型分发)
# ============================================================

_COLLECT_TEMPLATES = {
    "authority": (
        "{topic}——官方权威口径要点: 一、适用范围"
        "与办理条件; 二、申请材料清单(身份证明、"
        "申请表); 三、办理时限与结果反馈渠道。"
        "本条目来自可信权威源, 供政策解读与办事"
        "指引使用。"),
    "partner": (
        "{topic}——授权合作方供数要点: 服务流程、"
        "注意事项与常见问题解答。数据经授权协议"
        "接入, 供业务场景参考。"),
    "internal": (
        "{topic}——站内运营手册要点: 标准作业流程"
        " (SOP) 分步骤说明、责任岗位与升级路径。"
        "内部知识, 供工作人员培训与操作使用。"),
    "media": (
        "{topic}——白名单媒体报道摘要: 近期相关"
        "动态与背景信息。转载需标注来源, 供时效性"
        "参考。"),
}


class Kb57CollectService:
    """57号定向采集运行器(P1)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 采集主链
    # ============================================================

    async def run_collect(self, gap_id: int = None
                          ) -> dict:
        """执行一轮定向采集(open 缺口→源白名单
        资源落库 quarantined 隔离态)

        Args:
            gap_id: 指定缺口(缺省扫全部 open 态)

        Raises:
            KeyError: 指定缺口不存在
            ValueError: off 态/缺口状态机非法
        """
        _require_active_mode()

        # ① 缺口选取
        if gap_id is not None:
            gap = await self.repo.get_gap(int(gap_id))
            if gap is None:
                raise KeyError(
                    f"缺口 {gap_id} 不存在")
            if gap.get("status") not in ("open",
                                         "collecting"):
                raise ValueError(
                    f"缺口状态 {gap.get('status')}"
                    f"(需 open/collecting 方可采集)")
            gaps = [gap]
        else:
            gaps = await self.repo.list_gaps(
                status="open", limit=100)
            if not gaps:
                return {
                    "success": True,
                    "scanned": 0,
                    "collected": 0,
                    "resources": [],
                    "note": "无 open 态缺口——采集空转"
                            "(P0 诊断先行)",
                    "ranAt": ts(),
                }

        # 按优先级排序(high→medium→low)
        order = {"high": 0, "medium": 1, "low": 2}
        gaps.sort(key=lambda g: (
            order.get(g.get("priority"), 3),
            -float(g.get("necessityScore") or 0)))

        collected = []
        skipped_low = 0
        for gap in gaps:
            # 低优先级 → 观察队列留痕(不采集)
            if order.get(gap.get("priority"), 3) >= 2:
                skipped_low += 1
                await self._track(
                    gap.get("gapId"), "collect_observe", {
                        "reason": "low_priority",
                        "priority": gap.get("priority"),
                    })
                continue
            resources = await self._collect_gap(gap)
            collected.extend(resources)

        return {
            "success": True,
            "scanned": len(gaps),
            "collected": len(collected),
            "observed": skipped_low,
            "resources": [
                {"resourceId": r["resourceId"],
                 "gapId": r["gapId"],
                 "sourceId": r["sourceId"],
                 "status": r["status"]}
                for r in collected],
            "note": "定向采集完成——资源入沙箱隔离态"
                    "(quarantined), 三重合规鉴别待触发",
            "ranAt": ts(),
        }

    # ============================================================
    # 单缺口采集
    # ============================================================

    async def _collect_gap(self, gap: dict) -> list:
        """单缺口采集(建议源映射→mock 内容生成→
        quarantined 落库)"""
        gap_id = int(gap.get("gapId") or 0)
        topic = str(gap.get("topic") or "知识缺口")
        suggested = list(
            gap.get("suggestedSources") or [])[
            :MAX_SOURCES_PER_GAP]
        if not suggested:
            suggested = ["ops_manual"]

        # 源域解析(内置白名单+动态注册域)
        sources = await self._resolve_sources(suggested)

        resources = []
        for source_id, source_meta in sources:
            resource = await self._build_resource(
                gap, source_id, source_meta, topic)
            await self.repo.save_resource(resource)
            resources.append(resource)
            await self._track(gap_id, "collect", {
                "resourceId": resource["resourceId"],
                "sourceId": source_id,
                "sourceType":
                    resource["sourceType"],
            })

        # 缺口状态翻转 open→collecting+预算留痕
        if gap.get("status") == "open":
            gap["status"] = "collecting"
            gap["updatedAt"] = ts()
            await self.repo.save_gap(gap,
                                     create=False)

        return resources

    async def _resolve_sources(self,
                               suggested: list
                               ) -> list:
        """建议源解析(内置白名单优先+动态注册域;
        白名单外建议源跳过留痕——版权关前置)"""
        from services.kb57_registry import (
            SOURCE_REGISTRY,
        )
        resolved = []
        for source_id in suggested:
            if source_id in SOURCE_REGISTRY:
                meta = dict(
                    SOURCE_REGISTRY[source_id],
                    sourceId=source_id)
                resolved.append((source_id, meta))
                continue
            # 动态注册域
            dynamic = await self.repo.list_sources(
                limit=1000)
            match = next(
                (s for s in dynamic
                 if s.get("sourceKey") == source_id),
                None)
            if match:
                resolved.append((source_id, {
                    "label": match.get("label"),
                    "sourceType":
                        match.get("sourceType"),
                    "credibility": float(
                        match.get("credibility")
                        or 0),
                    "license": match.get("license"),
                    "sourceId": source_id,
                }))
            else:
                logger.warning(
                    "kb57_source_not_whitelisted: %s",
                    source_id)
        return resolved

    async def _build_resource(self, gap: dict,
                              source_id: str,
                              source_meta: dict,
                              topic: str) -> dict:
        """资源构建(mock 确定性内容+quarantined
        隔离态+内容指纹)"""
        resource_id = await \
            self.repo.next_resource_id()
        source_type = str(
            source_meta.get("sourceType") or "internal")
        template = _COLLECT_TEMPLATES.get(
            source_type,
            _COLLECT_TEMPLATES["internal"])
        content_text = template.format(
            topic=topic[:48])

        return {
            "resourceId": resource_id,
            "gapId": int(gap.get("gapId") or 0),
            "sourceId": source_id,
            "sourceType": source_type,
            "sourceCredibility": round(float(
                source_meta.get("credibility")
                or 0), 4),
            "license": str(
                source_meta.get("license") or ""),
            "title": f"[{topic[:32]}] "
                     f"{source_meta.get('label')
                      or source_id} 采集条目",
            "contentText": content_text,
            "maskedText": "",
            "contentHash": _content_hash(
                f"{source_id}:{content_text}"),
            "status": "quarantined",
            "reviewRequired": False,
            "budgetHalted": False,
            "resourceVersion": 1,
            "complianceReports": [],
            "createdAt": ts(),
            "updatedAt": ts(),
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
                "kb57_collect_track_failed: %s", exc)
