"""AI智能自动引流模块路由(P0+P1+P2, 36 端点)

鉴权:
    - 公开(5): /r/{code} 短链跳转 / attach 归并 / attach-order 回写 /
      /sitemap.xml / /robots.txt
    - 管理端(26): X-Role: admin(内容工厂/短码/点击流/报表/ROI引擎/
      SEO/AB/通知/裂变管理与刷新)
    - 用户端(5): X-Member-Id(任务宝进度/海报生成与列表)

异常映射(遵循项目约定):
    - KeyError → 404(选题/内容/点击/短码/关键词/活动不存在)
    - ValueError → 409(状态非法/合规分不足/已归并等)

端点分布:
    - 选题(2):   topic / topics
    - 内容(4):   generate / contents / review / publish
    - 短链(4):   短码创建 / /r/{code}跳转 / clicks / short-links
    - 归因(3):   attach(注册归并) / attach-order(下单回写) / attributions
    - 报表(3):   funnel / channel / content
    - ROI(3):    rebalance / budgets / suggest-topics
    - 联动(1):   traffic lead 状态推进
    - SEO(5):    keywords增查 / article生成 / sitemap.xml / robots.txt(P1)
    - AB(3):     page配置 / report报表 / pages列表(P1)
    - 通知(1):   notify/publish(P1)
    - 裂变(5):   fission创建/列表/结束/进度查询/进度刷新发奖(P2)
    - 海报(2):   poster生成 / posters列表(P2)
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


class KeywordRequest(PydBaseModel):
    word: str = Field(..., min_length=1, max_length=50, description="关键词")
    searchVolume: int = Field(0, ge=0, description="搜索量(可选)")


class AbPageRequest(PydBaseModel):
    code: str = Field(..., max_length=20, description="活动短码")
    pathA: str = Field(..., max_length=200, description="版本A落地页路径")
    pathB: str = Field(..., max_length=200, description="版本B落地页路径")
    weightA: int = Field(50, ge=1, le=99, description="版本A权重%")


class NotifyPublishRequest(PydBaseModel):
    contentId: int = Field(..., description="内容ID")
    memberIds: list = Field(..., min_length=1, description="通知会员ID列表")


class FissionRequest(PydBaseModel):
    title: str = Field(..., min_length=1, max_length=100, description="活动标题")
    inviteTarget: int = Field(5, ge=1, le=1000, description="邀请目标人数")
    rewardAmount: float = Field(20.0, ge=0, le=10000, description="达标奖励(钱包奖励余额)")
    rewardPoints: int = Field(100, ge=0, le=100000, description="达标积分(竹叶)")
    startTime: str = Field("", max_length=30, description="活动开始(ISO, 空不限)")
    endTime: str = Field("", max_length=30, description="活动结束(ISO, 空不限)")


class FissionProgressRequest(PydBaseModel):
    fissionId: int = Field(..., description="裂变活动ID")
    userId: int = Field(..., description="会员ID")


class PosterRequest(PydBaseModel):
    scene: str = Field(..., description="场景: invite(任务宝)/promote(推广)")
    fissionId: int = Field(0, description="裂变活动ID(invite场景必填)")
    contentId: int = Field(0, description="内容ID(promote场景必填)")


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


# ============================================================
# P1: AI-SEO + AB落地页 + 分发通知
# ============================================================

@router.post("/api/attract/seo/keywords", tags=["AI自动引流模块"])
async def add_keyword(
    data: KeywordRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """添加SEO关键词(去重)"""
    _require_admin(x_role)
    try:
        result = await _service.add_keyword(
            word=data.word, search_volume=data.searchVolume)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/seo/keywords", tags=["AI自动引流模块"])
async def list_keywords(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选 active/paused"),
):
    """关键词列表(含7个种子词)"""
    _require_admin(x_role)
    try:
        result = await _service.list_keywords(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/seo/article/generate", tags=["AI自动引流模块"])
async def generate_seo_article(
    x_role: str = Header(None, alias="X-Role"),
    keywordId: int = Query(..., description="关键词ID"),
):
    """按关键词生成SEO长文(入内容库待审核)"""
    _require_admin(x_role)
    try:
        result = await _service.generate_seo_article(
            keyword_id=keywordId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/sitemap.xml", tags=["AI自动引流模块"])
async def sitemap():
    """sitemap.xml(公开: 已发布SEO文章+短链落地页)"""
    try:
        content = await _service.generate_sitemap()
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=content,
                                  media_type="application/xml")
    except Exception as e:
        _handle(e)


@router.get("/robots.txt", tags=["AI自动引流模块"])
async def robots():
    """robots.txt(公开: 全站允许+sitemap指引)"""
    try:
        content = await _service.generate_robots()
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=content, media_type="text/plain")
    except Exception as e:
        _handle(e)


@router.post("/api/attract/ab/page", tags=["AI自动引流模块"])
async def create_ab_page(
    data: AbPageRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """为活动短码配置AB落地页(按权重分流)"""
    _require_admin(x_role)
    try:
        result = await _service.create_ab_page(
            code=data.code, path_a=data.pathA, path_b=data.pathB,
            weight_a=data.weightA)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/ab/report/{code}", tags=["AI自动引流模块"])
async def ab_report(
    code: str,
    x_role: str = Header(None, alias="X-Role"),
):
    """AB测试转化对比报表(点击/注册/CVR/胜出版本)"""
    _require_admin(x_role)
    try:
        result = await _service.ab_report(code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/ab/pages", tags=["AI自动引流模块"])
async def list_ab_pages(
    x_role: str = Header(None, alias="X-Role"),
):
    """AB落地页配置列表"""
    _require_admin(x_role)
    try:
        result = await _service.repo.list_ab_pages()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/notify/publish", tags=["AI自动引流模块"])
async def notify_publish(
    data: NotifyPublishRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """内容发布后通知分发网络会员(站内信, best-effort)"""
    _require_admin(x_role)
    try:
        result = await _service.notify_publish(
            content_id=data.contentId, member_ids=data.memberIds)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 裂变活动插件(任务宝 + 海报)
# ============================================================

@router.post("/api/attract/fission", tags=["AI自动引流模块"])
async def create_fission(
    data: FissionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建任务宝裂变活动(邀请N人得双通道奖励)"""
    _require_admin(x_role)
    try:
        result = await _service.create_fission(
            title=data.title, invite_target=data.inviteTarget,
            reward_amount=data.rewardAmount,
            reward_points=data.rewardPoints,
            start_time=data.startTime, end_time=data.endTime)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/fissions", tags=["AI自动引流模块"])
