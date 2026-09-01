"""39号·AI智能网站入口管理模块路由(P0, 18 端点)

鉴权(设计文档 §4):
    - 公开(白名单): recognize/login/step-up/qr 全协议
    - 登录态(JWT): 设备管理/qr confirm
    - 管理端: 决策留痕/看板

异常映射(遵循项目约定):
    - KeyError → 404(会话/设备不存在)
    - ValueError → 409(状态非法/凭证错误/风控拦截)
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.entry_service import EntryService


router = APIRouter()
_service = EntryService()


def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-Member-Id 须为数字")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class LoginRequest(PydBaseModel):
    mode: str = Field(..., description="通道: password|sms")
    phone: str = Field(..., min_length=11, max_length=11)
    password: str = Field("", max_length=64)
    smsCode: str = Field("", max_length=6)
    fingerprint: str = Field("", max_length=500,
                             description="设备弱特征指纹(客户端拼接)")


class StepUpRequest(PydBaseModel):
    memberId: int = Field(..., ge=1)
    phone: str = Field(..., min_length=11, max_length=11)
    smsCode: str = Field(..., min_length=4, max_length=6)
    fingerprint: str = Field("", max_length=500)


class QrCreateRequest(PydBaseModel):
    fingerprint: str = Field("", max_length=500)


class QrConfirmRequest(PydBaseModel):
    fingerprint: str = Field("", max_length=500)


class QrMockScanRequest(PydBaseModel):
    memberId: int = Field(None, ge=1,
                          description="Mock 轨: 模拟手机端确认人")


class QrExchangeRequest(PydBaseModel):
    loginTicket: str = Field(..., min_length=8, max_length=64)


class TrustDeviceRequest(PydBaseModel):
    days: int = Field(30, ge=1, le=90)


class RegistrationMergeRequest(PydBaseModel):
    memberId: int = Field(..., ge=1)
    clickId: int = Field(..., ge=1)


class BioEnrollRequest(PydBaseModel):
    bioType: str = Field(..., description="fingerprint|face")
    deviceId: str = Field(..., min_length=2, max_length=40)


class BioBindRequest(PydBaseModel):
    bioType: str = Field(...)
    deviceId: str = Field(..., min_length=2, max_length=40)
    enrollChallenge: str = Field("", max_length=64)
    publicKeyHash: str = Field(..., min_length=8, max_length=64)
    name: str = Field("", max_length=30)


class BioChallengeRequest(PydBaseModel):
    credentialId: str = Field(..., min_length=4, max_length=40)


class BioVerifyRequest(PydBaseModel):
    credentialId: str = Field(..., min_length=4, max_length=40)
    assertionHash: str = Field(..., min_length=8, max_length=64)


class DecisionReviewRequest(PydBaseModel):
    verdict: str = Field(..., description="confirm|false_block|false_allow")


# ============================================================
# 入口与识别(公开)
# ============================================================

@router.get("/api/entry/recognize", tags=["AI智能网站入口管理模块"])
async def recognize(
    fingerprint: str = Query("", max_length=500,
                             description="设备弱特征指纹"),
):
    """AI 预判入口: 设备识别 → 推荐登录方式排序 + 问候"""
    try:
        result = await _service.recognize(fingerprint)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/login", tags=["AI智能网站入口管理模块"])
async def login(
    data: LoginRequest,
    x_forwarded_for: str = Header("", alias="X-Forwarded-For"),
):
    """统一登录(密码/短信) → AI 风控自适应(allow 直发/step_up 二次)"""
    try:
        result = await _service.login(
            mode=data.mode, fingerprint=data.fingerprint,
            ip=x_forwarded_for.split(",")[0].strip(),
            phone=data.phone, password=data.password,
            sms_code=data.smsCode)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/step-up/verify",
             tags=["AI智能网站入口管理模块"])
async def step_up_verify(
    data: StepUpRequest,
    x_forwarded_for: str = Header("", alias="X-Forwarded-For"),
):
    """step_up 二次验证(短信)完成 → 签发令牌"""
    try:
        result = await _service.step_up_verify(
            member_id=data.memberId, phone=data.phone,
            sms_code=data.smsCode, fingerprint=data.fingerprint,
            ip=x_forwarded_for.split(",")[0].strip())
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 扫码登录(公开协议 + 手机端确认须登录态)
# ============================================================

@router.post("/api/entry/qr/create", tags=["AI智能网站入口管理模块"])
async def qr_create(data: QrCreateRequest):
    """PC 创建扫码会话(180s)"""
    try:
        result = await _service.qr_create(data.fingerprint)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/entry/qr/{qr_id}/status",
            tags=["AI智能网站入口管理模块"])
async def qr_status(qr_id: str):
    """PC 轮询状态(2s 间隔; pending→scanned→confirmed)"""
    try:
        result = await _service.qr_status(qr_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/qr/{qr_id}/scan",
             tags=["AI智能网站入口管理模块"])
async def qr_scan(
    qr_id: str,
    data: QrMockScanRequest | None = None,
):
    """扫码动作(pending → scanned; Mock 轨可带确认人单端演示)"""
    try:
        mock_member = (data.model_dump().get("memberId")
                       if data else None)
        result = await _service.qr_scan(
            qr_id, mock_member_id=mock_member)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/qr/{qr_id}/confirm",
             tags=["AI智能网站入口管理模块"])
async def qr_confirm(
    qr_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_forwarded_for: str = Header("", alias="X-Forwarded-For"),
):
    """手机端(已登录态)扫码确认 → 一次性 loginTicket(60s)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.qr_confirm(
            qr_id, member_id,
            ip=x_forwarded_for.split(",")[0].strip())
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/qr/{qr_id}/exchange",
             tags=["AI智能网站入口管理模块"])
