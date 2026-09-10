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


class CreateAccountRequest(PydBaseModel):
    platform: str = Field(..., description="平台: douyin/xiaohongshu/"
                                         "weibo/wechat_channels")
    alias: str = Field(..., min_length=1, max_length=64,
                       description="账号别名(如 抖音主号A)")
    note: str = Field("", max_length=200, description="备注")
    tags: list = Field(None, description="账号标签画像(P5c 协同调度: "
                                   "deals/lifestyle/warm/business/tasting)")


class GenerateCommentRequest(PydBaseModel):
    targetWorkKey: str = Field(..., min_length=1, max_length=64,
                               description="目标作品键(扫描结果返回)")


class SurvivalCheckRequest(PydBaseModel):
    alive: bool = Field(..., description="评论是否存活(被删→账号降权)")


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


@router.get("/api/blogger/learning/health",
            tags=["平台流量DV博主模块"])
async def learning_health(x_role: str = Header(None, alias="X-Role")):
    """学习健康三层视图(层1权重与污染熔断 / 层2冻结止损缓刑 / 质量门)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _service.learning_health()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/learning/calibrate",
             tags=["平台流量DV博主模块"])
async def learning_calibrate(x_role: str = Header(None, alias="X-Role")):
    """手动触发平台偏置重算(引流率差 ×λ, clamp ±8 分; 样本<5置0)"""
    _require_admin(x_role)
    try:
        result = await _service.recompute_platform_bias()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 发布账号矩阵(P3c, admin)
# ============================================================

@router.post("/api/blogger/accounts", tags=["平台流量DV博主模块"])
async def create_account(req: CreateAccountRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """新增发布账号(LRU 轮询池; 单账号日帽3条, 限流冷却24h)"""
    _require_admin(x_role)
    try:
        from services.blogger_account_service import \
            BloggerAccountService
        account = await BloggerAccountService().create_account(
            platform=req.platform, alias=req.alias, note=req.note,
            tags=req.tags)
        return {"success": True, "data": account}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/accounts", tags=["平台流量DV博主模块"])
async def list_accounts(
    x_role: str = Header(None, alias="X-Role"),
    platform: str = Query(None, description="平台筛选"),
    status: str = Query(None, description="active/cooling/banned"),
):
    """账号池列表(过期 cooling 自动回 active)"""
    _require_admin(x_role)
    try:
        from services.blogger_account_service import \
            BloggerAccountService
        accounts = await BloggerAccountService().list_accounts(
            platform=platform, status=status)
        return {"success": True, "data": accounts}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/accounts/overview",
            tags=["平台流量DV博主模块"])
async def accounts_overview(x_role: str = Header(None,
                                                 alias="X-Role")):
    """账号池全景(按平台聚合: 在役/冷却/封号/日计数)"""
    _require_admin(x_role)
    try:
        from services.blogger_account_service import \
            BloggerAccountService
        return {"success": True, "data":
                await BloggerAccountService().pool_overview()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/accounts/{account_id}/activate",
             tags=["平台流量DV博主模块"])
async def activate_account(account_id: int,
                           x_role: str = Header(None,
                                                alias="X-Role")):
    """恢复账号(banned/cooling → active, 清零失败计数)"""
    _require_admin(x_role)
    try:
        from services.blogger_account_service import \
            BloggerAccountService
        account = await BloggerAccountService().activate_account(
            account_id)
        return {"success": True, "data": account}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/accounts/{account_id}/ban",
             tags=["平台流量DV博主模块"])
async def ban_account(account_id: int,
                      x_role: str = Header(None, alias="X-Role")):
    """手动封号(违规/风险账号)"""
    _require_admin(x_role)
    try:
        from services.blogger_account_service import \
            BloggerAccountService
        account = await BloggerAccountService().ban_account(
            account_id)
        return {"success": True, "data": account}
    except Exception as e:
        _handle(e)


@router.delete("/api/blogger/accounts/{account_id}",
               tags=["平台流量DV博主模块"])
async def delete_account(account_id: int,
                         x_role: str = Header(None, alias="X-Role")):
    """删除账号"""
    _require_admin(x_role)
    try:
        from services.blogger_account_service import \
            BloggerAccountService
        account = await BloggerAccountService().delete_account(
            account_id)
        return {"success": True, "data": account}
    except Exception as e:
        _handle(e)


# ============================================================
# 评论区截流(P3d, admin)
# ============================================================

@router.post("/api/blogger/comments/scan",
             tags=["平台流量DV博主模块"])
async def comments_scan(x_role: str = Header(None, alias="X-Role")):
    """扫描热门大V作品(Mock源+风险否决+三因子评分+单作品护栏)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        result = await CommentInterceptService().scan_hot_works()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/comments/generate",
             tags=["平台流量DV博主模块"])
