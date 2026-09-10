"""40号 P7d·雷达2.0 自主响应触发服务(设计文档《40号 P7 规划方案》§6)

引擎四: L1 上下文任务包 + 人机协同确认流(46号轨) + 下游触发

架构口径:
    - 上下文任务包: 不只推标题——"推荐角度+合规要点+素材建议+
      钩子方向"四件套(P7c 预演沙盘产出, 零重复生成)
    - 人机协同确认流(46号审批总线铁律):
        建任务 → 46号 submit_change(config) pending 留痕
        → 人工裁决 46号 review_change
        → 确认界面展示雷达决策依据(三维分+生命周期+风险提示)
    - 46号 P0 人工通道范式(63号同款): config 变更审批通过后
      总线不自动执行业务侧(payload 留痕供模块执行)——P7d 侧
      校验 46号 reviewedBy 非空(人工裁决留痕)后走模块终审,
      与 ab63 calibrate_apply 完全同款双轨
    - 下游执行器复用(零新增创作逻辑): L1 确认后触发 P6b 创作轨
      generate_script(脚本全链——五层合规内生+水印断言);
      P6e 转发轨对"雷达事件"语义不合(转发=对已侦测作品的授权
      转发), 不接入
    - 溯源 ID: 每任务唯一 traceId(P7e 归因闭环锚点——
      任务→attract 漏斗全程跟踪)

红线(宪法域):
    - L1 须经 46号确认: 高价值任务人工确认轨(永不自动执行)
    - 确认不回滚: 人工已确认的裁决不被下游执行器失败撤销
      (派发 fail-soft 留痕, 人工重试)
    - LLM 禁入: 任务包=模板拼装/确认流=状态机/派发=参数映射
"""

import logging
from datetime import datetime, UTC

from repositories.radar_repository import (
    RadarRepository, GRADE_L1, TASK_STATUS_PENDING,
    TASK_STATUS_CONFIRMED, TASK_STATUS_REJECTED,
    TASK_STATUS_DISPATCHED,
)

logger = logging.getLogger(__name__)


# ============================================================
# P7d 常量(设计文档 §6)
# ============================================================

# 任务活跃态(同事件幂等——活跃任务存在时不重复建)
TASK_ACTIVE_STATUSES = (TASK_STATUS_PENDING, TASK_STATUS_CONFIRMED,
                        TASK_STATUS_DISPATCHED)

# 46号审批总线绑定(scorer=40号博主作品评分档案——既有注册)
GOV_SCORER_ID = "blogger_work_gate"
GOV_CHANGE_KIND = "config"        # P0 人工通道(63号范式)

# 下游执行器(零新增创作逻辑——P6b 创作轨)
DOWNSTREAM_P6B_CREATE = "p6b_create"

# 预案钩子方向 → P6b hook_type 映射(确定性——P7c hookDirection 文本)
HOOK_TYPE_BY_DIRECTION = (
    (("情感陪伴", "互助", "应急"), "hook_emotional"),
    (("礼赠", "礼盒", "送礼"), "hook_gift_face"),
    (("品鉴", "美食", "探店", "酒友"), "hook_tasting_pro"),
    (("价格", "理性", "锚点"), "hook_price_anchor"),
)
HOOK_TYPE_DEFAULT = "hook_scene_grass"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def map_hook_type(hook_direction: str) -> str:
    """预案钩子方向 → P6b hook_type(确定性映射)"""
    text = str(hook_direction or "")
    for keywords, hook_type in HOOK_TYPE_BY_DIRECTION:
        if any(k in text for k in keywords):
            return hook_type
    return HOOK_TYPE_DEFAULT


def build_decision_basis(event: dict, score: dict = None) -> dict:
    """确认界面决策依据(设计 §6: 三维分+生命周期+风险提示)

    Args:
        event: 事件记录(P7b 已回写 grade/valueScore)
        score: 最新评分快照(三维分明细; 空则仅事件层字段)
    """
    plan = event.get("rehearsalPlan") or {}
    score = score or {}
    return {
        "grade": event.get("grade", ""),
        "valueScore": event.get("valueScore", 0),
        "fit": int(score.get("fit") or 0),
        "safety": float(score.get("safety") or 0),
        "conversion": float(score.get("conversion") or 0),
        "lifecycle": event.get("lifecycle", ""),
        "phase": event.get("predictedPhase", ""),
        "heatBase": int(event.get("heatBase") or 0),
        "crowdEmotion": event.get("crowdEmotion", ""),
        "botFiltered": bool(event.get("botFiltered")),
        "hookDirection": plan.get("hookDirection", ""),
        "riskNotes": ["安全系数已过硬闸(≥0.8, P7b)",
                      "预案已过合规预演(词表+授权库, P7c)"],
        "note": "L1 全条件达成: 价值≥75 且 契合≥70 且 安全≥0.8"
                " 且 爆发/发酵期",
    }


