"""59号·AI智能服务编排 客服会话引擎
(ii59_conversation_service, P1)

计划(docs/59号_AI智能服务编排模块实施计划.md
§三/§九 P1):
    意图消费路由:
        58号 evaluate 结果 → ROUTING_TABLE
        服务通道 → 会话 serving 态+任务实例
    上游铁律继承:
        - clarify/partial 态不编排
          (无确认不执行——会话保持 opened)
        - boundaryIntercepted 拒绝开话
          (识别即合规下游执行)
        - confirm_required 需确认后编排
          (sensitive 模板 confirm 步骤衔接)
    任务编排(TASK_TEMPLATES):
        意图→步骤序列 DSL(确定性)+版本化+
        步骤推进+失败 fail-soft(转 escalated
        或 resolved(partial))
    人工接管(escalate):
        上下文脱敏移交(48号 mask_pii 复用)
        +排队位次+会话终态经人工闭话
    闭话+满意度采集:
        resolved→closed 必采 satisfaction
        (1-5 分——回流真值源)
"""

import logging
import os

from core.helpers import ts

from repositories.ii59_repository import (
    Ii59Repository,
)

logger = logging.getLogger(
    "ii59_conversation_service")

MODEL_VERSION = "v1-ii59-conversation"

# 任务编排模板(意图 → 步骤序列 DSL——
# 计划 §3.2; 封闭注册+版本化)
TASK_TEMPLATES: dict = {
    "trust.convert_intent": {
        "label": "信值兑换流程",
        "version": 1,
        "steps": [
            "confirm_amount",
            "verify_token",
            "execute_convert",
            "notify_result",
        ],
        "sensitive": True,
        "rollback": "notify_rollback",
    },
    "product.price_query": {
        "label": "价格查询流程",
        "version": 1,
        "steps": [
            "search_product",
            "render_price_card",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "product.new_query": {
        "label": "新品咨询流程",
        "version": 1,
        "steps": [
            "search_product",
            "render_product_card",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "trust.balance_query": {
        "label": "余额查询流程",
        "version": 1,
        "steps": [
            "load_balance",
            "render_balance_card",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "trust.score_query": {
        "label": "信值分查询流程",
        "version": 1,
        "steps": [
            "load_trust_score",
            "render_score_card",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "nav.page_jump": {
        "label": "页面导航流程",
        "version": 1,
        "steps": [
            "resolve_target_page",
            "jump_page",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "promo.query": {
        "label": "优惠查询流程",
        "version": 1,
        "steps": [
            "load_promotions",
            "render_promo_card",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "chat.human_transfer": {
        "label": "转人工流程",
        "version": 1,
        "steps": [
            "escalate_to_human",
        ],
        "sensitive": False,
        "rollback": "",
    },
    "explanation.report_query": {
        "label": "解读报告流程",
        "version": 1,
        "steps": [
            "load_report",
            "render_report",
        ],
        "sensitive": False,
        "rollback": "",
    },
}

# 步骤执行结果语义
STEP_OK = "done"
STEP_FAILED = "failed"


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = os.environ.get("II59_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"II59_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


def _mask_pii(text: str) -> str:
    """脱敏(48号 mask_pii 复用)"""
    try:
        from services.xiaozhu_service import (
            mask_pii,
        )
        return mask_pii(str(text or ""))
    except Exception:  # noqa: BLE001
        return str(text or "")


class Ii59ConversationService:
    """59号客服会话引擎(P1)"""

    def __init__(self):
        self.repo = Ii59Repository()

    # ============================================================
    # ① 意图消费路由
    # ============================================================

    async def route_intent(self, session_id: int,
                           text: str,
                           member_role: str
                           = "member") -> dict:
        """意图路由(58号 evaluate 纯消费→
        ROUTING_TABLE 服务通道)

        上游铁律:
            - clarify/partial → 不编排
              (会话保持 opened+候选引导)
            - boundaryIntercepted → 拒绝路由
            - confirm_required → sensitive
              模板 confirm 步骤衔接

        Raises:
            KeyError: 会话不存在
            ValueError: off 态/会话终态/
                文本为空
        """
        _require_active_mode()
        session = await self._live_session(
            int(session_id),
            allowed=("opened", "serving"))
        text = str(text or "").strip()
        if not text:
            raise ValueError("路由文本不能为空")

        # ① 58号 evaluate 纯消费
        # (fail-soft: 58号关闭/异常→clarify 兜底)
        intent_id = ""
        state = "clarify"
        confidence = 0.0
        candidates = []
        boundary = False
        confirm_required = False
        attribution = {}
        try:
            from services.ii58_service import (
                Ii58Service,
            )
            r = await Ii58Service().evaluate(
                text,
                member_id=session.get(
                    "memberId") or None,
                member_role=member_role)
            intent_id = str(r.get("intentId")
                           or "")
            state = str(r.get("state")
                        or "clarify")
            confidence = float(
                r.get("confidence") or 0)
            candidates = r.get(
                "candidates") or []
            boundary = bool(
                r.get("boundaryIntercepted"))
            confirm_required = bool(
                r.get("requireConfirm"))
            attribution = {
                "evalId": r.get("evalId"),
                "confidence": confidence,
                "tier": r.get("tier"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii59_route_eval_failed: %s", exc)
            attribution = {
                "evalError": str(exc)[:80]}

        # ② 上游铁律: 越界拦截拒绝路由
        if boundary:
            await self._track(
                session_id, "route", {
                    "action": "rejected_boundary",
                    "intentId": intent_id,
                })
            return {
                "success": True,
                "routed": False,
                "reason": "boundary_intercepted",
                "note": "58号已拦截该意图"
                        "(识别即合规——拒绝编排)",
                "routedAt": ts(),
            }

        # ③ 上游铁律: clarify/partial 不编排
        if state != "resolved":
            session["context"] = {
                **(session.get("context")
                   or {}),
                "lastCandidates": candidates,
                "lastState": state,
            }
            session["updatedAt"] = ts()
            await self.repo.save_session(
                session, create=False)
            await self._track(
                session_id, "route", {
                    "action": "awaiting_clarify",
                    "state": state,
                })
            return {
                "success": True,
                "routed": False,
                "reason": f"upstream_{state}",
                "candidates": candidates,
                "note": "上游澄清优于错误执行——"
                        "会话保持 opened 待澄清",
                "routedAt": ts(),
            }

        # ④ resolved → 服务路由
        from services.ii59_registry import (
            route_intent as route_table,
        )
        services = route_table(intent_id)
        template = TASK_TEMPLATES.get(
            intent_id) or {
            "label": "通用流程",
            "version": 1,
            "steps": ["generic_execute"],
            "sensitive": False,
            "rollback": "",
        }

        # ⑤ 任务实例创建+会话 serving
        task_id = await self.repo.next_task_id()
        task = {
            "taskId": task_id,
            "sessionId": int(session_id),
            "intentId": intent_id,
            "templateLabel": template["label"],
            "templateVersion":
                template["version"],
            "steps": template["steps"],
            "currentStep": 0,
            "status": "pending",
            "sensitive":
                template["sensitive"],
            "results": {},
            "attribution": {
                **attribution,
                "services": services,
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_task(task)

        # confirm_required 衔接(首步
        # confirm 衔接——模板 sensitive 域)
        first_step = template["steps"][0]
        await self._advance_to(
            session, task, first_step,
            confirm_required)

        session["state"] = "serving"
        session["intentId"] = intent_id
        session["taskStack"] = {
            "taskId": task_id,
            "currentStep": first_step,
            "stepIndex": 0,
        }
        session["turnCount"] = int(
            session.get("turnCount") or 0) + 1
        session["updatedAt"] = ts()
        await self.repo.save_session(
            session, create=False)

        await self._track(
            session_id, "route", {
                "action": "routed",
                "intentId": intent_id,
                "services": services,
                "taskId": task_id,
                "confidence": confidence,
            })
        return {
            "success": True,
            "routed": True,
            "intentId": intent_id,
            "services": services,
            "taskId": task_id,
            "template": {
                "label": template["label"],
                "version": template["version"],
                "steps": template["steps"],
            },
            "currentStep": first_step,
            "confirmRequired":
                confirm_required,
            "attribution": attribution,
            "note": "意图已路由(serving)——"
                    "任务编排启动",
            "routedAt": ts(),
        }

    # ============================================================
    # ② 步骤推进
    # ============================================================

    async def advance(self, session_id: int,
                      result: str = "done",
                      note: str = "") -> dict:
        """步骤推进(serving 态任务下一步)

        Args:
            result: done(成功)/failed(失败
                    ——fail-soft 转 escalated)

        Raises:
            KeyError: 会话/任务不存在
            ValueError: 非法状态/非法结果
        """
        _require_active_mode()
        session = await self._live_session(
            int(session_id),
            allowed=("serving",))
        stack = session.get("taskStack") or {}
        task_id = int(stack.get("taskId") or 0)
        task = await self.repo.get_task(task_id)
        if task is None:
            raise KeyError(
                f"任务 {task_id} 不存在")
        result = str(result or STEP_OK)
        if result not in (STEP_OK, STEP_FAILED):
            raise ValueError(
                f"非法步骤结果 {result}"
                f"(合法值: done/failed)")

        steps = task.get("steps") or []
        idx = int(task.get("currentStep") or 0)
        step_name = steps[idx] \
            if idx < len(steps) else ""

        # 失败 fail-soft: 转 escalated
        # (人工接管)或回滚通知——接管为
        # 客服兜底人工铁律(不受开关影响)
        if result == STEP_FAILED:
            rollback = None
            template = TASK_TEMPLATES.get(
                task.get("intentId")) or {}
            if template.get("rollback"):
                rollback = template["rollback"]
            task["status"] = "failed"
            task["results"] = {
                **(task.get("results") or {}),
                step_name: {
                    "result": "failed",
                    "note": note[:100],
                    "at": ts(),
                },
            }
            task["updatedAt"] = ts()
            await self.repo.save_task(
                task, create=False)
            # 会话转 escalated(人工接管——
            # escalate 本身不受开关影响)
            saved_mode = os.environ.get(
                "II59_MODE")
            os.environ["II59_MODE"] = "shadow"
            try:
                return await self.escalate(
                    int(session_id),
                    reason=f"步骤失败: "
                           f"{step_name}",
                    context_note=note)
            finally:
                if saved_mode is None:
                    os.environ.pop(
                        "II59_MODE", None)
                else:
                    os.environ[
                        "II59_MODE"] = saved_mode

        # 成功: 记录+推进
        task["results"] = {
            **(task.get("results") or {}),
            step_name: {
                "result": "done",
                "note": note[:100],
                "at": ts(),
            },
        }
        next_idx = idx + 1

        # 任务完成 → 会话 resolved
        if next_idx >= len(steps):
            task["status"] = "completed"
            task["currentStep"] = len(steps)
            task["updatedAt"] = ts()
            await self.repo.save_task(
                task, create=False)
            session["state"] = "resolved"
            session["taskStack"] = {
                **stack, "currentStep":
                    "completed"}
            session["updatedAt"] = ts()
            await self.repo.save_session(
                session, create=False)
            await self._track(
                session_id, "task", {
                    "action": "completed",
                    "taskId": task_id,
                    "steps": len(steps),
                })
            return {
                "success": True,
                "taskId": task_id,
                "status": "completed",
                "sessionState": "resolved",
                "note": "任务编排完成"
                        "(resolved——待闭话)",
                "advancedAt": ts(),
            }

        # 中间步骤推进
        next_step = steps[next_idx]
        task["currentStep"] = next_idx
        task["status"] = "running"
        task["updatedAt"] = ts()
        await self.repo.save_task(
            task, create=False)
        session["taskStack"] = {
            **stack,
            "currentStep": next_step,
            "stepIndex": next_idx,
        }
        session["updatedAt"] = ts()
        await self.repo.save_session(
            session, create=False)
        await self._track(
            session_id, "task", {
                "action": "advance",
                "taskId": task_id,
                "step": next_step,
            })
        return {
            "success": True,
            "taskId": task_id,
            "status": "running",
            "currentStep": next_step,
            "stepIndex": next_idx,
            "note": f"步骤推进 → {next_step}",
            "advancedAt": ts(),
        }

    # ============================================================
    # ③ 人工接管
    # ============================================================

    async def escalate(self, session_id: int,
                       reason: str = "",
                       context_note: str = ""
                       ) -> dict:
        """人工接管(escalated——脱敏上下文移交
        +排队位次)

        不受开关影响(客服兜底人工铁律——
        48号范式)。

        Raises:
            KeyError: 会话不存在
            ValueError: 会话终态
        """
        session = await self._live_session(
            int(session_id),
            allowed=("opened", "serving",
                     "escalated"))

        # 排队位次(当前 escalated 数+1)
        existing = await self.repo.list_sessions(
            state="escalated", limit=1000)
        position = len(existing) + 1

        # 脱敏上下文移交(48号 mask_pii)
        handoff = {
            "intentId":
                session.get("intentId") or "",
            "memberId":
                session.get("memberId"),
            "turnCount":
                session.get("turnCount"),
            "reason": _mask_pii(
                reason)[:100],
            "contextNote": _mask_pii(
                context_note)[:100],
        }
        session["state"] = "escalated"
        session["escalated"] = True
        session["queuePosition"] = position
        session["context"] = {
            **(session.get("context") or {}),
            "handoff": handoff,
        }
        session["updatedAt"] = ts()
        await self.repo.save_session(
            session, create=False)

        await self._track(
            session_id, "session", {
                "action": "escalated",
                "reason": reason[:100],
                "position": position,
            })
        return {
            "success": True,
            "sessionId": int(session_id),
            "state": "escalated",
            "queuePosition": position,
            "handoff": handoff,
            "note": "已人工接管——脱敏上下文"
                    "移交+排队",
            "escalatedAt": ts(),
        }

    # ============================================================
    # ④ 闭话+满意度
    # ============================================================

    async def close(self, session_id: int,
                    satisfaction: float = None,
                    note: str = "") -> dict:
        """闭话(closed——满意度采集回流真值源)

        满意度必采(1-5 分; resolved/escalated
        态闭话); off 态亦可闭话(人工铁律)。

        Raises:
            KeyError: 会话不存在
            ValueError: 非法满意度/未采集
        """
        session = await self._live_session(
            int(session_id),
            allowed=("resolved", "escalated",
                     "abandoned"))

        if satisfaction is None:
            raise ValueError(
                "闭话必采满意度(1-5 分——"
                "回流真值源)")
        try:
            satisfaction = float(satisfaction)
        except (TypeError, ValueError):
            raise ValueError("满意度须为数值")
        if not 1.0 <= satisfaction <= 5.0:
            raise ValueError(
                "满意度须在 [1,5]")

        session["satisfaction"] = \
            round(satisfaction, 1)
        session["state"] = "closed"
        session["updatedAt"] = ts()
        if note:
            session["context"] = {
                **(session.get("context")
                   or {}),
                "closeNote": _mask_pii(
                    note)[:100],
            }
        await self.repo.save_session(
            session, create=False)

        # 满意度反馈登记(回流真值源——
        # P4 collect 消费)
        feedback_id = await \
            self.repo.next_feedback_id()
        await self.repo.save_feedback({
            "feedbackId": feedback_id,
            "sessionId": int(session_id),
            "memberId": int(
                session.get("memberId")
                or 0),
            "kind": "satisfaction",
            "satisfaction":
                round(satisfaction, 1),
            "detail": {
                "intentId":
                    session.get("intentId"),
                "escalated": bool(
                    session.get("escalated")),
                "note": note[:100],
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(
            session_id, "session", {
                "action": "closed",
                "satisfaction":
                    round(satisfaction, 1),
            })
        return {
            "success": True,
            "sessionId": int(session_id),
            "state": "closed",
            "satisfaction":
                round(satisfaction, 1),
            "feedbackId": feedback_id,
            "note": "会话已闭话——满意度已采集"
                    "(回流真值源)",
            "closedAt": ts(),
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _live_session(self, session_id: int,
                            allowed: tuple) -> dict:
        """会话存在+状态域校验"""
        session = await self.repo.get_session(
            int(session_id))
        if session is None:
            raise KeyError(
                f"会话 {session_id} 不存在")
        state = str(session.get("state"))
        if state not in allowed:
            raise ValueError(
                f"会话状态 {state}"
                f"(需 {'/'.join(allowed)})")
        return session

    async def _advance_to(self, session: dict,
                          task: dict,
                          step: str,
                          confirm_required: bool
                          ) -> None:
        """首步定位(含 confirm 衔接标记)"""
        task["status"] = "running"
        task["currentStep"] = 0
        if confirm_required:
            task["results"] = {
                **(task.get("results")
                   or {}),
                "_confirm": {
                    "result": "required",
                    "note": "sensitive 意图——"
                            "需二次确认"
                            "(48号 confirmToken)",
                    "at": ts(),
                },
            }
        task["updatedAt"] = ts()
        await self.repo.save_task(
            task, create=False)

    async def _track(self, session_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "sessionId": int(
                    session_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii59_conv_track_failed: %s",
                exc)