async def comment_generate(req: GenerateCommentRequest,
                           x_role: str = Header(None,
                                                alias="X-Role")):
    """生成截流评论(≥70分目标: 共鸣+提及+短码 → 三审)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        comment = await CommentInterceptService().generate_comment(
            req.targetWorkKey)
        return {"success": True, "data": comment}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/comments", tags=["平台流量DV博主模块"])
async def list_comments(
    x_role: str = Header(None, alias="X-Role"),
    platform: str = Query(None, description="平台筛选"),
    status: str = Query(None,
                        description="pending/approved/posted/deleted"),
):
    """截流评论列表"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        comments = await CommentInterceptService().repo.list_comments(
            platform=platform, status=status)
        return {"success": True, "data": comments}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/comments/{comment_id}/review",
             tags=["平台流量DV博主模块"])
async def comment_review(comment_id: int, req: ReviewRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """评论人工审核(pending → approved/deleted)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        comment = await CommentInterceptService().review_comment(
            comment_id, req.approved)
        return {"success": True, "data": comment}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/comments/{comment_id}/post",
             tags=["平台流量DV博主模块"])
async def comment_post(comment_id: int,
                       x_role: str = Header(None, alias="X-Role")):
    """发布评论(账号矩阵选号, 单账号单作品1条)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        comment = await CommentInterceptService().post_comment(
            comment_id)
        return {"success": True, "data": comment}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/comments/collect",
             tags=["平台流量DV博主模块"])
async def comments_collect(x_role: str = Header(None, alias="X-Role")):
    """批量回流评论归因(过存活窗口 → 账号层 hit/miss 信号)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        result = await CommentInterceptService() \
            .collect_comment_feedback()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/comments/{comment_id}/survival",
             tags=["平台流量DV博主模块"])
async def comment_survival(
        comment_id: int, req: SurvivalCheckRequest,
        x_role: str = Header(None, alias="X-Role")):
    """存活检查上报(被删 → deleted + 账号降权, 24h 口径)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        comment = await CommentInterceptService().check_survival(
            comment_id, req.alive)
        return {"success": True, "data": comment}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/comments/report",
            tags=["平台流量DV博主模块"])
async def comments_report(x_role: str = Header(None,
                                               alias="X-Role")):
    """截流全景(评论量/状态分布/归因汇总)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        return {"success": True, "data":
                await CommentInterceptService().report()}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/comments/{comment_id}/attribution",
            tags=["平台流量DV博主模块"])
async def comment_attribution(
        comment_id: int,
        x_role: str = Header(None, alias="X-Role")):
    """单条评论归因(短码点击/注册/下单/GMV)"""
    _require_admin(x_role)
    try:
        from services.comment_intercept_service import \
            CommentInterceptService
        result = await CommentInterceptService() \
            .comment_attribution(comment_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P5a 自主学习引擎(信号三通道 + 复合奖励 + 微调研, 设计文档 P5 §3)
# ============================================================

class SignalCollectRequest(PydBaseModel):
    followId: int = Field(None, description="指定已发布内容(空则全量采集)")


class NegativeReportRequest(PydBaseModel):
    followId: int = Field(..., description="归属跟随内容")
    reports: int = Field(0, ge=0, description="举报数")
    complaints: int = Field(0, ge=0, description="投诉数")
    comments: list = Field(None, description="评论文本列表"
                                        "(负面词表密度判定)")
    rateLimits: int = Field(0, ge=0, description="平台限流次数")


class PollCreateRequest(PydBaseModel):
    topic: str = Field(..., min_length=1, max_length=64,
                       description="调研主题(如: 钩子风格选择)")
    question: str = Field(..., min_length=1, max_length=200,
                          description="调研问题")
    options: list = Field(..., min_length=2, max_length=5,
                          description="2-5 个选项")
    confidence: float = Field(..., ge=0, le=1,
                              description="当前决策置信度(须<0.8)")
    scenario: str = Field("creation", max_length=32,
                          description="场景: creation/publish/boost")


class PollVoteRequest(PydBaseModel):
    option: int = Field(..., ge=0, description="选项下标(0 起)")


def _auto_learn_service():
    from services.blogger_auto_learn_service import \
        BloggerAutoLearnService
    return BloggerAutoLearnService()


@router.post("/api/blogger/auto/signals/collect",
             tags=["平台流量DV博主模块"])
async def auto_signals_collect(req: SignalCollectRequest,
                               x_role: str = Header(None,
                                                    alias="X-Role")):
    """三通道信号采集(正向 attract 归因 + 合规三审分; 滚动采样)"""
    _require_admin(x_role)
    try:
        result = await _auto_learn_service().collect_signals(
            follow_id=req.followId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/signals/status",
            tags=["平台流量DV博主模块"])
async def auto_signals_status(
        x_role: str = Header(None, alias="X-Role")):
    """信号统计视图(通道/种类计数 + 未消费数)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _auto_learn_service().signals_status()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/signals/report-negative",
             tags=["平台流量DV博主模块"])
