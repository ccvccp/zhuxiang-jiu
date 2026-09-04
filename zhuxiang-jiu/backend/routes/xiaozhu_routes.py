"""48号·小竹智能语音中枢路由(P0 感知层)

端点(P0 共 6 + P1 4 + P2 2 + P3 7 + P4 2 + 49号P0 1
     + 49号P2 2 + 49号P4 1 + 50号P0 5 + 50号P2 2
     + 50号P3 6 + 50号P4 3 + 50号P5 3 = 44):
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


def register_xiaozhu_routes(app) -> None:
    """注册48号路由(main.py startup 调用)"""
    app.include_router(router)
