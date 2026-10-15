"""聊天 WebSocket 实时通道(P2: 设计文档 10.2 十五事件)

端点:
    WS /api/chat/ws

鉴权(首条消息, 对齐 xiaozhu /ws/asr 范式——JWT 中间件对 WS 透传不拦):
    {"type": "auth", "role": "member", "userId": 10001, "sessionId": "CS..."}
      → 校验会话存在且归属本人(他人 4403)
    {"type": "auth", "role": "staff"}
      → 客服工作台频道(assign/queue 事件; Mock 范式弱鉴权,
        生产须扩展 token 校验, 与 REST X-Role 同源)

事件协议(设计文档 10.2/10.3):
    上行(客户端→服务端) 7:
        chat_message / chat_typing / chat_read / chat_transfer /
        chat_end / chat_upload / chat_recall
    下行(服务端→客户端) 8:
        chat_message / chat_reply / chat_typing / chat_read /
        chat_transfer / chat_end / chat_assign / chat_queue
    心跳: 上行 {"type":"ping"} → 下行 {"type":"pong"}
          (客户端 25s 间隔穿 nginx 空闲超时, 对齐 /ws/asr 经验)

降级兜底:
    断线回落 REST 轮询 GET /api/chat/sessions/{id}/messages?since_message_id=N
    (对齐 entry 扫码登录 WS→REST 回落范式, 服务端两条通道并存)
"""


import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.chat_service import ChatService
from services.chat_ws_manager import ws_manager

logger = logging.getLogger("chat_ws")

router = APIRouter()
_service = ChatService()

AUTH_TIMEOUT_SECONDS = 10


@router.websocket("/api/chat/ws")
async def chat_ws(websocket: WebSocket):
    """聊天实时通道(单连接单身份; 会员绑定会话, 客服进工作台频道)"""
    await websocket.accept()

    # ---- 首条消息鉴权 ----
    auth: dict = {}
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        auth = json.loads(raw)
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
        await websocket.close(code=4400, reason="auth 超时或格式非法")
        return

    role = auth.get("role", "member")
    session_id = str(auth.get("sessionId", ""))
    user_id = auth.get("userId")

    if role == "member":
        # 会话存在 + 归属校验(HTTP 轨 _authorize_session 的 WS 等价)
        session = await _service.repo.get_session(session_id)
        if session is None:
            await websocket.close(code=4404, reason="会话不存在")
            return
        if int(user_id or 0) != int(session.get("userId") or -1):
            await websocket.close(code=4403, reason="会话不属于当前会员")
            return
    elif role != "staff":
        await websocket.close(code=4400, reason="role 须为 member/staff")
        return

    await ws_manager.connect(websocket, session_id, role)
    await websocket.send_json({"type": "auth_ok", "role": role,
                               "sessionId": session_id,
                               "heartbeat": "ping/25s"})
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except ValueError:
                await _safe_send(websocket, {
                    "type": "error", "event": raw[:32],
                    "detail": "非 JSON 帧"})
                continue
            etype = data.get("type", "")
            if etype == "ping":
                await _safe_send(websocket, {"type": "pong",
                                             "timestamp": _now()})
                continue
            await _dispatch_event(websocket, role, session_id,
                                  user_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket, session_id, role)


def _now() -> str:
    from core.helpers import ts
    return ts()


async def _safe_send(websocket, event: dict) -> None:
    try:
        await websocket.send_json(event)
    except Exception:
        pass


async def _dispatch_event(websocket, role: str, session_id: str,
                          user_id, data: dict) -> None:
    """上行事件分发(异常回 error 帧, 不中断连接)"""
    etype = data.get("type", "")
    try:
        handler = _EVENT_HANDLERS.get(etype)
        if handler is None:
            raise ValueError(f"未知事件类型({etype})")
        await handler(websocket, role, session_id, user_id, data)
    except (KeyError, ValueError) as e:
        msg = str(e)
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        await _safe_send(websocket, {
            "type": "error", "event": etype, "detail": msg})
    except Exception as e:   # noqa: BLE001
        logger.warning("chat_ws 事件处理异常: %s %s", etype, e)
        await _safe_send(websocket, {
            "type": "error", "event": etype, "detail": "服务内部错误"})


# ============================================================
# 上行事件实现(7 个)
# ============================================================