async def auto_signals_report_negative(
        req: NegativeReportRequest,
        x_role: str = Header(None, alias="X-Role")):
    """负向信号上报(举报/投诉/评论负面情绪/限流——确定性词表判定)"""
    _require_admin(x_role)
    try:
        result = await _auto_learn_service().report_negative(
            req.followId, reports=req.reports,
            complaints=req.complaints,
            comments=req.comments, rate_limits=req.rateLimits)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/learn/run",
             tags=["平台流量DV博主模块"])
async def auto_learn_run(x_role: str = Header(None, alias="X-Role")):
    """信号消费学习轮(聚合→复合奖励→44号 Hedge→学习轮触发)"""
    _require_admin(x_role)
    try:
        result = await _auto_learn_service().run_learning()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/reward/config",
            tags=["平台流量DV博主模块"])
async def auto_reward_config(
        x_role: str = Header(None, alias="X-Role")):
    """复合奖励参数只读(β 宪法域不可调整)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _auto_learn_service().get_reward_config()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/polls/create",
             tags=["平台流量DV博主模块"])
async def auto_polls_create(req: PollCreateRequest,
                            x_role: str = Header(None,
                                                 alias="X-Role")):
    """发起微调研(仅低置信<0.8 场景; 人机共学)"""
    _require_admin(x_role)
    try:
        poll = await _auto_learn_service().create_poll(
            req.topic, req.question, req.options,
            req.confidence, scenario=req.scenario)
        return {"success": True, "data": poll}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/polls/{poll_id}/vote",
             tags=["平台流量DV博主模块"])
async def auto_polls_vote(poll_id: int, req: PollVoteRequest,
                          x_role: str = Header(None,
                                               alias="X-Role")):
    """运营投票(单票即定; 生成 human_feedback 信号留痕)"""
    _require_admin(x_role)
    try:
        poll = await _auto_learn_service().vote_poll(
            poll_id, req.option)
        return {"success": True, "data": poll}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/polls/pending",
            tags=["平台流量DV博主模块"])
async def auto_polls_pending(x_role: str = Header(None,
                                                  alias="X-Role")):
    """待调研列表(open)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_learn_service().list_pending_polls()}
    except Exception as e:
        _handle(e)


# ============================================================
# P5b 自主创作工坊(多版本生成 + AB实验 + 策略库 + UGC, 设计文档 P5 §4)
# ============================================================

class AutoGenerateRequest(PydBaseModel):
    topic: str = Field(..., min_length=1, max_length=64,
                       description="选题主题(如: 年货送礼攻略)")
    audience: str = Field(..., description="人群画像: student/mom/"
                                        "senior/business/wine_lover")
    platform: str = Field("douyin", description="发布平台")


class VersionMetricsRequest(PydBaseModel):
    clicks: int = Field(None, ge=0, description="点击数(归因回填)")
    registered: int = Field(None, ge=0, description="注册数")
    ordered: int = Field(None, ge=0, description="下单数")


class UgcAssetRequest(PydBaseModel):
    ownerId: int = Field(..., description="素材归属会员(高信值用户)")
    title: str = Field(..., min_length=1, max_length=64,
                       description="素材标题")
    license: str = Field(..., description="授权: authorized/cc_by/"
                                        "purchased")
    commissionRate: float = Field(0.05, ge=0, le=0.5,
                                  description="分成比例[0,0.5]")


class UgcRevenueRequest(PydBaseModel):
    gmv: float = Field(..., gt=0, description="结算 GMV(元)")


def _auto_create_service():
    from services.blogger_auto_create_service import \
        BloggerAutoCreateService
    return BloggerAutoCreateService()


@router.post("/api/blogger/auto/works/generate",
             tags=["平台流量DV博主模块"])
