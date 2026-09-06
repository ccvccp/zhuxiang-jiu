"""59号·AI智能服务编排 会话状态机底座
(ii59_service, P0)

计划(docs/59号_AI智能服务编排模块实施计划.md
§三/§九 P0):
    会话状态机:
        opened → serving → resolved → closed
           │         │
           │         └→ escalated(人工接管)
           └→ abandoned(超时/客户离开——
                        15 分钟无轮次)

    P0 底座:
        ① 开话(open_session——决策面 off 409;
           归因链: memberId+channel+attribution)
        ② 会话查询(观测面)
        ③ 状态机流转校验(_VALID_TRANSITIONS
           非法流转拒绝)
        ④ registry 观测面自描述
        ⑤ model_status 第34档案(44号复用)

铁律(计划 §一):
    - 默认零影响(II59_MODE off——决策面关闭)
    - 归因 ID 强制(每次开话携带归因链)
    - 58号 boundaryIntercepted 意图拒绝开话
      (识别即合规下游执行——P1 路由时校验)
"""

import logging
import os

from core.helpers import ts

from repositories.ii59_repository import (
    Ii59Repository,
)

logger = logging.getLogger("ii59_service")

MODEL_VERSION = "v1-ii59-service"

SCORER_ID = "service_orchestration"

# 会话状态机(计划 §三——合法流转表)
VALID_TRANSITIONS = {
    "opened": ["serving", "abandoned"],
    "serving": ["resolved", "escalated",
                "abandoned"],
    "resolved": ["closed"],
    "escalated": ["resolved", "closed"],
    "abandoned": ["closed"],
    "closed": [],   # 终态
}

# 会话超时窗(15 分钟无轮次→abandoned)
SESSION_TIMEOUT_SEC = 900

# 会话通道
CHANNEL_VALUES = ("text", "voice")


def current_mode() -> str:
    """模块开关(II59_MODE, 默认 off)"""
    return os.environ.get(
        "II59_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist
    开放)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"II59_MODE={mode}(默认 off——决策面"
            f"关闭, registry 观测面不受影响)")


class Ii59Service:
    """59号会话状态机底座+观测面(P0)"""

    def __init__(self):
        self.repo = Ii59Repository()

    # --------------------------------------------------------
    # 观测面(注册表自描述)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """服务编排注册表视图(观测面不受
        开关影响)"""
        from services.ii59_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe", "optimize",
                               "urgent"),
            },
            "session": {
                "states": ("opened", "serving",
                           "resolved", "escalated",
                           "abandoned", "closed"),
                "timeoutSec":
                    SESSION_TIMEOUT_SEC,
                "channels":
                    list(CHANNEL_VALUES),
            },
            "note": "P0 底座: 服务编排注册表"
                    "三位一体+会话状态机+第34档案"
                    "(P1 客服引擎接管)",
        })
        return view

    # ============================================================
    # 会话生命周期
    # ============================================================

    async def open_session(self, member_id: int,
                          channel: str = "text"
                          ) -> dict:
        """开话(opened 态——归因链强制)

        Args:
            member_id: 会员(0=游客态)
            channel: 通道(text/voice)

        Raises:
            ValueError: off 态/通道非法/
                会员非法
        """
        require_active_mode()
        channel = str(channel or "text").strip()
        if channel not in CHANNEL_VALUES:
            raise ValueError(
                f"非法通道 {channel}"
                f"(合法值: {'/'.join(
                    CHANNEL_VALUES)})")
        if member_id is None \
                or int(member_id) < 0:
            raise ValueError("会员身份非法")

        session_id = await \
            self.repo.next_session_id()
        record = {
            "sessionId": session_id,
            "memberId": int(member_id),
            "channel": channel,
            "state": "opened",
            "turnCount": 0,
            "intentId": "",
            "taskStack": {},
            "escalated": False,
            "queuePosition": 0,
            "satisfaction": 0.0,
            "pooledFeedbackId": 0,
            "attribution": {
                "openedAt": ts(),
                "track": "session",
            },
            "context": {},
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_session(record)

        await self._track(session_id,
                          "session", {
                              "action": "open",
                              "memberId":
                                  int(member_id),
                              "channel": channel,
                          })
        return {
            "success": True,
            "sessionId": session_id,
            "state": "opened",
            "note": "会话已开启(opened)——等待"
                    "意图路由(P1)",
            "openedAt": record["createdAt"],
        }

    async def transition(self, session_id: int,
                         new_state: str,
                         note: str = "") -> dict:
        """状态机流转(合法流转校验——非法
        流转拒绝)

        Raises:
            KeyError: 会话不存在
            ValueError: 非法状态/非法流转
        """
        session = await self.repo.get_session(
            int(session_id))
        if session is None:
            raise KeyError(
                f"会话 {session_id} 不存在")
        if new_state not in VALID_TRANSITIONS:
            raise ValueError(
                f"非法状态 {new_state}"
                f"(合法值: {sorted(
                    VALID_TRANSITIONS)})")
        old_state = str(session.get("state"))
        if new_state not in VALID_TRANSITIONS.get(
                old_state, []):
            raise ValueError(
                f"非法流转 {old_state}→"
                f"{new_state}(状态机)")

        session["state"] = new_state
        if new_state == "escalated":
            session["escalated"] = True
        session["updatedAt"] = ts()
        await self.repo.save_session(
            session, create=False)

        await self._track(int(session_id),
                          "session", {
                              "action": "transition",
                              "from": old_state,
                              "to": new_state,
                              "note": note[:100],
                          })
        return {
            "success": True,
            "sessionId": int(session_id),
            "state": new_state,
            "from": old_state,
            "note": f"状态流转 {old_state}→"
                    f"{new_state}",
        }

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    async def get_session(self,
                          session_id: int
                          ) -> dict:
        """会话详情(观测面)"""
        session = await self.repo.get_session(
            int(session_id))
        if session is None:
            raise KeyError(
                f"会话 {session_id} 不存在")
        return {
            "success": True,
            "session": session,
            "note": "会话详情——状态机+归因链",
        }

    async def list_sessions(self,
                            member_id: int = None,
                            state: str = None
                            ) -> dict:
        """会话列表(观测面)"""
        records = await self.repo.list_sessions(
            member_id=member_id, state=state)
        by_state: dict = {}
        for s in records:
            st = str(s.get("state") or "unknown")
            by_state[st] = \
                by_state.get(st, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byState": by_state,
            "sessions": records,
            "note": "会话列表——六态状态机",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view 复用
        ——第34档案)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        view.update({
            "module": "ii59",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "session_resolution": "会话解决",
                "search_adoption": "搜索采纳",
                "recommend_diversity":
                    "推荐多样性",
                "risk_accuracy": "风控准确",
                "member_trust": "会员信值",
                "escalation_rate": "接管率",
                "latency_budget": "延迟预算",
                "coverage_breadth": "服务覆盖",
            },
            "decisions": ["observe", "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——第34档案",
        })
        return {"success": True, "status": view}

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

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
                "ii59_track_failed %s: %s",
                event_type, exc)
