"""42号·AI无感开票模块路由(P0, 12 端点)

鉴权:
    - 会员端: X-Member-Id(抬头簿/我的发票/队列确认/手动触发)
    - 管理端: X-Role: admin(决策流水/统计)

异常映射(遵循项目约定):
    - KeyError → 404(抬头/订单/队列不存在)
    - ValueError → 409(类型非法/重复/状态非法)

端点分布:
    - 抬头簿(4): GET/POST /titles / POST /titles/{id}/default
                 / DELETE /titles/{id}
    - 我的(3):   GET /mine / GET /queue / POST /queue/{order_id}/confirm
    - 触发(1):  POST /orders/{order_id}/request
    - 管理端(2): GET /admin/decisions / GET /admin/stats
    - 内部(2):   POST /internal/on-completed / POST /internal/on-refunded
                 (订单钩子触发口径, 供测试与运维直调)

P1 预留: 管理拦截面板 / 存证验证 / 学习回流
"""

from fastapi import APIRouter, Header, HTTPException, Query

from pydantic import BaseModel as PydBaseModel, Field

from services.invoice_service import Invoice42Service


router = APIRouter()
_service = Invoice42Service()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=403, detail="缺少 X-Member-Id")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=403, detail="X-Member-Id 须为数字")


def _handle(exc: Exception):
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

class TitleRequest(PydBaseModel):
    titleType: str = Field(..., description="personal/company")
    title: str = Field(..., min_length=1, max_length=128,
                       description="发票抬头")
    taxNo: str = Field("", max_length=32, description="税号(企业必填)")
    isDefault: bool = Field(None, description="设为默认(缺省首个自动)")


class AutoTriggerRequest(PydBaseModel):
    memberId: int = Field(None, gt=0, description="缺省取订单归属")
    amount: float = Field(None, ge=0, description="缺省取订单实付")
    orderRiskAction: str = Field("pass", description="订单风控动作信号")


class ManualRequest(PydBaseModel):
    titleId: int = Field(None, gt=0,
                         description="指定抬头ID(缺省取默认抬头)")


class AppealRequest(PydBaseModel):
    reason: str = Field("", max_length=500,
                        description="申诉理由(缺省默认话术)")


class AppealDecideRequest(PydBaseModel):
    approve: bool = Field(..., description="true=误拦恢复 / false=维持拦截")
    reviewer: str = Field("admin", max_length=50, description="裁决人")
    note: str = Field("", max_length=200, description="裁决备注")


# ============================================================
# 抬头簿(会员端)
# ============================================================