async def auto_works_generate(req: AutoGenerateRequest,
                              x_role: str = Header(None,
                                                   alias="X-Role")):
    """多版本并行生成(人群钩子矩阵×结构, 每版独立追踪码+合规内生)"""
    _require_admin(x_role)
    try:
        result = await _auto_create_service().generate_versions(
            req.topic, req.audience, platform=req.platform)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/experiments",
            tags=["平台流量DV博主模块"])
async def auto_experiments_list(
        x_role: str = Header(None, alias="X-Role"),
        status: str = Query(None, description="running/promoted/aborted")):
    """实验列表(含版本指标摘要)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_create_service().list_experiments(
                    status=status)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/experiments/{experiment_id}/promote",
             tags=["平台流量DV博主模块"])
async def auto_experiments_promote(
        experiment_id: int,
        x_role: str = Header(None, alias="X-Role")):
    """胜出评估+固化入策略库(样本≥50点击; 平局合规优先)"""
    _require_admin(x_role)
    try:
        result = await _auto_create_service().promote_experiment(
            experiment_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/strategies",
            tags=["平台流量DV博主模块"])
async def auto_strategies_list(
        x_role: str = Header(None, alias="X-Role"),
        type: str = Query(None, description="hook/structure")):
    """策略库排行(winCount → avgReward)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_create_service().list_strategies(
                    type=type)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/versions/{version_id}/metrics",
             tags=["平台流量DV博主模块"])
async def auto_version_metrics(version_id: int,
                               req: VersionMetricsRequest,
                               x_role: str = Header(None,
                                                    alias="X-Role")):
    """实验版本指标注入(attract 归因回填/测试轨; reward 就地重算)"""
    _require_admin(x_role)
    try:
        result = await _auto_create_service().record_version_metrics(
            version_id, clicks=req.clicks,
            registered=req.registered, ordered=req.ordered)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/ugc/assets",
             tags=["平台流量DV博主模块"])
async def auto_ugc_assets(req: UgcAssetRequest,
                         x_role: str = Header(None,
                                              alias="X-Role")):
    """UGC素材入库(授权登记; 高信值用户素材优先复用)"""
    _require_admin(x_role)
    try:
        asset = await _auto_create_service().register_ugc_asset(
            req.ownerId, req.title, req.license,
            commission_rate=req.commissionRate)
        return {"success": True, "data": asset}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/ugc/{asset_id}/revenue",
             tags=["平台流量DV博主模块"])
async def auto_ugc_revenue(asset_id: int, req: UgcRevenueRequest,
                            x_role: str = Header(None,
                                                 alias="X-Role")):
    """分成结算建议(仅生成 pending 建议书——给付须人工审批, 永不自动)"""
    _require_admin(x_role)
    try:
        asset = await _auto_create_service().propose_ugc_revenue(
            asset_id, req.gmv)
        return {"success": True, "data": asset}
    except Exception as e:
        _handle(e)


# ============================================================
# P5c 自主发布调度器(动态时机 + 账号协同 + 1h后调控, 设计文档 P5 §5)
# ============================================================

class BoostRequest(PydBaseModel):
    followId: int = Field(..., description="归属已发布内容")
    budget: float = Field(..., gt=0, description="推广预算(元; "
                                       "高预算>100须人工质押审批)")
    reason: str = Field("", max_length=200, description="推广理由")


def _auto_publish_service():
    from services.blogger_auto_publish_service import \
        BloggerAutoPublishService
    return BloggerAutoPublishService()


@router.get("/api/blogger/auto/publish/windows",
            tags=["平台流量DV博主模块"])
async def auto_publish_windows(
        x_role: str = Header(None, alias="X-Role"),
        platform: str = Query(None, description="平台过滤"),
        smart: str = Query(None, description="传 1 附加动态决策"
                                  "下一发布时间与TOP3时段")):
    """时段效果曲线视图(平台×小时 EMA 期望点击)"""
    _require_admin(x_role)
    try:
        svc = _auto_publish_service()
        data = {"windows": await svc.get_windows(platform)}
        if smart:
            targets = ([platform] if platform
                       else list(data["windows"].keys()))
            data["smart"] = {
                p: {"topSlots": await svc.best_slots(p),
                    "nextPublishAt":
                        await svc.next_smart_publish_time(p)}
                for p in targets}
        return {"success": True, "data": data}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/publish/windows/learn",
             tags=["平台流量DV博主模块"])
