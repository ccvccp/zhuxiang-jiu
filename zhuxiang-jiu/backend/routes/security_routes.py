"""43号·AI智能安全管理路由(P1, 14 端点)

端点:
    会员端(4):
        GET  /api/security/status                       我的安全状态
        POST /api/security/challenge/verify             挑战应答(mock 验证码)
        POST /api/security/appeals                      误报申诉提交
        GET  /api/security/appeals                      我的申诉列表

    管理端(10):
        GET  /api/security/admin/dashboard              态势总览
        GET  /api/security/admin/events                 攻击事件流水
        POST /api/security/admin/events/{id}/decide     事件裁决(确认攻击/误报)
        GET  /api/security/admin/ips                    IP 信誉列表
        POST /api/security/admin/ips/{ip}/ban           手动封禁
        POST /api/security/admin/ips/{ip}/unban          手动解封
        POST /api/security/admin/ips/{ip}/pin           信誉钉住/解钉
        GET  /api/security/admin/blocks                  封禁列表
        GET  /api/security/admin/appeals                 申诉队列
        POST /api/security/admin/appeals/{id}/decide     申诉裁决

鉴权:
    - 会员端: X-Member-Id 头(项目 Mock 口径)
    - 管理端: X-Role: admin

异常映射(遵循项目约定):
    - KeyError  → 404(资源不存在)
    - ValueError → 409(参数/业务冲突)
"""

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel as PydBaseModel, Field

from services.security_service import Security43Service


router = APIRouter(prefix="/api/security", tags=["安全管理(43号)"])
_service = Security43Service()


# ============================================================
#  请求模型
# ============================================================

class ChallengeVerifyRequest(PydBaseModel):
    token: str = Field("", description="挑战令牌(响应头 x-security-challenge)")
    answer: str = Field(..., min_length=1, max_length=64,
                         description="验证应答(mock: 非空即通过)")


class AppealRequest(PydBaseModel):
    eventId: int = Field(..., gt=0, description="被申诉的安全事件ID")
    reason: str = Field("", max_length=500, description="申诉理由")


class EventDecideRequest(PydBaseModel):
    confirm: bool = Field(..., description="true=确认攻击 / false=误报")
    reviewer: str = Field("admin", max_length=50, description="裁决人")
    note: str = Field("", max_length=200, description="裁决备注")


class AppealDecideRequest(PydBaseModel):
    approve: bool = Field(..., description="true=误报恢复 / false=维持处置")
    reviewer: str = Field("admin", max_length=50, description="裁决人")
    note: str = Field("", max_length=200, description="裁决备注")


class BanRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="封禁原因")


class PinRequest(PydBaseModel):
    pinned: bool = Field(..., description="true=钉住 / false=解钉")


# ============================================================
#  异常映射辅助(与 invoice_routes 一致)
# ============================================================

def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=403, detail="缺少 X-Member-Id")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=403, detail="X-Member-Id 须为数字")


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _client_ip(request: Request) -> str:
    """取客户端 IP(mock 口径直连; 反代场景 P3 接 X-Forwarded-For)"""
    client = request.client
    return str(client.host) if client else "unknown"


# ============================================================
#  会员端
# ============================================================

