"""40号·平台流量DV博主模块路由(P0, 19 端点)

鉴权:
    - 全部管理端: X-Role: admin(博主池/雷达/跟随/发布/报表)

异常映射(遵循项目约定):
    - KeyError → 404(博主/作品/跟随内容不存在)
    - ValueError → 409(状态非法/冷却期/超单日上限/平台无效等)

端点分布:
    - 博主池(7):  POST/GET /pool / GET/PUT/DELETE /pool/{id}
                  / POST /pool/{id}/pause|activate
    - 雷达侦测(4): POST /radar/scan / GET /works / GET /works/{id}
                  / POST /works/{id}/decide(重决策)
    - 跟随流水线(5): POST /works/{id}/manual-decide
                  / POST /works/{id}/follow(生成跟随)
                  / GET /follows / POST /follows/{id}/review
                  / GET /reviews/pending
    - 发布(2):    POST /follows/{id}/publish(入队三限) / POST /publish/run
    - 报表(2):    GET /report/overview / GET /report/blogger/{id}

P1 预留: /api/blogger/learning/*(Hedge 回流, 对齐 36号 P2 端点形态)
"""

from fastapi import APIRouter, Header, HTTPException, Query

from pydantic import BaseModel as PydBaseModel, Field

from services.blogger_service import BloggerService


router = APIRouter()
_service = BloggerService()


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

class CreateBloggerRequest(PydBaseModel):
    platform: str = Field(..., description="平台: douyin/xiaohongshu/"
                                         "weibo/wechat_channels")
    account: str = Field(..., min_length=1, max_length=64,
                         description="平台账号ID")
    nickname: str = Field(..., min_length=1, max_length=64,
                          description="博主昵称")
    fansWan: float = Field(..., gt=0, description="粉丝量(万)")
    domain: str = Field(..., description="领域: wine/food/gift/lifestyle")
    engagementRate: float = Field(0.05, ge=0, le=1,
                                  description="互动率(0-1)")


class UpdateBloggerRequest(PydBaseModel):
    nickname: str = Field(None, min_length=1, max_length=64)
    fansWan: float = Field(None, gt=0)
    domain: str = Field(None)
    engagementRate: float = Field(None, ge=0, le=1)
    status: str = Field(None)
    platform: str = Field(None)


class ManualDecideRequest(PydBaseModel):
    engage: bool = Field(..., description="true=确认跟随 / false=放弃留痕")
    note: str = Field("", max_length=200, description="裁决备注")


class ReviewRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过(三审人工)")
    reviewer: str = Field("admin", max_length=50, description="审核人")


class PublishRequest(PydBaseModel):
    publishAt: str = Field("", max_length=40,
                           description="指定发布时间(ISO, 空则取黄金时段)")


class LearningFeedbackRequest(PydBaseModel):
    followId: int = Field(..., description="已发布跟随内容ID")
    clicks: int = Field(None, ge=0,
                        description="引流量(空则自动从attract归因聚合)")
    registrations: int = Field(None, ge=0, description="注册数(可选)")
    orders: int = Field(None, ge=0, description="订单数(可选)")


# ============================================================
# 博主池管理(admin)
# ============================================================

@router.post("/api/blogger/pool", tags=["平台流量DV博主模块"])
async def create_blogger(req: CreateBloggerRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """新增博主(领域准入门槛: 须与酒/美食/礼品/生活相关)"""
    _require_admin(x_role)
    try:
        blogger = await _service.create_blogger(
            platform=req.platform, account=req.account,
            nickname=req.nickname, fans_wan=req.fansWan,
            domain=req.domain, engagement_rate=req.engagementRate)
        return {"success": True, "data": blogger}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/pool", tags=["平台流量DV博主模块"])
async def list_bloggers(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="active/paused"),
    platform: str = Query(None, description="平台筛选"),
    limit: int = Query(100, ge=1, le=1000),
):
    """博主池列表(按权重降序, 含流量归因体系关联ID)"""
    _require_admin(x_role)
    try:
        bloggers = await _service.repo.list_bloggers(
            status=status, platform=platform, limit=limit)
        return {"success": True, "data": bloggers}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/pool/{blogger_id}", tags=["平台流量DV博主模块"])
async def get_blogger(blogger_id: int,
                      x_role: str = Header(None, alias="X-Role")):
    """博主详情"""
    _require_admin(x_role)
    try:
        blogger = await _service.repo.get_blogger(blogger_id)
        if blogger is None:
            raise KeyError(f"博主不存在(bloggerId={blogger_id})")
        return {"success": True, "data": blogger}
    except Exception as e:
        _handle(e)