async def auto_publish_windows_learn(
        x_role: str = Header(None, alias="X-Role")):
    """时段曲线重算(已发布内容点击效果 EMA 增量收敛)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _auto_publish_service().learn_windows()}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/publish/postcheck",
            tags=["平台流量DV博主模块"])
async def auto_publish_postcheck(
        x_role: str = Header(None, alias="X-Role"),
        followId: int = Query(None, description="指定已发布内容"
                                                "(空则取最新一条)")):
    """发布后 1h 调控视图(互动低迷→推广建议/高频疑问→FAQ置顶/
    负面苗头→隐藏候选仅建议)"""
    _require_admin(x_role)
    try:
        svc = _auto_publish_service()
        if followId is None:
            published = await svc.repo.list_follows(
                status="published", limit=1)
            if not published:
                raise ValueError("无已发布内容可调控")
            followId = published[0]["followId"]
        return {"success": True,
                "data": await svc.postcheck(followId)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/publish/boost",
             tags=["平台流量DV博主模块"])
async def auto_publish_boost(req: BoostRequest,
                            x_role: str = Header(None,
                                                 alias="X-Role")):
    """追加推广(低预算线内自动执行; 高预算仅生成待审建议书
    ——质押审批, 永不自动执行)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                _auto_publish_service().execute_boost(
                    req.followId, req.budget, reason=req.reason)}
    except Exception as e:
        _handle(e)


# ============================================================
# P5d 自治理与进化层(自愈 + 透明度看板 + 干预通道, 设计文档 P5 §6)
# ============================================================

class PauseRequest(PydBaseModel):
    reason: str = Field(..., min_length=1, max_length=200,
                       description="暂停理由(留痕审计)")
    operator: str = Field("admin", max_length=50)


class RollbackRequest(PydBaseModel):
    snapshotId: int = Field(None, description="指定快照(空则最新)")
    operator: str = Field("admin", max_length=50)


class InjectRequest(PydBaseModel):
    rule: dict = Field(..., description="规则 {type, value, scope?}"
                       "(type/value 必填, 即时生效留痕)")
    operator: str = Field("admin", max_length=50)


def _auto_govern_service():
    from services.blogger_auto_govern_service import \
        BloggerAutoGovernService
    return BloggerAutoGovernService()


@router.get("/api/blogger/auto/health/evolution",
            tags=["平台流量DV博主模块"])
async def auto_health_evolution(
        x_role: str = Header(None, alias="X-Role")):
    """进化透明度看板(自治开关/漏斗/策略排行/干预史/自愈流水)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_govern_service().evolution_dashboard()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/intervention/pause",
             tags=["平台流量DV博主模块"])
async def auto_intervention_pause(req: PauseRequest,
                                 x_role: str = Header(None,
                                                      alias="X-Role")):
    """人工暂停(最高优先级——冻结全部自主行为, 即时生效留痕)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_govern_service().pause_autonomy(
                    req.reason, operator=req.operator)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/intervention/resume",
             tags=["平台流量DV博主模块"])
async def auto_intervention_resume(
        x_role: str = Header(None, alias="X-Role")):
    """恢复自主行为(显式 resume——永不自动恢复)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_govern_service().resume_autonomy()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/intervention/rollback",
             tags=["平台流量DV博主模块"])
async def auto_intervention_rollback(req: RollbackRequest,
                                      x_role: str = Header(None,
                                                          alias="X-Role")):
    """策略库回滚(pause 时自动抓快照; 快照缺失即拒绝)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_govern_service().rollback_strategies(
                    req.snapshotId, operator=req.operator)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/auto/intervention/inject",
             tags=["平台流量DV博主模块"])
async def auto_intervention_inject(req: InjectRequest,
                                   x_role: str = Header(None,
                                                       alias="X-Role")):
    """规则注入(热更新——即时生效并留痕)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_govern_service().inject_rule(
                    req.rule, operator=req.operator)}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/auto/audit/decision/{follow_id}",
            tags=["平台流量DV博主模块"])
async def auto_audit_decision(follow_id: int,
                             x_role: str = Header(None,
                                                  alias="X-Role")):
    """决策可解释报告(规则命中链路回放——AI 为何这样决策)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _auto_govern_service().decision_explain(
                    follow_id)}
    except Exception as e:
        _handle(e)


# ============================================================
# P6a 多模态自主学习引擎(视听信号 + 情感对齐奖励 γ≥α + 调性表
# + 封禁元素库, 设计文档《40号 P6 升级方案》§3)
# ============================================================