async def _ev_chat_message(websocket, role, session_id, user_id, data):
    """发送消息: 落库 + 会话内 echo chat_message + AI/客服回 chat_reply"""
    if role != "member":
        raise ValueError("仅会员身份可发送消息(客服请走工作台 REST)")
    message = data.get("message") or {}
    result = await _service.send_message(
        session_id, "user", int(user_id),
        message.get("messageType", "text"),
        message.get("content", ""),
        media_url=message.get("mediaUrl"),
        media_thumb=message.get("mediaThumb"),
        media_size=message.get("mediaSize", 0),
        duration=message.get("duration", 0))
    # 用户消息事件(chat_message)
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_message",
        "message": {"messageId": result.get("userMessageId"),
                    "senderType": "user", "senderId": user_id,
                    **message}})
    # AI 回复事件(chat_reply)
    ai_reply = result.get("aiReply")
    if ai_reply:
        await ws_manager.broadcast_session(session_id, {
            "type": "chat_reply", "reply": ai_reply})
    # 转接联动(chat_transfer + chat_queue)
    if result.get("transferred"):
        trigger = result.get("transferTrigger") or {}
        await ws_manager.broadcast_session(session_id, {
            "type": "chat_transfer", "reason": trigger.get("reason", ""),
            "trigger": trigger.get("trigger", "")})
        await ws_manager.notify_queue(session_id, "transferring")


async def _ev_chat_typing(websocket, role, session_id, user_id, data):
    """正在输入: 向会话内其他端广播"""
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_typing", "from": role,
        "senderId": user_id}, exclude=websocket)


async def _ev_chat_read(websocket, role, session_id, user_id, data):
    """已读回执: 标记 + 广播(含本端, 支持多端同步)"""
    result = await _service.mark_session_read(session_id)
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_read", "by": role,
        "marked": result.get("marked", 0)})


async def _ev_chat_transfer(websocket, role, session_id, user_id, data):
    """请求转人工: 转接 + 广播 + 排队态"""
    result = await _service.transfer_to_human(
        session_id, reason=data.get("reason", ""))
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_transfer", "reason": data.get("reason", ""),
        "customerServiceId": result.get("customerServiceId")})
    await ws_manager.notify_queue(session_id, "waiting")


async def _ev_chat_end(websocket, role, session_id, user_id, data):
    """结束会话: 关闭 + 广播"""
    result = await _service.close_session(session_id)
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_end", "endedAt": result.get("endedAt")})


async def _ev_chat_upload(websocket, role, session_id, user_id, data):
    """上传文件: 以媒体消息落库(chat_message 事件)"""
    if role != "member":
        raise ValueError("仅会员身份可上传(客服请走工作台 REST)")
    message = data.get("message") or {}
    mtype = message.get("messageType", "image")
    if mtype not in ("image", "video", "voice", "file"):
        raise ValueError(f"上传类型非法({mtype})")
    if not message.get("mediaUrl"):
        raise ValueError("上传须提供 mediaUrl")
    result = await _service.send_message(
        session_id, "user", int(user_id), mtype, "",
        media_url=message.get("mediaUrl"),
        media_thumb=message.get("mediaThumb"),
        media_size=message.get("mediaSize", 0),
        duration=message.get("duration", 0))
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_message",
        "message": {"messageId": result.get("userMessageId"),
                    "senderType": "user", "senderId": user_id,
                    **message}})


async def _ev_chat_recall(websocket, role, session_id, user_id, data):
    """撤回消息: 2 分钟内本人软撤回 + 广播"""
    if role != "member":
        raise ValueError("仅会员身份可撤回")
    result = await _service.recall_message(
        session_id, int(data.get("messageId", 0)), "user", int(user_id))
    await ws_manager.broadcast_session(session_id, {
        "type": "chat_recall",
        "messageId": result.get("messageId"),
        "recalledAt": result.get("recalledAt")})


_EVENT_HANDLERS = {
    "chat_message": _ev_chat_message,
    "chat_typing": _ev_chat_typing,
    "chat_read": _ev_chat_read,
    "chat_transfer": _ev_chat_transfer,
    "chat_end": _ev_chat_end,
    "chat_upload": _ev_chat_upload,
    "chat_recall": _ev_chat_recall,
}


def register_chat_ws_routes(app):
    """注册聊天 WS 实时通道路由"""
    app.include_router(router)
