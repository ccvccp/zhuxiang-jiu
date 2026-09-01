"""36号·AI智能推广模块路由(P0, 14 端点)

鉴权:
    - 全部管理端: X-Role: admin(热点雷达/决策/内容工厂/发布/报表)

异常映射(遵循项目约定):
    - KeyError → 404(热点/内容/内容组/决策不存在)
    - ValueError → 409(状态非法/冷却期/超单日上限/平台无效等)

端点分布:
    - 雷达(3):  scan / hotspots / hotspots/{id}
    - 决策(2):  decisions / decisions/{hotspot_id}/decide
    - 内容(4):  generate / contents / contents/{id} / review
    - 发布(3):  publish / queue / process
    - 报表(3):  overview / platform / content-group/{group_id}
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.promo_service import PromoService


router = APIRouter()
_service = PromoService()


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

class ManualDecideRequest(PydBaseModel):
    engage: bool = Field(..., description="true=跟进进入内容工厂 / false=放弃")
    note: str = Field("", max_length=200, description="裁决备注")


class GenerateContentsRequest(PydBaseModel):
    hotspotId: int = Field(..., description="热点ID(须已跟进)")
    platforms: list = Field(["douyin"], description="发布平台: douyin/xiaohongshu/wechat_moments")


class ReviewRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    reviewer: str = Field("admin", max_length=50, description="审核人")


class PublishRequest(PydBaseModel):
    publishAt: str = Field("", max_length=40,
                           description="指定发布时间(ISO, 空则取黄金时段)")


class UpdateProfileRequest(PydBaseModel):
    audience: str = Field(None, max_length=100, description="目标人群描述")
    tone: str = Field(None, max_length=100, description="话术基调")
    format: str = Field(None, max_length=100, description="格式约束")
    scenes: list = Field(None, description="擅长场景列表")
    productTones: list = Field(None, description="亲和产品调性列表")


class AudienceMatchRequest(PydBaseModel):
    platform: str = Field(..., description="平台: douyin/xiaohongshu/wechat_moments")
    angle: str = Field(..., min_length=1, max_length=50,
                       description="内容角度(如 婚宴/送礼/日常小酌)")
    productTone: str = Field("口粮酒", description="产品调性")


class AuthoritySourceRequest(PydBaseModel):
    title: str = Field(..., min_length=1, max_length=200,
                       description="信源标题(如国标全称)")
    category: str = Field(..., description="类别: standard/association/media")
    content: str = Field(..., min_length=1, max_length=2000,
                         description="可引用客观事实内容")
    allowedUsage: str = Field("", max_length=200,
                              description="允许引用方式(空则默认客观事实引用)")


# ============================================================
# 雷达与决策(admin)
# ============================================================

@router.post("/api/promo/radar/scan", tags=["AI智能推广模块"])
async def radar_scan(
    x_role: str = Header(None, alias="X-Role"),
):
    """手动触发热点扫描(5平台模拟源 + 评分 + 风险否决 + 去重 + 自动决策)"""
    _require_admin(x_role)
    try:
        result = await _service.scan()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/radar/hotspots", tags=["AI智能推广模块"])
async def list_hotspots(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态: active/engaged/passed/discarded"),
    platform: str = Query(None, description="平台: baidu/douyin/weibo/zhihu/xiaohongshu"),
    minScore: int = Query(0, ge=0, le=100, description="最低评分"),
):
    """热点列表(按评分降序)"""
    _require_admin(x_role)
    try:
        result = await _service.list_hotspots(
            status=status, platform=platform, min_score=minScore)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/radar/hotspots/{hotspot_id}", tags=["AI智能推广模块"])
async def get_hotspot(
    hotspot_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """热点详情(含评分分项/品牌命中词/风险标记)"""
    _require_admin(x_role)
    try:
        result = await _service.get_hotspot(hotspot_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/decisions", tags=["AI智能推广模块"])
async def list_decisions(
    x_role: str = Header(None, alias="X-Role"),
    decision: str = Query(None,
                          description="档位筛选: auto_engage/manual_queue/pass"),
    pendingOnly: bool = Query(False, description="仅待人工裁决"),
):
    """蹭点决策列表(审计留痕, 含可解释 reason)"""
    _require_admin(x_role)
    try:
        result = await _service.list_decisions(
            decision=decision, pending_only=pendingOnly)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/promo/decisions/{hotspot_id}/decide", tags=["AI智能推广模块"])
async def manual_decide(
    hotspot_id: int,
    data: ManualDecideRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """人工裁决(50-70 分区间热点: 跟进/放弃)"""
    _require_admin(x_role)
    try:
        result = await _service.manual_decide(
            hotspot_id=hotspot_id, engage=data.engage, note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 内容工厂(admin)
# ============================================================

@router.post("/api/promo/contents/generate", tags=["AI智能推广模块"])
async def generate_contents(
    data: GenerateContentsRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """GLM-5.3 Agent 生成一源多态内容(四步链+三级降级+合规预审+短码)"""
    _require_admin(x_role)
    try:
        platforms = tuple(p for p in (data.platforms or []) if p) or ("douyin",)
        result = await _service.generate_contents(
            hotspot_id=data.hotspotId, platforms=platforms)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/contents", tags=["AI智能推广模块"])
async def list_contents(
    x_role: str = Header(None, alias="X-Role"),
    platform: str = Query(None, description="平台筛选"),
    status: str = Query(None, description="状态: pending/approved/rejected/queued/published"),
    hotspotId: int = Query(None, description="热点ID筛选"),
    groupId: int = Query(None, description="内容组ID筛选(一源多态)"),
):
    """内容列表"""
    _require_admin(x_role)
    try:
        result = await _service.list_contents(
            platform=platform, status=status, hotspot_id=hotspotId,
            group_id=groupId)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/contents/{content_id}", tags=["AI智能推广模块"])
async def get_content(
    content_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """内容详情(含 agentTrace 降级轨迹/合规报告/attract短码映射)"""
    _require_admin(x_role)
    try:
        result = await _service.get_content(content_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/promo/contents/{content_id}/review", tags=["AI智能推广模块"])
async def review_content(
    content_id: int,
    data: ReviewRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """人工审核(三审: 硬性违规/分数不足不可通过)"""
    _require_admin(x_role)
    try:
        result = await _service.review_content(
            content_id=content_id, approved=data.approved,
            reviewer=data.reviewer)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 发布调度(admin)
# ============================================================

@router.post("/api/promo/contents/{content_id}/publish", tags=["AI智能推广模块"])
async def publish_content(
    content_id: int,
    data: PublishRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """内容入发布队列(黄金时段窗口 + 单日上限防刷屏)"""
    _require_admin(x_role)
    try:
        result = await _service.publish_content(
            content_id=content_id, publish_at=data.publishAt)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/publish/queue", tags=["AI智能推广模块"])
async def list_publish_queue(
    x_role: str = Header(None, alias="X-Role"),
):
    """发布队列与黄金时段窗口状态"""
    _require_admin(x_role)
    try:
        result = await _service.list_publish_queue()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/promo/publish/process", tags=["AI智能推广模块"])
async def process_publish_queue(
    x_role: str = Header(None, alias="X-Role"),
):
    """处理到期发布(模拟轨回执; 调度器周期调用, 亦可手动触发)"""
    _require_admin(x_role)
    try:
        result = await _service.process_publish_queue()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 受众画像库(admin)
# ============================================================

@router.get("/api/promo/audience/profiles", tags=["AI智能推广模块"])
async def list_audience_profiles(
    x_role: str = Header(None, alias="X-Role"),
):
    """平台受众画像列表(首次访问自动初始化种子)"""
    _require_admin(x_role)
    try:
        result = await _service.audience.list_profiles()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/promo/audience/profiles/{platform}", tags=["AI智能推广模块"])
async def update_audience_profile(
    platform: str,
    data: UpdateProfileRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新平台画像(部分字段; 生成 Step2 即时生效)"""
    _require_admin(x_role)
    try:
        updates = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await _service.audience.update_profile(platform, updates)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/promo/audience/match", tags=["AI智能推广模块"])