async def list_fissions(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选 ongoing/ended"),
):
    """裂变活动列表"""
    _require_admin(x_role)
    try:
        result = await _service.list_fissions(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/fission/end", tags=["AI自动引流模块"])
async def end_fission(
    data: FissionProgressRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """结束裂变活动(停止计数与发奖)"""
    _require_admin(x_role)
    try:
        result = await _service.end_fission(fission_id=data.fissionId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/fission/progress", tags=["AI自动引流模块"])
async def get_fission_progress(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    fissionId: int = Query(..., description="裂变活动ID"),
):
    """我的任务宝进度(只读初始化, X-Member-Id 鉴权)"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        result = await _service.get_fission_progress(
            fission_id=fissionId, user_id=int(x_member_id))
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/fission/refresh", tags=["AI自动引流模块"])
async def refresh_fission_progress(
    data: FissionProgressRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """刷新任务进度并检查达标发奖(幂等; 邀请计数来自归因表注册数)"""
    _require_admin(x_role)
    try:
        result = await _service.refresh_fission_progress(
            fission_id=data.fissionId, user_id=data.userId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/attract/poster", tags=["AI自动引流模块"])
async def create_poster(
    data: PosterRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """生成裂变海报(文本卡片: 标题/文案/二维码内容, 前端canvas渲染)"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        result = await _service.create_poster(
            user_id=int(x_member_id), scene=data.scene,
            fission_id=data.fissionId, content_id=data.contentId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/attract/posters", tags=["AI自动引流模块"])
async def list_posters(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    scene: str = Query(None, description="场景筛选 invite/promote"),
):
    """我的海报列表"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        result = await _service.list_posters(user_id=int(x_member_id),
                                             scene=scene)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)
