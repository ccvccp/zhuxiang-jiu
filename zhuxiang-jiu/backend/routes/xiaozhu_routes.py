"""48号·小竹智能语音中枢路由(P0 感知层)

端点(P0 共 6 + P1 4 + P2 2 + P3 7 + P4 2 + 49号P0 1
     + 49号P2 2 + 49号P4 1 = 25):
    POST /api/xiaozhu/sessions              开启会话
    POST /api/xiaozhu/sessions/{id}/voice   语音轮次(音频全链)
    POST /api/xiaozhu/sessions/{id}/text    文本轮次(同链)
    GET  /api/xiaozhu/sessions/{id}          会话视图(轮次历史)
    DELETE /api/xiaozhu/sessions/{id}       一键清除(级联轮次)
    GET  /api/xiaozhu/commands              指令集自描述
    GET  /api/xiaozhu/dashboard             看板七区块(48号P4+49号P4 FC分区, admin)
    POST /api/xiaozhu/dashboard/fairness-bridge  公平性桥接(P4)
    GET  /api/xiaozhu/fc/audit              FC审计流水(49号P0, admin)
    GET  /api/xiaozhu/privacy/budget        隐私预算视图(49号P2)
    PUT  /api/xiaozhu/privacy/preferences   隐私偏好调整(49号P2)
    POST /api/xiaozhu/fc/redteam            红队用例集执行(49号P4, admin)

鉴权: X-Member-Id(会员标识, 35号 Hub 同款惯例);
管理端 X-Role: admin(43-47号同款口径)。
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


def _require_member_strict(x_member_id: str | None) -> int:
    """强会员标识(P1 绑定/上下文——身份操作必须携带)"""
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="需要 X-Member-Id")
    try:
        return int(x_member_id)
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


# ============================================================
# P1 认知层(绑定 + 角色上下文)
# ============================================================

@router.post("/bindings")
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
    except (TypeError, ValueError):
        raise HTTPException(status_code=409,
                            detail="trustId 需为整数")
    except Exception as e:
        raise _handle(e) from e


@router.delete("/bindings")
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
    member_id = _require_member_strict(x_member_id)
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


@router.get("/sessions/{session_id}/actions")
async def list_actions(session_id: int,
                       x_member_id: str | None = Header(
                           None, alias="X-Member-Id"),
                       ):
    """执行留痕回溯("我刚才做了什么")——写/高敏轮次视图"""
    _require_member_strict(x_member_id)
    try:
        from services.xiaozhu_service import XiaozhuService
        view = await XiaozhuService().get_session(
            session_id)
        actions = [
            {"seq": t.get("seq"), "action":
             t.get("intent"), "ts": t.get("ts"),
             "reply": t.get("reply"),
             "card": t.get("card") or {}}
            for t in view.get("turns") or []
            if t.get("intent") in (
                "cart.submit", "trust.convert",
                "trust.bind")]
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


@router.post("/dashboard/fairness-bridge")
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


def register_xiaozhu_routes(app) -> None:
    """注册48号路由(main.py startup 调用)"""
    app.include_router(router)