class AVSignalReportRequest(PydBaseModel):
    followId: int = Field(..., description="跟随内容 ID(已发布)")
    completionRate: float = Field(None, ge=0, le=1,
                                  description="完播率 [0,1]")
    shareRate: float = Field(None, ge=0, le=1,
                             description="分享率 [0,1]")
    favoriteRate: float = Field(None, ge=0, le=1,
                                description="收藏率 [0,1]")
    danmaku: list = Field(None, description="弹幕样本(词表密度判定)")
    comments: list = Field(None, description="评论样本(正面密度分量)")


class AVSignalCollectRequest(PydBaseModel):
    followId: int = Field(None, description="指定内容(空=全量批量)")


class BannedElementRequest(PydBaseModel):
    kind: str = Field(..., max_length=30,
                      description="种类: bgm/transition")
    value: str = Field(..., min_length=1, max_length=200,
                      description="元素取值(BGM 名/转场名)")
    platform: str = Field(None, max_length=30,
                          description="平台(空=全平台封禁)")
    source: str = Field("admin", max_length=50)


def _av_learn_service():
    from services.blogger_av_learn_service import BloggerAVLearnService
    return BloggerAVLearnService()


@router.post("/api/blogger/av/signals/report",
             tags=["平台流量DV博主模块"])
async def av_signals_report(req: AVSignalReportRequest,
                            x_role: str = Header(None,
                                                 alias="X-Role")):
    """视听指标上报(上传轨——完播/分享/收藏/弹幕情感, 滚动采样)"""
    _require_admin(x_role)
    try:
        result = await _av_learn_service().report_av_metrics(
            req.followId, completion_rate=req.completionRate,
            share_rate=req.shareRate,
            favorite_rate=req.favoriteRate,
            danmaku=req.danmaku, comments=req.comments)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/signals/collect",
             tags=["平台流量DV博主模块"])
async def av_signals_collect(req: AVSignalCollectRequest,
                             x_role: str = Header(None,
                                                  alias="X-Role")):
    """视听信号批量采集(滚动轨——avMetrics 重采样)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_learn_service().collect_av_signals(
                    req.followId)}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/av/signals/status",
            tags=["平台流量DV博主模块"])
async def av_signals_status(
        x_role: str = Header(None, alias="X-Role")):
    """视听信号统计视图(AV kind 计数 + 未消费数)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_learn_service().av_signals_status()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/learn/run",
             tags=["平台流量DV博主模块"])
async def av_learn_run(x_role: str = Header(None, alias="X-Role")):
    """视听信号消费学习轮(聚合→情感对齐奖励→44号 Hedge)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_learn_service().run_av_learning()}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/av/reward/config",
            tags=["平台流量DV博主模块"])
async def av_reward_config(
        x_role: str = Header(None, alias="X-Role")):
    """情感对齐奖励参数只读(β/γ 宪法域: β 不可调, γ≥α 拒改)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_learn_service().get_av_reward_config()}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/av/tone/{platform}",
            tags=["平台流量DV博主模块"])
async def av_tone(platform: str,
                  x_role: str = Header(None, alias="X-Role")):
    """平台调性参数视图(节奏/时长/密度 + 适用封禁元素)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_learn_service().get_tone(platform)}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/av/banned",
             tags=["平台流量DV博主模块"])
async def av_banned_list(platform: str = None, kind: str = None,
                         x_role: str = Header(None,
                                              alias="X-Role")):
    """封禁视听元素列表(全平台共享 + 平台增量)"""
    _require_admin(x_role)
    try:
        elements = await _av_learn_service().repo.list_banned_elements(
            platform=platform, kind=kind)
        return {"success": True, "data": elements}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/banned",
             tags=["平台流量DV博主模块"])
async def av_banned_add(req: BannedElementRequest,
                        x_role: str = Header(None,
                                             alias="X-Role")):
    """封禁视听元素登记(实时同步全平台约束库)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_learn_service().add_banned_element(
                    req.kind, req.value, platform=req.platform,
                    source=req.source)}
    except Exception as e:
        _handle(e)


# ============================================================
# P6b 音视频自主创作工坊(脚本生成 + 人设/授权管理 + 渲染 + AB,
# 设计文档《40号 P6 升级方案》§4)
# ============================================================

class AVScriptGenerateRequest(PydBaseModel):
    topic: str = Field(..., min_length=1, max_length=200,
                       description="选题主题")
    platform: str = Field(..., max_length=30,
                          description="六平台之一")
    personaId: int = Field(..., description="人设 ID")
    hookType: str = Field(..., max_length=50,
                          description="P5b 五类人群钩子")
    structureType: str = Field(None, max_length=50,
                               description="结构(空=痛点解法)")
    style: str = Field("", max_length=30,
                       description="风格指令(更温暖/更专业/更活泼)")
    bgmLicenseId: int = Field(None, description="BGM 授权 ID")
    materialSlots: list = Field(None,
                                 description="素材槽授权 ID 列表")


class PersonaRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    personaType: str = Field(..., max_length=30,
                              description="original_ip/licensed")
    voiceStyle: str = Field("medium", max_length=20,
                            description="slow/medium/fast")
    toneStyle: str = Field("warm", max_length=20,
                           description="warm/professional/playful")
    licenseId: int = Field(None,
                           description="licensed 人设绑定的授权 ID")


class LicenseRequest(PydBaseModel):
    kind: str = Field(..., max_length=20,
                      description="voice/likeness/bgm/material")
    name: str = Field(..., min_length=1, max_length=200)
    grantor: str = Field(..., min_length=1, max_length=100)
    scope: str = Field("", max_length=200)
    expiresAt: str = Field("", max_length=40,
                           description="ISO(空=永久)")


class AVRenderRequest(PydBaseModel):
    scriptId: int = Field(..., description="脚本 ID")


class AVExperimentRequest(PydBaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(..., max_length=30)
    workIds: list = Field(..., description="已渲染作品 ID 列表(≥2)")


def _av_create_service():
    from services.blogger_av_create_service import \
        BloggerAVCreateService
    return BloggerAVCreateService()


@router.post("/api/blogger/av/scripts/generate",
             tags=["平台流量DV博主模块"])
async def av_scripts_generate(req: AVScriptGenerateRequest,
                              x_role: str = Header(None,
                                                   alias="X-Role")):
    """确定性脚本生成(形式决策+授权硬门+分镜列表+水印断言)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().generate_script(
                    req.topic, req.platform, req.personaId,
                    req.hookType, structure_type=req.structureType,
                    style=req.style, bgm_license_id=req.bgmLicenseId,
                    material_slots=req.materialSlots)}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/av/personas",
            tags=["平台流量DV博主模块"])
async def av_personas_list(status: str = None,
                           x_role: str = Header(None,
                                                alias="X-Role")):
    """人设库列表(原创 IP + 已授权声纹/肖像)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().repo.list_personas(
                    status=status)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/personas",
             tags=["平台流量DV博主模块"])
async def av_personas_add(req: PersonaRequest,
                          x_role: str = Header(None,
                                               alias="X-Role")):
    """人设入库(licensed 须绑定在役声纹/肖像授权)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().register_persona(
                    req.name, req.personaType,
                    voice_style=req.voiceStyle,
                    tone_style=req.toneStyle,
                    license_id=req.licenseId)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/licenses",
             tags=["平台流量DV博主模块"])
async def av_licenses_add(req: LicenseRequest,
                          x_role: str = Header(None,
                                              alias="X-Role")):
    """授权记录登记(声纹/肖像/BGM/素材——哈希存证)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().register_license(
                    req.kind, req.name, req.grantor, scope=req.scope,
                    expires_at=req.expiresAt)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/licenses/{license_id}/revoke",
             tags=["平台流量DV博主模块"])
async def av_licenses_revoke(license_id: int,
                             x_role: str = Header(None,
                                                  alias="X-Role")):
    """授权撤回(撤回后关联人设/脚本生成即拒绝——秒级联动)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().revoke_license(
                    license_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/works/render",
             tags=["平台流量DV博主模块"])
async def av_works_render(req: AVRenderRequest,
                          x_role: str = Header(None,
                                               alias="X-Role")):
    """音视频渲染(AV_CHANNEL_MODE 三态: mock/real/mock_fallback)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().render_work(
                    req.scriptId)}
    except Exception as e:
        _handle(e)


@router.get("/api/blogger/av/works/{work_id}",
            tags=["平台流量DV博主模块"])
async def av_works_detail(work_id: int,
                          x_role: str = Header(None,
                                              alias="X-Role")):
    """作品详情(meta + 渲染状态)"""
    _require_admin(x_role)
    try:
        work = await _av_create_service().repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        return {"success": True, "data": work}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/works/ab",
             tags=["平台流量DV博主模块"])
