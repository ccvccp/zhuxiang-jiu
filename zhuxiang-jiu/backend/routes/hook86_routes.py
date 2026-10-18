"""86号·合规引流钩子 Agent 路由

载体: 小程序「竹香酒馆」webview 内 H5(zyjiu.com, 自有备案域名)
P0: POST /chat 意图对话(合规闸包夹) + POST /lead 留资(幂等+24h频控)
    + GET /leads /channels (admin 归因查询) + GET /status
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.hook86_service import (
    Hook86Service, HookIntentError)

router = APIRouter(prefix="/api/hook86", tags=["86号合规引流钩子Agent"])

_svc = Hook86Service()


def _require_admin(x_role: str | None) -> None:
    if (x_role or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")


def _subject(member_id: str, fingerprint: str, phone_hint: str = "") -> str:
    """频控主体: 登录会员优先, 否则匿名指纹"""
    if (member_id or "").strip():
        return f"m:{member_id.strip()}"
    if (fingerprint or "").strip():
        return f"fp:{fingerprint.strip()[:64]}"
    return f"ph:{phone_hint}" if phone_hint else "anon"


class ChatReq(BaseModel):
    message: str
    memberId: str = ""
    fingerprint: str = ""
    channelCode: str = ""


class LeadReq(BaseModel):
    phone: str
    channelCode: str = ""
    intent: str = ""
    memberId: str = ""
    fingerprint: str = ""


@router.post("/chat")
async def hook86_chat(req: ChatReq):
    """意图对话(输入/输出双向过小竹合规闸, 强制理性饮酒声明)"""
    if not (req.message or "").strip():
        raise HTTPException(status_code=422, detail="message 不能为空")
    subj = _subject(req.memberId, req.fingerprint)
    return {"code": 0, "data": await _svc.chat(
        req.message, subject=subj, channel_code=req.channelCode)}


@router.post("/lead")
async def hook86_lead(req: LeadReq):
    """留资登记(手机号+渠道码; 幂等 + 24h≤2 次频控硬闸)"""
    subj = _subject(req.memberId, req.fingerprint, req.phone[-4:])
    try:
        out = await _svc.submit_lead(
            req.phone, channel_code=req.channelCode,
            intent=req.intent, subject=subj)
    except HookIntentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"code": 0, "data": out}


@router.get("/leads")
async def hook86_leads(status: str = "", channel: str = "",
                       limit: int = 100,
                       x_role: str | None = Header(default=None)):
    """留资查询(admin)"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.leads(
        status=status, channel=channel, limit=limit)}


@router.get("/channels")
async def hook86_channels(x_role: str | None = Header(default=None)):
    """渠道码归因汇总(admin): 各渠道留资量/转化分布"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.channels()}


@router.get("/metrics")
async def hook86_metrics(x_role: str | None = Header(default=None)):
    """互动质量指标(admin): 有效对话率/拦截率/chat→lead 转化/意图分布/recent7"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.metrics_with_trend()}


@router.get("/lead/export")
async def hook86_export_lead(phone: str,
                             x_role: str | None = Header(default=None)):
    """数据主体导出权(PIPL §45, admin 代执行): 完整副本+强制审计留痕"""
    _require_admin(x_role)
    if not __import__("re").match(r"^1[3-9][0-9]{9}$",
                                  (phone or "").strip()):
        raise HTTPException(status_code=422, detail="手机号格式不正确")
    return {"code": 0, "data": await _svc.export_lead(
        phone.strip(), operator="admin")}


@router.delete("/lead/{lead_id}")
async def hook86_delete_lead(lead_id: int,
                             x_role: str | None = Header(default=None)):
    """数据主体删除权(admin 代执行, PIPL §47): 删除留资与幂等索引"""
    _require_admin(x_role)
    ok = await _svc.delete_lead(lead_id)
    if not ok:
        raise HTTPException(status_code=404, detail="lead 不存在")
    return {"code": 0, "data": {"deleted": True, "leadId": lead_id}}


@router.get("/status")
async def hook86_status():
    """模块诊断(存储模式/频控策略/意图清单)"""
    return {"code": 0, "data": _svc.status()}


def register_hook86_routes(app) -> None:
    app.include_router(router)
