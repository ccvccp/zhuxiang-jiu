"""48号·小竹智能语音中枢路由(P0 感知层)

端点(P0 共 6 + P1 4 + P2 2 + P3 7 + P4 2 + 49号P0 1
     + 49号P2 2 + 49号P4 1 + 50号P0 5 + 50号P2 2
     + 50号P3 6 + 50号P4 3 + 50号P5 3 + 三期观测 1
     = 45):
    POST /api/xiaozhu/sessions              开启会话
    POST /api/xiaozhu/sessions/{id}/voice   语音轮次(音频全链;
                                             P1 流式轨传 textTranscript
                                             跳过重复转写)
    POST /api/xiaozhu/sessions/{id}/text    文本轮次(同链)
    WS   /api/xiaozhu/ws/asr                流式语音识别代理(P1:
                                             H5 PCM 帧→百炼实时
                                             识别→partial 流式推回)
    GET  /api/xiaozhu/sessions/{id}          会话视图(轮次历史)
    DELETE /api/xiaozhu/sessions/{id}       一键清除(级联轮次)
    GET  /api/xiaozhu/commands              指令集自描述
    GET  /api/xiaozhu/dashboard             看板七区块(48号P4+49号P4 FC分区, admin)
    POST /api/xiaozhu/dashboard/fairness-bridge  公平性桥接(P4)
    GET  /api/xiaozhu/fc/audit              FC审计流水(49号P0, admin)
    GET  /api/xiaozhu/privacy/budget        隐私预算视图(49号P2)
    PUT  /api/xiaozhu/privacy/preferences   隐私偏好调整(49号P2)
    POST /api/xiaozhu/fc/redteam            红队用例集执行(49号P4, admin)
    GET  /api/xiaozhu/voice50/my            我的语音积分(50号P0)
    GET  /api/xiaozhu/voice50/risk-state    L1风控状态(50号P0, admin)
    GET  /api/xiaozhu/voice50/rules         规则注册表(50号P0, admin)
    PUT  /api/xiaozhu/voice50/rules/{behavior}  规则热更新(50号P0, admin)
    POST /api/xiaozhu/voice50/unfreeze      积分冻结恢复(50号P0, admin)
    POST /api/xiaozhu/voice50/settle        T+1结算手动补偿(50号P2, admin)
    GET  /api/xiaozhu/voice50/settlements   结算批次视图(50号P2, admin)
    POST /api/xiaozhu/voice50/evidence      佐证per-claim验真(50号P3)
    POST /api/xiaozhu/voice50/corpus        语料捐赠提交(50号P3)
    POST /api/xiaozhu/voice50/corpus/{id}/review  语料审核(50号P3, admin)
    POST /api/xiaozhu/voice50/qa            社区知识问答(50号P3)
    POST /api/xiaozhu/voice50/companion/check  伴侣月度核算(50号P3)
    POST /api/xiaozhu/voice50/fairness-bridge  L3分布公平采样(50号P3, admin)
    POST /api/xiaozhu/voice50/adjudications/{id}/appeal  申诉提交(50号P4)
    POST /api/xiaozhu/voice50/adjudications/{id}/decide  申诉复核(50号P4, admin)
    GET  /api/xiaozhu/voice50/adjudications  处置台账视图(50号P4, admin)
    PUT  /api/xiaozhu/voice50/group-profile  群体画像设置(50号P5, admin)
    POST /api/xiaozhu/voice50/decay          激励池月度衰减(50号P5, admin)
    POST /api/xiaozhu/voice50/offset         池对冲修复(50号P5)
    GET  /api/xiaozhu/voicepay/overview      支付安全观测(三期, admin)

鉴权: X-Member-Id(会员标识, 35号 Hub 同款惯例);
管理端 X-Role: admin(43-47号同款口径)。
语音传输: base64 JSON(35号 /api/hub/asr 同款惯例——
不依赖 python-multipart)。

统一口径:
    - 模块纯增量(零既有路由改动; ASR 链路 import 复用)
    - 反语音霸权: 未唤醒不执行只提示
    - KeyError → 404 / ValueError → 409(44-47号同款)
"""

import asyncio
import base64
import contextlib
import json
import logging
import os

from fastapi import (
    APIRouter, Header, HTTPException, WebSocket,
)
from fastapi.responses import Response

logger = logging.getLogger("xiaozhu_routes")

router = APIRouter(prefix="/api/xiaozhu",
                   tags=["小竹智能语音中枢(48号)"])


def _require_member(x_member_id: str | None) -> int:
    """会员标识(缺省按游客 0 处理——语音入口低门槛,
    信值类指令由 P1 绑定表补强身份)"""
    try:
        return int(x_member_id) if x_member_id else 0
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 需为整数") from exc