@router.put("/api/blogger/pool/{blogger_id}", tags=["平台流量DV博主模块"])
async def update_blogger(blogger_id: int, req: UpdateBloggerRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """更新博主档案(粉丝量变化联动权重)"""
    _require_admin(x_role)
    try:
        fields = {k: v for k, v in req.model_dump().items()
                  if v is not None}
        blogger = await _service.update_blogger(blogger_id, fields)
        return {"success": True, "data": blogger}
    except Exception as e:
        _handle(e)


@router.delete("/api/blogger/pool/{blogger_id}",
               tags=["平台流量DV博主模块"])
async def delete_blogger(blogger_id: int,
                         x_role: str = Header(None, alias="X-Role")):
    """删除博主(存在跟随内容时拒绝)"""
    _require_admin(x_role)
    try:
        blogger = await _service.delete_blogger(blogger_id)
        return {"success": True, "data": blogger}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/pool/{blogger_id}/pause",
             tags=["平台流量DV博主模块"])
async def pause_blogger(blogger_id: int,
                        x_role: str = Header(None, alias="X-Role")):
    """暂停博主(不再进入雷达扫描)"""
    _require_admin(x_role)
    try:
        blogger = await _service.set_blogger_status(blogger_id, "paused")
        return {"success": True, "data": blogger}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/pool/{blogger_id}/activate",
             tags=["平台流量DV博主模块"])
async def activate_blogger(blogger_id: int,
                           x_role: str = Header(None, alias="X-Role")):
    """恢复博主(重新进入雷达扫描)"""
    _require_admin(x_role)
    try:
        blogger = await _service.set_blogger_status(blogger_id, "active")
        return {"success": True, "data": blogger}
    except Exception as e:
        _handle(e)


# ============================================================
# 雷达与侦测(admin/调度器)
# ============================================================

@router.post("/api/blogger/radar/scan", tags=["平台流量DV博主模块"])
async def radar_scan(
    x_role: str = Header(None, alias="X-Role"),
    blogger_ids: str = Query("", description="指定博主ID(逗号分隔, "
                                             "空则全池active)"),
):
    """手动触发全池扫描(Mock增量源+指纹去重+风险否决+自动决策)"""
    _require_admin(x_role)
    try:
        ids = None
        if blogger_ids.strip():
            ids = tuple(int(x) for x in blogger_ids.split(",")
                        if x.strip().isdigit())
        result = await _service.scan(blogger_ids=ids)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/works", tags=["平台流量DV博主模块"])
async def list_works(
    x_role: str = Header(None, alias="X-Role"),
    bloggerId: int = Query(None, description="博主筛选"),
    status: str = Query(None, description="detected/auto_follow/"
                                          "manual_queue/passed/discarded/"
                                          "following"),
    limit: int = Query(100, ge=1, le=1000),
):
    """侦测作品列表(含评分快照与决策)"""
    _require_admin(x_role)
    try:
        works = await _service.radar.list_works(
            blogger_id=bloggerId, status=status, limit=limit)
        return {"success": True, "data": works}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/works/{work_id}", tags=["平台流量DV博主模块"])
async def get_work(work_id: int,
                   x_role: str = Header(None, alias="X-Role")):
    """作品详情(含评分快照与决策理由)"""
    _require_admin(x_role)
    try:
        work = await _service.radar.get_work(work_id)
        return {"success": True, "data": work}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/works/{work_id}/decide",
             tags=["平台流量DV博主模块"])
async def decide_work(work_id: int,
                      x_role: str = Header(None, alias="X-Role")):
    """手动重决策(第21档案评分 → 三档路由, detected 状态作品)"""
    _require_admin(x_role)
    try:
        work = await _service.repo.get_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(workId={work_id})")
        result = await _service.decide_work(work)
        return {"success": True, "data": {
            "work": result["work"], "scoring": result["scoring"]}}
    except Exception as e:
        _handle(e)


# ============================================================
# 跟随流水线(admin/调度器)
# ============================================================

@router.post("/api/blogger/works/{work_id}/manual-decide",
             tags=["平台流量DV博主模块"])
async def manual_decide(work_id: int, req: ManualDecideRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """人工裁决(50-70 区间人工确认队列: 确认跟随/放弃留痕)"""
    _require_admin(x_role)
    try:
        work = await _service.manual_decide(
            work_id, req.engage, note=req.note)
        return {"success": True, "data": work}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/works/{work_id}/follow",
             tags=["平台流量DV博主模块"])
async def generate_follow(work_id: int,
                          x_role: str = Header(None, alias="X-Role")):
    """生成跟随内容(auto_follow 作品: KOL码挂链+三段式生成+三审+存证)"""
    _require_admin(x_role)
    try:
        follow = await _service.generate_follow(work_id)
        return {"success": True, "data": follow}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/follows", tags=["平台流量DV博主模块"])
