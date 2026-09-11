"""智图·AI智能地图大模型路由(P0-P3 全量, 22 端点)

位置地图管理模块升级: 智图·AI智能地图大模型(全域角色时空服务中枢)。
鉴权: 全部管理端 X-Role: admin(时空调度敏感域)。
既有 /api/location/* 13 端点保留零改动(叠加式升级)。
地图底座: 百度地图(前端测试期接入, AK 经 env 注入)。

端点分布:
    - P0 意图引擎: intent/parse / intent/ontology / intent/search
                   / intent/roles / intent/behavior-hints
    - P1 资源聚合: poi/nearby / pois / poi/register
                   / supplier/dock-booking / supplier/dock-status
                   / b2b/warehouse-match / b2b/fulfillment-board
    - P2 全域调度: command/situation / command/scan / command/tickets
                   / command/tickets/{id}/ack / agent/cockpit
                   / management/radar
    - P3 进化闭环: evolution/behavior / evolution/behaviors
                   / evolution/sandbox / evolution/performance
                   / evolution/feedback / evolution/feedbacks

异常映射: KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.zt_fabric_service import _ZtStore
from services.zt_intent_service import ZtIntentService
from services.zt_resource_service import ZtResourceService
from services.zt_command_service import ZtCommandService
from services.zt_evolution_service import ZtEvolutionService

router = APIRouter()
_store = _ZtStore()
_intent = ZtIntentService(store=_store)
_resource = ZtResourceService(store=_store)
_command = ZtCommandService(store=_store)
_evo = ZtEvolutionService(store=_store)


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class IntentParseRequest(PydBaseModel):
    text: str = Field(..., min_length=1, max_length=200)


class IntentSearchRequest(PydBaseModel):
    text: str = Field(..., min_length=1, max_length=200)
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    role: str = Field("consumer")
    radiusKm: float = Field(10.0, ge=0.5, le=200)
    limit: int = Field(10, ge=1, le=50)


class BehaviorHintRequest(PydBaseModel):
    role: str = Field(...)


class PoiRegisterRequest(PydBaseModel):
    poiCode: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=40)
    poiType: str = Field(...)
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    capabilities: list = Field(default_factory=list)
    rating: float = Field(4.0, ge=0, le=5)
    stockLevel: float = Field(1.0, ge=0, le=1)
    seatsTotal: int = Field(None, ge=1)
    seatsAvailable: int = Field(None, ge=0)
    open: bool = Field(True)


class DockBookingRequest(PydBaseModel):
    supplierName: str = Field(..., min_length=1, max_length=40)
    slot: str = Field(...)
    goodsType: str = Field("高粱", max_length=20)
    truckCount: int = Field(1, ge=1, le=5)


class WarehouseMatchRequest(PydBaseModel):
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    quantity: int = Field(..., ge=1, le=5000)


class BehaviorRequest(PydBaseModel):
    memberId: int = Field(..., ge=1)
    stage: str = Field(...)
    role: str = Field("consumer")
    query: str = Field("", max_length=200)
    poiCode: str = Field("", max_length=20)
    resultScore: float | None = Field(None, ge=0, le=100)


class SandboxRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    poiType: str = Field("flagship")
    monthlyCost: float = Field(50000.0, ge=1000, le=10_000_000)


class FeedbackRequest(PydBaseModel):
    targetType: str = Field(...)
    verdict: str = Field(...)
    note: str = Field("", max_length=200)


class TicketAckRequest(PydBaseModel):
    disposition: str = Field("acked")


# ============================================================
# P0: 意图引擎(admin)
# ============================================================

@router.post("/api/map-ai/intent/parse", tags=["智图AI智能地图大模型"])
async def intent_parse(data: IntentParseRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """自然语言→复合意图(确定性本体分词)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": ZtIntentService.parse_text(
            data.text)}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/intent/ontology", tags=["智图AI智能地图大模型"])
async def intent_ontology(x_role: str = Header(None, alias="X-Role")):
    """业务本体表(意图词→能力标签)"""
    _require_admin(x_role)
    return {"success": True, "data": _intent.ontology()}


