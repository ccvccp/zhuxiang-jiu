"""48号·小竹智能语音中枢路由(P0 感知层)

端点(P0 共 6):
    POST /api/xiaozhu/sessions              开启会话
    POST /api/xiaozhu/sessions/{id}/voice   语音轮次(音频全链)
    POST /api/xiaozhu/sessions/{id}/text    文本轮次(同链)
    GET  /api/xiaozhu/sessions/{id}          会话视图(轮次历史)
    DELETE /api/xiaozhu/sessions/{id}       一键清除(级联轮次)
    GET  /api/xiaozhu/commands              指令集自描述

鉴权: X-Member-Id(会员标识, 35号 Hub 同款惯例)。
语音传输: base64 JSON(35号 /api/hub/asr 同款惯例——
不依赖 python-multipart)。

统一口径:
    - 模块纯增量(零既有路由改动; ASR 链路 import 复用)
    - 反语音霸权: 未唤醒不执行只提示
    - KeyError → 404 / ValueError → 409(44-47号同款)
"""

import base64

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/xiaozhu",
                   tags=["小竹智能语音中枢(48号)"])


def _require_member(x_member_id: str | None) -> int:
    """会员标识(缺省按游客 0 处理——语音入口低门槛,
    信值类指令由 P1 绑定表补强身份)"""
    try:
        return int(x_member_id) if x_member_id else 0
    except (TypeError, ValueError):
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 需为整数")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions")
async def open_session(
    body: dict = None,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """开启小竹会话(body 可选 {channel: voice|text})"""
    member_id = _require_member(x_member_id)
    channel = "voice"
    if isinstance(body, dict) and body.get("channel"):
        channel = str(body["channel"])
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().open_session(
            member_id, channel)
    except Exception as e:
        raise _handle(e) from e


@router.post("/sessions/{session_id}/voice")
async def voice_turn(session_id: int, body: dict,
                     x_member_id: str | None = Header(
                         None, alias="X-Member-Id"),
):
    """语音轮次: 音频→ASR(35号链路)→唤醒判定→指令直达

    body: {audioBase64(必填), filename?, durationSec?}
    音频即转即删(临时文件在 hub 层删除, 小竹只落元信息)
    """
    member_id = _require_member(x_member_id)
    if not isinstance(body, dict) \
            or not body.get("audioBase64"):
        raise HTTPException(
            status_code=409, detail="请求体需含 audioBase64")
    try:
        audio_bytes = base64.b64decode(
            str(body["audioBase64"]))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=409, detail="audioBase64 编码非法")
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().handle_voice(
            session_id, audio_bytes, member_id,
            filename=str(body.get("filename")
                         or "audio.webm"),
            duration_sec=body.get("durationSec"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/sessions/{session_id}/text")
async def text_turn(session_id: int, body: dict):
    """文本轮次(键盘兜底/无障碍入口——与语音同链)"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=409,
                            detail="请求体需为对象")
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().handle_text(
            session_id, str(body.get("text") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/sessions/{session_id}")
async def get_session(session_id: int):
    """会话视图(含轮次历史——脱敏后文本)"""
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().get_session(session_id)
    except Exception as e:
        raise _handle(e) from e


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int):
    """一键清除会话(级联轮次——隐私红线)"""
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().delete_session(
            session_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/commands")
async def get_commands():
    """指令集自描述(帮助/前端快捷指令数据源)"""
    from services.xiaozhu_service import list_commands
    return {"success": True,
            "commands": list_commands(),
            "wakeWords": ["小竹"],
            "wakeFreeWindowSeconds": 300}


def register_xiaozhu_routes(app) -> None:
    """注册48号路由(main.py startup 调用)"""
    app.include_router(router)
