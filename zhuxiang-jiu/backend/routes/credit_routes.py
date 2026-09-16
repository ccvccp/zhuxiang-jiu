"""信用管理模块路由(20 端点)

鉴权:
    - 用户端(14接口): X-Member-Id 头标识当前会员
    - 管理端(6接口): X-Role: admin 头(调整/升降级/黑名单/恢复/季度结算/人工审批)
    - 公开(部分查询接口)

异常映射:
    - KeyError → 404(账户/订单不存在)
    - ValueError → 409(业务冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(20个):
    - 查询(8):    查询信用分 / 信用流水 / 额度查询 / 信用统计 / 信用报告 /
                  季度结算记录 / 兑换记录 / 先享后付订单列表
    - 操作(4):    调整信用分 / 信用升级 / 先享后付下单 / 先享后付还款
    - 用户兑换(2): 积分兑换 / AI兑换推荐
    - 等级评估(1): 等级评估详情(触发规则评估)
    - 管理(5):    降级 / 黑名单 / 恢复 / 季度结算 / 先享后付人工审批
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.credit_service import CreditService


router = APIRouter()
_service = CreditService()


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


def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 大模型二代·三态灰度门控(CREDIT_MODE, 全站范式)
# ============================================================

async def _gate() -> dict:
    """决策面门槛(CREDIT_MODE=off → 409;
    shadow/assist 放行——大模型二代读取链:
    护栏暂停 > 运行时 override > env)"""
    from services.credit_mode_service import (
        CreditModeService,
    )
    return await CreditModeService() \
        .require_decision_mode()


def _decision(fn=None, *, strict=False,
              admin=False):
    """决策端点装饰器: 门控(off 409) +
    shadow/assist 标记(creditMode)

    参数(鉴权优先——401/403 before 409, 小竹/钱包范式):
        strict=True  用户端(X-Member-Id 缺失
                     放行函数体触发 401)
        admin=True   管理端(X-Role 非 admin
                     放行函数体触发 403)
        默认         直接门控

    宪法豁免面(履约与兑换权——永不关停)不加本装饰器:
    paylater repay(还款履约权)/exchange(积分兑换权)。
    观测面(GET)不加——永不关停。
    """
    import functools
    from services.credit_mode_service import (
        MODE_VALUES,
    )

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # 鉴权优先: 无效/缺失凭据放行
            # 函数体触发 401/403——不预判门控
            x_role = kwargs.get("x_role")
            x_member_id = kwargs.get(
                "x_member_id")
            gate_needed = not (
                (admin and x_role != "admin")
                or (strict and not x_member_id))
            mode_state = None
            if gate_needed:
                try:
                    mode_state = await _gate()
                except ValueError as e:
                    raise HTTPException(
                        status_code=409,
                        detail=str(e)) from e
            result = await fn(*args, **kwargs)
            if isinstance(result, dict) \
                    and mode_state \
                    and mode_state.get("mode") \
                    in MODE_VALUES[1:]:
                result = {**result,
                          "creditMode":
                              mode_state["mode"]}
            return result
        return wrapper

    return deco(fn) if fn else deco


# ============================================================
# 请求模型
# ============================================================

class AdjustScoreRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    delta: int = Field(..., description="信用分变化(正加分/负扣分)")
    reason: str = Field("", description="调整原因")
    operator: str = Field("system", description="操作者")
    roleType: str = Field("member", description="角色类型")


class UpgradeRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    targetLevel: str = Field(..., description="目标等级: L2/L3/L4/L5")
    reason: str = Field("", description="升级原因")
    operator: str = Field("admin", description="操作者")


class DowngradeRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    targetLevel: str = Field(..., description="目标等级: L1/L2/L3/L4")
    reason: str = Field("", description="降级原因")
    operator: str = Field("admin", description="操作者")


class BlacklistRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    reason: str = Field("", description="拉黑原因")
    operator: str = Field("admin", description="操作者")


class RestoreRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    restoreScore: int = Field(350, ge=0, le=1000, description="恢复后分数")
    reason: str = Field("", description="恢复原因")
    operator: str = Field("admin", description="操作者")


class PaylaterOrderRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    amount: float = Field(..., gt=0, description="订单金额(元)")
    accountType: str = Field("member", description="账户类型: member会员/b端")
    orderNo: str = Field("", description="关联业务订单号")
    source: str = Field("order", description="来源: order/order_pay/agent_purchase")


class PaylaterRepayRequest(PydBaseModel):
    orderId: int = Field(..., description="先享后付订单ID")
    repayChannel: str = Field("wallet", description="还款方式: wallet/bank/alipay/wechat")


class PaylaterReviewRequest(PydBaseModel):
    orderId: int = Field(..., description="先享后付订单ID")
    approved: bool = Field(..., description="审批结果: true通过/false拒绝")
    operator: str = Field("admin", description="操作者")


class QuarterlySettleRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    year: int = Field(..., ge=2020, le=2100, description="年份")
    quarter: int = Field(..., ge=1, le=4, description="季度(1-4)")
    operator: str = Field("system", description="操作者")


class ExchangeRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    exchangeType: str = Field(..., description="兑换类型: cash现金/goods商品/benefit权益/combo组合")
    points: int = Field(..., gt=0, description="兑换积分")
    itemId: str = Field(None, description="兑换目录ID(商品/权益必填)")


# ============================================================
# P0 接口(10 个) — 静态路径优先于动态路径
# ============================================================

# --- 查询接口(静态路径优先) ---

@router.get("/api/credit/list", tags=["信用管理模块"])
async def list_logs(
    user_id: int = Query(..., description="会员ID"),
    log_type: str = Query(None, description="按类型筛选: earn/deduct/adjust/upgrade/downgrade/blacklist/restore"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询信用流水(支持按类型筛选)"""
    try:
        result = await _service.list_logs(user_id, log_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/stats/{user_id}", tags=["信用管理模块"])
async def get_stats(
    user_id: int,
):
    """信用统计(按类型统计流水)"""
    try:
        result = await _service.get_stats(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/report/{user_id}", tags=["信用管理模块"])
async def get_credit_report(
    user_id: int,
):
    """信用报告(全维度画像)"""
    try:
        result = await _service.get_credit_report(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/quota/{user_id}", tags=["信用管理模块"])
async def get_paylater_quota(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询先享后付额度"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_paylater_quota(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/score/{user_id}", tags=["信用管理模块"])
async def get_score(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询信用分(不存在则按会员创建)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_score(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 操作接口 ---

@router.post("/api/credit/adjust", tags=["信用管理模块"])
@_decision(admin=True)
async def adjust_score(
    data: AdjustScoreRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """调整信用分(加分/扣分/人工调整)"""
    _require_admin(x_role)
    try:
        result = await _service.adjust_score(
            user_id=data.userId,
            delta=data.delta,
            reason=data.reason,
            operator=data.operator,
            role_type=data.roleType,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/upgrade", tags=["信用管理模块"])
@_decision(admin=True)
async def upgrade_level(
    data: UpgradeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """信用升级(强制设为目标等级对应分数下限)"""
    _require_admin(x_role)
    try:
        result = await _service.upgrade_level(
            user_id=data.userId,
            target_level=data.targetLevel,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/downgrade", tags=["信用管理模块"])
@_decision(admin=True)
async def downgrade_level(
    data: DowngradeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """信用降级(强制设为目标等级对应分数上限)"""
    _require_admin(x_role)
    try:
        result = await _service.downgrade_level(
            user_id=data.userId,
            target_level=data.targetLevel,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/blacklist", tags=["信用管理模块"])
@_decision(admin=True)
async def add_to_blacklist(
    data: BlacklistRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """加入黑名单(状态: blacklist, 竹信分扣至0)"""
    _require_admin(x_role)
    try:
        result = await _service.add_to_blacklist(
            user_id=data.userId,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/restore", tags=["信用管理模块"])
@_decision(admin=True)
async def restore_credit(
    data: RestoreRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """恢复信用(解除黑名单/冻结, 重置分数)"""
    _require_admin(x_role)
    try:
        result = await _service.restore_credit(
            user_id=data.userId,
            restore_score=data.restoreScore,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# v8.0 扩展端点(10 个) — 先享后付订单 / 季度结算 / 兑换 / 等级评估
# ============================================================

@router.get("/api/credit/level/evaluation/{user_id}", tags=["信用管理模块"])
async def get_level_evaluation(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """等级评估详情(触发规则评估: 区间持续天数/保护期/修复期/预警)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.evaluate_level_transition(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/exchange/recommend/{user_id}", tags=["信用管理模块"])
async def recommend_exchange(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """AI兑换方案推荐(Top3方案+推荐理由)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.recommend_exchange(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/exchange/catalog", tags=["信用管理模块"])
async def get_exchange_catalog():
    """积分商城兑换目录(商品/权益/费率/上限, 公开)"""
    try:
        from repositories.credit_repository import (
            EXCHANGE_CATALOG, EXCHANGE_RATES,
            QUARTER_CASH_CAP, CASH_TAX_FREE_AMOUNT, CASH_TAX_RATE,
        )
        items = [
            {"itemId": item_id, **item}
            for item_id, item in EXCHANGE_CATALOG.items()
        ]
        return {
            "success": True,
            "data": {
                "items": items,
                "count": len(items),
                "rates": EXCHANGE_RATES,
                "quarterCashCap": QUARTER_CASH_CAP,
                "cashTaxFreeAmount": CASH_TAX_FREE_AMOUNT,
                "cashTaxRate": CASH_TAX_RATE,
            },
        }
    except Exception as e:
        _handle(e)


@router.get("/api/credit/quarterly/{user_id}", tags=["信用管理模块"])
async def list_quarterly_settlements(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    limit: int = Query(20, ge=1, le=100, description="查询条数"),
):
    """查询季度信用积分结算记录"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_quarterly_settlements(user_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/exchanges/{user_id}", tags=["信用管理模块"])
async def list_exchanges(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    exchange_type: str = Query(None, description="按类型筛选: cash/goods/benefit/combo"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询积分兑换记录"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_exchanges(user_id, exchange_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/paylater/orders/{user_id}", tags=["信用管理模块"])
async def list_paylater_orders(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    status: str = Query(None, description="按状态筛选: review/active/repaid/rejected"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询先享后付订单列表"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_paylater_orders(user_id, status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/paylater/order", tags=["信用管理模块"])
@_decision(strict=True)
async def create_paylater_order(
    data: PaylaterOrderRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建先享后付订单(AI智能授信审批: 自动通过/人工审批/自动拒绝)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.create_paylater_order(
            user_id=data.userId,
            amount=data.amount,
            account_type=data.accountType,
            order_no=data.orderNo,
            source=data.source,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/paylater/repay", tags=["信用管理模块"])
async def repay_paylater_order(
    data: PaylaterRepayRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """先享后付还款(恢复额度+逾期费用+逾期信用分惩罚)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.repay_paylater_order(
            order_id=data.orderId,
            repay_channel=data.repayChannel,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/paylater/review", tags=["信用管理模块"])
@_decision(admin=True)
async def review_paylater_order(
    data: PaylaterReviewRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """先享后付订单人工审批(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.review_paylater_order(
            order_id=data.orderId,
            approved=data.approved,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/quarterly/settle", tags=["信用管理模块"])
@_decision(admin=True)
async def settle_quarter(
    data: QuarterlySettleRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """季度信用积分结算(管理端: 行为分×权重×时序系数+等级加成)"""
    _require_admin(x_role)
    try:
        result = await _service.settle_quarter(
            user_id=data.userId,
            year=data.year,
            quarter=data.quarter,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/exchange", tags=["信用管理模块"])
async def exchange_rewards(
    data: ExchangeRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """积分兑换(现金/商品/权益/组合, 含个税与季度上限校验)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.exchange_rewards(
            user_id=data.userId,
            exchange_type=data.exchangeType,
            points=data.points,
            item_id=data.itemId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 控制面(大模型二代——全站范式 4 端点)
# ============================================================

@router.get("/api/credit/mode", tags=["信用管理模块"])
async def credit_mode_status(
    x_role: str = Header(None, alias="X-Role"),
):
    """灰度总览(观测面——模式+护栏+红线公示;
    不受开关影响)"""
    _require_admin(x_role)
    from services.credit_mode_service import (
        CreditModeService,
    )
    return await CreditModeService().status_view()


@router.post("/api/credit/mode/override", tags=["信用管理模块"])
async def credit_mode_override(
    data: dict = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """运行时切档(免容器重建; 空 mode=清除 override)"""
    _require_admin(x_role)
    data = data or {}
    from services.credit_mode_service import (
        CreditModeService,
    )
    try:
        return await CreditModeService().set_override(
            str(data.get("mode") or ""),
            operator="admin")
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/credit/mode/guard", tags=["信用管理模块"])
async def credit_mode_guard(
    data: dict = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """护栏手动检查(三指标恶化 >3% 自动暂停)

    body 可选 {overdueRepayRate, paylaterRejectRate,
    blacklistRate}——缺省从仓储层实时聚合
    (逾期终态占比/rejected 订单占比/黑名单账户占比)。
    """
    _require_admin(x_role)
    data = data or {}
    from services.credit_mode_service import (
        CreditModeService,
    )
    try:
        if any(k in data for k in (
                "overdueRepayRate",
                "paylaterRejectRate",
                "blacklistRate")):
            return await CreditModeService(
            ).guard_check(
                float(data.get("overdueRepayRate") or 0),
                float(data.get(
                    "paylaterRejectRate") or 0),
                float(data.get(
                    "blacklistRate") or 0))
        from services.credit_scheduler import (
            run_guard_patrol,
        )
        r = await run_guard_patrol()
        return {"success": True,
                "metrics": r.get("metrics"),
                "samples": r.get("samples"),
                "breached": r.get("breached"),
                "pausedNow": r.get("pausedNow"),
                "breaches": r.get("breaches") or []}
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/credit/mode/resume", tags=["信用管理模块"])
async def credit_mode_resume(
    data: dict = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """人工恢复(护栏暂停解除——决策留痕)"""
    _require_admin(x_role)
    data = data or {}
    from services.credit_mode_service import (
        CreditModeService,
    )
    try:
        return await CreditModeService().resume(
            note=str(data.get("note") or ""))
    except ValueError as e:
        raise _map_value_error(e) from e


def register_credit_routes(app):
    """注册信用管理模块路由"""
    app.include_router(router)