@router.get("/api/invoice/titles")
async def my_titles(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的抬头簿"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.get_book(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/titles")
async def add_title(
    body: TitleRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """新增抬头(首个自动成为默认)"""
    member_id = _require_member(x_member_id)
    try:
        book = await _service.add_title(
            member_id, body.titleType, body.title, body.taxNo,
            body.isDefault)
        return {"success": True, "titles": book["titles"]}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/titles/{title_id}/default")
async def set_default(
    title_id: int,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """切换默认抬头"""
    member_id = _require_member(x_member_id)
    try:
        book = await _service.set_default_title(member_id, title_id)
        return {"success": True, "titles": book["titles"]}
    except Exception as e:
        raise _handle(e) from e


@router.delete("/api/invoice/titles/{title_id}")
async def remove_title(
    title_id: int,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """删除抬头(默认被删则首个剩余顶替)"""
    member_id = _require_member(x_member_id)
    try:
        book = await _service.remove_title(member_id, title_id)
        return {"success": True, "titles": book["titles"]}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 我的发票/队列(会员端)
# ============================================================

@router.get("/api/invoice/mine")
async def my_invoices(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的发票列表(自动+手动, 含红冲票)"""
    member_id = _require_member(x_member_id)
    try:
        invoices = await _service.my_invoices(member_id)
        return {"success": True, "total": len(invoices),
                "invoices": invoices}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/invoice/queue")
async def my_queue(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的待确认开票队列"""
    member_id = _require_member(x_member_id)
    try:
        items = await _service.my_queue(member_id)
        return {"success": True, "total": len(items), "queue": items}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/queue/{order_id}/confirm")
async def confirm_queue(
    order_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """待确认一键开票(队列抬头快照)"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.confirm_queue(member_id, order_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/orders/{order_id}/request")
async def request_invoice(
    order_id: str,
    body: ManualRequest = None,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """手动触发开票(无感漏网兜底, 如 collect/申诉恢复后补开)"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.request_invoice(
            member_id, order_id,
            (body.titleId if body else None))
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/orders/{order_id}/appeal")
async def submit_appeal(
    order_id: str,
    body: AppealRequest = None,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """会员对拦截决策提交申诉(P1: 拦截面板四步法第 2 步)"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.submit_appeal(
            member_id, order_id,
            (body.reason if body else ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/invoice/appeals")
async def my_appeals(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的申诉记录"""
    member_id = _require_member(x_member_id)
    try:
        appeals = await _service.my_appeals(member_id)
        return {"success": True, "total": len(appeals),
                "appeals": appeals}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 内部触发口径(订单钩子直调/测试与运维)
# ============================================================

@router.post("/api/invoice/internal/on-completed")
async def internal_on_completed(
    order_id: str,
    body: AutoTriggerRequest = None,
):
    """订单完成 → 无感开票决策(订单路由钩子的直调口径)"""
    try:
        return await _service.on_order_completed(
            order_id,
            member_id=(body.memberId if body else None),
            amount=(body.amount if body else None),
            order_risk_action=((body.orderRiskAction if body
                                else "pass")))
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/internal/on-refunded")
async def internal_on_refunded(order_id: str):
    """订单退款 → 自动红冲(订单路由钩子的直调口径)"""
    try:
        return await _service.on_order_refunded(order_id)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 管理端
# ============================================================

@router.get("/api/invoice/admin/decisions")
async def admin_decisions(
    action: str = Query(None, description="auto_issue/manual_queue/"
                                       "reject/collect 过滤"),
    limit: int = Query(100, ge=1, le=1000),
    x_role: str = Header(default="", alias="X-Role"),
):
    """决策流水列表(管理端)"""
    _require_admin(x_role)
    try:
        decisions = await _service.admin_decisions(action=action,
                                                   limit=limit)
        return {"success": True, "total": len(decisions),
                "action": action, "decisions": decisions}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/invoice/admin/stats")
async def admin_stats(
    x_role: str = Header(default="", alias="X-Role"),
):
    """自动化率统计(管理端看板: 四档分布/开票数/自动率/误拦截率)"""
    _require_admin(x_role)
    try:
        return await _service.admin_stats()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 管理端申诉裁决(P1: 拦截面板四步法第 2/3 步)
# ============================================================

@router.get("/api/invoice/admin/appeals")
async def admin_appeals(
    status: str = Query(None, description="pending/approved/"
                                       "rejected 过滤"),
    limit: int = Query(100, ge=1, le=1000),
    x_role: str = Header(default="", alias="X-Role"),
):
    """申诉队列(管理端, 待裁决=拦截面板工作列表)"""
    _require_admin(x_role)
    try:
        appeals = await _service.admin_appeals(status=status,
                                                limit=limit)
        return {"success": True, "total": len(appeals),
                "status": status, "appeals": appeals}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/admin/appeals/{appeal_id}/decide")
async def decide_appeal(
    appeal_id: int,
    body: AppealDecideRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """申诉裁决: approve=误拦恢复(会员手动补开) / 拒绝=维持拦截归档"""
    _require_admin(x_role)
    try:
        return await _service.decide_appeal(
            appeal_id, body.approve, body.reviewer, body.note)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P2: 学习回流(申诉裁决真值 → 第25档案 Hedge 学习)
# ============================================================

@router.post("/api/invoice/admin/learning/collect")
async def collect_learning(
    x_role: str = Header(default="", alias="X-Role"),
):
    """批量回流已裁决申诉 → 决策正确性反馈(幂等)"""
    _require_admin(x_role)
    try:
        return await _service.collect_appeal_feedback()
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/invoice/admin/learning/run")
async def run_learning(
    x_role: str = Header(default="", alias="X-Role"),
):
    """触发第25档案一轮 Hedge 学习(反馈不足抛 409)"""
    _require_admin(x_role)
    try:
        return await _service.run_learning()
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/invoice/admin/learning/status")
async def learning_status(
    x_role: str = Header(default="", alias="X-Role"),
):
    """学习回流状态(裁决申诉计数/当前权重)"""
    _require_admin(x_role)
    try:
        return await _service.learning_status()
    except Exception as e:
        raise _handle(e) from e


def register_invoice_routes(app) -> None:
    """注册42号路由(main.py startup 调用)"""
    app.include_router(router)
