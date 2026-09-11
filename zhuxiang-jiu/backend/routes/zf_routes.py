"""智法·AI智能法务大模型路由(P0-P3 全量, 24 端点)

24号「合规合法智能监控模块」升级更名: 智法·AI智能法务大模型
(产-销-法一体化智能合规中枢)。
鉴权: 全部管理端 X-Role: admin(法务敏感域)。
既有 /api/compliance/* 16 端点保留不动(向后兼容)。

端点分布:
    - 生产合规 P0:  process-check(GET+POST) / passport(GET+POST)
                    / passport/{batch_id}(验证) / esg / quality-risk
    - 供应链金融 P1: credit-assess / credit/{id} / contract-generate
                    / contracts / fund-monitor
    - 数据资产 P2:  classify / catalogs / license-generate
                    / cross-border-assess
    - 电商深化 P3:  price-audit / presale-guard / anti-blackmail
    - 进化闭环 P3:  feedback / feedbacks / precedents / twin / status

异常映射: KeyError → 404 / ValueError → 409 / PermissionError → 403
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.zf_fabric_service import _ZfStore
from services.zf_production_service import ZfProductionService
from services.zf_finance_service import ZfFinanceService
from services.zf_asset_service import ZfAssetService
from services.zf_commerce_service import ZfCommerceService
from services.zf_evolution_service import ZfEvolutionService

router = APIRouter()
_store = _ZfStore()
_production = ZfProductionService(store=_store)
_finance = ZfFinanceService(store=_store)
_asset = ZfAssetService(store=_store)
_commerce = ZfCommerceService(store=_store)
_evolution = ZfEvolutionService(store=_store)


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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

class ProcessCheckRequest(PydBaseModel):
    batchId: str = Field(..., min_length=1, max_length=40)
    params: dict = Field(..., description="工艺参数(fermentation_days/"
                                          "fermentation_temp/storage_"
                                          "months/additive_count/blend_ratio)")
    operator: str = Field("", max_length=40)


class PassportRequest(PydBaseModel):
    batchId: str = Field(..., min_length=1, max_length=40)
    processParams: dict = Field(default_factory=dict)
    labResults: dict = Field(default_factory=dict,
                             description="理化指标(alcohol/methanol)")
    operator: str = Field(..., min_length=1, max_length=40)
    qualityInsp: str = Field("", max_length=40)


class EsgRequest(PydBaseModel):
    period: str = Field(..., min_length=6, max_length=6,
                        description="YYYYMM")
    energyKwh: float = Field(..., ge=0)
    wastewaterTons: float = Field(..., ge=0)
    recycledRatio: float = Field(0.0, ge=0, le=1)


class CreditAssessRequest(PydBaseModel):
    entityId: str = Field(..., min_length=1, max_length=40)
    entityName: str = Field(..., min_length=1, max_length=60)
    monthlyOrders: float = Field(..., ge=0)
    inventoryValue: float = Field(..., ge=0)
    productionCapacity: float = Field(..., gt=0)
    repaymentRate: float = Field(..., ge=0, le=1)


class ContractGenerateRequest(PydBaseModel):
    entityId: str = Field(..., min_length=1, max_length=40)
    loanAmount: float = Field(..., gt=0)


class FundMonitorRequest(PydBaseModel):
    entityId: str = Field(..., min_length=1, max_length=40)
    contractId: int = Field(..., ge=1)
    flows: list = Field(..., min_length=1,
                        description="[{amount, use}] 定向流向")


class ClassifyRequest(PydBaseModel):
    dataSamples: list = Field(..., min_length=1,
                              description="数据字段名样本")
    source: str = Field("", max_length=40)


class LicenseGenerateRequest(PydBaseModel):
    assetDesc: str = Field(..., min_length=2, max_length=100)
    dataLevel: str = Field(..., description="L1|L2(L3/L4 禁止许可)")
    licensee: str = Field(..., min_length=1, max_length=60)
    revenueShare: float = Field(0.1, gt=0, le=0.5)
    termMonths: int = Field(12, ge=1, le=60)


class CrossBorderRequest(PydBaseModel):
    region: str = Field(..., description="EU|US|OTHER")
    dataLevels: list = Field(..., min_length=1)
    businessPurpose: str = Field("", max_length=100)


class PriceAuditRequest(PydBaseModel):
    productId: str = Field(..., min_length=1, max_length=40)
    priceHistory: list = Field(..., min_length=1,
                               description="[{day, dealPrice}] 近30日")
    current: dict = Field(..., description="{original, strikethrough,"
                                           " coupon, member}")


class PresaleGuardRequest(PydBaseModel):
    productId: str = Field(..., min_length=1, max_length=40)
    termDays: int = Field(..., ge=1, le=365)
    deposit: float = Field(..., gt=0)
    totalPrice: float = Field(..., gt=0)
    presaleType: str = Field("new_wine",
                             description="new_wine|cellar")


class AntiBlackmailRequest(PydBaseModel):
    memberId: int = Field(..., ge=1)
    orderId: str = Field(..., min_length=1, max_length=40)
    complaints90d: int = Field(..., ge=0)
    returnRatio: float = Field(..., ge=0, le=1)
    lawsuitCount: int = Field(0, ge=0)


class FeedbackRequest(PydBaseModel):
    targetType: str = Field(..., description="process_check|credit|"
                                            "passport_verify|price_audit|"
                                            "presale_guard")
    verdict: str = Field(..., description="adopted|corrected|rejected")
    note: str = Field("", max_length=200)


# ============================================================
# P0: 生产合规锚定(admin)
# ============================================================

@router.post("/api/legal/production/process-check",
             tags=["智法AI智能法务大模型"])
async def process_check(data: ProcessCheckRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """工艺合规实时校验(国标限值比对, 违规→处置工单建议)"""
    _require_admin(x_role)
    try:
        result = await _production.process_check(
            batch_id=data.batchId, params=data.params,
            operator=data.operator)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/production/process-checks",
            tags=["智法AI智能法务大模型"])
async def process_checks(x_role: str = Header(None, alias="X-Role"),
                         limit: int = Query(50, ge=1, le=200)):
    """工艺校验记录列表"""
    _require_admin(x_role)
    try:
        rows = await _production.process_checks(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/production/passport",
             tags=["智法AI智能法务大模型"])
async def passport_issue(data: PassportRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """数字产品护照签发(指纹链存证, 前置工艺校验)"""
    _require_admin(x_role)
    try:
        result = await _production.passport(
            batch_id=data.batchId, process_params=data.processParams,
            lab_results=data.labResults, operator=data.operator,
            quality_insp=data.qualityInsp)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/production/passports",
            tags=["智法AI智能法务大模型"])
async def passports(x_role: str = Header(None, alias="X-Role"),
                    limit: int = Query(50, ge=1, le=200)):
    """数字产品护照列表"""
    _require_admin(x_role)
    try:
        rows = await _production.passports(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/production/passport/{batch_id}",
            tags=["智法AI智能法务大模型"])
async def passport_verify(batch_id: str,
                          x_role: str = Header(None, alias="X-Role")):
    """数字产品护照验证(内容哈希重算, 防篡改)"""
    _require_admin(x_role)
    try:
        result = await _production.verify_passport(batch_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/production/esg",
             tags=["智法AI智能法务大模型"])
async def esg_report(data: EsgRequest,
                     x_role: str = Header(None, alias="X-Role")):
    """《碳排放/排污合规报告》(ESG 确定性评分)"""
    _require_admin(x_role)
    try:
        result = await _production.esg_report(
            period=data.period, energy_kwh=data.energyKwh,
            wastewater_tons=data.wastewaterTons,
            recycled_ratio=data.recycledRatio)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/production/quality-risk",
            tags=["智法AI智能法务大模型"])
async def quality_risk(x_role: str = Header(None, alias="X-Role")):
    """质量风险预测(历史不合格×工艺参数偏离, 确定性加权)"""
    _require_admin(x_role)
    try:
        result = await _production.quality_risk()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 供应链金融合规(admin)
# ============================================================

@router.post("/api/legal/finance/credit-assess",
             tags=["智法AI智能法务大模型"])
async def credit_assess(data: CreditAssessRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """供应链信用评估(多维交叉验证+欺诈矛盾检测)"""
    _require_admin(x_role)
    try:
        result = await _finance.credit_assess(
            entity_id=data.entityId, entity_name=data.entityName,
            monthly_orders=data.monthlyOrders,
            inventory_value=data.inventoryValue,
            production_capacity=data.productionCapacity,
            repayment_rate=data.repaymentRate)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/finance/credit/{entity_id}",
            tags=["智法AI智能法务大模型"])
async def credit_file(entity_id: str,
                      x_role: str = Header(None, alias="X-Role")):
    """主体信用档案"""
    _require_admin(x_role)
    try:
        result = await _finance.credit_file(entity_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/finance/contract-generate",
             tags=["智法AI智能法务大模型"])
async def contract_generate(data: ContractGenerateRequest,
                            x_role: str = Header(None, alias="X-Role")):
    """动态合约生成(按信用评级差异化条款)"""
    _require_admin(x_role)
    try:
        result = await _finance.contract_generate(
            entity_id=data.entityId, loan_amount=data.loanAmount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/finance/contracts",
            tags=["智法AI智能法务大模型"])
async def contracts(x_role: str = Header(None, alias="X-Role"),
                    limit: int = Query(50, ge=1, le=200)):
    """动态合约列表"""
    _require_admin(x_role)
    try:
        rows = await _finance.contracts(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/finance/fund-monitor",
             tags=["智法AI智能法务大模型"])
async def fund_monitor(data: FundMonitorRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """资金流向合规监控(定向白名单+违约预警函建议书)"""
    _require_admin(x_role)
    try:
        result = await _finance.fund_monitor(
            entity_id=data.entityId, contract_id=data.contractId,
            flows=data.flows)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 数据资产合规(admin)
# ============================================================

@router.post("/api/legal/asset/classify",
             tags=["智法AI智能法务大模型"])
async def classify(data: ClassifyRequest,
                  x_role: str = Header(None, alias="X-Role")):
    """数据分类分级扫描(个人信息/工艺秘方/商业数据→资产目录)"""
    _require_admin(x_role)
    try:
        result = await _asset.classify(
            data_samples=data.dataSamples, source=data.source)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/asset/catalogs",
            tags=["智法AI智能法务大模型"])
async def catalogs(x_role: str = Header(None, alias="X-Role"),
                   limit: int = Query(20, ge=1, le=100)):
    """数据资产目录列表"""
    _require_admin(x_role)
    try:
        rows = await _asset.catalogs(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/asset/license-generate",
             tags=["智法AI智能法务大模型"])
async def license_generate(data: LicenseGenerateRequest,
                           x_role: str = Header(None, alias="X-Role")):
    """《数据许可协议》生成(三要素; L3/L4 红线禁止)"""
    _require_admin(x_role)
    try:
        result = await _asset.license_generate(
            asset_desc=data.assetDesc, data_level=data.dataLevel,
            licensee=data.licensee, revenue_share=data.revenueShare,
            term_months=data.termMonths)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/asset/cross-border-assess",
             tags=["智法AI智能法务大模型"])
async def cross_border_assess(data: CrossBorderRequest,
                              x_role: str = Header(None,
                                                   alias="X-Role")):
    """《数据出境安全自评估报告》(GDPR/CCPA 匹配)"""
    _require_admin(x_role)
    try:
        result = await _asset.cross_border_assess(
            region=data.region, data_levels=data.dataLevels,
            business_purpose=data.businessPurpose)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 电商合规深化(admin)
# ============================================================

@router.post("/api/legal/commerce/price-audit",
             tags=["智法AI智能法务大模型"])
async def price_audit(data: PriceAuditRequest,
                      x_role: str = Header(None, alias="X-Role")):
    """价格合规动态监测(先涨后降/划线价依据/会员价)"""
    _require_admin(x_role)
    try:
        result = await _commerce.price_audit(
            product_id=data.productId,
            price_history=data.priceHistory, current=data.current)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/commerce/presale-guard",
             tags=["智法AI智能法务大模型"])
async def presale_guard(data: PresaleGuardRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """预售合规护栏(预售协议+风险提示书, 暂行规定校验)"""
    _require_admin(x_role)
    try:
        result = await _commerce.presale_guard(
            product_id=data.productId, term_days=data.termDays,
            deposit=data.deposit, total_price=data.totalPrice,
            presale_type=data.presaleType)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/legal/commerce/anti-blackmail",
             tags=["智法AI智能法务大模型"])
async def anti_blackmail(data: AntiBlackmailRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """职业打假防御(高风险画像+证据固化清单→应诉证据包)"""
    _require_admin(x_role)
    try:
        result = await _commerce.anti_blackmail(
            member_id=data.memberId, order_id=data.orderId,
            complaints_90d=data.complaints90d,
            return_ratio=data.returnRatio,
            lawsuit_count=data.lawsuitCount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 进化闭环(admin)
# ============================================================

@router.post("/api/legal/evolution/feedback",
             tags=["智法AI智能法务大模型"])
async def evolution_feedback(data: FeedbackRequest,
                             x_role: str = Header(None, alias="X-Role")):
    """反馈闭环(严格度 strictness ±0.1, 安全阀 [0.6, 1.4])"""
    _require_admin(x_role)
    try:
        result = await _evolution.feedback(
            target_type=data.targetType, verdict=data.verdict,
            note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/evolution/feedbacks",
            tags=["智法AI智能法务大模型"])
async def evolution_feedbacks(x_role: str = Header(None,
                                                    alias="X-Role"),
                               limit: int = Query(50, ge=1, le=200)):
    """反馈进化记录列表"""
    _require_admin(x_role)
    try:
        rows = await _evolution.feedbacks(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/evolution/precedents",
            tags=["智法AI智能法务大模型"])
async def precedents(x_role: str = Header(None, alias="X-Role")):
    """判例回流(同类酒企败诉案例→规则更新建议)"""
    _require_admin(x_role)
    try:
        rows = await _evolution.precedents()
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/evolution/twin",
            tags=["智法AI智能法务大模型"])
async def twin(x_role: str = Header(None, alias="X-Role")):
    """合规数字孪生总览(四域+三重校验+进化参数)"""
    _require_admin(x_role)
    try:
        result = await _evolution.twin()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/legal/status", tags=["智法AI智能法务大模型"])
async def status(x_role: str = Header(None, alias="X-Role")):
    """智法大模型总览(生产/进化/判例)"""
    _require_admin(x_role)
    try:
        result = await _evolution.status()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_zf_routes(app):
    """注册智法·AI智能法务大模型路由"""
    app.include_router(router)