@router.post("/api/map-ai/intent/search", tags=["智图AI智能地图大模型"])
async def intent_search(data: IntentSearchRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """复合意图时空搜索(能力 AND 匹配+四因子加权)"""
    _require_admin(x_role)
    try:
        result = await _intent.search(
            text=data.text, longitude=data.longitude,
            latitude=data.latitude, role=data.role,
            radius_km=data.radiusKm, limit=data.limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/intent/roles", tags=["智图AI智能地图大模型"])
async def intent_roles(x_role: str = Header(None, alias="X-Role")):
    """六角色画像矩阵"""
    _require_admin(x_role)
    return {"success": True, "data": _intent.roles()}


@router.post("/api/map-ai/intent/behavior-hints",
             tags=["智图AI智能地图大模型"])
async def behavior_hints(data: BehaviorHintRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """角色主动服务提示(视图入口建议)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": _intent.behavior_hints(
            data.role)}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 资源聚合(admin)
# ============================================================

@router.get("/api/map-ai/poi/nearby", tags=["智图AI智能地图大模型"])
async def poi_nearby(x_role: str = Header(None, alias="X-Role"),
                     longitude: float = Query(..., ge=-180, le=180),
                     latitude: float = Query(..., ge=-90, le=90),
                     capability: str = Query(None),
                     radiusKm: float = Query(10.0, ge=0.5, le=200),
                     limit: int = Query(20, ge=1, le=100)):
    """附近 POI(距离排序+能力过滤)"""
    _require_admin(x_role)
    try:
        result = await _resource.nearby(
            longitude=longitude, latitude=latitude,
            capability=capability, radius_km=radiusKm, limit=limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/pois", tags=["智图AI智能地图大模型"])
async def list_pois(x_role: str = Header(None, alias="X-Role"),
                    poiType: str = Query(None),
                    capability: str = Query(None)):
    """全业务 POI 列表(种子+注册表)"""
    _require_admin(x_role)
    try:
        rows = await _resource.fabric.list_pois(
            poi_type=poiType, capability=capability)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/poi/register", tags=["智图AI智能地图大模型"])
async def poi_register(data: PoiRegisterRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """配置化 POI 注册(建议书制: 投产须人工确认)"""
    _require_admin(x_role)
    try:
        result = await _resource.register_poi(
            poi_code=data.poiCode, name=data.name,
            poi_type=data.poiType, longitude=data.longitude,
            latitude=data.latitude, capabilities=data.capabilities,
            rating=data.rating, stock_level=data.stockLevel,
            seats_total=data.seatsTotal,
            seats_available=data.seatsAvailable, open=data.open)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/supplier/dock-booking",
             tags=["智图AI智能地图大模型"])
async def dock_booking(data: DockBookingRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """供货商月台预约(排队最短分配建议)"""
    _require_admin(x_role)
    try:
        result = await _resource.dock_booking(
            supplier_name=data.supplierName, slot=data.slot,
            goods_type=data.goodsType,
            truck_count=data.truckCount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/supplier/dock-status",
            tags=["智图AI智能地图大模型"])
async def dock_status(x_role: str = Header(None, alias="X-Role")):
    """月台实时收货状态(排队/占用)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _resource.dock_status()}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/b2b/warehouse-match",
             tags=["智图AI智能地图大模型"])
async def warehouse_match(data: WarehouseMatchRequest,
                          x_role: str = Header(None, alias="X-Role")):
    """B 端多仓货源匹配(库存/距离/时效+成本测算)"""
    _require_admin(x_role)
    try:
        result = await _resource.warehouse_match(
            longitude=data.longitude, latitude=data.latitude,
            quantity=data.quantity)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/b2b/fulfillment-board",
            tags=["智图AI智能地图大模型"])
async def fulfillment_board(x_role: str = Header(None, alias="X-Role")):
    """B 端履约看板(在途/异常聚合)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _resource.fulfillment_board()}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 全域调度(admin)
# ============================================================

@router.get("/api/map-ai/command/situation",
            tags=["智图AI智能地图大模型"])
async def situation(x_role: str = Header(None, alias="X-Role")):
    """态势一张图(POI 负载+物流运力+异常)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _command.situation()}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/command/scan", tags=["智图AI智能地图大模型"])