async def audience_match(
    data: AudienceMatchRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """三维匹配预览: 内容角度×平台画像×产品调性 → 匹配分与建议"""
    _require_admin(x_role)
    try:
        result = await _service.audience.match(
            platform=data.platform, angle=data.angle,
            product_tone=data.productTone)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/audience/onsite", tags=["AI智能推广模块"])
async def audience_onsite_feedback(
    x_role: str = Header(None, alias="X-Role"),
    platform: str = Query(..., description="平台"),
):
    """站内画像回传(会员等级分布聚合 + 画像校准建议)"""
    _require_admin(x_role)
    try:
        result = await _service.audience.onsite_feedback(platform)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 权威信源库(admin)
# ============================================================

@router.get("/api/promo/authority/sources", tags=["AI智能推广模块"])
async def list_authority_sources(
    x_role: str = Header(None, alias="X-Role"),
    keyword: str = Query(None, description="标题/内容关键词过滤"),
):
    """权威信源列表(国标/协会公开数据/权威媒体; 首次访问自动初始化种子)"""
    _require_admin(x_role)
    try:
        result = await _service.authority.list_sources(keyword=keyword)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/promo/authority/sources", tags=["AI智能推广模块"])
async def add_authority_source(
    data: AuthoritySourceRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """新增权威信源(类别白名单 + 权威背书红线词校验)"""
    _require_admin(x_role)
    try:
        result = await _service.authority.add_source(
            title=data.title, category=data.category,
            content=data.content, allowed_usage=data.allowedUsage)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/authority/search", tags=["AI智能推广模块"])
async def authority_search(
    x_role: str = Header(None, alias="X-Role"),
    query: str = Query(..., min_length=1, description="检索查询"),
    topK: int = Query(3, ge=1, le=10, description="返回条数"),
):
    """权威信源 RAG 检索预览(2-gram 余弦 top-k, 生成引用池同一链路)"""
    _require_admin(x_role)
    try:
        result = await _service.authority.retrieve(query=query,
                                                   top_k=topK)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 报表(admin, 归因复用 attract)
# ============================================================

@router.get("/api/promo/report/overview", tags=["AI智能推广模块"])
async def report_overview(
    x_role: str = Header(None, alias="X-Role"),
):
    """全景报表(热点/决策/内容/发布/归因/单日上限)"""
    _require_admin(x_role)
    try:
        result = await _service.report_overview()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/report/platform", tags=["AI智能推广模块"])
async def report_platform(
    x_role: str = Header(None, alias="X-Role"),
):
    """平台维度报表(内容/发布/点击/注册/下单/单点击GMV)"""
    _require_admin(x_role)
    try:
        result = await _service.report_platform()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/promo/report/content-group/{group_id}", tags=["AI智能推广模块"])
async def report_content_group(
    group_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """一源多态横向对比(哪条平台版本跑得动)"""
    _require_admin(x_role)
    try:
        result = await _service.report_content_group(group_id=group_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_promo_routes(app):
    """注册36号·AI智能推广模块路由"""
    app.include_router(router)
