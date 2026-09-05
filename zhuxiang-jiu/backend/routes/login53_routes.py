"""53号·小竹智能登录引擎路由(P0-P2)

端点(P0 6 + P1 2 + P2 2 = 10):
    GET  /api/login53/registry        注册表视图(admin)
    GET  /api/login53/portal          角色四态判定(会员面)
    POST /api/login53/prelogin/sense  态势感知(会员面, on)
    POST /api/login53/hook/generate   价值钩子生成(会员面, on)
    POST /api/login53/baseline        基线指纹登记(会员面, on)
    GET  /api/login53/my/profile      本人入口档案(会员面)
    POST /api/login53/auth/orchestrate 统一多模态登录编排(P1, on)
    GET  /api/login53/my/events       本人登录历史(P1, 脱敏)
    POST /api/login53/voice/wake-login 唤醒即认证(P2, on)
    GET  /api/login53/voice/briefing   登录后语音导览(P2, on)

鉴权: 会员面 X-Member-Id(compat 兼容头)/管理面
X-Role: admin(43-52号同款口径)。
统一口径:
    - 观测面(registry/查询/事件历史)不受
      LOGIN53_MODE 影响
    - 编排面(sense/hook/baseline/orchestrate/
      wake-login/briefing) off=拒绝(409——
      直通存量 39号登录)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.login53_service import Login53Service

router = APIRouter(prefix="/api/login53",
                   tags=["小竹智能登录引擎(53号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(
            status_code=403,
            detail="需要 X-Member-Id 会员头")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="X-Member-Id 须为数字") from None


class SenseIn(BaseModel):
    fingerprint: str = ""
    visitSource: str = ""
    hour: int | None = None


class BaselineIn(BaseModel):
    fingerprint: str


class OrchestrateIn(BaseModel):
    channel: str
    credential: dict | None = None
    fingerprint: str = ""
    ip: str = ""
    confirmToken: str | None = None
    challengeResponse: str | None = None
    hour: int | None = None


class WakeLoginIn(BaseModel):
    utterance: str
    fingerprint: str = ""
    ip: str = ""
    hour: int | None = None


@router.get("/registry")
async def get_registry(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """注册表视图(五通道矩阵+角色四态+六指标+
    话术 17 场景——观测面不受开关影响)"""
    _require_admin(x_role)
    return Login53Service.registry()


@router.get("/portal")
async def get_portal(
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """角色四态判定(new/active/dormant/high_risk
    ——门户自适应配置)"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True,
                "portal": await (
                    Login53Service()
                    .resolve_portal_state(member_id))}
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/prelogin/sense")
async def prelogin_sense(
        body: SenseIn | None = None,
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """态势感知(基线匹配→静默/一键/常规+
    意图预判+隐私预算预检——49号只读探针)"""
    member_id = _require_member(x_member_id)
    body = body or SenseIn()
    try:
        result = await Login53Service().prelogin_sense(
            member_id, fingerprint=body.fingerprint,
            visit_source=body.visitSource,
            hour=body.hour)
        return {"success": True, "sense": result}
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/hook/generate")
async def generate_hook(
        body: dict | None = None,
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """价值钩子生成(登录前投放——45/50号
    只读聚合+话术渲染)"""
    member_id = _require_member(x_member_id)
    script_key = (body or {}).get("scriptKey")
    try:
        result = await Login53Service().generate_hook(
            member_id, script_key=script_key)
        return {"success": True, "hook": result}
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/baseline")
async def register_baseline(
        body: BaselineIn,
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """基线指纹登记(登录成功后调用——态势感知
    匹配参照)"""
    member_id = _require_member(x_member_id)
    try:
        return await (
            Login53Service()
            .register_baseline_fingerprint(
                member_id, body.fingerprint))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/my/profile")
async def my_profile(
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """本人入口档案(四态+基线+意图标签)"""
    member_id = _require_member(x_member_id)
    profile = await Login53Service().repo.get_profile(
        member_id)
    if not profile:
        return {"success": True, "profile": None,
                "note": "尚无入口档案(首次登录时建档)"}
    return {"success": True, "profile": profile}


@router.post("/auth/orchestrate")
async def auth_orchestrate(
        body: OrchestrateIn,
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """统一多模态登录编排(P1——五通道+
    风险分级四级响应+安全兜底+事件流水)"""
    member_id = _require_member(x_member_id)
    try:
        result = await Login53Service().orchestrate(
            member_id, body.channel,
            credential=body.credential,
            fingerprint=body.fingerprint,
            ip=body.ip,
            confirm_token=body.confirmToken,
            challenge_response=body.challengeResponse,
            hour=body.hour)
        return {"success": True,
                "orchestration": result}
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/my/events")
async def my_events(
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """本人登录历史(脱敏——观测面不受开关影响;
    无原始凭证数据, 仅事件六字段)"""
    member_id = _require_member(x_member_id)
    return await Login53Service().list_events(
        member_id=member_id)


@router.post("/voice/wake-login")
async def voice_wake_login(
        body: WakeLoginIn,
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """唤醒即认证(P2——唤醒词+声纹初验+
    语义口令双因子+会话建立+编排签发+
    登录后导览首播)"""
    member_id = _require_member(x_member_id)
    try:
        result = await Login53Service().voice_wake_login(
            member_id, body.utterance,
            fingerprint=body.fingerprint,
            ip=body.ip, hour=body.hour)
        return {"success": True,
                "wakeLogin": result}
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/voice/briefing")
async def voice_briefing(
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """登录后语音导览(P2——信值/积分/待办
    个性化摘要+快捷指令)"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True,
                "briefing": await (
                    Login53Service()
                    .voice_briefing(member_id))}
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_login53_routes(app) -> None:
    """注册53号路由(main.py startup 调用)"""
    app.include_router(router)