async def list_follows(
    x_role: str = Header(None, alias="X-Role"),
    bloggerId: int = Query(None, description="博主筛选"),
    status: str = Query(None, description="pending/approved/rejected/"
                                          "queued/published"),
    limit: int = Query(100, ge=1, le=1000),
):
    """跟随内容列表(含三段式文案/短码/回执)"""
    _require_admin(x_role)
    try:
        follows = await _service.repo.list_follows(
            blogger_id=bloggerId, status=status, limit=limit)
        return {"success": True, "data": follows}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/follows/{follow_id}/review",
             tags=["平台流量DV博主模块"])
async def review_follow(follow_id: int, req: ReviewRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """三审人工审核(pending → approved/rejected)"""
    _require_admin(x_role)
    try:
        follow = await _service.review_follow(
            follow_id, req.approved, reviewer=req.reviewer)
        return {"success": True, "data": follow}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/reviews/pending", tags=["平台流量DV博主模块"])
async def pending_reviews(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(100, ge=1, le=1000),
):
    """待人工审核队列(二审 60-79 强制人工 + manual_queue 确认后)"""
    _require_admin(x_role)
    try:
        follows = await _service.repo.list_follows(
            status="pending", limit=limit)
        return {"success": True, "data": follows}
    except Exception as e:
        _handle(e)


# ============================================================
# 发布调度(admin/调度器)
# ============================================================

@router.post("/api/blogger/follows/{follow_id}/publish",
             tags=["平台流量DV博主模块"])
async def publish_follow(follow_id: int, req: PublishRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """跟随内容入发布队列(approved → queued, 三限校验)"""
    _require_admin(x_role)
    try:
        follow = await _service.publish_follow(
            follow_id, publish_at=req.publishAt)
        return {"success": True, "data": follow}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/publish/run", tags=["平台流量DV博主模块"])
async def run_publish(x_role: str = Header(None, alias="X-Role")):
    """手动触发发布出队(到期 queued → 通道发布 + 回执 + SEO 推送)"""
    _require_admin(x_role)
    try:
        published = await _service.process_publish_queue()
        return {"success": True, "data": {
            "count": len(published), "published": published}}
    except Exception as e:
        _handle(e)


# ============================================================
# 归因与报表(admin)
# ============================================================

@router.get("/api/blogger/report/overview", tags=["平台流量DV博主模块"])
async def report_overview(x_role: str = Header(None, alias="X-Role")):
    """全景报表(博主池/侦测/跟随/发布/归因漏斗/三限参数)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _service.report_overview()}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/report/blogger/{blogger_id}",
            tags=["平台流量DV博主模块"])
async def report_blogger(blogger_id: int,
                         x_role: str = Header(None, alias="X-Role")):
    """单博主归因(引流量/注册/下单/GMV + KOL体系归因合并)"""
    _require_admin(x_role)
    try:
        result = await _service.get_blogger_attribution(blogger_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 学习闭环与权重自进化(P1, 对齐 36号 P2 端点形态)
# ============================================================

@router.post("/api/blogger/learning/feedback",
             tags=["平台流量DV博主模块"])
async def learning_feedback(req: LearningFeedbackRequest,
                            x_role: str = Header(None, alias="X-Role")):
    """单条效果回流(层1 Hedge反馈 + 层2博主权重进化, learningFed幂等)"""
    _require_admin(x_role)
    try:
        result = await _service.submit_learning_feedback(
            req.followId, clicks=req.clicks,
            registrations=req.registrations, orders=req.orders)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/learning/collect",
             tags=["平台流量DV博主模块"])
async def learning_collect(x_role: str = Header(None, alias="X-Role")):
    """批量回流: 已发布未回流且过沉淀窗口(24h)的内容"""
    _require_admin(x_role)
    try:
        result = await _service.collect_learning_feedback()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/learning/run",
             tags=["平台流量DV博主模块"])
async def learning_run(x_role: str = Header(None, alias="X-Role")):
    """触发一轮 Hedge 学习(第21档案, 反馈不足时 409)"""
    _require_admin(x_role)
    try:
        result = await _service.run_learning()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/learning/status",
            tags=["平台流量DV博主模块"])
async def learning_status(x_role: str = Header(None, alias="X-Role")):
    """回流与学习状态(层1权重档案/漂移 + 层2进化榜/止损榜)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _service.learning_status()}
    except Exception as e:
        _handle(e)


def register_blogger_routes(app) -> None:
    """注册40号路由(main.py startup 调用)"""
    app.include_router(router)