def _require_member_strict(x_member_id: str | None) -> int:
    """强会员标识(P1 绑定/上下文——身份操作必须携带)"""
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="需要 X-Member-Id")
    try:
        return int(x_member_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 需为整数") from exc


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 大模型二代·三态灰度门控(XIAOZHU_MODE, 全站范式)
# ============================================================

async def _gate() -> dict:
    """决策面门槛(XIAOZHU_MODE=off → 409;
    shadow/assist 放行——大模型二代读取链:
    护栏暂停 > 运行时 override > env)"""
    from services.xiaozhu_mode_service import (
        XiaozhuModeService,
    )
    return await XiaozhuModeService() \
        .require_decision_mode()


def _decision(fn=None, *, strict=False,
              admin=False):
    """决策端点装饰器: 门控(off 409) +
    shadow/assist 标记(xiaoMode)

    参数(鉴权优先——401/403 before 409, 65号范式):
        strict=True  strict member 面(X-Member-Id
                     缺失放行函数体触发 401)
        admin=True   admin 面(X-Role 非 admin
                     放行函数体触发 403)
        默认         低门槛 member 面(游客 0
                     合法)直接门控

    宪法豁免面(sessions 删除/privacy
    preferences/confirm 核销/voice50
    appeal)与观测面(GET)不加本装饰器
    ——永不关停。
    """
    import functools
    from services.xiaozhu_mode_service import (
        MODE_VALUES,
    )

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # 鉴权优先: 无效/缺失凭据放行
            # 函数体触发 401/403——不预判门控
            x_role = kwargs.get("x_role")
            x_member_id = kwargs.get(
                "x_member_id")
            gate_needed = not (
                (admin and x_role != "admin")
                or (strict and not x_member_id))
            mode_state = None
            if gate_needed:
                try:
                    mode_state = await _gate()
                except ValueError as e:
                    raise HTTPException(
                        status_code=409,
                        detail=str(e)) from e
            result = await fn(*args, **kwargs)
            if isinstance(result, dict) \
                    and mode_state \
                    and mode_state.get("mode") \
                    in MODE_VALUES[1:]:
                result = {**result,
                          "xiaoMode":
                              mode_state["mode"]}
            return result
        return wrapper

    return deco(fn) if fn else deco


@router.post("/sessions")
@_decision
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
@_decision
async def voice_turn(session_id: int, body: dict,
                     x_member_id: str | None = Header(
                         None, alias="X-Member-Id"),
):
    """语音轮次: 音频→ASR(35号链路)→唤醒判定→指令直达

    body: {audioBase64(整段轨) 或 textTranscript(P1 流式轨,
           二选一), filename?, durationSec?, streamBytes?(流式
           PCM 累计字节), mode?='tap'(点击录音——用户主动按下
           麦克风=明确交互, 免唤醒词; H5 免提后台录音不传)}
    音频即转即删(临时文件在 hub 层删除, 小竹只落元信息)
    """
    member_id = _require_member(x_member_id)
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=409, detail="请求体需为对象")
    transcript = str(body.get("textTranscript") or "").strip()
    audio_bytes = b""
    if not transcript:
        if not body.get("audioBase64"):
            raise HTTPException(
                status_code=409,
                detail="请求体需含 audioBase64 或 textTranscript")
        try:
            audio_bytes = base64.b64decode(
                str(body["audioBase64"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=409,
                detail="audioBase64 编码非法") from exc
    try:
        from services.xiaozhu_service import XiaozhuService
        # 前端唤醒标志点亮 member 级免唤醒窗(21:00:35 实证:
        # 唤醒恰撞 WS token 断连, 唤醒词识别轮没走完 finish,
        # _LAST_WAKE_AT 永不点亮→真指令 not_woken 打回; 面板
        # 开窗期提交带 wake=1——不依赖 WS 连接健康, 覆盖所有
        # 唤醒路径; 点浮球手动开窗同语义(手势=主动交互))
        if body.get("wake") and member_id:
            import time as _t
            XiaozhuService._LAST_WAKE_AT[member_id] = \
                _t.time()
        return await XiaozhuService().handle_voice(
            session_id, audio_bytes, member_id,
            filename=str(body.get("filename")
                         or "audio.webm"),
            duration_sec=body.get("durationSec"),
            wakeup_free=(str(body.get("mode") or "")
                         == "tap"),
            transcript=transcript or None,
            stream_bytes=int(body.get("streamBytes") or 0))
    except Exception as e:
        raise _handle(e) from e


@router.post("/sessions/{session_id}/text")
@_decision
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


# ============================================================
# 流式语音识别代理(P1: H5 PCM 帧→百炼实时识别→partial 推回)
# ============================================================

@router.websocket("/ws/asr")
async def ws_asr(ws: WebSocket):
    """流式 ASR 代理(首条消息鉴权——浏览器 WS 不能自定义 header)

    协议(H5 ↔ 后端):
        → {"type":"auth","token":"<JWT>"}     鉴权
        ← {"type":"ready"}                     就绪(可发音频)
        → 二进制帧(16k 16bit mono PCM, ~200ms) 音频
        ← {"type":"partial","text":"..."}      中间结果(流式)
        → {"type":"finish"}                    说完
        ← {"type":"final","text":"..."}        最终文本
        ← {"type":"error","error":"...","fallback":"upload"}
        (任何失败 H5 回退整段上传轨)
    """
    await ws.accept()
    raw = None
    auth = None
    try:
        # ① 首条消息鉴权(JWT 中间件不拦 WS——type=websocket)
        raw = await asyncio.wait_for(
            ws.receive_text(), timeout=5)
        auth = json.loads(raw)
        from services.auth_service import AuthService
        member = await AuthService().get_current_member(
            str(auth.get("token") or ""))
        if not member:
            raise ValueError("会员不存在")
    except Exception as e:
        # 结构化留痕: 鉴权失败原因可观测(真机排查——刷新成功但
        # WS 持续失败时, 无日志只能盲猜; token 只记前缀防泄露)
        try:
            _tk = str((auth or {}).get("token") or "")[:16]
        except Exception:  # noqa: BLE001
            _tk = ""
        logger.warning(
            "ws_asr_auth_failed err=%s token_prefix=%s "
            "recv=%s", e, _tk, "json" if raw else "empty")
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error",
                                "error": f"鉴权失败: {e}"})
        await ws.close()
        return

    # ② 并发上限(资源红线)
    from services.asr_stream_service import (
        AsrStreamSession, _max_conns, active_conns,
    )
    if active_conns() >= _max_conns():
        await ws.send_json({"type": "error",
                           "error": "流式通道繁忙, 请稍后重试",
                           "fallback": "upload"})
        await ws.close()
        return

    # ③ 热词三源 + 唤醒词注入 + 双协议建流
    from services.xiaozhu_service import XiaozhuService

    async def _wake_hot(wk_raw) -> list[str]:
        """唤醒词热词(空=双预设展开; 自定义词原样)——百炼
        vocabulary 权重偏置修正「猪小猪」类小词汇错识;
        置于表首防 start 内 120 截断, 去重保序(每次 arm 重算
        ——唤醒词可变/产品名可变)"""
        _wk = str(wk_raw or "").strip()
        _wh = (["小竹小竹", "你好小竹", "小竹"]
               if (not _wk or _wk == "你好小竹") else [_wk])
        _seen: set[str] = set()
        return [w for w in (_wh
                            + await XiaozhuService()._asr_hotwords())
                if not (w in _seen or _seen.add(w))]

    session: "AsrStreamSession | None" = None
    # 诊断落盘(XIAOZHU_WS_DUMP=1): 客户端推流音频存 PCM
    # (16k16bit mono)→ /tmp/wsdump_*.wav——X5 间歇坏流分析
    # (V2 健康检测特征设计用; 常态关闭零开销)
    dump_f = None
    if os.environ.get("XIAOZHU_WS_DUMP", "") == "1":
        import time as _time
        import wave as _wave
        _dp = f"/tmp/wsdump_{_time.strftime('%H%M%S')}.wav"
        dump_f = _wave.open(_dp, "wb")
        dump_f.setnchannels(1)
        dump_f.setsampwidth(2)
        dump_f.setframerate(16000)
        logger.info("ws_asr_dump open %s", _dp)
    _ws_mid = int((member or {}).get("memberId") or 0)
    try:
        if str(auth.get("ver") or "") != "2":
            # 旧协议(v<=37 缓存客户端): auth → 建流 → ready →
            # 单段 → final → 断连(原行为原样, 零影响)
            session = AsrStreamSession(ws.send_json)
            if not await session.start(
                    await _wake_hot(auth.get("wakeword"))):
                await ws.send_json({"type": "error",
                                    "error": "流式识别不可用",
                                    "fallback": "upload"})
                await ws.close()
                return
            await ws.send_json({"type": "ready"})
            await _seg_pump(ws, session, dump_f, _ws_mid)
        else:
            # v2 预热协议: armed → [arm → 建流 → ready → 段 →
            # final → 回等 arm]*——连接复用省握手+鉴权(唤醒
            # 起段直接 arm, ~1s 握手延时不进唤醒关键路径);
            # ping(客户端 25s 心跳穿 nginx 空闲超时)忽略;
            # 并发红线不挤: _ACTIVE 只数活跃百炼流, 空闲
            # 预热连接零百炼资源
            await ws.send_json({"type": "armed"})
            logger.info("ws_asr_v2_armed")  # 埋点: 预热就绪
            while True:
                arm = None
                while arm is None:
                    msg = await ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    t = msg.get("text")
                    if not t:
                        continue
                    try:
                        m = json.loads(t)
                    except (ValueError, TypeError):
                        continue
                    _mt = m.get("type")
                    if _mt == "ping":
                        continue
                    if _mt == "arm":
                        arm = m
                logger.info("ws_asr_v2_arm wakeword=%r",
                            arm.get("wakeword"))  # 埋点: 段开始
                session = AsrStreamSession(ws.send_json)
                if not await session.start(
                        await _wake_hot(arm.get("wakeword"))):
                    # 建流失败: 报错回等下一 arm(连接保活,
                    # 客户端 diagTip 后下一段自动重试)
                    logger.warning(
                        "ws_asr_v2_stream_unavailable")
                    await ws.send_json(
                        {"type": "error",
                         "error": "流式识别不可用",
                         "fallback": "upload"})
                    await session.close()
                    session = None
                    continue
                await ws.send_json({"type": "ready"})
                await _seg_pump(ws, session, dump_f, _ws_mid)
                await session.close()
                session = None
    except Exception as e:
        logger.warning("ws_asr_error: %s", e)
    finally:
        if session is not None:
            await session.close()
        if dump_f:
            dump_f.close()
            logger.info("ws_asr_dump closed")