async def qr_exchange(qr_id: str, data: QrExchangeRequest):
    """PC 用一次性 loginTicket 换 JWT 双令牌(防重放)"""
    try:
        result = await _service.qr_exchange(qr_id, data.loginTicket)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/qr/{qr_id}/cancel",
             tags=["AI智能网站入口管理模块"])
async def qr_cancel(qr_id: str):
    """取消扫码会话(幂等)"""
    try:
        result = await _service.qr_cancel(qr_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 设备管理(登录态)
# ============================================================

@router.get("/api/entry/devices", tags=["AI智能网站入口管理模块"])
async def list_devices(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的设备清单(含可信标记)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.list_devices(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/devices/{device_id}/trust",
             tags=["AI智能网站入口管理模块"])
async def trust_device(
    device_id: str,
    data: TrustDeviceRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """开启设备可信免登录(默认 30 天; 风险 allow 级才豁免)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.trust_device(member_id, device_id,
                                             data.days)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.delete("/api/entry/devices/{device_id}",
               tags=["AI智能网站入口管理模块"])
async def remove_device(
    device_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """删除设备(吊销信任; 幂等)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.remove_device(member_id, device_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 生物凭证中心(P1, 设计文档 §2.3: 绑定须登录态, 挑战/验证公开)
# ============================================================

@router.post("/api/entry/bio/enroll", tags=["AI智能网站入口管理模块"])
async def bio_enroll(
    data: BioEnrollRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """发起生物凭证绑定(设备端本地生成凭证对, 原始数据不上送)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.bio_enroll(member_id, data.bioType,
                                           data.deviceId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/bio/bind", tags=["AI智能网站入口管理模块"])
async def bio_bind(
    data: BioBindRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """完成绑定(登记 publicKeyHash 摘要, 不落原始生物数据)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.bio_bind(
            member_id, data.bioType, data.deviceId,
            data.enrollChallenge, data.publicKeyHash, data.name)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/bio/challenge",
             tags=["AI智能网站入口管理模块"])
async def bio_challenge(data: BioChallengeRequest):
    """发起生物登录挑战(60s 一次性 assertionChallenge)"""
    try:
        result = await _service.bio_challenge(data.credentialId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/entry/bio/verify", tags=["AI智能网站入口管理模块"])
async def bio_verify(
    data: BioVerifyRequest,
    x_forwarded_for: str = Header("", alias="X-Forwarded-For"),
):
    """验证断言 → AI 风控决策 → allow 直发令牌(Mock 轨确定性派生)"""
    try:
        result = await _service.bio_verify(
            data.credentialId, data.assertionHash,
            ip=x_forwarded_for.split(",")[0].strip())
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/entry/bio/credentials",
            tags=["AI智能网站入口管理模块"])
async def bio_list(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的生物凭证清单"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.bio_list(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.delete("/api/entry/bio/credentials/{credential_id}",
               tags=["AI智能网站入口管理模块"])
async def bio_revoke(
    credential_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """吊销生物凭证(忘记这台设备的生物登录, 幂等)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.bio_revoke(member_id, credential_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 决策复核回流 + 角色落地页(P1)
# ============================================================

@router.post("/api/entry/decisions/{decision_id}/review",
             tags=["AI智能网站入口管理模块"])
async def review_decision(
    decision_id: int,
    data: DecisionReviewRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """管理员复核风控决策 → ai_learning 反馈回流(auth_risk 自学习)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        result = await _service.review_decision(decision_id,
                                                data.verdict)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/entry/landing", tags=["AI智能网站入口管理模块"])
async def landing(
    role: str = Query("member", description="member|cs_staff|admin|guest"),
    memberId: int = Query(None, ge=1),
):
    """角色落地页(hub 面板 chips + 连续登录激励 + 问候)"""
    try:
        result = await _service.landing(role, memberId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 注册归并挂接(公开: 注册成功即调)
# ============================================================

@router.post("/api/entry/registration-merge",
             tags=["AI智能网站入口管理模块"])
async def registration_merge(data: RegistrationMergeRequest):
    """注册归并挂接(attract 三合一: lead+推广绑定+归因, best-effort)"""
    try:
        result = await _service.registration_merge(
            data.memberId, data.clickId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 风控决策留痕与看板(管理端)
# ============================================================

@router.get("/api/entry/decisions", tags=["AI智能网站入口管理模块"])
async def list_decisions(
    x_role: str = Header(None, alias="X-Role"),
    member_id: int = Query(None, alias="memberId"),
    action: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """风控决策留痕(admin)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        result = await _service.repo.list_decisions(
            member_id=member_id, action=action, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/entry/report/overview",
            tags=["AI智能网站入口管理模块"])
async def report_overview(
    x_role: str = Header(None, alias="X-Role"),
):
    """入口看板: 通道漏斗/风险动作分布/降级统计(admin)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        result = await _service.overview()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/entry/events", tags=["AI智能网站入口管理模块"])
async def list_events(
    x_role: str = Header(None, alias="X-Role"),
    memberId: int = Query(None, ge=1),
    mode: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """登录事件流水(admin, 看板明细)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        result = await _service.repo.list_events(
            member_id=memberId, mode=mode, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


def register_entry_routes(app):
    """注册39号·AI智能网站入口管理模块路由"""
    app.include_router(router)