class RadarTaskService:
    """40号 P7d·雷达2.0 自主响应触发(L1 任务包→46号确认→
    P6b 创作轨派发)"""

    def __init__(self, repo: RadarRepository = None):
        self.repo = repo if repo is not None else RadarRepository()

    # ============================================================
    # 1. 任务包创建(46号留痕 + 溯源 ID)
    # ============================================================

    async def ensure_task(self, event_id: int,
                          requested_by: str = "radar_p7d") -> dict:
        """L1 事件任务包创建(幂等——预演通过后入执行队列)

        流程:
            ① 校验: 仅 L1 + 预演通过(预案四件套是任务包核心)
            ② 幂等: 同事件活跃任务存在 → 直接返回
            ③ 46号 submit_change(config) → changeId 绑定
               (审批总线留痕铁律)
            ④ 溯源 ID: 每任务唯一 traceId(P7e 归因锚)

        Raises:
            KeyError: 事件不存在
            ValueError: 非 L1 / 未过预演 / 46号提交冲突
        """
        event = await self.repo.get_event(event_id)
        if event is None:
            raise KeyError(f"事件不存在(eventId={event_id})")
        grade = event.get("grade") or ""
        if grade != GRADE_L1:
            raise ValueError(
                f"仅 L1 事件可建任务包(当前 {grade or '未评分'})"
                "——先执行三维评分(P7b)")
        if not event.get("rehearsalPassed"):
            raise ValueError(
                "事件未过合规预演沙盘(P7c)——先 rehearse 生成预案")
        existing = await self.repo.find_task_by_event(
            event_id, TASK_ACTIVE_STATUSES)
        if existing is not None:
            return existing
        # 决策依据: 三维分取最新评分快照(P7b)
        scores = await self.repo.list_scores(event_id=event_id,
                                            limit=1)
        basis = build_decision_basis(event, scores[0] if scores
                                     else None)
        # 46号审批总线留痕(config——P0 人工通道)
        from services.ai_governance_service import (
            AiGovernanceService)
        gov = AiGovernanceService()
        task_seq = await self.repo.next_id("task")
        trace_id = f"RADAR-{task_seq:06d}"
        try:
            result = await gov.submit_change(
                scorer_id=GOV_SCORER_ID, kind=GOV_CHANGE_KIND,
                payload={
                    "module": "radar_p7d",
                    "traceId": trace_id,
                    "eventId": event_id,
                    "title": event.get("title", ""),
                    "decisionBasis": basis,
                },
                reason=f"雷达L1高价值任务人工确认"
                       f"(traceId={trace_id}, "
                       f"{event.get('title', '')[:30]})",
                requested_by=requested_by)
        except KeyError:
            # 档案未入册 → 注册中心自发现(幂等)后重试
            await gov.sync_registry()
            result = await gov.submit_change(
                scorer_id=GOV_SCORER_ID, kind=GOV_CHANGE_KIND,
                payload={
                    "module": "radar_p7d",
                    "traceId": trace_id,
                    "eventId": event_id,
                    "title": event.get("title", ""),
                    "decisionBasis": basis,
                },
                reason=f"雷达L1高价值任务人工确认"
                       f"(traceId={trace_id}, "
                       f"{event.get('title', '')[:30]})",
                requested_by=requested_by)
        change_id = int(result.get("changeId") or 0)
        task = {
            "taskId": task_seq,
            "traceId": trace_id,
            "eventId": event_id,
            "changeId": change_id,
            "status": TASK_STATUS_PENDING,
            "plan": event.get("rehearsalPlan") or {},
            "decisionBasis": basis,
            "downstream": DOWNSTREAM_P6B_CREATE,
            "dispatchError": "",
            "dispatchScriptId": 0,
            "dispatchExecuted": False,
            "requestedBy": requested_by,
            "confirmedBy": "", "confirmNote": "",
            "createdAt": _now_iso(), "confirmedAt": "",
        }
        await self.repo.save_task(task)
        logger.info("p7d_task_created taskId=%s traceId=%s "
                    "changeId=%s", task_seq, trace_id, change_id)
        return task

    # ============================================================
    # 2. 人工确认(46号裁决轨——P0 人工通道范式)
    # ============================================================

    async def confirm_task(self, task_id: int, approve: bool,
                           reviewer: str = "admin",
                           note: str = "") -> dict:
        """L1 人工确认(46号轨——永不自动执行)

        裁决流:
            reject → 46号 review_change(approve=False) 干净驳回
                → 任务 rejected(决策回流 P7e)
            approve → 46号 review_change(approve=True)——P0
                config 人工通道抛 ValueError(reviewer 已留痕);
                校验 46号 reviewedBy 非空(63号 calibrate_apply
                同款)后走模块侧终审 → 派发 P6b 创作轨

        派发 fail-soft: 确认不回滚(下游失败留痕人工重试)。

        Raises:
            KeyError: 任务不存在
            ValueError: 任务非 pending / 46号裁决未留痕
        """
        task = await self.repo.get_task(task_id)
        if task is None:
            raise KeyError(f"任务不存在(taskId={task_id})")
        if task.get("status") != TASK_STATUS_PENDING:
            raise ValueError(
                f"任务已裁决({task.get('status')}), 不可重复确认")
        change_id = int(task.get("changeId") or 0)
        from services.ai_governance_service import (
            AiGovernanceService)
        gov = AiGovernanceService()
        if not approve:
            await gov.review_change(
                change_id, approve=False,
                reviewed_by=reviewer, review_note=note)
            task.update({
                "status": TASK_STATUS_REJECTED,
                "confirmedBy": reviewer,
                "confirmNote": note[:500],
                "confirmedAt": _now_iso()})
            task = await self.repo.update_task(task_id, task)
            logger.info("p7d_task_rejected taskId=%s by=%s",
                        task_id, reviewer)
            return {"task": task, "dispatched": False,
                    "note": "任务已否决(46号驳回留痕, "
                            "决策回流 P7e)"}
        # approve 轨: 46号 P0 config 人工通道
        reviewed_ok = False
        try:
            await gov.review_change(
                change_id, approve=True,
                reviewed_by=reviewer, review_note=note)
            reviewed_ok = True   # 未来总线支持时直达
        except ValueError:
            # P0 人工通道: 执行器不支持 config 自动执行
            # ——reviewedBy 已留痕(payload 供模块执行)
            from repositories.ai_governance_repository import (
                AiGovernance46Repository)
            change = await AiGovernance46Repository().get_change(
                change_id)
            if change is not None \
                    and change.get("reviewedBy"):
                reviewed_ok = True
        if not reviewed_ok:
            raise ValueError(
                f"46号裁决未留痕(changeId={change_id})"
                "——人工审批异常, 任务保持 pending")
        # 模块侧终审通过 → 派发下游(确认不回滚)
        task.update({
            "status": TASK_STATUS_CONFIRMED,
            "confirmedBy": reviewer,
            "confirmNote": note[:500],
            "confirmedAt": _now_iso()})
        dispatch_result = await self._dispatch(task)
        if dispatch_result.get("dispatchExecuted"):
            dispatch_result["status"] = TASK_STATUS_DISPATCHED
        task.update(dispatch_result)
        task = await self.repo.update_task(task_id, task)
        return {"task": task,
                "dispatched": bool(dispatch_result.get(
                    "dispatchExecuted")),
                "note": "任务已确认并派发(P6b 创作轨)" if
                dispatch_result.get("dispatchExecuted") else
                "任务已确认; 派发失败留痕(fail-soft, 人工重试)"}

    # ============================================================
    # 3. 下游派发(P6b 创作轨——零新增创作逻辑)
    # ============================================================

    async def _dispatch(self, task: dict) -> dict:
        """派发执行器(fail-soft——确认不因下游失败回滚)

        参数映射(确定性):
            topic=事件标题 / platform=事件平台 /
            persona=P6b 首个 active 原创 IP /
            hook_type=预案钩子方向映射
        """
        event = await self.repo.get_event(int(task["eventId"]))
        if event is None:
            return self._dispatch_fail(
                f"事件不存在(eventId={task['eventId']})")
        plan = task.get("plan") or {}
        hook_type = map_hook_type(plan.get("hookDirection"))
        # 人设: P6b 首个 active 原创 IP(无人设 → fail-soft)
        try:
            from repositories.blogger_repository import (
                BloggerRepository)
            personas = await BloggerRepository().list_personas(
                status="active", limit=100)
        except Exception as exc:  # noqa: BLE001
            return self._dispatch_fail(f"人设库读取失败: {exc}")
        persona = next(
            (p for p in personas
             if p.get("personaType") == "original_ip"), None)
        if persona is None:
            return self._dispatch_fail(
                "P6b 无 active 原创 IP 人设(先在 P6b 建人设)"
                "——人工建人设后重试派发")
        platform = event.get("platform") or "douyin"
        try:
            from services.blogger_av_create_service import (
                BloggerAVCreateService)
            script = await BloggerAVCreateService().generate_script(
                topic=event.get("title", ""),
                platform=platform,
                persona_id=int(persona["personaId"]),
                hook_type=hook_type)
        except Exception as exc:  # noqa: BLE001 确认不回滚
            logger.warning("p7d_dispatch_failed taskId=%s: %s",
                          task["taskId"], exc)
            return self._dispatch_fail(str(exc)[:300])
        return {
            "dispatchExecuted": True,
            "dispatchScriptId": int(script.get("scriptId") or 0),
            "dispatchError": "",
        }

    @staticmethod
    def _dispatch_fail(reason: str) -> dict:
        """派发失败留痕(fail-soft——confirmed 态保留)"""
        return {"dispatchExecuted": False, "dispatchScriptId": 0,
                "dispatchError": reason[:300]}

    # ============================================================
    # 4. 任务队列(观测面)
    # ============================================================

    async def list_tasks(self, status: str = None,
                         limit: int = 50) -> list[dict]:
        """任务包队列(确认状态过滤; 决策依据+预案随任务返回)"""
        tasks = await self.repo.list_tasks(status=status,
                                           limit=limit)
        return [{**t, "dispatchExecuted": bool(
            t.get("dispatchExecuted"))} for t in tasks]