async def _seg_pump(ws, session, dump_f,
                   member_id: int = 0) -> None:
    """段消息泵: 二进制帧→feed / finish→final(新旧协议共用)"""
    while True:
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            break
        if msg.get("bytes"):
            if dump_f:
                dump_f.writeframes(msg["bytes"])
            await session.feed(msg["bytes"])
            continue
        text = msg.get("text")
        if not text:
            continue
        try:
            m = json.loads(text)
        except (ValueError, TypeError):
            continue
        if m.get("type") == "ping":
            continue
        if m.get("type") == "finish":
            final = await session.finish()
            # v3 观测: final 转写留痕(唤醒不中诊断——标点形态/
            # 空转写/误听形态一日志见; 与轮次 rawText 落库
            # 同隐私口径)
            logger.info("ws_asr_final text=%r failed=%r",
                        final, session.failed)
            # widget 唤醒词点亮 member 级免唤醒窗(20:26:53
            # 实证: widget 轨唤醒不提交文本轮, service 的
            # _LAST_WAKE_AT 永不点亮→唤醒后真指令不带前缀
            # 被 not_woken 打回; final 含小竹=唤醒意图, 与
            # detect_wake 文本命中同语义)
            if final and "小竹" in final and member_id:
                try:
                    import time as _t
                    from services.xiaozhu_service import (
                        XiaozhuService,
                    )
                    XiaozhuService._LAST_WAKE_AT[member_id] = \
                        _t.time()
                except Exception:
                    pass
            if final is None:
                await ws.send_json(
                    {"type": "error",
                     "error": session.failed
                     or "流式识别未出结果",
                     "fallback": "upload"})
            else:
                await ws.send_json({"type": "final",
                                    "text": final})
            break


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


@router.get("/voices")
async def get_voices():
    """78号·悦声灵犀 P1: 音色档案(前端音色选择数据源)

    GET /api/xiaozhu/voices → cogtts 实证可用音色策展档案
    (生产 spike 2026-09-23 两轮: 未知名 HTTP 400 硬拒绝非静默
    回退, spike_joyvoice_voices.py 留痕); voice 经 /tts?voice=
    透传合成, TTS 缓存键已含音色维度防串台。
    P1·竹语: ttsStream 字段携带流式灰度开关——前端据此走
    流式首声(XIAOZHU_TTS_STREAM)或整句双轨。
    """
    import os
    from services.joyvoice_service import (
        JOYVOICE_PROFILES, tts_stream_enabled, lat_report_enabled,
    )
    return {"success": True,
            "voices": [dict(p) for p in JOYVOICE_PROFILES],
            "current": os.environ.get("TTS_VOICE", "tongtong"),
            "ttsStream": "on" if tts_stream_enabled() else "off",
            "latReport": "on" if lat_report_enabled() else "off"}


