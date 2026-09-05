"""55号·二维码AI智能管理路由(P0)

端点(P0 4):
    GET  /api/qr55/registry        注册表自描述(admin, 观测面)
    POST /api/qr55/intent/parse    意图解析演示(admin, 观测面)
    POST /api/qr55/code/generate   签名码生成(admin, on, P0 能力验证)
    GET  /api/qr55/model/status    模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-54号同款口径)。
统一口径:
    - 观测面(registry/model/status)不受 QR55_MODE
      影响; intent/parse 为规则轨确定性演示亦开放
    - 生成面(generate): off=拒绝(409——存量二维码
      链路零影响)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/qr55",
                   tags=["二维码AI智能管理(55号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


class IntentParseIn(BaseModel):
    """意图解析入参"""
    text: str
    audience: str | None = None


class GenerateIn(BaseModel):
    """签名码生成入参"""
    serviceId: str
    params: dict = {}
    memberId: int
    ttlSeconds: int = 300


@router.get("/registry")
async def get_registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """注册表自描述(12 项服务白名单+模板四类+
    敏感度口径——观测面不受开关影响)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    return Qr55Service.registry()


@router.post("/intent/parse")
async def intent_parse(
        body: IntentParseIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图解析(规则轨三态 resolved/partial/clarify
    ——白名单映射零 LLM 依赖; 观测演示)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    try:
        return Qr55Service().parse_intent(
            body.text, audience=body.audience)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/code/generate")
async def code_generate(
        body: GenerateIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """签名码生成(白名单校验+HMAC+exp+nonce
    ——P0 能力验证; off 态 409)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    try:
        return await Qr55Service().generate_code(
            body.serviceId, body.params,
            body.memberId,
            ttl_seconds=body.ttlSeconds)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(champion/challenger/八因子
    ——44号 get_weights_view 复用; 观测面)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    return await Qr55Service().model_status()


def register_qr55_routes(app) -> None:
    """注册55号路由(main.py startup 调用)"""
    app.include_router(router)