async def command_scan(x_role: str = Header(None, alias="X-Role")):
    """异常扫描→《处置预案工单》草稿(人工确认)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _command.scan()}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/command/tickets", tags=["智图AI智能地图大模型"])
async def tickets(x_role: str = Header(None, alias="X-Role"),
                  status: str = Query(None)):
    """工单列表(可按状态过滤)"""
    _require_admin(x_role)
    try:
        rows = await _command.tickets(status=status)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/command/tickets/{ticket_id}/ack",
             tags=["智图AI智能地图大模型"])
async def ticket_ack(ticket_id: int, data: TicketAckRequest,
                     x_role: str = Header(None, alias="X-Role")):
    """工单确认闭环(驳回记负样本)"""
    _require_admin(x_role)
    try:
        result = await _command.ack_ticket(ticket_id, data.disposition)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/agent/cockpit", tags=["智图AI智能地图大模型"])
async def agent_cockpit(x_role: str = Header(None, alias="X-Role"),
                        region: str = Query("成都")):
    """代理商辖区驾驶舱(热力+合规风险点)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _command.agent_cockpit(
            region)}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/management/radar",
            tags=["智图AI智能地图大模型"])
async def risk_radar(x_role: str = Header(None, alias="X-Role")):
    """管理层风险雷达(三域指标分级)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _command.risk_radar()}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 进化闭环(admin)
# ============================================================

@router.post("/api/map-ai/evolution/behavior",
             tags=["智图AI智能地图大模型"])
async def save_behavior(data: BehaviorRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """行为流留痕(搜索→下单→核销→评价)"""
    _require_admin(x_role)
    try:
        result = await _evo.save_behavior(
            member_id=data.memberId, stage=data.stage,
            role=data.role, query=data.query,
            poi_code=data.poiCode,
            result_score=data.resultScore)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/evolution/behaviors",
            tags=["智图AI智能地图大模型"])
async def behaviors(x_role: str = Header(None, alias="X-Role"),
                    memberId: int = Query(None),
                    stage: str = Query(None),
                    limit: int = Query(50, ge=1, le=200)):
    """行为留痕列表"""
    _require_admin(x_role)
    try:
        rows = await _evo.behaviors(member_id=memberId, stage=stage,
                                    limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/evolution/sandbox",
             tags=["智图AI智能地图大模型"])
async def sandbox(data: SandboxRequest,
                 x_role: str = Header(None, alias="X-Role")):
    """时空战略沙盘(选址模拟, 确定性因子加权)"""
    _require_admin(x_role)
    try:
        result = await _evo.sandbox(
            name=data.name, longitude=data.longitude,
            latitude=data.latitude, poi_type=data.poiType,
            monthly_cost=data.monthlyCost)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/evolution/performance",
            tags=["智图AI智能地图大模型"])
async def performance(x_role: str = Header(None, alias="X-Role")):
    """时空绩效画像(按类型效能聚合)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _evo.performance()}
    except Exception as e:
        _handle(e)


@router.post("/api/map-ai/evolution/feedback",
             tags=["智图AI智能地图大模型"])
async def evolution_feedback(data: FeedbackRequest,
                              x_role: str = Header(None, alias="X-Role")):
    """反馈闭环(拒绝记负样本回流)"""
    _require_admin(x_role)
    try:
        result = await _evo.feedback(
            target_type=data.targetType, verdict=data.verdict,
            note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/map-ai/evolution/feedbacks",
            tags=["智图AI智能地图大模型"])
async def evolution_feedbacks(
        x_role: str = Header(None, alias="X-Role"),
        limit: int = Query(50, ge=1, le=200)):
    """反馈留痕列表"""
    _require_admin(x_role)
    try:
        rows = await _evo.feedbacks(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


def register_zt_routes(app):
    """注册智图·AI智能地图大模型路由"""
    app.include_router(router)