@router.post("/lat")
async def post_lat_report(
        payload: dict = None,
        x_member_id: str | None = Header(
            None, alias="X-Member-Id")):
    """P2·H2 观察期: 客户端 [LAT] 四段延迟上报(轻量)

    POST /api/xiaozhu/lat {"s1","s2","s3","tt","proto","mood"}
    → Redis 日键 voice78:lat:{YYYYMMDD} hash, TTL 9 天。
    XIAOZHU_LAT_REPORT=off 时 204 静默(前端读 /voices 提前
    分流不上报; 204 仅兜底)。鉴权同 /tts(JWT strict)。
    """
    from services.joyvoice_service import lat_report_enabled
    if not lat_report_enabled():
        return {"success": True, "skipped": "off"}
    _require_member_strict(x_member_id)
    p = payload or {}
    try:
        s1 = max(0, min(600000, int(p.get("s1") or 0)))
        s2 = max(0, min(600000, int(p.get("s2") or 0)))
        s3 = max(0, min(600000, int(p.get("s3") or 0)))
        tt = max(0, min(600000, int(p.get("tt") or 0)))
        proto = str(p.get("proto") or "")[:16]
        mood = str(p.get("mood") or "")[:16]
    except (TypeError, ValueError):
        raise HTTPException(status_code=409,
                            detail="字段须为整数")
    from repositories.backend import (
        is_redis_mode, get_redis_client,
    )
    if not is_redis_mode():
        return {"success": True, "skipped": "no-redis"}
    import datetime as _dt
    import json as _json
    import uuid as _uuid
    client = await get_redis_client()
    day = _dt.date.today().strftime("%Y%m%d")
    key = f"voice78:lat:{day}"
    await client.hset(key, _uuid.uuid4().hex[:10], _json.dumps({
        "s1": s1, "s2": s2, "s3": s3, "tt": tt,
        "proto": proto, "mood": mood,
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False))
    await client.expire(key, 9 * 86400)
    return {"success": True}


@router.get("/lat/stats")
async def get_lat_stats(days: int = 7):
    """P2·H2 观察期看板数据源: [LAT] 四段聚合(P50/P90)

    GET /api/xiaozhu/lat/stats?days=7 → 逐日+总并: 四段
    P50/P90/max、样本数、h2 协议占比、异常轮数(s2>5s)。
    鉴权同 /voices(JWT——看板页登录态拉取)。
    """
    import datetime as _dt
    import json as _json
    from repositories.backend import (
        is_redis_mode, get_redis_client,
    )
    n = max(1, min(9, days))

    def _pct(xs, p):
        if not xs:
            return None
        xs = sorted(xs)
        return xs[min(len(xs) - 1,
                      int(round(p / 100 * (len(xs) - 1))))]

    def _agg(rows):
        s1 = [r["s1"] for r in rows]
        s2 = [r["s2"] for r in rows]
        s3 = [r["s3"] for r in rows]
        tt = [r["tt"] for r in rows]
        h2n = sum(1 for r in rows if "h2" in r.get("proto"))
        slow = [r for r in rows if r["s2"] > 5000]
        return {
            "n": len(rows),
            "s1": {"p50": _pct(s1, 50), "p90": _pct(s1, 90),
                   "max": max(s1) if s1 else None},
            "s2": {"p50": _pct(s2, 50), "p90": _pct(s2, 90),
                   "max": max(s2) if s2 else None},
            "s3": {"p50": _pct(s3, 50), "p90": _pct(s3, 90),
                   "max": max(s3) if s3 else None},
            "tt": {"p50": _pct(tt, 50), "p90": _pct(tt, 90),
                   "max": max(tt) if tt else None},
            "h2pct": round(h2n * 100 / len(rows), 1) if rows
            else None,
            "slowN": len(slow),
            "slow": [{"ts": r.get("ts"), "s2": r["s2"],
                      "proto": r.get("proto")}
                     for r in slow[-20:]],
        }

    days_out, all_rows = [], []
    if is_redis_mode():
        client = await get_redis_client()
        today = _dt.date.today()
        for i in range(n - 1, -1, -1):
            d = today - _dt.timedelta(days=i)
            key = f"voice78:lat:{d.strftime('%Y%m%d')}"
            data = await client.hgetall(key) or {}
            rows = []
            for v in data.values():
                try:
                    r = _json.loads(v if isinstance(v, str)
                                    else v.decode())
                    rows.append(r)
                except Exception:
                    continue
            days_out.append({"day": d.strftime("%m-%d"),
                             **_agg(rows)})
            all_rows.extend(rows)
    return {"success": True, "days": days_out,
            "total": _agg(all_rows)}


@router.get("/tts/stream")
async def get_tts_stream(text: str = "",
                         speed: float = 1.0,
                         voice: str = "",
                         x_member_id: str | None = Header(
                             None, alias="X-Member-Id")):
    """P1·竹语: 流式 TTS(SSE 分块转发——首声 60~110ms 级)

    GET /api/xiaozhu/tts/stream?text=...&speed=...&voice=...
    → text/event-stream(智谱 chat delta 风格 data 行透传:
      音频在 choices[0].delta.content, base64 pcm)。
    鉴权/参数域同 /tts(JWT strict + X-Member-Id 注入);
    XIAOZHU_TTS_STREAM=off 时 403(前端读 /voices 开关
    提前分流, 403 仅作越权兜底)。
    G3 spike 实证: 首音频块 32~83ms, 44 字 6 块边合成边发。
    """
    from services.joyvoice_service import tts_stream_enabled
    if not tts_stream_enabled():
        raise HTTPException(
            status_code=403,
            detail="流式 TTS 未开启(XIAOZHU_TTS_STREAM)")
    _require_member_strict(x_member_id)
    t = str(text or "").strip()[:200]
    if not t:
        raise HTTPException(status_code=409,
                            detail="text 不能为空")
    spd = 0.5 if speed < 0.5 else (
        2.0 if speed > 2.0 else speed)
    from services.llm_client import provider_client
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        provider_client.synthesize_stream(
            t, spd, (voice or None)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "X-Accel-Buffering": "no"})


@router.get("/tts")
async def get_tts(text: str = "",
                  speed: float = 1.0,
                  voice: str = "",
                  x_member_id: str | None = Header(
                      None, alias="X-Member-Id")):
    """语音合成播报(微信 X5 无系统 TTS 引擎——服务端兜底)

    GET /api/xiaozhu/tts?text=...&speed=...&voice=...
    → audio/mpeg(mp3) 二进制
    鉴权: X-Member-Id(登录会员); Redis 缓存 10 分钟(同文本
    去重防刷——计费友好); 文本限 200 字。
    speed 0.5-2.0(v2 G: spike 实证 cogtts 语速生效——0.6≈
    11.3s/1.0≈8.3s/1.5≈5.3s 单调); voice 透传音色。
    缓存键含 speed/voice 维度(防语速串台)。
    """
    _require_member_strict(x_member_id)
    t = str(text or "").strip()[:200]
    if not t:
        raise HTTPException(status_code=409,
                            detail="text 不能为空")
    # 语速合法域(超出取边界——参数错误不 500)
    spd = 0.5 if speed < 0.5 else (
        2.0 if speed > 2.0 else speed)
    import base64 as _b64
    # 78号P1.5·竹语: 键构造共享 joyvoice_service.tts_cache_key
    # (等值重构)——服务端响应内预合成与路由读/写同键, 预合成
    # 写入的缓存前端请求才能命中; 键仍含 :mp3 版本隔离与
    # text|voice|speed 三维
    from services.joyvoice_service import tts_cache_key
    cache_key = tts_cache_key(t, voice, spd)
    try:
        from repositories.backend import (
            is_redis_mode, get_redis_client,
        )
        if is_redis_mode():
            client = await get_redis_client()
            hit = await client.get(cache_key)
            if hit:
                return Response(
                    content=_b64.b64decode(hit),
                    media_type="audio/mpeg")
    except Exception as e:
        logger.debug("tts_cache_read_skip: %s", e)
    from services.llm_client import provider_client
    # v2 并发修复: 合成是同步 urllib(2-5s)——直调阻塞事件循环
    # 冻结全后端(含 /voice ASR 轮), 线程池执行解除串行
    import asyncio as _aio
    audio = await _aio.to_thread(
        provider_client.synthesize_mp3, t,
        spd, (voice or None))
    if not audio:
        raise HTTPException(
            status_code=503,
            detail="语音合成暂不可用(不影响文字交互)")
    # lameenc 未装/转码失败回退 wav → 按 wav 响应(浏览器可播)
    is_mp3 = audio[:3] == b"ID3" or audio[0:1] == b"\xff"
    if not is_mp3:
        return Response(content=audio, media_type="audio/wav")
    try:
        from repositories.backend import (
            is_redis_mode, get_redis_client,
        )
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                cache_key, _b64.b64encode(audio).decode(),
                ex=600)
    except Exception as e:
        logger.debug("tts_cache_write_skip: %s", e)
    return Response(content=audio, media_type="audio/mpeg")


