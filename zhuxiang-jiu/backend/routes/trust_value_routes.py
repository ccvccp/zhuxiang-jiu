"""45号·信值模块路由(P0 角色档案 + 三层评分)

端点(P0, 自助 3 + 管理 1):
    POST /api/trust/roles                自助建档(证件明文仅本次
                                         使用, 落盘 SHA-256 摘要)
    GET  /api/trust/roles/{trustId}      档案视图(分层明细/熔断态/
                                         最近事件/摘要脱敏)
    POST /api/trust/roles/{trustId}/score 触发重算(熔断判定→
                                         三层评分→锁档→落盘)
    POST /api/trust/roles/{trustId}/events 行为事件灌入(P0 数据
                                         通道——管理端 manual;
                                         P1 起雷达/存证接管)

鉴权:
    - 自助面(建档/查询/重算): 公开(信值查询脱敏口径——
      摘要掩码展示, 明文永不返回)
    - 事件灌入: X-Role: admin(43/44号同款口径)

统一口径:
    - 模块纯增量(零既有路由改动); TRUST_VALUE_MODE 保留给
      P3(兑换/发行开关)与后续主动行为
    - KeyError → 404 / ValueError → 409(44号同款)
"""

from fastapi import APIRouter, Header, HTTPException

from services.trust_scoring_service import TrustProfileService

router = APIRouter(prefix="/api/trust",
                   tags=["信值模块(45号)"])
_service = TrustProfileService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射(43/44号同款)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


@router.post("/roles")
async def create_role(body: dict):
    """自助建档(双角色: person 个人 / org 企业机构)

    body: {role, name, idNumber}——证件号仅本次使用,
    存储只留 SHA-256 摘要(防重复建档唯一键)。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        return await _service.create_role(
            str(body.get("role") or ""),
            str(body.get("name") or ""),
            str(body.get("idNumber") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/roles/{trust_id}")
async def get_role(trust_id: int):
    """档案视图(分层明细 + 熔断态 + 最近事件)"""
    try:
        return await _service.get_profile(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/roles/{trust_id}/score")
async def rescore_role(trust_id: int):
    """触发重算(熔断判定 → 三层评分 → 锁档 → 落盘)"""
    try:
        return await _service.compute_score(trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/roles/{trust_id}/events")
async def record_role_event(
    trust_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """行为事件灌入(P0 管理端数据通道)

    body: {layer: L1|L2|L3, factor, delta ∈ [-100,100],
           severity?: general|severe|criminal(仅 L1 语义),
           summary?}
    P1 起由 AI 雷达/授权探针/自愿存证以 source 接管, 本端点
    保留为 manual 通道。
    """
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        return await _service.record_event(
            trust_id,
            layer=str(body.get("layer") or ""),
            factor=str(body.get("factor") or ""),
            delta=body.get("delta") or 0,
            severity=str(body.get("severity") or "general"),
            source="manual",
            summary=str(body.get("summary") or ""))
    except Exception as e:
        raise _handle(e) from e


def register_trust_value_routes(app) -> None:
    """注册45号路由(main.py startup 调用)"""
    app.include_router(router)
