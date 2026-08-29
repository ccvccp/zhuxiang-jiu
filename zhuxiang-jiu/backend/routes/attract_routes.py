"""AI智能自动引流模块路由(P0, 21 端点)

鉴权:
    - 公开(3): /r/{code} 短链跳转(无鉴权) / attach 注册归因上报 / attach-order
    - 管理端(18): X-Role: admin(内容工厂/短码/点击流/报表/ROI引擎)

异常映射(遵循项目约定):
    - KeyError → 404(选题/内容/点击/短码不存在)
    - ValueError → 409(状态非法/合规分不足/已归并等)

端点分布:
    - 选题(2):   topic / topics
    - 内容(5):   generate / contents / contents/{id} / review / publish
    - 短链(4):   短码创建 / /r/{code}跳转 / clicks / short-links
    - 归因(3):   attach(注册归并) / attach-order(下单回写) / attributions
    - 报表(3):   funnel / channel / content
    - ROI(3):    rebalance / budgets / suggest-topics
    - 联动(1):   traffic lead 状态推进(修复既有空白)
"""

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel as PydBaseModel, Field

from services.attract_service import AttractService


router = APIRouter()
_service = AttractService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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

class CreateTopicRequest(PydBaseModel):
    title: str = Field(..., min_length=1, max_length=100, description="选题标题")
    angle: str = Field("culture", description="角度: culture/scene/craft/offer")
    keywords: str = Field("竹香型白酒", max_length=50, description="关键词")


class ReviewContentRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    reviewer: str = Field("admin", max_length=50, description="审核人")


class PublishContentRequest(PydBaseModel):
    channelCode: str = Field("", max_length=50, description="分发绑定码(推广码/短码)")


class CreateShortLinkRequest(PydBaseModel):
    landingPath: str = Field("", max_length=200, description="自定义落地页路径(空则活动页)")
    note: str = Field("", max_length=200, description="备注")


class AttachRegistrationRequest(PydBaseModel):
    clickId: int = Field(..., description="注册前最后点击的clickId")
    memberId: int = Field(..., description="新注册会员ID")


class AttachOrderRequest(PydBaseModel):
    clickId: int = Field(..., description="归因clickId")
    orderId: str = Field(..., max_length=50, description="订单号")
    orderAmount: float = Field(..., gt=0, description="订单金额")
    commission: float = Field(0, ge=0, description="佣金(可由traffic计算回填)")


class LeadStatusRequest(PydBaseModel):
    status: str = Field(..., description="目标状态: registered/ordered/invalid")


# ============================================================
# 公开端点(无鉴权)
# ============================================================

@router.get("/r/{code}", tags=["AI自动引流模块"], include_in_schema=True)
async def short_link_redirect(
    code: str,
    request: Request,
    utm_source: str = Query("", alias="utm_source", description="渠道来源"),
    utm_medium: str = Query("", alias="utm_medium", description="引流方式"),
    utm_campaign: str = Query("", alias="utm_campaign", description="活动名称"),
):
    """智能短链跳转(公开, 修复 KOL 链接断链: 302 + 匿名点击落库 + 分流)

    码类型分流(D-10): ZXBJ→注册页 / KOL→产品页 / A-→活动页
    """
    try:
        result = await _service.resolve_click(
            code=code, utm_source=utm_source, utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
            referer=request.headers.get("referer", ""))
        # 前端约定: 落地页携带 clickId 参数, 注册时回传完成归并
        sep = "&" if "?" in result["landingPath"] else "?"
        target = f"{result['landingPath']}{sep}clickId={result['clickId']}"
        return RedirectResponse(url=target, status_code=302)
    except Exception as e:
        _handle(e)