# ============================================================
# P1 认知层(绑定 + 角色上下文)
# ============================================================

@router.post("/bindings")
@_decision(strict=True)
async def bind_trust(body: dict,
                     x_member_id: str | None = Header(
                         None, alias="X-Member-Id")):
    """绑定会员↔信值档案(两套 ID 体系衔接; 重复绑定=改绑)

    body: {trustId(必填), note?}
    """
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or not body.get("trustId"):
        raise HTTPException(status_code=409,
                            detail="请求体需含 trustId")
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().bind_trust(
            member_id, int(body["trustId"]),
            note=str(body.get("note") or ""))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409,
                            detail="trustId 需为整数") from exc
    except Exception as e:
        raise _handle(e) from e


@router.delete("/bindings")
@_decision(strict=True)
async def unbind_trust(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """解除绑定(零不可逆)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().unbind(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/bindings")
async def get_binding(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """绑定视图"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().get_binding(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/context")
async def get_context(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """角色上下文调试视图(等级/绑定/余额/偏好/LLM 轨态)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().get_context_view(
            member_id)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P2 执行层·安全业务代理(confirmToken 高敏流)
# ============================================================

@router.post("/confirm/{token}")
async def confirm_action(token: str, body: dict,
                         x_member_id: str | None = Header(
                             None, alias="X-Member-Id"),
                         ):
    """核销高敏确认码执行(confirmToken 屏幕码流)

    高敏操作不可纯语音完成红线——数字码为准(语音念码
    不算, 须屏幕输入)。body: {code(必填 4 位数字)}
    """
    _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or not body.get("code"):
        raise HTTPException(
            status_code=409,
            detail="请求体需含 code(4 位确认码)")
    code = str(body["code"]).strip()
    if not (code.isdigit() and len(code) == 4):
        raise HTTPException(
            status_code=409, detail="code 需为 4 位数字")
    try:
        from services.xiaozhu_service import XiaozhuService
        return await XiaozhuService().confirm_action(
            token, code)
    except Exception as e:
        raise _handle(e) from e


@router.post("/sessions/{session_id}/turns/{turn_id}/feedback")
async def turn_feedback(
        session_id: int, turn_id: str, body: dict,
        x_member_id: str | None = Header(
            None, alias="X-Member-Id")):
    """轮次反馈落痕(v2 A: 👍/👎——turn hash feedback 字段)

    覆盖式(最后一次为准, up/down 可切换); 不触发 TTS、
    不产生新轮次(不污染对话流与清单聚合)。
    鉴权: 会话归属校验(仅本人可反馈自己会话的轮次)。
    """
    member_id = _require_member_strict(x_member_id)
    if (not isinstance(body, dict)
            or body.get("rating") not in ("up", "down")):
        raise HTTPException(status_code=409,
                            detail="rating 需为 up/down")
    try:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        session = await repo.get_session(session_id)
        if session is None:
            raise KeyError("会话不存在")
        if session.get("memberId") != member_id:
            raise HTTPException(
                status_code=403,
                detail="仅会话归属人可反馈")
        # turnId 反查轮次(会话内轮次量级小, 线性可接受)
        turns = await repo.list_turns(session_id)
        target = next((t for t in turns
                       if t.get("turnId") == turn_id), None)
        if target is None:
            raise KeyError("轮次不存在")
        await repo.save_turn_feedback(
            session_id, int(target.get("seq") or 0),
            str(body["rating"]))
        # P3 学习进化: 👎 自动入学习队列(fail-soft——
        # 队列异常不阻断反馈落痕; 👍 不入队)
        try:
            from services.xiaozhu_service import XiaozhuService
            await XiaozhuService().learn_enqueue(
                session, target, str(body["rating"]))
        except Exception as e:
            logger.warning("learn_enqueue_route_skip: %s", e)
        return {"success": True, "turnId": turn_id,
                "seq": target.get("seq"),
                "rating": body["rating"]}
    except HTTPException:
        raise
    except Exception as e:
        raise _handle(e) from e


@router.get("/sessions/{session_id}/actions")
async def list_actions(session_id: int,
                       x_member_id: str | None = Header(
                           None, alias="X-Member-Id"),
                       ):
    """执行留痕回溯("我刚才做了什么")——写/高敏轮次视图

    v2 B1: 过滤集扩为 _AUDIT_INTENTS(含 cart.add/setqty/
    decqty/undo——此前只含三类高敏, 普通加购不可回溯)。
    """
    _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_service import (
            XiaozhuService, _AUDIT_INTENTS,
        )
        view = await XiaozhuService().get_session(
            session_id)
        actions = [
            {"seq": t.get("seq"), "action":
             t.get("intent"), "ts": t.get("ts"),
             "reply": t.get("reply"),
             "card": t.get("card") or {}}
            for t in view.get("turns") or []
            if t.get("intent") in _AUDIT_INTENTS]
        return {"success": True,
                "sessionId": session_id,
                "actions": actions,
                "count": len(actions)}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P3 进化层(积分 + 主动关怀 + 失败挖掘 + 共创指令)
# ============================================================

@router.get("/points")
async def points_view(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """语音积分视图(余额+流水+可兑换单位)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService(
        ).points_view(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/points/redeem")
@_decision(strict=True)
async def points_redeem(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """积分兑换——走 45号 deposit 验真申报通道(不直改
    信值; 47号风控全链审查)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService().redeem(
            member_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/commands/custom")
@_decision(strict=True)
async def submit_custom(body: dict,
                       x_member_id: str | None = Header(
                           None, alias="X-Member-Id"),
                       ):
    """提交共创指令(短语→白名单 action 映射; pending 审核)

    body: {phrase(2-30 字符), action(白名单)}
    """
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409,
                            detail="请求体需为对象")
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService(
        ).submit_custom(
            member_id, str(body.get("phrase") or ""),
            str(body.get("action") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/commands/custom")
async def custom_view(
    x_role: str = Header(default="", alias="X-Role"),
):
    """共创指令队列(admin——pending 审核/已上架)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService(
        ).custom_view()
    except Exception as e:
        raise _handle(e) from e


@router.post("/commands/custom/{cmd_id}/review")
@_decision(admin=True)
async def review_custom(cmd_id: int, body: dict,
                       x_role: str = Header(
                           default="", alias="X-Role"),
                       ):
    """审核共创指令(上架→贡献者+100/驳回留痕)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    if not isinstance(body, dict) \
            or not isinstance(body.get("approve"), bool):
        raise HTTPException(status_code=409,
                            detail="请求体需含 approve 布尔字段")
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService(
        ).review_custom(
            cmd_id, body.get("approve"),
            str(body.get("note") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.post("/proactive/scan")
@_decision(admin=True)
async def proactive_scan(
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动触发关怀扫描(admin; 调度器默认 off——
    XIAOZHU_PROACTIVE_MODE=on 时日度自动)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService(
        ).scan_proactive()
    except Exception as e:
        raise _handle(e) from e


@router.get("/failures")
async def failures_view(
    x_role: str = Header(default="", alias="X-Role"),
):
    """失败案例聚类视图(admin——top 未命中短语→
    建议新增指令 pattern)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        return await XiaozhuEvolutionService(
        ).failures_view()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P4 语音中枢看板与治理桥接(fail-soft 六区块)
# ============================================================

@router.get("/dashboard")
async def xiaozhu_dashboard(
    x_role: str = Header(default="", alias="X-Role"),
):
    """语音中枢看板(六区块一次拉取, fail-soft 分区)

    ①使用总览 ②指令命中 ③高敏台账 ④积分账本
    ⑤共创队列 ⑥治理桥接——前端面板单次 GET 零拼装。
    """
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        return await XiaozhuDashboardService().build()
    except Exception as e:
        raise _handle(e) from e


@router.get("/dashboard/asr-fixes")
async def asr_fixes_list(
        x_role: str = Header(default="", alias="X-Role")):
    """ASR 误听自学习表(v2 C——运营查询, 含命中计数)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        fixes = await Xiaozhu48Repository().list_asr_fixes()
        return {"success": True, "fixes": fixes,
                "count": len(fixes)}
    except Exception as e:
        raise _handle(e) from e


@router.post("/dashboard/asr-fixes")
async def asr_fixes_add(
        body: dict,
        x_role: str = Header(default="", alias="X-Role")):
    """新增/更新误听词条(v2 C——真机留痕转运营即时生效)

    body: {wrong: 误听词, right: 修正词}; 校验: 词条≤12 字、
    wrong≠right、循环修正拒绝(A→B 且 B→A)。
    """
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    wrong = str((body or {}).get("wrong") or "").strip()
    right = str((body or {}).get("right") or "").strip()
    if not wrong or not right:
        raise HTTPException(status_code=409,
                            detail="需含 wrong/right")
    if len(wrong) > 12 or len(right) > 12:
        raise HTTPException(status_code=409,
                            detail="词条过长(≤12 字)")
    if wrong == right:
        raise HTTPException(status_code=409,
                            detail="误听词与修正词相同")
    try:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        existing = await repo.list_asr_fixes()
        rec = existing.get(right) or {}
        if rec.get("to") == wrong:
            raise HTTPException(
                status_code=409,
                detail=f"循环修正拒绝: 「{right}」已指向"
                       f"「{wrong}」")
        record = await repo.save_asr_fix(wrong, right)
        return {"success": True, "wrong": wrong,
                "record": record}
    except HTTPException:
        raise
    except Exception as e:
        raise _handle(e) from e


@router.delete("/dashboard/asr-fixes")
async def asr_fixes_delete(
        wrong: str,
        x_role: str = Header(default="", alias="X-Role")):
    """删除误听词条(builtin 真机实证基线 + dialect 方言种子
    均拒绝删除——P2 方言模块基线)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        existing = await repo.list_asr_fixes()
        rec = existing.get(wrong) or {}
        if rec.get("source") in ("builtin", "dialect"):
            raise HTTPException(
                status_code=409,
                detail=f"{rec.get('source')} 词条不可删除"
                       "(种子基线——可新增同名词覆盖方向)")
        removed = await repo.delete_asr_fix(wrong)
        return {"success": True, "removed": removed}
    except HTTPException:
        raise
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 学习进化队列(P3: 👎→队列→建议→采纳→词条生效)
# ============================================================

@router.get("/dashboard/learn-queue")
async def learn_queue_list(
        status: str = "pending",
        x_role: str = Header(default="", alias="X-Role")):
    """学习进化队列(P3——👎 轮上下文+处置状态, admin)

    query status: pending(默认)/adopted/dismissed/all
    """
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        status = status if status in ("pending", "adopted",
                                      "dismissed") else None
        queue = await repo.list_learn_queue(status)
        stats = {"pending": 0, "adopted": 0, "dismissed": 0}
        for entry in (await repo.list_learn_queue()).values():
            s = entry.get("status")
            if s in stats:
                stats[s] += 1
        return {"success": True, "queue": queue,
                "stats": stats,
                "llmSuggestOn": os.environ.get(
                    "XIAOZHU_LEARN_LLM", "off").lower()
                in ("on", "1", "true")}
    except Exception as e:
        raise _handle(e) from e


@router.post("/dashboard/learn-queue/{key}/suggest")
async def learn_queue_suggest(
        key: str,
        x_role: str = Header(default="", alias="X-Role")):
    """LLM 修正建议(P3 辅助——手动触发, 建议不直接生效)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_service import XiaozhuService
        return {"success": True,
                "result": await XiaozhuService().learn_suggest(
                    str(key))}
    except Exception as e:
        raise _handle(e) from e


@router.post("/dashboard/learn-queue/{key}/adopt")
async def learn_queue_adopt(
        key: str, body: dict,
        x_role: str = Header(default="", alias="X-Role")):
    """采纳学习条目(P3 闭环: 词条落误听表 source=learn,
    队列标记 adopted——下一轮语音即生效)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_service import XiaozhuService
        record = await XiaozhuService().learn_adopt(
            str(key),
            str((body or {}).get("wrong") or ""),
            str((body or {}).get("right") or ""))
        return {"success": True, "record": record}
    except Exception as e:
        raise _handle(e) from e


@router.post("/dashboard/learn-queue/{key}/dismiss")
async def learn_queue_dismiss(
        key: str,
        x_role: str = Header(default="", alias="X-Role")):
    """忽略学习条目(非误听问题——闲聊不满/回复错等)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        resolved = await Xiaozhu48Repository().resolve_learn(
            str(key), "dismissed")
        if not resolved:
            raise KeyError("队列条目不存在")
        return {"success": True}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 语音数据周报(P4: 近 7 天聚合+环比, 周一自动站内信)
# ============================================================

@router.get("/dashboard/voice-weekly")
async def voice_weekly(
        snapshot: str = "",
        x_role: str = Header(default="", alias="X-Role")):
    """语音数据周报(P4——近 7 天聚合, admin)

    默认即时现算(只读不动快照——周一自动站内信的幂等不受
    手动查看影响); query snapshot=1 返回最近一次自动生成的
    快照(含站内信触达结果)。
    """
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        if snapshot == "1":
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            snap = await Xiaozhu48Repository() \
                .load_weekly_snapshot()
            return {"success": True, "generated": False,
                    "report": snap}
        from services.xiaozhu_weekly_service import (
            XiaozhuWeeklyService,
        )
        report = await XiaozhuWeeklyService() \
            .build_weekly_report()
        return {"success": True, "generated": True,
                "report": report}
    except Exception as e:
        raise _handle(e) from e


@router.get("/voicepay/overview")
async def voicepay_overview(
    x_role: str = Header(default="", alias="X-Role"),
):
    """支付安全观测面(三期 L1-L3——shadow 观察期数据源)

    档位/统计(尝试/拦截分布/L3 提级/shadow 放行/成单)/
    最近 20 条风控留痕/规则常量。资金敏感数据不进公开
    白名单(admin 鉴权——zjian 观测面同款口径)。
    """
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    from services.xiaozhu_voicepay_service import (
        get_gateway,
    )
    return get_gateway().overview()


@router.post("/dashboard/fairness-bridge")
@_decision(admin=True)
async def fairness_bridge(
    x_role: str = Header(default="", alias="X-Role"),
):
    """语音直达率→46号公平性采样桥接(member_level 维度
    ——防语音层歧视; 无个人标识字段, 脱敏红线)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        return await XiaozhuDashboardService(
        ).bridge_fairness()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 49号 P0 可信函数调用(FC 审计视图)
# ============================================================

@router.get("/fc/audit")
async def fc_audit_view(
    member_id: int = None,
    limit: int = 100,
    x_role: str = Header(default="", alias="X-Role"),
):
    """FC 调用审计流水视图(admin——六字段铁律核查)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        return await XiaozhuFcGateway().audit_view(
            limit=limit, member_id=member_id)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 49号 P2 隐私预算(会员自主——知情权与控制权)
# ============================================================

@router.get("/privacy/budget")
async def privacy_budget_view(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """隐私预算视图(会员——余额/偏好/近 7 日消耗)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        return await XiaozhuPrivacyService(
        ).budget_view(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.put("/privacy/preferences")
async def privacy_set_preference(
    body: dict,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """调整隐私偏好(0.5-2.0——会员自主, 与信值等级无关)"""
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or "preference" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 preference 数值字段")
    try:
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        return await XiaozhuPrivacyService(
        ).set_preference(member_id, body.get("preference"))
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 49号 P4 红队用例集(上线检查清单第 5 项——Prompt 注入防护)
# ============================================================

@router.post("/fc/redteam")
@_decision(admin=True)
async def fc_redteam_run(
    x_role: str = Header(default="", alias="X-Role"),
):
    """红队用例集执行(admin——四类攻击向量 14 用例跑真
    网关: 越狱/成本篡改/伪造 token/越权诱导; breached>0
    即上线阻断, 拒绝细节落 FC 审计流水)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_fc_redteam import (
            XiaozhuFcRedteamService,
        )
        return await XiaozhuFcRedteamService().run()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 50号 P0 语音信值积分引擎(台账轨+L1 实时轨)
# ============================================================

@router.get("/voice50/my")
async def voice50_my(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """我的语音积分(会员——激励池余额/近期事件/ref 可溯)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().my_view(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/voice50/risk-state")
async def voice50_risk_state(
    member_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """L1 风控状态(风控域——47号画像消费口径)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().risk_state(
            member_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/voice50/rules")
async def voice50_rules(
    x_role: str = Header(default="", alias="X-Role"),
):
    """规则注册表视图(admin——14 行为+参数+更新留痕)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().rules_admin_view()
    except Exception as e:
        raise _handle(e) from e


@router.put("/voice50/rules/{behavior}")
@_decision(admin=True)
async def voice50_update_rule(
    behavior: str,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """规则热更新(admin——base/dailyCap 可调, 留痕)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    if not isinstance(body, dict) or not body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含待更新字段(base/dailyCap)")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().update_rule(
            behavior, body)
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/settle")
@_decision(admin=True)
async def voice50_settle(
    body: dict = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """T+1 结算手动补偿(admin——L2/L3 pending 聚合走
    45号 deposit 验真; body 可选 {dayKey, memberId})"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    body = body or {}
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().settle_day(
            day_key=body.get("dayKey"),
            member_id=body.get("memberId"),
            operator="manual")
    except Exception as e:
        raise _handle(e) from e


@router.get("/voice50/settlements")
async def voice50_settlements(
    day_key: str = None,
    member_id: int = None,
    limit: int = 100,
    x_role: str = Header(default="", alias="X-Role"),
):
    """结算批次视图(admin——done/rejected/skipped 与
    拒收原因)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().settlement_view(
            day_key=day_key, member_id=member_id,
            limit=limit)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 50号 P3 L3 五行为+公平天花板
# ============================================================

@router.post("/voice50/evidence")
@_decision(strict=True)
async def voice50_evidence(
    body: dict,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """真伪鉴别辅助验证(会员——per-claim 走 45号验真,
    采信 ×2)"""
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or "evidence" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 evidence 字段")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().record_evidence(
            member_id, body.get("evidence"),
            sources=body.get("sources"),
            summary=body.get("summary") or "")
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/corpus")
@_decision(strict=True)
async def voice50_corpus_submit(
    body: dict,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """新场景语料捐赠提交(会员——捐赠即得基础 10 分,
    采纳 +20)"""
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or "scenario" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 scenario 字段")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().submit_corpus(
            member_id, body.get("scenario"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/corpus/{corpus_id}/review")
@_decision(admin=True)
async def voice50_corpus_review(
    corpus_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """语料审核(admin——adopted 纳入训练集 +20)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    if not isinstance(body, dict) \
            or "adopted" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 adopted 布尔字段")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().review_corpus(
            corpus_id, bool(body.get("adopted")),
            note=str(body.get("note") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/qa")
@_decision(strict=True)
async def voice50_qa(
    body: dict,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """社区知识问答(会员——内容安全过滤+点赞 ×1.5)"""
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or "content" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 content 字段")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().record_qa(
            member_id, body.get("content"),
            liked=bool(body.get("liked")))
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/companion/check")
@_decision(strict=True)
async def voice50_companion_check(
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """长期语音伴侣关系核算(会员——30 天日均 ≥3/
    多样性 ×1.3/月限 1)"""
    member_id = _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().check_companion(
            member_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/fairness-bridge")
@_decision(admin=True)
async def voice50_fairness_bridge(
    x_role: str = Header(default="", alias="X-Role"),
):
    """L3 日积分分布上报 46号公平性采样(admin——
    高/中/低三组, 各组 <5 样本不上报)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().bridge_fairness()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 50号 P4 反作弊处置台账(申诉/复核)
# ============================================================

@router.post("/voice50/adjudications/{adj_id}/appeal")
async def voice50_appeal(
    adj_id: int,
    body: dict,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """申诉提交(会员——≤48h SLA; 原始录音+设备日志/
    合理业务场景说明/家庭成员报备)"""
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or "note" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 note 申诉说明字段")
    try:
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        return await Voice50GateService().submit_appeal(
            member_id, adj_id, body.get("note"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/adjudications/{adj_id}/decide")
@_decision(admin=True)
async def voice50_decide(
    adj_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """申诉复核裁决(admin——upheld 维持/overturned
    翻转并解除积分域)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    if not isinstance(body, dict) \
            or "upheld" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 upheld 布尔字段")
    try:
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        return await Voice50GateService().decide_appeal(
            adj_id, bool(body.get("upheld")),
            review_note=str(body.get("reviewNote") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/voice50/adjudications")
async def voice50_adjudications(
    member_id: int = None,
    limit: int = 100,
    x_role: str = Header(default="", alias="X-Role"),
):
    """处置台账视图(admin——180 天保留/分布统计)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        return await Voice50GateService(
        ).adjudication_view(member_id=member_id,
                           limit=limit)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 50号 P5 群体/衰减/对冲收官
# ============================================================

@router.put("/voice50/group-profile")
@_decision(admin=True)
async def voice50_group_profile(
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """设置群体画像(admin——minor/elder/disabled/
    org_proxy/none; 系数只作用积分折算不碰预算)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    if not isinstance(body, dict) \
            or "memberId" not in body \
            or "group" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 memberId 与 group 字段")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().set_group_profile(
            int(body["memberId"]), body.get("group"),
            verified=bool(body.get("verified")),
            guardian_id=body.get("guardianId"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/decay")
@_decision(admin=True)
async def voice50_decay(
    x_role: str = Header(default="", alias="X-Role"),
):
    """激励池月度衰减(admin——90 天无交互 5%/月,
    保底 30%; 只作用池不碰信值)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().run_decay()
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/offset")
@_decision(strict=True)
async def voice50_offset(
    body: dict,
    x_member_id: str | None = Header(
        None, alias="X-Member-Id"),
):
    """池余额抵扣历史违规(会员——≤50%/次, 走 45号
    submit_repair 修复通道)"""
    member_id = _require_member_strict(x_member_id)
    if not isinstance(body, dict) \
            or "violationEventId" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 violationEventId 字段")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().offset_violation(
            member_id, int(body["violationEventId"]),
            amount=body.get("amount"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/voice50/unfreeze")
@_decision(admin=True)
async def voice50_unfreeze(
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """L1 降级人工复核恢复(admin——只解冻积分域)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    if not isinstance(body, dict) \
            or "memberId" not in body:
        raise HTTPException(
            status_code=409,
            detail="请求体需含 memberId")
    try:
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        return await Voice50Service().unfreeze(
            int(body["memberId"]),
            note=str(body.get("note") or ""))
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 控制面(大模型二代——全站范式 4 端点)
# ============================================================

@router.get("/mode")
async def xiaozhu_mode_status(
    x_role: str = Header(default="", alias="X-Role"),
):
    """灰度总览(观测面——模式+护栏+红线公示;
    不受开关影响)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    from services.xiaozhu_mode_service import (
        XiaozhuModeService,
    )
    return await XiaozhuModeService().status_view()


@router.post("/mode/override")
async def xiaozhu_mode_override(
    body: dict = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """运行时切档(免容器重建; 空 mode=清除 override)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    body = body or {}
    from services.xiaozhu_mode_service import (
        XiaozhuModeService,
    )
    try:
        return await XiaozhuModeService().set_override(
            str(body.get("mode") or ""),
            operator="admin")
    except ValueError as e:
        raise HTTPException(status_code=409,
                            detail=str(e)) from e


@router.post("/mode/guard")
async def xiaozhu_mode_guard(
    body: dict = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """护栏手动检查(三指标恶化 >3% 自动暂停)

    body 可选 {asrFailRate, adjudicationRate,
    corpusRejectRate}——缺省从仓储层实时聚合
    (asr_failed 轮次占比/P50 处置密度/语料拒审占比)。
    """
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    body = body or {}
    from services.xiaozhu_mode_service import (
        XiaozhuModeService,
    )
    try:
        if any(k in body for k in (
                "asrFailRate",
                "adjudicationRate",
                "corpusRejectRate")):
            return await XiaozhuModeService(
            ).guard_check(
                float(body.get("asrFailRate") or 0),
                float(body.get(
                    "adjudicationRate") or 0),
                float(body.get(
                    "corpusRejectRate") or 0))
        from services.xiaozhu_scheduler import (
            run_guard_patrol,
        )
        r = await run_guard_patrol()
        return {"success": True,
                "metrics": r.get("metrics"),
                "samples": r.get("samples"),
                "breached": r.get("breached"),
                "pausedNow": r.get("pausedNow"),
                "breaches": r.get("breaches") or []}
    except ValueError as e:
        raise HTTPException(status_code=409,
                            detail=str(e)) from e


@router.post("/mode/resume")
async def xiaozhu_mode_resume(
    body: dict = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """人工恢复(护栏暂停解除——决策留痕)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")
    body = body or {}
    from services.xiaozhu_mode_service import (
        XiaozhuModeService,
    )
    try:
        return await XiaozhuModeService().resume(
            note=str(body.get("note") or ""))
    except ValueError as e:
        raise HTTPException(status_code=409,
                            detail=str(e)) from e


def register_xiaozhu_routes(app) -> None:
    """注册48号路由(main.py startup 调用)"""
    app.include_router(router)
