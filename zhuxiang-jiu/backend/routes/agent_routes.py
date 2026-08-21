"""代理商管理路由(10 端点)

覆盖 3 个业务域:
    - 准入管理(3):  apply / audit / applications
    - 档案管理(4):  list / detail / update / levels
    - 进货管理(3):  purchase / purchases(list) / purchases(detail)

鉴权:
    - 管理端: X-Role: admin(audit / applications)
    - 用户端: X-Agent-Id 头标识当前代理商(进货/档案查询/更新)
    - 公开: apply(申请入驻) / levels(等级体系)

异常映射(遵循项目约定):
    KeyError   → 404(资源不存在)
    ValueError → 409(业务冲突: 状态异常/参数非法/余额不足等)

注意: 路由声明顺序 — 静态 GET 路径(list/levels/applications)必须
      声明在参数化路径 /api/agent/{agentId} 之前, 避免被参数路由吞掉。
"""

from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.agent_service import AgentService

router = APIRouter()
_service = AgentService()


# ============================================================
#  请求模型
# ============================================================

class AgentApplyRequest(PydBaseModel):
    companyName: str = Field(..., description="公司名")
    contactName: str = Field(..., description="联系人")
    contactPhone: str = Field(..., description="联系电话")
    region: str = Field(..., description="代理区域")
    applyLevel: str = Field(..., description="申请等级 S/A/B/C/D")


class AgentAuditRequest(PydBaseModel):
    decision: str = Field(..., description="审核决定: approved/rejected")
    auditRemark: str = Field("", description="审核备注")


class AgentUpdateRequest(PydBaseModel):
    contactName: Optional[str] = None
    contactPhone: Optional[str] = None
    address: Optional[str] = None
    class Config:
        extra = "allow"


class PurchaseItem(PydBaseModel):
    productId: Any
    quantity: int = Field(..., gt=0)


class AgentPurchaseRequest(PydBaseModel):
    items: List[PurchaseItem] = Field(..., min_length=1)


class RebateCalcRequest(PydBaseModel):
    purchaseAmount: float = Field(..., ge=0, description="月度进货额(元)")
    period: Optional[str] = Field(None, description="账期 YYYY-MM, 默认当前月")


class RebateWithdrawRequest(PydBaseModel):
    rebateId: str = Field(..., description="返利记录 ID")


# ============================================================
#  鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str):
    """校验管理员权限(X-Role: admin)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_agent_id(x_agent_id: str) -> int:
    """从 X-Agent-Id 头提取代理商ID, 缺失/格式错误返回 401"""
    if not x_agent_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Agent-Id 头")
    try:
        return int(x_agent_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Agent-Id 须为数字")


def _map_key_error(exc: KeyError) -> HTTPException:
    """KeyError → 404"""
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    """ValueError → 409"""
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc):
    """统一异常映射(对齐 finance/order_routes 风格)"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc) from exc
    if isinstance(exc, ValueError):
        raise _map_value_error(exc) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================================
#  准入管理(3 端点)
# ============================================================

