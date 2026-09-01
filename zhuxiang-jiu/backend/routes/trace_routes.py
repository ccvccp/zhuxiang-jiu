"""双码追溯管理模块路由(22 端点)

鉴权:
    - 用户端: X-Member-Id 头(扫码激活/转让/查询/开箱)
    - 管理端: X-Role: admin 头(生成箱码/生命码/绑定/统计/处罚)
    - 代理商端: X-Agent-Id 头(箱级库存, 须与路径 agent_id 一致)

端点分布:
    - 箱码(4):     generate-box / bind-box / open-box / query-box
    - 生命码(3):    generate-life / bind-life / query-life
    - 扫码(3):     activate / scan-trace / trace-chain
    - 防窜(4):     anti-channel(检测) / punish / penalties / warnings汇总
    - 转让(1):     transfer
    - 记录(1):     scan-logs
    - 统计(1):     stats
    - 代理商箱级库存(5, P1-6): inbound / outbound / inventory / stocktake / warnings
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.trace_service import TraceService
from repositories.trace_repository import (
    SCAN_TYPE_QUERY,
)


router = APIRouter()
_service = TraceService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class GenerateBoxRequest(PydBaseModel):
    productId: str = Field(..., description="产品ID")
    batchNo: str = Field(..., description="批次号")
    count: int = Field(..., ge=1, le=1000, description="生成数量(1-1000)")
    agentId: int | None = Field(None, description="代理商ID")
    agentRegion: str | None = Field(None, description="代理区域")


class BindBoxRequest(PydBaseModel):
    boxId: int = Field(..., description="箱码ID")
    lifeCodeIds: list[int] = Field(..., min_items=1, description="生命码ID数组")
    agentId: int | None = Field(None, description="代理商ID")


class OpenBoxRequest(PydBaseModel):
    boxCode: str = Field(..., description="箱顶码(TBC), 扫码开箱触发失效")
    operatorId: int | None = Field(None, description="操作人ID(代理商/店员)")
    longitude: float | None = Field(None, description="开箱经度")
    latitude: float | None = Field(None, description="开箱纬度")
    province: str | None = Field(None, description="开箱省")
    city: str | None = Field(None, description="开箱市")


class GenerateLifeRequest(PydBaseModel):
    productId: str = Field(..., description="产品ID")
    batchNo: str = Field(..., description="批次号")
    count: int = Field(..., ge=1, le=1000, description="生成数量(1-1000)")
    productName: str = Field("", description="产品名称")
    productAbv: int = Field(42, description="度数")
    productVolume: str = Field("500ml", description="容量")


class BindLifeRequest(PydBaseModel):
    lifeId: int = Field(..., description="生命码ID")
    boxId: int = Field(..., description="箱码ID")


class ActivateRequest(PydBaseModel):
    lifeCode: str = Field(..., description="生命码")
    userId: int = Field(..., description="激活用户ID")
    userPhone: str | None = Field(None, description="手机号")
    userName: str | None = Field(None, description="持有人姓名")
    longitude: float | None = Field(None, description="经度")
    latitude: float | None = Field(None, description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")
    district: str | None = Field(None, description="区")
    purchaseChannel: str = Field("online", description="购买渠道")
    purchasePrice: float = Field(0, description="购买价格")
    orderId: str = Field("", max_length=50, description="关联订单号(可选, 激活回写→工人分润自动取数)")


class ScanTraceRequest(PydBaseModel):
    code: str = Field(..., description="箱码/生命码")
    userId: int | None = Field(None, description="扫码人ID")
    longitude: float | None = Field(None, description="经度")
    latitude: float | None = Field(None, description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")
    scanType: str = Field(SCAN_TYPE_QUERY, description="扫码类型")


class AntiChannelRequest(PydBaseModel):
    lifeCode: str = Field(..., description="生命码")
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")


class TransferRequest(PydBaseModel):
    lifeCode: str = Field(..., description="生命码")
    fromUserId: int = Field(..., description="转让人ID")
    toUserId: int = Field(..., description="受让人ID")
    toName: str | None = Field(None, description="受让人姓名")
    transferType: str = Field("gift", description="转让类型(gift/trade/inherit)")
    longitude: float | None = Field(None, description="经度")
    latitude: float | None = Field(None, description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 箱码 ---

@router.post("/api/trace/box/generate", tags=["双码追溯模块"])
async def generate_box_codes(
    data: GenerateBoxRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """生成箱码(TBC+BBC双码, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.generate_box_codes(
            product_id=data.productId,
            batch_no=data.batchNo,
            count=data.count,
            agent_id=data.agentId,
            agent_region=data.agentRegion,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/box/bind", tags=["双码追溯模块"])
async def bind_box_code(
    data: BindBoxRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """绑定箱码(关联生命码, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.bind_box_code(
            box_id=data.boxId,
            life_code_ids=data.lifeCodeIds,
            agent_id=data.agentId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/box/open", tags=["双码追溯模块"])
async def open_box_code(
    data: OpenBoxRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """扫描箱顶码开箱(开箱即失效·不可逆)

    规则: 仅TBC箱顶码触发开箱; 仅已绑定箱码可开箱;
    开箱后不参与3年增值回收; 箱底码永久有效;
    箱内生命码可逐瓶激活(不受影响)。
    """
    _require_member_id(x_member_id)
    try:
        result = await _service.open_box_code(
            box_code=data.boxCode,
            operator_id=data.operatorId,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/box/{box_id}", tags=["双码追溯模块"])
async def get_box_code(box_id: int):
    """查询箱码(含回收资格判定)"""
    try:
        result = await _service.get_box_code(box_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 生命码 ---

@router.post("/api/trace/life/generate", tags=["双码追溯模块"])
async def generate_life_codes(
    data: GenerateLifeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """生成生命码(BLC格式, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.generate_life_codes(
            product_id=data.productId,
            batch_no=data.batchNo,
            count=data.count,
            product_name=data.productName,
            product_abv=data.productAbv,
            product_volume=data.productVolume,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/life/bind", tags=["双码追溯模块"])
async def bind_life_to_box(
    data: BindLifeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """绑定生命码到箱码(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.bind_life_to_box(data.lifeId, data.boxId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/life/{life_id}", tags=["双码追溯模块"])
async def get_life_code(life_id: int):
    """查询生命码"""
    try:
        result = await _service.get_life_code(life_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 扫码追溯 ---

@router.post("/api/trace/activate", tags=["双码追溯模块"])
async def activate_life_code(
    data: ActivateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """扫码激活生命码(P4: 消费者首扫激活, 首启日期为老酒回收唯一基准)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.activate_life_code(
            life_code=data.lifeCode,
            user_id=data.userId,
            user_phone=data.userPhone,
            user_name=data.userName,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
            district=data.district,
            purchase_channel=data.purchaseChannel,
            purchase_price=data.purchasePrice,
            order_id=data.orderId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/scan", tags=["双码追溯模块"])
async def scan_trace(
    data: ScanTraceRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """扫码追溯(箱码/生命码)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.scan_trace(
            code=data.code,
            user_id=data.userId,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
            scan_type=data.scanType,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/chain/{code}", tags=["双码追溯模块"])
async def get_trace_chain(code: str):
    """查询追溯链"""
    try:
        result = await _service.get_trace_chain(code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 防窜货 ---

@router.post("/api/trace/anti-channel", tags=["双码追溯模块"])
async def detect_anti_channel(
    data: AntiChannelRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """防窜货检测"""
    _require_member_id(x_member_id)
    try:
        result = await _service.detect_anti_channel(
            life_code=data.lifeCode,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 转让 ---

@router.post("/api/trace/transfer", tags=["双码追溯模块"])
async def transfer_life_code(
    data: TransferRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """生命码转让(持有人变更)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.transfer_life_code(
            life_code=data.lifeCode,
            from_user_id=data.fromUserId,
            to_user_id=data.toUserId,
            to_name=data.toName,
            transfer_type=data.transferType,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 扫码记录 ---

@router.get("/api/trace/scan-logs", tags=["双码追溯模块"])
async def list_scan_logs(
    code: str = Query(None, description="按码筛选"),
    user_id: int = Query(None, description="按用户筛选"),
    scan_type: str = Query(None, description="按类型筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询扫码记录列表"""
    try:
        result = await _service.list_scan_logs(code, user_id, scan_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 统计 ---

@router.get("/api/trace/stats", tags=["双码追溯模块"])
async def get_stats(
    batch_no: str = Query(None, description="按批次筛选"),
    x_role: str = Header(None, alias="X-Role"),
):
    """追溯统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats(batch_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 代理商箱级库存(P1-6: 入库/出库/看板/盘点/预警)
# ============================================================

def _require_agent(x_agent_id: str | None, agent_id: int):
    """代理商自校验: X-Agent-Id 头须与目标 agent_id 一致(admin 放行)"""
    if x_agent_id is None:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Agent-Id 头")
    if str(x_agent_id) != str(agent_id):
        raise HTTPException(status_code=403, detail="无权操作其他代理商的库存")


class AgentInboundRequest(PydBaseModel):
    boxCodes: list[str] = Field(..., min_items=1, max_items=200,
                                description="箱码列表(TBC 箱顶码或 BBC 箱底码)")
    location: str = Field("", description="入库位置(仓库地址)")
    purchaseId: str = Field("", description="关联进货单号(打通 agent 进货断层)")
    operatorId: int | None = Field(None, description="操作人ID")


class AgentOutboundRequest(PydBaseModel):
    boxCodes: list[str] = Field(..., min_items=1, max_items=200,
                                description="出库箱码列表")
    target: str = Field("", description="出库去向(门店/终端名称)")
    reason: str = Field("sale", description="出库原因(sale/transfer/return)")


class AgentStocktakeRequest(PydBaseModel):
    actualBoxCodes: list[str] = Field(..., description="实盘箱码清单(TBC/BBC 均可)")
    safetyStock: int | None = Field(None, ge=0, description="安全库存(箱, 缺省默认)")


@router.post("/api/trace/agent/{agent_id}/inbound", tags=["双码追溯模块"])
async def agent_inbound(
    agent_id: int,
    data: AgentInboundRequest,
    x_agent_id: str = Header(None, alias="X-Agent-Id"),
):
    """代理商箱级入库(扫箱底码 BBC 批量入库, 设计文档 4.2)

    校验: 箱码已绑定(bound)且归属该代理商; 重复入库幂等跳过。
    """
    _require_agent(x_agent_id, agent_id)
    try:
        result = await _service.agent_inbound(
            agent_id, data.boxCodes,
            location=data.location,
            operator_id=data.operatorId,
            purchase_id=data.purchaseId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/agent/{agent_id}/outbound", tags=["双码追溯模块"])
async def agent_outbound(
    agent_id: int,
    data: AgentOutboundRequest,
    x_agent_id: str = Header(None, alias="X-Agent-Id"),
):
    """代理商箱级出库(发货门店/终端, 仅已入库未出库箱可出)"""
    _require_agent(x_agent_id, agent_id)
    try:
        result = await _service.agent_outbound(
            agent_id, data.boxCodes,
            target=data.target, reason=data.reason,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/agent/{agent_id}/inventory", tags=["双码追溯模块"])
async def agent_inventory(
    agent_id: int,
    x_agent_id: str = Header(None, alias="X-Agent-Id"),
):
    """代理商库存看板(箱级在库/出库/开箱 + 瓶级折算 + 批次分布 + 防窜统计)"""
    _require_agent(x_agent_id, agent_id)
    try:
        result = await _service.agent_inventory_dashboard(agent_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/agent/{agent_id}/stocktake", tags=["双码追溯模块"])
async def agent_stocktake(
    agent_id: int,
    data: AgentStocktakeRequest,
    x_agent_id: str = Header(None, alias="X-Agent-Id"),
):
    """代理商库存盘点(实盘箱码清单 vs 系统在库, 产出盘盈/盘亏差异单)"""
    _require_agent(x_agent_id, agent_id)
    try:
        result = await _service.agent_stocktake(
            agent_id, data.actualBoxCodes, safety_stock=data.safetyStock)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/agent/{agent_id}/warnings", tags=["双码追溯模块"])
async def agent_warnings(
    agent_id: int,
    safety_stock: int = Query(None, ge=0, description="安全库存阈值(箱)"),
    overstock_days: int = Query(None, ge=1, description="积压阈值(天)"),
    x_agent_id: str = Header(None, alias="X-Agent-Id"),
):
    """代理商库存预警(库存不足/积压滞留/临期回收 三类)"""
    _require_agent(x_agent_id, agent_id)
    try:
        result = await _service.agent_inventory_warnings(
            agent_id, safety_stock=safety_stock,
            overstock_days=overstock_days)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 窜货分级处罚(P1-7, 设计文档 5.3: 轻微/一般/严重/极重)
# ============================================================

class AntiChannelPunishRequest(PydBaseModel):
    agentId: int = Field(..., description="被处罚代理商ID")
    crossBoxCount: int | None = Field(
        None, ge=1, description="跨区开箱箱数(缺省自动统计该代理商跨区箱)")
    violationLevel: str | None = Field(
        None, description="处罚分级 minor/moderate/severe/extreme"
                          "(缺省按箱数自动定级; extreme 须人工显式指定)")
    remark: str = Field("", description="处罚备注(取证说明等)")


@router.post("/api/trace/anti-channel/punish", tags=["双码追溯模块"])
async def anti_channel_punish(
    data: AntiChannelPunishRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """窜货分级处罚执行(管理员, 设计文档 5.3)

    分级: minor(1-2 箱, 警告) / moderate(3-10 箱, 扣返利10%+保证金20%) /
    severe(>10 箱, 扣返利30%+保证金50%) / extreme(恶意窜货, 取消资格+保证金清零)。
    """
    _require_admin(x_role)
    try:
        result = await _service.anti_channel_punish(
            agent_id=data.agentId,
            cross_box_count=data.crossBoxCount,
            violation_level=data.violationLevel,
            handled_by="admin",
            remark=data.remark,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/anti-channel/penalties/{agent_id}", tags=["双码追溯模块"])
async def agent_penalties(
    agent_id: int,
    x_agent_id: str = Header(None, alias="X-Agent-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询代理商窜货处罚单记录(admin 或代理商本人)"""
    if x_role != "admin":
        _require_agent(x_agent_id, agent_id)
    try:
        result = await _service.list_agent_penalties(agent_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/anti-channel/warnings", tags=["双码追溯模块"])
async def anti_channel_warnings(
    x_role: str = Header(None, alias="X-Role"),
):
    """全代理商防窜预警汇总(管理员巡检: 跨区箱数/批次/建议分级)"""
    _require_admin(x_role)
    try:
        result = await _service.anti_channel_warning_summary()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_trace_routes(app):
    """注册双码追溯模块路由"""
    app.include_router(router)
