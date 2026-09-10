"""40号 P6d·自治理与进化层扩展服务(设计文档《40号 P6 升级方案》§6)

六层归因漏斗(曝光→完播→点击→注册→信值激活→首单) + 共鸣度看板区 +
AV 专项自愈分类(转码/音画/上传) + AV 决策可解释(脚本链路回放)

架构口径:
    - 六层漏斗: 全量已发布 AV 作品 metrics 聚合——"works 触达"计数
      (P5d withClicks 范式扩展)+ 总量合计 + 平均完播
    - 共鸣度看板: P6a compute_resonance 四分量(完播/分享/收藏/正面
      密度)逐作品计算; < RESONANCE_WARN_LINE(0.3) 列低共鸣预警
    - AV 专项自愈: classify_av_failure 词表三分类(转码/音画/上传)
      + P5d 分类兜底(限流/下架/受限/瞬时/未知); 音画轨委托
      P6c heal_av_desync 重渲染; 耗尽永不静默丢弃(转人工留痕)
    - 全部自愈动作写入 blogger_audits(action=auto_heal)——与 P5d
      看板自愈流水区同源聚合

红线(宪法域):
    - 自愈耗尽转人工, 永不静默丢弃(P5d 铁律继承)
    - pause 后自愈拒绝(P6c heal_av_desync 口径一致)
    - LLM 禁入: 分类=词表匹配, 档位=整数比较, 共鸣=确定性公式
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository,
)
from services.blogger_service import BloggerService
from services.blogger_av_learn_service import (
    compute_resonance,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)
from services.blogger_av_publish_service import (
    BloggerAVPublishService, AV_DESYNC_WORDS, AV_REHEAL_RETRY_MAX,
    PUBLISH_STATUS_PUBLISHED,
)

logger = logging.getLogger(__name__)


# ============================================================
# P6d 常量(设计文档 §6)
# ============================================================

# AV 专项自愈: 三类失败词表(转码/上传; 音画复用 P6c AV_DESYNC_WORDS)
AV_TRANSCODE_WORDS = ("转码失败", "codec", "分辨率", "transcode",
                      "编码")
AV_UPLOAD_WORDS = ("上传超时", "upload timeout", "上传失败",
                   "upload failed", "upload")

# 自愈重试上限(与 P6c AV_REHEAL_RETRY_MAX 同口径)
AV_HEAL_RETRY_MAX = AV_REHEAL_RETRY_MAX

# 低共鸣预警线(看板预警区)
RESONANCE_WARN_LINE = 0.3

# 自愈动作状态
HEAL_STATUS_RETRY = "retry"
HEAL_STATUS_REHEALED = "re_rendered"
HEAL_STATUS_MANUAL_QUEUE = "manual_queue"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class BloggerAVGovernService:
    """40号 P6d·自治理扩展(六层漏斗/共鸣看板/AV 自愈/可解释)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())
        self._publish_svc = None

    @property
    def publish_svc(self) -> BloggerAVPublishService:
        """P6c 调度器(音画自愈委托)"""
        if self._publish_svc is None:
            self._publish_svc = BloggerAVPublishService(
                repo=self.repo, blogger_service=self.svc)
        return self._publish_svc

    async def _require_running(self) -> None:
        """P5d 铁律: pause 后自愈拒绝(P6c 口径一致)"""
        await BloggerAutoGovernService(
            repo=self.repo, blogger_service=self.svc
        ).require_running_async()

    # ============================================================
    # 1. AV 专项失败分类(词表三分类 + P5d 兜底)
    # ============================================================

    @staticmethod
    def classify_av_failure(error: str) -> str:
        """AV 失败词表归因(确定性分类, LLM 禁入)

        Returns:
            "transcode_failed" | "audio_desync" | "upload_timeout"
            | P5d 五类(rate_limit/content_takedown/
            account_restricted/transient/unknown)
        """
        text = str(error or "").lower()
        if any(w in text for w in AV_TRANSCODE_WORDS):
            return "transcode_failed"
        if any(w in text for w in AV_DESYNC_WORDS):
            return "audio_desync"
        if any(w in text for w in AV_UPLOAD_WORDS):
            return "upload_timeout"
        return BloggerAutoGovernService.classify_failure(error)

    # ============================================================
    # 2. AV 异常自愈状态机
    # ============================================================

    async def heal_av_failure(self, work_id: int, error: str,
                              attempt: int = 1) -> dict:
        """AV 异常自愈(确定性状态机, 设计文档 §6.2)

        状态机:
            audio_desync → 重渲染自愈(委托 P6c heal_av_desync)
            transcode_failed → retry(换渲染参数) → 耗尽转人工
            upload_timeout → retry(换通道) → 耗尽转人工
            rate_limit → 换时段重试
            content_takedown/account_restricted → 人工队列
            transient/unknown → retry 兜底
            重试耗尽 → 人工队列(留痕, 永不静默丢弃)

        Raises:
            KeyError: 作品不存在
            ValueError: 重试耗尽(转人工) / 自主行为已暂停
        """
        await self._require_running()
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        category = self.classify_av_failure(error)
        attempt = max(1, int(attempt or 1))
        # 音画不同步 → 委托 P6c 重渲染轨(attempt 由 reHeals 承载)
        if category == "audio_desync":
            reheal = await self.publish_svc.heal_av_desync(work_id)
            action = {"kind": HEAL_STATUS_REHEALED,
                      "note": f"音画不同步——重渲染自愈"
                              f"({reheal['attempt']}/{AV_HEAL_RETRY_MAX})"}
        elif category in ("content_takedown",
                          "account_restricted"):
            action = {"kind": HEAL_STATUS_MANUAL_QUEUE,
                      "note": f"{category}——转人工队列(留痕)"}
        elif category == "rate_limit":
            action = {"kind": HEAL_STATUS_RETRY,
                      "note": "限流——换时段重试"}
        elif attempt <= AV_HEAL_RETRY_MAX:
            note_map = {
                "transcode_failed": "转码失败——换渲染参数重试",
                "upload_timeout": "上传超时——换通道重试",
            }
            action = {"kind": HEAL_STATUS_RETRY,
                      "note": f"{note_map.get(category, '失败重试')}"
                              f"({attempt}/{AV_HEAL_RETRY_MAX})"}
        else:
            action = {"kind": HEAL_STATUS_MANUAL_QUEUE,
                      "note": f"重试{AV_HEAL_RETRY_MAX}次耗尽——"
                              "转人工队列(留痕不丢弃)"}
        # 自愈动作留痕(blogger_audits, 与 P5d 看板自愈流水同源)
        await self.svc._audit(
            0, "auto_heal",
            {"avWorkId": work_id, "category": category,
             "attempt": attempt, "action": action["kind"],
             "error": str(error)[:120]})
        if action["kind"] != HEAL_STATUS_REHEALED:
            await self.repo.update_av_work(work_id, {
                "healStatus": action["kind"],
                "healNote": action["note"],
                "healCategory": category,
                "healAttempt": attempt})
        if action["kind"] == HEAL_STATUS_MANUAL_QUEUE \
                and attempt > AV_HEAL_RETRY_MAX:
            raise ValueError(
                f"自愈耗尽({AV_HEAL_RETRY_MAX} 次)——"
                "已转人工队列(永不静默丢弃)")
        return {"avWorkId": work_id, "category": category,
                "attempt": attempt, "action": action}

    # ============================================================
    # 3. 六层归因漏斗(设计文档 §6.1)
    # ============================================================

    async def av_funnel(self) -> dict:
        """六层漏斗聚合(全量已发布 AV 作品)

        层: 曝光→完播→点击→注册→信值激活→首单;
        口径: works 触达计数(with 范式)+总量合计+平均完播。
        """
        works = await self.repo.list_av_works(limit=10000)
        published = [w for w in works
                     if w.get("publishStatus")
                     == PUBLISH_STATUS_PUBLISHED]

        def _m(w: dict, key: str) -> float:
            return float((w.get("metrics") or {}).get(key) or 0)

        def _with(key: str) -> int:
            return sum(1 for w in published if _m(w, key) > 0)

        completions = [_m(w, "completionRate") for w in published]
        avg_completion = round(
            sum(completions) / len(completions), 4
        ) if completions else 0.0
        return {
            "published": len(published),
            "withExposure": _with("exposures"),
            "withCompletion": _with("completionRate"),
            "withClicks": _with("clicks"),
            "withRegistered": _with("registered"),
            "withActivated": _with("activated"),
            "withOrdered": _with("ordered"),
            "totals": {
                "exposures": int(sum(
                    _m(w, "exposures") for w in published)),
                "clicks": int(sum(
                    _m(w, "clicks") for w in published)),
                "registered": int(sum(
                    _m(w, "registered") for w in published)),
                "activated": int(sum(
                    _m(w, "activated") for w in published)),
                "ordered": int(sum(
                    _m(w, "ordered") for w in published))},
            "avgCompletionRate": avg_completion,
        }

    # ============================================================
    # 4. 共鸣度看板区(设计文档 §6.1: 情感共鸣度驱动内容温度优化)
    # ============================================================

    async def resonance_view(self) -> dict:
        """共鸣度视图: 逐作品共鸣分 + 低共鸣预警列表"""
        works = await self.repo.list_av_works(limit=10000)
        published = [w for w in works
                     if w.get("publishStatus")
                     == PUBLISH_STATUS_PUBLISHED]
        entries, warnings = [], []
        for w in published:
            m = w.get("metrics") or {}
            if not m:
                continue
            resonance = compute_resonance(
                m.get("completionRate", 0),
                m.get("shareRate", 0),
                m.get("favoriteRate", 0),
                m.get("commentPositive", 0))
            entry = {"avWorkId": w["avWorkId"],
                     "platform": w.get("platform", ""),
                     "resonance": resonance}
            entries.append(entry)
            if resonance < RESONANCE_WARN_LINE:
                warnings.append(dict(entry, warn=True))
        avg = round(sum(e["resonance"] for e in entries)
                    / len(entries), 4) if entries else 0.0
        return {"sampled": len(entries),
                "avgResonance": avg,
                "warnLine": RESONANCE_WARN_LINE,
                "lowResonanceWorks": sorted(
                    warnings, key=lambda x: x["resonance"])[:10],
                "trend": sorted(entries,
                                key=lambda x: -x["resonance"])[:10]}

    # ============================================================
    # 5. 进化透明度看板(六层漏斗 + 共鸣区 + P5d 四区扩展)
    # ============================================================

    async def av_evolution_dashboard(self) -> dict:
        """AV 进化透明度看板: 自治开关/六层漏斗/共鸣度/
        策略排行/干预史/自愈流水(与 P5d 看板同范式聚合)"""
        # 区1: 自治开关(P5d 状态共享)
        state = await self.repo.get_autonomy_state()
        # 区2: 六层漏斗 + 区3: 共鸣度
        funnel = await self.av_funnel()
        resonance = await self.resonance_view()
        # 区4: 策略库排行 TOP5(P5b 策略库共享)
        strategies = await self.repo.list_strategies(limit=5)
        # 区5: 干预史(最近 10 条, P5d 干预记录共享)
        interventions = await self.repo.list_interventions(limit=10)
        # 区6: 自愈流水(auto_heal 审计聚合——P5d 同源)
        heals = []
        for a in await self.repo.list_audits(limit=200):
            detail = a.get("detail") or {}
            if a.get("action") == "auto_heal" \
                    and "avWorkId" in detail:
                heals.append({"auditId": a["auditId"],
                              "avWorkId": detail.get("avWorkId"),
                              "category": detail.get("category"),
                              "action": detail.get("action")})
                if len(heals) >= 10:
                    break
        return {
            "autonomy": {"paused": bool(state.get("paused")),
                         "reason": state.get("reason", ""),
                         "pausedAt": state.get("pausedAt", "")},
            "funnel": funnel,
            "resonance": resonance,
            "strategies": [
                {"strategyId": s["strategyId"],
                 "type": s.get("type"),
                 "displayName": s.get("displayName"),
                 "winCount": s.get("winCount"),
                 "avgReward": s.get("avgReward")}
                for s in strategies],
            "interventions": [
                {"interventionId": i["interventionId"],
                 "kind": i.get("kind"), "note": i.get("note"),
                 "operator": i.get("operator"),
                 "createdAt": i.get("createdAt")}
                for i in interventions],
            "heals": heals,
        }

    # ============================================================
    # 6. AV 决策可解释(脚本链路回放——"AI 为何这样创作")
    # ============================================================

    async def av_decision_explain(self, work_id: int) -> dict:
        """AV 作品决策可解释报告(规则链路回放)

        链路: 形式决策(平台调性) → 人设匹配(授权硬门) →
        合规内生(水印断言) → 渲染(三态) → 发布(首发审批)。

        Raises:
            KeyError: 作品不存在
        """
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        script = await self.repo.get_av_script(
            int(work.get("scriptId") or 0)) or {}
        chain = []
        # 链路1: 形式决策(平台调性表回放)
        if script:
            form_exp = (f"形式{script.get('form')}"
                        f"(平台{script.get('platform')}调性表映射), "
                        f"钩子{script.get('hookName')}, "
                        f"结构{script.get('structureName')}")
            chain.append({"step": "form_decision",
                          "explain": form_exp})
            # 链路2: 人设匹配(授权硬门回放)
            persona_exp = (f"人设{script.get('personaName')}"
                          f"(情绪{script.get('emotion')}, "
                          f"语速{script.get('voiceStyle')})")
            chain.append({"step": "persona_match",
                          "explain": persona_exp})
            # 链路3: 合规内生(水印断言回放)
            wm_hash = str(script.get("watermarkHash") or "")[:8]
            ai_mark = ("已嵌入" if script.get("aiWatermark")
                       else "缺失")
            comp_exp = (f"合规分{script.get('complianceScore')}"
                        f"(三审闸门), 水印哈希{wm_hash}…, "
                        f"AI 标识{ai_mark}")
            chain.append({"step": "compliance_intrinsic",
                          "explain": comp_exp})
        # 链路4: 渲染(三态回放)
        meta = work.get("meta") if isinstance(
            work.get("meta"), dict) else {}
        file_path = str(meta.get("filePath") or "—")[:120]
        render_exp = (f"渲染{work.get('renderMode')}轨"
                      f"(状态{work.get('renderStatus')}), "
                      f"产物{file_path}")
        chain.append({"step": "render", "explain": render_exp})
        # 链路5: 发布(首发审批回放)
        publish_exp = f"发布状态{work.get('publishStatus')}"
        if work.get("publishProposal"):
            publish_exp += ", 首发审批提单"
        if work.get("healStatus"):
            publish_exp += f", 自愈{work.get('healStatus')}"
        chain.append({"step": "publish", "explain": publish_exp})
        return {"avWorkId": work_id,
                "platform": work.get("platform", ""),
                "chain": chain}