@router.post("/api/agent/apply", tags=["代理商服务"])
async def agent_apply(req: AgentApplyRequest):
    """申请入驻(提交资料: 公司名/联系人/电话/区域/申请等级)"""
    try:
        return await _service.apply(
            req.companyName, req.contactName, req.contactPhone,
            req.region, req.applyLevel,
        )
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/agent/audit/{apply_id}", tags=["代理商服务"])
async def agent_audit(
    apply_id: int,
    req: AgentAuditRequest,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """审核申请(admin; 通过则创建代理商档案, 拒绝则记录备注)"""
    _require_admin(x_role)
    try:
        return await _service.audit(apply_id, req.decision, req.auditRemark)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/agent/applications", tags=["代理商服务"])
async def agent_applications(
    status: Optional[str] = Query(default=None,
        description="状态筛选 pending/approved/rejected"),
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """申请列表(admin, 支持状态筛选)"""
    _require_admin(x_role)
    return await _service.list_applications(status)


# ============================================================
#  档案管理(4 端点) — 静态路径须先于 {agent_id} 声明
# ============================================================

@router.get("/api/agent/levels", tags=["代理商服务"])
async def agent_levels():
    """等级体系说明(S/A/B/C/D 的权益与进货折扣)"""
    return await _service.get_levels()


@router.get("/api/agent/list", tags=["代理商服务"])
async def agent_list(
    level: Optional[str] = Query(default=None, description="等级筛选 S/A/B/C/D"),
    status: Optional[str] = Query(default=None,
        description="状态筛选 active/suspended/terminated"),
    page: int = Query(1, ge=1, description="页码(从 1 起)"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    """代理商列表(支持等级/状态筛选 + 分页)"""
    return await _service.list_agents(level=level, status=status,
                                       page=page, page_size=page_size)


@router.get("/api/agent/rebate/tiers", tags=["代理商服务"])
async def agent_rebate_tiers():
    """返利档位说明(T0-T3 规则, 超额累进制)"""
    return await _service.get_rebate_tiers()


@router.get("/api/agent/risk/alerts", tags=["代理商服务"])
async def agent_risk_alerts(
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """窜货预警列表(admin, 跨区域销售检测)"""
    _require_admin(x_role)
    return await _service.risk_alerts()


@router.get("/api/agent/{agent_id}", tags=["代理商服务"])
async def agent_detail(agent_id: int):
    """代理商详情"""
    try:
        return await _service.get_detail(agent_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.put("/api/agent/{agent_id}", tags=["代理商服务"])
async def agent_update(
    agent_id: int,
    req: AgentUpdateRequest,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """更新代理商资料(联系人/电话/地址; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可更新自身代理商资料")
    try:
        return await _service.update_profile(
            agent_id,
            contact_name=req.contactName,
            contact_phone=req.contactPhone,
            address=req.address,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  进货管理(3 端点)
# ============================================================

@router.post("/api/agent/{agent_id}/purchase", tags=["代理商服务"])
async def agent_purchase(
    agent_id: int,
    req: AgentPurchaseRequest,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """进货下单(关联产品+库存, 扣减代理商钱包; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可为自身代理商进货")
    try:
        items = [item.model_dump() for item in req.items]
        return await _service.purchase(agent_id, items)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/agent/{agent_id}/purchases", tags=["代理商服务"])
async def agent_purchases(
    agent_id: int,
    page: int = Query(1, ge=1, description="页码(从 1 起)"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """进货记录(分页; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可查询自身进货记录")
    try:
        return await _service.list_purchases(agent_id, page=page, page_size=page_size)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.get("/api/agent/{agent_id}/purchases/{purchase_id}", tags=["代理商服务"])
async def agent_purchase_detail(
    agent_id: int,
    purchase_id: str,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """进货明细(需 X-Agent-Id 自身; 越权访问返回 404)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可查询自身进货明细")
    try:
        return await _service.get_purchase_detail(agent_id, purchase_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  返利结算管理(4 端点, 需 X-Agent-Id 自身)
# ============================================================

@router.post("/api/agent/{agent_id}/rebate/calc", tags=["代理商服务"])
async def agent_rebate_calc(
    agent_id: int,
    req: RebateCalcRequest,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """返利计算(超额累进制, 基于月度进货额; 需 X-Agent-Id 自身)

    生成返利记录(status=pending), 等待提现。
    """
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可计算自身代理商返利")
    try:
        return await _service.rebate_calc(
            agent_id, req.purchaseAmount, period=req.period)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/agent/{agent_id}/rebates", tags=["代理商服务"])
async def agent_rebates(
    agent_id: int,
    page: int = Query(1, ge=1, description="页码(从 1 起)"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    status: Optional[str] = Query(default=None,
        description="状态筛选 pending/withdrawn"),
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """返利记录列表(分页; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可查询自身返利记录")
    try:
        return await _service.list_rebates(
            agent_id, page=page, page_size=page_size, status=status)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/agent/{agent_id}/rebate/withdraw", tags=["代理商服务"])
async def agent_rebate_withdraw(
    agent_id: int,
    req: RebateWithdrawRequest,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """返利提现(转入钱包; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可提现自身代理商返利")
    try:
        return await _service.rebate_withdraw(agent_id, req.rebateId)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/agent/{agent_id}/rebate/summary", tags=["代理商服务"])
async def agent_rebate_summary(
    agent_id: int,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """返利汇总(本年累计/本月/可提现; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可查询自身返利汇总")
    try:
        return await _service.rebate_summary(agent_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  风控管理(2 端点)
# ============================================================

@router.get("/api/agent/{agent_id}/risk/report", tags=["代理商服务"])
async def agent_risk_report(
    agent_id: int,
    x_agent_id: Annotated[Optional[str], Header(alias="X-Agent-Id")] = None,
):
    """风控报告(信用评分 + 异常指标; 需 X-Agent-Id 自身)"""
    requester = _require_agent_id(x_agent_id)
    if requester != agent_id:
        raise HTTPException(status_code=403, detail="仅可查询自身风控报告")
    try:
        return await _service.risk_report(agent_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/agent/{agent_id}/risk/assess", tags=["代理商服务"])
async def agent_risk_assess(
    agent_id: int,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """信用评级(基于进货/退货/付款记录; admin)"""
    _require_admin(x_role)
    try:
        return await _service.risk_assess(agent_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  注册函数
# ============================================================

def register_agent_routes(app):
    """注册代理商管理端点到 FastAPI app"""
    app.include_router(router)