async def av_works_ab(req: AVExperimentRequest,
                     x_role: str = Header(None,
                                          alias="X-Role")):
    """AV 作品入 A/B 实验(复用 P5b 实验状态机)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_create_service().create_av_experiment(
                    req.topic, req.platform, req.workIds)}
    except Exception as e:
        _handle(e)


# ============================================================
# P6c 音视频自主发布调度器(渲染参数表 + 完播加权黄金时段 +
# 首发审批 + 1h 互动自愈, 设计文档《40号 P6 升级方案》§5)
# ============================================================

class AVPublishRequest(PydBaseModel):
    workId: int = Field(..., description="已渲染作品 ID")
    publishAt: str = Field(None, max_length=40,
                            description="发布时间 ISO(空=立即)")
    approved: bool = Field(False,
                           description="新平台首发人工放行标记")


class AVPostcheckRequest(PydBaseModel):
    workId: int = Field(..., description="已发布作品 ID")
    danmaku: list = Field(None, description="弹幕样本(1h 窗)")
    comments: list = Field(None, description="评论样本(1h 窗)")
    playbackError: str = Field(None, max_length=200,
                               description="播放回执错误(音画不同步轨)")


class AVBoostRequest(PydBaseModel):
    workId: int = Field(..., description="已发布作品 ID")
    budget: float = Field(..., gt=0,
                          description="推广预算(>100 须质押审批)")
    reason: str = Field("", max_length=200)


class AVMetricsRequest(PydBaseModel):
    workId: int = Field(..., description="作品 ID")
    clicks: int = Field(None, ge=0)
    completionRate: float = Field(None, ge=0, le=1)
    shareRate: float = Field(None, ge=0, le=1)


def _av_publish_service():
    from services.blogger_av_publish_service import \
        BloggerAVPublishService
    return BloggerAVPublishService()


@router.get("/api/blogger/av/render/profiles",
            tags=["平台流量DV博主模块"])
async def av_render_profiles(
        x_role: str = Header(None, alias="X-Role")):
    """跨平台渲染参数表(六平台: 分辨率/码率/时长/字幕样式)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_publish_service()
                .ensure_render_profiles()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/publish/schedule",
             tags=["平台流量DV博主模块"])
async def av_publish_schedule(
        platform: str = Query(..., max_length=30),
        x_role: str = Header(None, alias="X-Role")):
    """黄金时段决策(完播加权 EMA TOP3; 冷启动回退静态窗)"""
    _require_admin(x_role)
    try:
        svc = _av_publish_service()
        return {"success": True, "data": {
            "platform": platform,
            "nextPublishAt": await svc.next_av_publish_time(
                platform),
            "bestSlots": await svc.best_av_slots(platform)}}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/publish/work",
             tags=["平台流量DV博主模块"])
async def av_publish_work(req: AVPublishRequest,
                          x_role: str = Header(None,
                                               alias="X-Role")):
    """发布 AV 作品(新平台前 10 条须人工放行)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_publish_service().publish_av_work(
                    req.workId, publish_at=req.publishAt,
                    approved=req.approved)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/windows/learn",
             tags=["平台流量DV博主模块"])
async def av_windows_learn(x_role: str = Header(None,
                                                alias="X-Role")):
    """AV 时段曲线重算(完播加权 EMA)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_publish_service().learn_av_windows()}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/works/metrics",
             tags=["平台流量DV博主模块"])
async def av_works_metrics(req: AVMetricsRequest,
                           x_role: str = Header(None,
                                                alias="X-Role")):
    """AV 作品指标注入(平台回执落地/测试轨)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_publish_service()
                .report_av_work_metrics(
                    req.workId, clicks=req.clicks,
                    completion_rate=req.completionRate,
                    share_rate=req.shareRate)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/publish/postcheck",
             tags=["平台流量DV博主模块"])
async def av_publish_postcheck(req: AVPostcheckRequest,
                               x_role: str = Header(None,
                                                   alias="X-Role")):
    """发布后 1h 互动自愈(推广建议/FAQ 置顶/负面仅建议/自愈重渲染)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_publish_service().postcheck_av(
                    req.workId, danmaku=req.danmaku,
                    comments=req.comments,
                    playback_error=req.playbackError)}
    except Exception as e:
        _handle(e)


@router.post("/api/blogger/av/publish/boost",
             tags=["平台流量DV博主模块"])
async def av_publish_boost(req: AVBoostRequest,
                           x_role: str = Header(None,
                                                alias="X-Role")):
    """追加推广(≤100 自动 mock 轨 / >100 生成待审建议书)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _av_publish_service().execute_av_boost(
                    req.workId, req.budget, reason=req.reason)}
    except Exception as e:
        _handle(e)


def register_blogger_routes(app) -> None:
    """注册40号路由(main.py startup 调用)"""
    app.include_router(router)
