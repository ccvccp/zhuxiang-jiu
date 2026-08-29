"""AI智能管理模块路由(角色经济中枢, 34 端点)

鉴权(对齐 ticket_routes 风格):
    - 用户端(8): X-Member-Id 头(目录/认领/契约/收益/信用事件)
    - 管理端(23): X-Role 头, 仅 admin(目录维护/审批/契约动作/总账/风控/
      追回/重试/试用sweep/记账/工人分润含自动结算/AI监管扫描/预警处置/
      季度联合结算)
    - 客服(2): grab 抢单池/抢单(X-Role: admin/cs_staff)
    - 结算(2): service-profit/settle 与 clawback 查询(X-Role: admin/cs_staff)
    - 内部(1): dispatch 调度中枢(X-Role: admin/cs_staff)

异常映射(遵循项目约定):
    - KeyError → 404(目录/申请/契约/工单/批次/预警不存在)
    - ValueError → 409(状态非法/重复认领/已结算等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 目录(2):   catalog / admin upsert
    - 认领(3):   claim / my claims / admin claims+review
    - 契约(4):   sign / my contracts / terminate / admin action
    - 收益(4):   my earnings / my credit-events / admin ledger / risk-summary
    - 客服分润(4): settle(幂等) / retry / reverse(退款追回) / clawback查询
    - 契约治理(1): probation-sweep(试用期满自动转正)
    - 统一记账(1): ledger/record(外部模块回写, P1)
    - 工人分润(4): settle / preview / settle-auto / preview-auto
      (P1+订单-批次自动关联: 生命码激活回写 orderId)
    - AI监管(5): 满意度风险扫描 / 异常分润检测 / 信用异动扫描 /
      预警列表 / 预警处置(P2)
    - 抢单(2):   grab tickets(工单池) / grab ticket(抢单)(P2)
    - 季度结算(1): quarterly-joint-settle(溢出池联合发放)(P2)
    - 派单(1):   dispatch(调度中枢测试入口)
    - 事件(1):   credit event 发布(内部)
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.role_service import RoleService


router = APIRouter()
_service = RoleService()


# ============================================================
# 鉴权与异常映射辅助(对齐 ticket_routes 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 格式不正确") from None


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_staff(x_role: str | None):
    """校验客服/管理员权限(结算触发方), 失败返回 403"""
    if x_role not in ("admin", "cs_staff"):
        raise HTTPException(status_code=403, detail="需要客服或管理员权限")


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

class ClaimRoleRequest(PydBaseModel):
    roleCode: str = Field(..., description="角色编码(customer_service/production_worker/agent等)")
    statement: str = Field("", max_length=1000, description="认领陈述(可选)")


class ReviewClaimRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    reviewer: str = Field("admin", description="审批人")
    comment: str = Field("", max_length=500, description="审批意见")


class SignContractRequest(PydBaseModel):
    claimId: int = Field(..., description="认领申请ID")


class ContractActionRequest(PydBaseModel):
    action: str = Field(..., description="动作: activate/suspend/terminate")


class UpsertCatalogRequest(PydBaseModel):
    roleCode: str = Field(..., max_length=50, description="角色编码")
    roleName: str = Field(..., max_length=50, description="角色名称")
    category: str = Field("other", description="分类: consumer/service/production/channel/traffic")
    claimConditions: str = Field("", max_length=500, description="认领条件")
    creditThreshold: int = Field(400, ge=0, le=1000, description="信用门槛(竹信分)")
    quota: int = Field(0, ge=0, description="配额(0=不限)")
    profitDesc: str = Field("", max_length=500, description="分润说明")
    dutyTerms: str = Field("", max_length=500, description="责任条款")
    status: str = Field("active", description="状态: active/disabled")


class SettleProfitRequest(PydBaseModel):
    ticketNo: str = Field(..., description="工单号")
    orderAmount: float = Field(None, gt=0, description="订单实际销售价格(工单未关联订单时显式提供)")


class ReverseProfitRequest(PydBaseModel):
    ticketNo: str = Field(..., description="工单号")
    reason: str = Field("订单退款", max_length=200, description="追回原因")
    operator: str = Field("admin", max_length=50, description="操作人")


class RecordLedgerRequest(PydBaseModel):
    ledgerNo: str = Field(..., max_length=120, description="流水号(幂等键, 如 VEN-1-xxx-agent)")
    sourceModule: str = Field(..., max_length=50, description="来源模块(venue/agent/traffic等)")
    roleCode: str = Field(..., max_length=50, description="角色编码(partner/agent/promoter/platform等)")
    userId: int = Field(..., description="收益方用户ID(平台份额传0)")
    basis: str = Field(..., description="口径: sale_price/diff_profit/purchase_amount")
    base: float = Field(..., ge=0, description="计算基数")
    rate: float = Field(..., ge=0, le=1, description="费率/比例")
    amount: float = Field(..., ge=0, description="分润金额")
    refNo: str = Field("", max_length=100, description="关联单号(合作商ID/账期/订单号等)")
    note: str = Field("", max_length=200, description="备注")


class WorkerProfitSettleRequest(PydBaseModel):
    batchNo: str = Field(..., max_length=50, description="生产批次号(须已放行)")
    orderAmount: float = Field(..., gt=0, description="关联订单实际销售价格")
    qualityGrade: str = Field("pass", description="质量等级: pass/premium/accident")


class WorkerProfitAutoRequest(PydBaseModel):
    batchNo: str = Field(..., max_length=50, description="生产批次号(须已放行)")
    qualityGrade: str = Field("pass", description="质量等级: pass/premium/accident")


class GrabTicketRequest(PydBaseModel):
    ticketNo: str = Field(..., max_length=30, description="工单号(须为pending状态)")
    csUserId: int = Field(..., description="抢单客服用户ID")


class QuarterlyJointSettleRequest(PydBaseModel):
    year: int = Field(..., ge=2020, le=2100, description="年份")
    quarter: int = Field(..., ge=1, le=4, description="季度 1-4")
    operator: str = Field("admin", max_length=50, description="操作人")


class ResolveAlertRequest(PydBaseModel):
    operator: str = Field("admin", max_length=50, description="处置人")
    resolution: str = Field("", max_length=500, description="处置说明")


class DispatchRequest(PydBaseModel):
    sessionId: str = Field(..., description="会话ID")
    userId: int = Field(0, description="会话用户ID")
    reason: str = Field("", max_length=200, description="转接原因")


class CreditEventRequest(PydBaseModel):
    userId: int = Field(..., description="用户ID")
    roleCode: str = Field(..., description="角色编码")
    behavior: str = Field(..., description="行为码(如 cs_satisfaction_good)")
    sourceModule: str = Field("role", description="来源模块")
    refId: str = Field("", description="关联单号")


# ============================================================
# 用户端接口(8)
# ============================================================

@router.get("/api/role/catalog", tags=["AI智能管理模块"])
async def list_catalog(
    status: str = Query(None, description="状态筛选 active/disabled"),
):
    """角色目录(含认领条件/配额/分润说明)"""
    try:
        result = await _service.list_catalog(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/claim", tags=["AI智能管理模块"])
async def create_claim(
    data: ClaimRoleRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交角色认领申请(AI预审: 信用门槛/配额/重复认领)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.create_claim(
            user_id=member_id, role_code=data.roleCode,
            statement=data.statement)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/my/claims", tags=["AI智能管理模块"])
async def list_my_claims(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的认领申请列表"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_my_claims(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/contracts/sign", tags=["AI智能管理模块"])
async def sign_contract(
    data: SignContractRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """签署权责利三合一契约(审批通过后; 签约即进试用期)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.sign_contract(
            claim_id=data.claimId, user_id=member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/my/contracts", tags=["AI智能管理模块"])
async def list_my_contracts(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    roleCode: str = Query(None, description="角色编码筛选"),
    status: str = Query(None, description="状态筛选 probation/active/suspended/terminated"),
):
    """我的契约列表(含试用期到期动态标记)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_contracts(
            user_id=member_id, role_code=roleCode, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/my/contracts/{contract_id}/terminate", tags=["AI智能管理模块"])
async def terminate_contract(
    contract_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """主动退出契约(未入账分润留待人工结算)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.terminate_contract(
            contract_id=contract_id, user_id=member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/my/earnings", tags=["AI智能管理模块"])
async def list_my_earnings(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的分润流水(含系数明细)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_my_earnings(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/role/my/credit-events", tags=["AI智能管理模块"])
async def list_my_credit_events(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的信用行为记录(信用总线事件)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_my_credit_events(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 管理端接口(9)
# ============================================================

@router.post("/api/role/admin/catalog", tags=["AI智能管理模块"])
async def upsert_catalog(
    data: UpsertCatalogRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """维护角色目录(新增/更新)"""
    _require_admin(x_role)
    try:
        result = await _service.upsert_catalog_role(
            role_code=data.roleCode, role_name=data.roleName,
            category=data.category,
            claim_conditions=data.claimConditions,
            credit_threshold=data.creditThreshold, quota=data.quota,
            profit_desc=data.profitDesc, duty_terms=data.dutyTerms,
            status=data.status)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/admin/claims", tags=["AI智能管理模块"])
async def admin_list_claims(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选 pending/approved/rejected"),
    roleCode: str = Query(None, description="角色编码筛选"),
):
    """认领申请列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_list_claims(
            status=status, role_code=roleCode)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/admin/claims/{claim_id}/review", tags=["AI智能管理模块"])
async def admin_review_claim(
    claim_id: int,
    data: ReviewClaimRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """审批认领申请(pending → approved/rejected)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_review_claim(
            claim_id=claim_id, approved=data.approved,
            reviewer=data.reviewer, comment=data.comment)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/admin/contracts", tags=["AI智能管理模块"])
async def admin_list_contracts(
    x_role: str = Header(None, alias="X-Role"),
    roleCode: str = Query(None, description="角色编码筛选"),
    status: str = Query(None, description="状态筛选"),
):
    """契约列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_contracts(
            role_code=roleCode, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/admin/contracts/{contract_id}/action", tags=["AI智能管理模块"])
async def admin_contract_action(
    contract_id: int,
    data: ContractActionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """契约管理动作: activate(转正)/suspend(冻结)/terminate(清退)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_contract_action(
            contract_id=contract_id, action=data.action)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/admin/ledger", tags=["AI智能管理模块"])
async def admin_list_ledger(
    x_role: str = Header(None, alias="X-Role"),
    userId: int = Query(None, description="用户ID筛选"),
    roleCode: str = Query(None, description="角色编码筛选"),
    basis: str = Query(None, description="分润口径筛选 sale_price/diff_profit"),
    status: str = Query(None, description="状态筛选 pending/settled/reversed"),
):
    """分润总账查询(双轨口径筛选)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_list_ledger(
            user_id=userId, role_code=roleCode, basis=basis, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/role/admin/risk-summary", tags=["AI智能管理模块"])
async def get_risk_summary(
    x_role: str = Header(None, alias="X-Role"),
):
    """AI风控汇总(总账/契约/认领/负面事件统计)"""
    _require_admin(x_role)
    try:
        result = await _service.get_risk_summary()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 结算与内部接口(2+1)
# ============================================================

@router.post("/api/role/service-profit/settle", tags=["AI智能管理模块"])
async def settle_service_profit(
    data: SettleProfitRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """工单满意度确认后即时结算服务分润(幂等, D-8 参数)

    满意度≥4星且关联订单才产生分润; ≤3星为0分润+信用扣减。
    """
    _require_staff(x_role)
    try:
        result = await _service.settle_service_profit(
            ticket_no=data.ticketNo, order_amount=data.orderAmount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/service-profit/retry", tags=["AI智能管理模块"])
async def retry_ledger_settlement(
    data: SettleProfitRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """重试 pending 总账入钱包(钱包开通后)"""
    _require_admin(x_role)
    try:
        ledger_no = f"SVC-{data.ticketNo}"
        result = await _service.retry_ledger_settlement(ledger_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/service-profit/reverse", tags=["AI智能管理模块"])
async def reverse_service_profit(
    data: ReverseProfitRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """订单退款后追回服务分润(D-8 细化: 记负账, 下月抵扣; 负账>¥500冻结接单)"""
    _require_admin(x_role)
    try:
        result = await _service.reverse_service_profit(
            ticket_no=data.ticketNo, reason=data.reason,
            operator=data.operator)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/service-profit/clawback/{user_id}", tags=["AI智能管理模块"])
async def get_clawback(
    user_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询客服负账余额与接单冻结状态"""
    _require_staff(x_role)
    try:
        result = await _service.get_clawback(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/admin/probation-sweep", tags=["AI智能管理模块"])
async def admin_probation_sweep(
    x_role: str = Header(None, alias="X-Role"),
):
    """试用期满自动转正扫描(近30天有负面信用事件者保持试用留人工处置)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_probation_sweep()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/ledger/record", tags=["AI智能管理模块"])
async def record_ledger(
    data: RecordLedgerRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """统一分润总账记账(服务间调用/手工补账, 幂等: 同流水号不重复记账)"""
    _require_admin(x_role)
    try:
        result = await _service.record_external_settlement(
            ledger_no=data.ledgerNo, source_module=data.sourceModule,
            role_code=data.roleCode, user_id=data.userId, basis=data.basis,
            base=data.base, rate=data.rate, amount=data.amount,
            ref_no=data.refNo, note=data.note)
        return {"success": True, "data": result["ledger"],
                "created": result["created"]}
    except Exception as e:
        _handle(e)


@router.post("/api/role/worker-profit/settle", tags=["AI智能管理模块"])
async def settle_worker_profit(
    data: WorkerProfitSettleRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """生产工人分润结算(P1: 批次维度幂等; 工人取各工段生命码打卡留痕责任人)"""
    _require_admin(x_role)
    try:
        result = await _service.settle_worker_profit(
            batch_no=data.batchNo, order_amount=data.orderAmount,
            quality_grade=data.qualityGrade)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/worker-profit/preview/{batch_no}", tags=["AI智能管理模块"])
async def preview_worker_profit(
    batch_no: str,
    x_role: str = Header(None, alias="X-Role"),
    orderAmount: float = Query(..., gt=0, description="订单实际销售价格"),
    qualityGrade: str = Query("pass", description="质量等级: pass/premium/accident"),
):
    """工人分润预演(只读, 不写账不入钱包)"""
    _require_admin(x_role)
    try:
        result = await _service.preview_worker_profit(
            batch_no=batch_no, order_amount=orderAmount,
            quality_grade=qualityGrade)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/worker-profit/settle-auto", tags=["AI智能管理模块"])
async def settle_worker_profit_auto(
    data: WorkerProfitAutoRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """工人分润自动结算(订单额自动取数: 批次经激活生命码关联的订单实售价合计, 幂等)"""
    _require_admin(x_role)
    try:
        result = await _service.settle_worker_profit_auto(
            batch_no=data.batchNo, quality_grade=data.qualityGrade)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/worker-profit/preview-auto/{batch_no}", tags=["AI智能管理模块"])
async def preview_worker_profit_auto(
    batch_no: str,
    x_role: str = Header(None, alias="X-Role"),
    qualityGrade: str = Query("pass", description="质量等级: pass/premium/accident"),
):
    """工人分润自动预演(订单额自动取数, 只读)"""
    _require_admin(x_role)
    try:
        result = await _service.preview_worker_profit_auto(
            batch_no=batch_no, quality_grade=qualityGrade)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# AI监管大脑(P2)
# ============================================================

@router.post("/api/role/ai-brain/scan/satisfaction", tags=["AI智能管理模块"])
async def scan_satisfaction_risk(
    x_role: str = Header(None, alias="X-Role"),
):
    """满意度风险扫描(进行中人工会话: 情绪+交互特征→风险分, 高风险生成干预预警)"""
    _require_admin(x_role)
    try:
        result = await _service.scan_satisfaction_risk()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/ai-brain/scan/anomaly", tags=["AI智能管理模块"])
async def scan_profit_anomaly(
    x_role: str = Header(None, alias="X-Role"),
):
    """异常分润检测(金额离群/同人高频/顶格频发, 生成预警)"""
    _require_admin(x_role)
    try:
        result = await _service.scan_profit_anomaly()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/ai-brain/scan/credit-drop", tags=["AI智能管理模块"])
async def scan_credit_drop(
    x_role: str = Header(None, alias="X-Role"),
):
    """信用异动扫描(7天下滑≥100→冻结接单+预警待人工复核)"""
    _require_admin(x_role)
    try:
        result = await _service.scan_credit_drop()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/role/ai-brain/alerts", tags=["AI智能管理模块"])
async def list_alerts(
    x_role: str = Header(None, alias="X-Role"),
    alertType: str = Query(None, description="类型筛选 satisfaction_risk/profit_anomaly/credit_drop"),
    status: str = Query(None, description="状态筛选 open/resolved"),
):
    """监管预警列表"""
    _require_admin(x_role)
    try:
        result = await _service.list_alerts(alert_type=alertType,
                                            status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/ai-brain/alerts/{alert_id}/resolve", tags=["AI智能管理模块"])
async def resolve_alert(
    alert_id: int,
    data: ResolveAlertRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """处置预警(open→resolved, 信用异动冻结者复核后可 activate 契约恢复接单)"""
    _require_admin(x_role)
    try:
        result = await _service.resolve_alert(
            alert_id=alert_id, operator=data.operator,
            resolution=data.resolution)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 抢单模式 + 季度联合结算(P2)
# ============================================================

@router.get("/api/role/grab/tickets", tags=["AI智能管理模块"])
async def list_grabbable_tickets(
    x_role: str = Header(None, alias="X-Role"),
):
    """待抢单工单池(pending 状态工单)"""
    _require_staff(x_role)
    try:
        result = await _service.list_grabbable_tickets()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/role/grab/ticket", tags=["AI智能管理模块"])
async def grab_ticket(
    data: GrabTicketRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """客服抢单(先到先得, 锁内防双抢; 须持有效契约且未被负账冻结)"""
    _require_staff(x_role)
    try:
        result = await _service.grab_ticket(
            ticket_no=data.ticketNo, cs_user_id=data.csUserId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/quarterly-joint-settle", tags=["AI智能管理模块"])
async def quarterly_joint_settle(
    data: QuarterlyJointSettleRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """季度联合结算(各客服月度封顶溢出额合并一次性入账, 幂等)"""
    _require_admin(x_role)
    try:
        result = await _service.quarterly_joint_settle(
            year=data.year, quarter=data.quarter, operator=data.operator)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/dispatch", tags=["AI智能管理模块"])
async def dispatch_service(
    data: DispatchRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """AI服务调度中枢派单入口(创建source=ai工单+最优客服分配)

    chat 模块转人工时内部调用; 此端点用于测试与运营手动触发。
    """
    _require_staff(x_role)
    try:
        session = {"sessionId": data.sessionId, "userId": data.userId}
        result = await _service.dispatch_customer_service(
            session, reason=data.reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/role/credit/event", tags=["AI智能管理模块"])
async def publish_credit_event(
    data: CreditEventRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """信用行为事件发布(服务间调用: 各模块行为统一入信用总线)"""
    _require_admin(x_role)
    try:
        result = await _service.publish_credit_event(
            user_id=data.userId, role_code=data.roleCode,
            behavior=data.behavior, source_module=data.sourceModule,
            ref_id=data.refId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_role_routes(app):
    """注册AI智能管理模块路由"""
    app.include_router(router)