@router.get("/status")
async def my_status(
    request: Request,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的安全状态(当前 IP 信誉/封禁/挑战通行证/我的事件)"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.my_status(member_id, ip=_client_ip(request))
    except Exception as e:
        raise _handle(e) from e


@router.post("/challenge/verify")
async def challenge_verify(
    request: Request,
    body: ChallengeVerifyRequest,
):
    """挑战应答验证(mock 验证码: 应答非空即通过 → IP 通行证 TTL 900s)"""
    try:
        return await _service.verify_challenge(
            _client_ip(request), token=body.token, answer=body.answer)
    except Exception as e:
        raise _handle(e) from e


@router.post("/appeals")
async def submit_appeal(
    body: AppealRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """对挑战/封禁事件提交误报申诉(一事件一申诉幂等)"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.submit_appeal(
            member_id, body.eventId, body.reason)
    except Exception as e:
        raise _handle(e) from e


@router.get("/appeals")
async def my_appeals(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的申诉记录"""
    member_id = _require_member(x_member_id)
    try:
        appeals = await _service.repo.list_appeals(
            member_id=member_id, limit=100)
        return {"success": True, "total": len(appeals),
                "appeals": appeals}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
#  管理端
# ============================================================

@router.get("/admin/dashboard")
async def admin_dashboard(
    x_role: str = Header(default="", alias="X-Role"),
):
    """态势总览(事件分布/误报率/申诉/封禁)"""
    _require_admin(x_role)
    try:
        return await _service.stats()
    except Exception as e:
        raise _handle(e) from e


@router.get("/admin/events")
async def admin_events(
    action: str = Query(None, description="处置档位过滤"
                         "(challenge/block/throttle)"),
    verdict: str = Query(None, description="裁决状态过滤"
                          "(pending/confirmed/false_positive)"),
    limit: int = Query(100, ge=1, le=1000),
    x_role: str = Header(default="", alias="X-Role"),
):
    """攻击事件流水(含因子明细)"""
    _require_admin(x_role)
    try:
        events = await _service.list_events(action=action, limit=limit)
        if verdict:
            events = [e for e in events if e.get("verdict") == verdict]
        return {"success": True, "total": len(events),
                "events": events}
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/events/{event_id}/decide")
async def admin_decide_event(
    event_id: int,
    body: EventDecideRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """事件裁决: 确认攻击/误报(误报自动恢复 IP 信誉, P2 学习真值)"""
    _require_admin(x_role)
    try:
        return await _service.decide_event(
            event_id, body.confirm, body.reviewer, body.note)
    except Exception as e:
        raise _handle(e) from e


@router.get("/admin/ips")
async def admin_ips(
    x_role: str = Header(default="", alias="X-Role"),
):
    """IP 信誉列表(三态分布)"""
    _require_admin(x_role)
    try:
        reps = await _service.list_reputations()
        return {"success": True, "total": len(reps),
                "ips": reps}
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/ips/{ip}/ban")
async def admin_ban_ip(
    ip: str,
    body: BanRequest = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动封禁 IP(TTL 自动解封)"""
    _require_admin(x_role)
    try:
        return await _service.admin_ban_ip(
            ip, reason=(body.reason if body else ""))
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/ips/{ip}/unban")
async def admin_unban_ip(
    ip: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动解封(误报兜底)"""
    _require_admin(x_role)
    try:
        return await _service.admin_unban_ip(ip)
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/ips/{ip}/pin")
async def admin_pin_ip(
    ip: str,
    body: PinRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """信誉钉住/解钉(钉住不受冷却恢复影响)"""
    _require_admin(x_role)
    try:
        record = await _service.pin_reputation(ip, body.pinned)
        return {"success": True, "ip": record}
    except Exception as e:
        raise _handle(e) from e


@router.get("/admin/blocks")
async def admin_blocks(
    x_role: str = Header(default="", alias="X-Role"),
):
    """当前封禁列表(TTL 剩余)"""
    _require_admin(x_role)
    try:
        blocks = await _service.list_blocks()
        return {"success": True, "total": len(blocks),
                "blocks": blocks}
    except Exception as e:
        raise _handle(e) from e


@router.get("/admin/appeals")
async def admin_appeals(
    status: str = Query(None, description="状态过滤"
                        "(pending/approved/rejected)"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """申诉队列(按状态过滤)"""
    _require_admin(x_role)
    try:
        appeals = await _service.repo.list_appeals(status=status,
                                                   limit=200)
        return {"success": True, "total": len(appeals),
                "appeals": appeals}
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/appeals/{appeal_id}/decide")
async def admin_decide_appeal(
    appeal_id: int,
    body: AppealDecideRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """申诉裁决(approve → 误报恢复信誉+解封; reject → 维持归档)"""
    _require_admin(x_role)
    try:
        return await _service.decide_appeal(
            appeal_id, body.approve, body.reviewer, body.note)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
#  管理端·UEBA 行为基线(P2, 方案 §6)
# ============================================================

@router.post("/admin/behavior/rebuild")
async def admin_behavior_rebuild(
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动重建行为基线(30天窗口口径, 幂等)"""
    _require_admin(x_role)
    try:
        from services.ueba_service import UebaService
        return await UebaService().rebuild_baselines()
    except Exception as e:
        raise _handle(e) from e


@router.get("/admin/behavior/baselines")
async def admin_behavior_baselines(
    role: str = Query(None, description="角色过滤(member/admin)"),
    actor: str = Query(None, description="actorKey 模糊过滤"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """行为基线查询(时段直方图/P95/功能分布)"""
    _require_admin(x_role)
    try:
        from services.ueba_service import UebaService
        baselines = await UebaService().list_baselines(
            role=role, actor=actor)
        return {"success": True, "total": len(baselines),
                "baselines": baselines}
    except Exception as e:
        raise _handle(e) from e


@router.get("/admin/behavior/deviations")
async def admin_behavior_deviations(
    limit: int = Query(100, ge=1, le=500),
    x_role: str = Header(default="", alias="X-Role"),
):
    """行为偏离记录(近 behavior_alert 事件, 含四检测器明细)"""
    _require_admin(x_role)
    try:
        from services.ueba_service import UebaService
        alerts = await UebaService().list_deviations(limit=limit)
        return {"success": True, "total": len(alerts),
                "deviations": alerts}
    except Exception as e:
        raise _handle(e) from e


def register_security_routes(app) -> None:
    """注册43号路由(main.py startup 调用)"""
    app.include_router(router)
