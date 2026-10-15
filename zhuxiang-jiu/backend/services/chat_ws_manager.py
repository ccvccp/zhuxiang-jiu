"""聊天 WebSocket 连接管理器(P2: 实时通道 15 事件)

职责:
    - 连接注册: 会话维度(多端)+ 身份维度(会员/客服工作台)
    - 事件广播: 向会话内所有在线连接推送下行事件
    - 在线状态: 会话在线连接计数(chat_online 计数可观测)

部署说明:
    - 内存版连接表适用于单 worker(Mock/默认部署);
      多 worker 部署需 Redis Pub/Sub 跨进程广播(键 chat:ws:channel,
      对齐设计文档 10.1 "Redis 会话状态+在线状态", 排期后续)。
"""

import asyncio
import logging
from collections import defaultdict

from core.helpers import ts

logger = logging.getLogger("chat_ws")


class ChatWsManager:
    """聊天 WS 连接管理器(会话级广播 + 客服工作台频道)"""

    def __init__(self):
        # sessionId → set[WebSocket]
        self._session_socks: dict[str, set] = defaultdict(set)
        # 客服工作台频道(接收全量 assign/queue 事件)
        self._staff_socks: set = set()
        self._lock = asyncio.Lock()
        self._stats = {"connectionsTotal": 0, "eventsSent": 0}

    # ============================================================
    # 连接注册
    # ============================================================

    async def connect(self, websocket, session_id: str,
                      role: str = "member") -> None:
        """注册连接(member 绑定会话; staff 进工作台频道)"""
        async with self._lock:
            if role == "staff":
                self._staff_socks.add(websocket)
            else:
                self._session_socks[session_id].add(websocket)
            self._stats["connectionsTotal"] += 1

    async def disconnect(self, websocket, session_id: str,
                         role: str = "member") -> None:
        """注销连接(best-effort, 断线清理)"""
        async with self._lock:
            self._staff_socks.discard(websocket)
            socks = self._session_socks.get(session_id)
            if socks is not None:
                socks.discard(websocket)
                if not socks:
                    self._session_socks.pop(session_id, None)

    def session_online(self, session_id: str) -> int:
        """会话在线连接数"""
        return len(self._session_socks.get(session_id, ()))

    def staff_online(self) -> int:
        """工作台在线连接数"""
        return len(self._staff_socks)

    # ============================================================
    # 事件广播
    # ============================================================

    async def _send(self, websocket, event: dict) -> bool:
        try:
            await websocket.send_json(event)
            return True
        except Exception:
            return False

    async def broadcast_session(self, session_id: str, event: dict,
                                exclude=None) -> int:
        """向会话内所有在线连接广播(返回成功数)"""
        event = dict(event)
        event.setdefault("sessionId", session_id)
        event.setdefault("timestamp", ts())
        sent = 0
        for ws in list(self._session_socks.get(session_id, ())):
            if ws is exclude:
                continue
            if await self._send(ws, event):
                sent += 1
        self._stats["eventsSent"] += sent
        return sent

    async def broadcast_staff(self, event: dict) -> int:
        """向客服工作台频道广播(assign/queue 等)"""
        event = dict(event)
        event.setdefault("timestamp", ts())
        sent = 0
        for ws in list(self._staff_socks):
            if await self._send(ws, event):
                sent += 1
        self._stats["eventsSent"] += sent
        return sent

    async def notify_assignment(self, session_id: str,
                                customer_service_id: int,
                                extra: dict = None) -> None:
        """chat_assign 事件(会话内 + 工作台, REST 接入/转接时 best-effort)"""
        event = {"type": "chat_assign",
                 "customerServiceId": customer_service_id}
        if extra:
            event.update(extra)
        await self.broadcast_session(session_id, event)
        await self.broadcast_staff(event)

    async def notify_queue(self, session_id: str,
                           queue_status: str) -> None:
        """chat_queue 事件(排队状态, 工作台 + 会话内)"""
        event = {"type": "chat_queue", "queueStatus": queue_status}
        await self.broadcast_session(session_id, event)
        await self.broadcast_staff(event)

    def stats(self) -> dict:
        return {
            **self._stats,
            "sessionsOnline": len(self._session_socks),
            "staffOnline": len(self._staff_socks),
        }


# 模块级单例(routes 共享)
ws_manager = ChatWsManager()