@router.post("/api/attract/attach", tags=["AI自动引流模块"])
async def attach_registration(
    data: AttachRegistrationRequest,
):
    """注册归因上报(公开: 注册请求携带clickId → 三合一归并)

    一次点击同时完成: traffic lead + promotion 绑定 + 统一归因表。
    """
    try:
        result = await _service.attach_registration(
            click_id=data.clickId, member_id=data.memberId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/attach-order", tags=["AI自动引流模块"])
async def attach_order(
    data: AttachOrderRequest,
):
    """下单归因回写(下单/佣金计算后调用, 更新归因漏斗)"""
    try:
        result = await _service.attach_order(
            click_id=data.clickId, order_id=data.orderId,
            order_amount=data.orderAmount, commission=data.commission)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 内容工厂(admin)
# ============================================================

@router.post("/api/attract/topic", tags=["AI自动引流模块"])
async def create_topic(
    data: CreateTopicRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """录入选题(或由 suggest-topics 数据回流生成)"""
    _require_admin(x_role)
    try:
        result = await _service.create_topic(
            title=data.title, angle=data.angle, keywords=data.keywords)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/topics", tags=["AI自动引流模块"])
async def list_topics(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选"),
):
    """选题列表"""
    _require_admin(x_role)
    try:
        result = await _service.list_topics(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/content/generate", tags=["AI自动引流模块"])
async def generate_contents(
    data: CreateTopicRequest,
    x_role: str = Header(None, alias="X-Role"),
    topicId: int = Query(..., description="选题ID"),
):
    """AI生成四平台内容变体(规则引擎B级, 大模型接口抽象)"""
    _require_admin(x_role)
    try:
        result = await _service.generate_contents(topic_id=topicId)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/contents", tags=["AI自动引流模块"])
async def list_contents(
    x_role: str = Header(None, alias="X-Role"),
    platform: str = Query(None, description="平台筛选"),
    topicId: int = Query(None, description="选题ID筛选"),
    status: str = Query(None, description="状态筛选"),
):
    """内容列表(按平台/选题/状态)"""
    _require_admin(x_role)
    try:
        result = await _service.list_contents(
            platform=platform, topic_id=topicId, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/contents/{content_id}/review", tags=["AI自动引流模块"])
async def review_content(
    content_id: int,
    data: ReviewContentRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """内容审核(合规分不足不可通过)"""
    _require_admin(x_role)
    try:
        result = await _service.review_content(
            content_id=content_id, approved=data.approved,
            reviewer=data.reviewer)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/contents/{content_id}/publish", tags=["AI自动引流模块"])
async def publish_content(
    content_id: int,
    data: PublishContentRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """发布内容(绑定分发码)"""
    _require_admin(x_role)
    try:
        result = await _service.publish_content(
            content_id=content_id, channel_code=data.channelCode)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 短链与点击流(admin)
# ============================================================

@router.post("/api/attract/short-link", tags=["AI自动引流模块"])
async def create_short_link(
    data: CreateShortLinkRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建活动短码(A-xxxx)"""
    _require_admin(x_role)
    try:
        result = await _service.create_short_link(
            landing_path=data.landingPath, note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/short-links", tags=["AI自动引流模块"])
async def list_short_links(
    x_role: str = Header(None, alias="X-Role"),
    active: bool = Query(None, description="启用状态筛选"),
):
    """短码列表"""
    _require_admin(x_role)
    try:
        result = await _service.repo.list_short_links(active=active)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/clicks", tags=["AI自动引流模块"])
async def list_clicks(
    x_role: str = Header(None, alias="X-Role"),
    code: str = Query(None, description="推广码筛选"),
    channel: str = Query(None, description="渠道筛选"),
    limit: int = Query(100, ge=1, le=1000),
):
    """匿名点击流查询(核心增量: 不要求注册)"""
    _require_admin(x_role)
    try:
        result = await _service.repo.list_clicks(code=code, channel=channel,
                                                 limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 归因与报表(admin)
# ============================================================

@router.get("/api/attract/attributions", tags=["AI自动引流模块"])
async def list_attributions(
    x_role: str = Header(None, alias="X-Role"),
    channel: str = Query(None, description="渠道筛选"),
    promoterId: int = Query(None, description="推广员ID筛选"),
    influencerId: int = Query(None, description="博主ID筛选"),
    memberId: int = Query(None, description="会员ID筛选"),
):
    """统一归因总表(四维筛选)"""
    _require_admin(x_role)
    try:
        result = await _service.list_attributions(
            channel=channel, promoter_id=promoterId,
            influencer_id=influencerId, member_id=memberId)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/report/funnel", tags=["AI自动引流模块"])
async def report_funnel(
    x_role: str = Header(None, alias="X-Role"),
):
    """漏斗报表(点击→注册→下单→GMV→佣金)"""
    _require_admin(x_role)
    try:
        result = await _service.report_funnel()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/report/channel", tags=["AI自动引流模块"])
async def report_channel(
    x_role: str = Header(None, alias="X-Role"),
):
    """渠道ROI报表(按ROI降序)"""
    _require_admin(x_role)
    try:
        result = await _service.report_channel()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/report/content", tags=["AI自动引流模块"])
async def report_content(
    x_role: str = Header(None, alias="X-Role"),
):
    """内容效果报表(按平台聚合)"""
    _require_admin(x_role)
    try:
        result = await _service.report_content()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# ROI 引擎(admin)
# ============================================================

@router.post("/api/attract/roi/rebalance", tags=["AI自动引流模块"])
async def rebalance_budgets(
    x_role: str = Header(None, alias="X-Role"),
):
    """渠道奖励系数再分配(ROI高↑/低↓, 池内此消彼长)"""
    _require_admin(x_role)
    try:
        result = await _service.rebalance_budgets()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/roi/budgets", tags=["AI自动引流模块"])
async def list_budgets(
    x_role: str = Header(None, alias="X-Role"),
):
    """ROI 分配账本查询"""
    _require_admin(x_role)
    try:
        result = await _service.list_budgets()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/roi/suggest-topics", tags=["AI自动引流模块"])
async def suggest_topics(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(3, ge=1, le=10, description="建议选题数"),
):
    """AI选题建议(高ROI渠道数据回流)"""
    _require_admin(x_role)
    try:
        result = await _service.suggest_topics(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 联动: 补齐 traffic lead 状态推进(修复既有空白#6)
# ============================================================

@router.post("/api/attract/lead/{lead_id}/status", tags=["AI自动引流模块"])
async def update_lead_status(
    lead_id: int,
    data: LeadStatusRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """推进 traffic 引流记录状态(registered→ordered等, 原服务方法无路由暴露)"""
    _require_admin(x_role)
    try:
        from services.traffic_service import TrafficService
        result = await TrafficService().update_lead_status(
            lead_id=lead_id, status=data.status)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_attract_routes(app):
    """注册AI智能自动引流模块路由"""
    app.include_router(router)
