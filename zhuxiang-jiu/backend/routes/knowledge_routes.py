"""AI智能知识库训练模块路由(P0+P1+P2: 知识底座+三源接入+智能进化, 32 端点)

鉴权:
    - 管理端(31): X-Role: admin 头(条目治理/缺口处置/迁移/种子/统计/
      教学会话/文档/多模态/抓取源管理/质量进化)
    - 公开(1): 检索测试(供联调与消费方验证)

异常映射(遵循项目约定):
    - KeyError → 404(条目/缺口/会话/文档/种子源不存在)
    - ValueError → 409(状态非法/违禁词/品牌表述禁忌/重复知识/
      主题域外/疗效断言/非法消费方等)
    - 权限校验 → 403(无权操作)

端点分布:
    - 条目(5):   创建 / 列表 / 详情 / 更新 / 版本历史
    - 治理(3):   审核 / 发布 / 退役
    - 缺口(2):   队列查询 / 处置
    - 迁移(1):   旧 chat FAQ 一次性迁移(幂等)
    - 种子(1):   品牌基准知识种子(D-17, 幂等)
    - 统计(1):   治理看板
    - 检索(1):   统一检索测试(公开)
    - 教学(3):   会话创建 / 提问 / 教学提交(P1)
    - 教学列表(1): 教学会话列表(P1)
    - 文档(2):   上传解析分块 / 文档列表(P1)
    - 多模态(2): 图片描述入库 / 视频时间轴入库(P1, D-14)
    - 抓取(4):   种子源添加 / 列表 / 内容接入 / 执行抓取(P1, D-15)
    - 质量(2):   淘汰扫描 / 质量报表(P2)
    - 缺口摘要(1): 高频缺口聚合(P2)
    - 自动过审(1): 渐进信任执行(P2, D-16)
    - 分发(1):   跨模块知识分发建议(P2)
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.knowledge_service import (
    KnowledgeService,
    ENTRY_STATUS_PENDING, ENTRY_STATUS_APPROVED,
    ENTRY_STATUS_PUBLISHED, ENTRY_STATUS_REJECTED, ENTRY_STATUS_RETIRED,
    SOURCE_MANUAL,
    GAP_STATUS_OPEN, GAP_STATUS_RESOLVED, GAP_STATUS_IGNORED,
    COMPLIANCE_PASS_SCORE, DUP_SIMILARITY_THRESHOLD, MIN_SIMILARITY,
)

router = APIRouter()
_service = KnowledgeService()

# 合法状态/来源(参数校验用)
_VALID_STATUSES = (ENTRY_STATUS_PENDING, ENTRY_STATUS_APPROVED,
                   ENTRY_STATUS_PUBLISHED, ENTRY_STATUS_REJECTED,
                   ENTRY_STATUS_RETIRED)
_VALID_SOURCES = ("manual", "chat_teaching", "document", "crawl",
                  "migration")


# ============================================================
# 鉴权与异常映射辅助(对齐 chat_routes 风格)
# ============================================================

def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=f"服务内部错误: {exc}")


# ============================================================
# 请求模型
# ============================================================

class CreateEntryRequest(PydBaseModel):
    question: str = Field(..., min_length=1, description="标准问题")
    answer: str = Field(..., min_length=1, description="标准答案")
    category: str = Field("faq", description="分类: product/faq/policy/order/activity/compliance")
    keywords: str = Field("", description="关键词(空格分隔, 增强召回)")
    source: str = Field("manual", description="来源: manual/chat_teaching/document/crawl")


class UpdateEntryRequest(PydBaseModel):
    question: str = Field(None, description="标准问题(不改传 None)")
    answer: str = Field(None, description="标准答案")
    keywords: str = Field(None, description="关键词")
    category: str = Field(None, description="分类")


class ReviewRequest(PydBaseModel):
    approve: bool = Field(..., description="true=通过 / false=拒绝")
    reason: str = Field("", description="拒绝原因(拒绝时必填建议)")


class ResolveGapRequest(PydBaseModel):
    action: str = Field("resolve", description="resolve=已补知识 / ignore=忽略")
    entryId: int = Field(0, description="关联的新知识条目ID(resolve 必填)")


class SearchRequest(PydBaseModel):
    query: str = Field(..., min_length=1, description="检索问题")
    category: str = Field(None, description="分类过滤")
    topK: int = Field(5, ge=1, le=20, description="返回条数")


# ---- P1 三源接入 ----

class CreateTeachSessionRequest(PydBaseModel):
    topic: str = Field(..., min_length=1, description="教学主题")


class TeachAskRequest(PydBaseModel):
    question: str = Field(..., min_length=1, description="提问内容")


class TeachSubmitRequest(PydBaseModel):
    question: str = Field(..., min_length=1, description="标准问题")
    answer: str = Field(..., min_length=1, description="标准答案")
    keywords: str = Field("", description="关键词")
    category: str = Field("faq", description="分类")


class IngestDocumentRequest(PydBaseModel):
    title: str = Field(..., min_length=1, description="文档标题")
    content: str = Field(..., min_length=1, description="文档正文(txt/md/csv/json 文本)")
    format: str = Field("text", description="格式: text/md/csv/json")
    category: str = Field("faq", description="知识分类")


class IngestImageRequest(PydBaseModel):
    title: str = Field(..., min_length=1, description="图片标题")
    description: str = Field(..., min_length=1, description="图片描述(rule 轨管理员配置)")
    url: str = Field("", description="图片地址")
    tags: str = Field("", description="标签(空格分隔)")


class VideoSegmentItem(PydBaseModel):
    timecode: str = Field("", description="时间点(如 03:20)")
    desc: str = Field(..., min_length=1, description="分段描述")
    keywords: str = Field("", description="分段关键词")


class IngestVideoRequest(PydBaseModel):
    title: str = Field(..., min_length=1, description="视频标题")
    url: str = Field(..., min_length=1, description="视频地址")
    segments: list[VideoSegmentItem] = Field(..., min_length=1,
                                              description="分段时间轴(rule 轨)")


class AddCrawlSourceRequest(PydBaseModel):
    name: str = Field(..., min_length=1, description="种子源名称")
    url: str = Field(..., min_length=1, description="种子源地址")
    topics: list[str] = Field(..., min_length=1,
                               description="主题域: wine/bamboo/bamboo_culture/bamboo_med/brand")


class CrawlIngestRequest(PydBaseModel):
    sourceId: int = Field(..., description="种子源ID")
    title: str = Field(..., min_length=1, description="内容标题")
    content: str = Field(..., min_length=1, description="网页正文(粘贴)")


# ============================================================
# 条目(5)
# ============================================================

@router.post("/api/knowledge/entries", tags=["AI智能知识库训练模块"])
async def create_entry(
    data: CreateEntryRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建知识条目(进入候选池 pending; 合规筛查+相似去重)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok", "data": await _service.create_entry(
            question=data.question, answer=data.answer,
            category=data.category, keywords=data.keywords,
            source=data.source)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/entries", tags=["AI智能知识库训练模块"])
async def list_entries(
    status: str = Query(None, description="状态筛选"),
    category: str = Query(None, description="分类筛选"),
    source: str = Query(None, description="来源筛选"),
    limit: int = Query(100, ge=1, le=500),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询知识条目列表(治理看板用)"""
    _require_admin(x_role)
    if status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=409,
                            detail=f"非法状态({status})")
    if source and source not in _VALID_SOURCES:
        raise HTTPException(status_code=409,
                            detail=f"非法来源({source})")
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.list_entries(
                    status=status, category=category, source=source,
                    limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/entries/{entry_id}", tags=["AI智能知识库训练模块"])
async def get_entry(
    entry_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询知识条目详情"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.get_entry(entry_id)}
    except Exception as exc:
        _handle(exc)


@router.put("/api/knowledge/entries/{entry_id}", tags=["AI智能知识库训练模块"])
async def update_entry(
    entry_id: int,
    data: UpdateEntryRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新知识条目(仅 pending/rejected 可改, 重新过合规与去重)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.update_entry(
                    entry_id, question=data.question, answer=data.answer,
                    keywords=data.keywords, category=data.category)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/entries/{entry_id}/versions",
            tags=["AI智能知识库训练模块"])
async def list_versions(
    entry_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询条目版本历史(每次发布生成快照)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.list_versions(entry_id)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 治理(3)
# ============================================================

@router.post("/api/knowledge/entries/{entry_id}/review",
             tags=["AI智能知识库训练模块"])
async def review_entry(
    entry_id: int,
    data: ReviewRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: int = Header(0, alias="X-Admin-Id"),
):
    """审核条目(pending → approved / rejected)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.review_entry(
                    entry_id, approve=data.approve,
                    reviewer_id=x_admin_id, reason=data.reason)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/entries/{entry_id}/publish",
             tags=["AI智能知识库训练模块"])
async def publish_entry(
    entry_id: int,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: int = Header(0, alias="X-Admin-Id"),
):
    """发布条目(approved → published, 生成版本快照)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.publish_entry(
                    entry_id, publisher_id=x_admin_id)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/entries/{entry_id}/retire",
             tags=["AI智能知识库训练模块"])
async def retire_entry(
    entry_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """退役条目(published → retired, 检索不再命中, 终态)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.retire_entry(entry_id)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 缺口(2)
# ============================================================

@router.get("/api/knowledge/gaps", tags=["AI智能知识库训练模块"])
async def list_gaps(
    status: str = Query(None, description="open/resolved/ignored"),
    limit: int = Query(100, ge=1, le=500),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询知识缺口队列(默认按提问次数倒序, 高频优先补)"""
    _require_admin(x_role)
    if status and status not in (GAP_STATUS_OPEN, GAP_STATUS_RESOLVED,
                                  GAP_STATUS_IGNORED):
        raise HTTPException(status_code=409, detail=f"非法状态({status})")
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.list_gaps(status=status, limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/gaps/{gap_id}/resolve",
             tags=["AI智能知识库训练模块"])
async def resolve_gap(
    gap_id: int,
    data: ResolveGapRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """处置缺口(resolve=已补知识并关联条目 / ignore=忽略)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.resolve_gap(
                    gap_id, action=data.action, entry_id=data.entryId)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 迁移(1)
# ============================================================

@router.post("/api/knowledge/migrate-chat", tags=["AI智能知识库训练模块"])
async def migrate_chat_faq(
    x_role: str = Header(None, alias="X-Role"),
):
    """旧 chat FAQ 一次性迁移到新知识库(D-13, 幂等, 旧表保留只读)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.migrate_chat_faq()}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/seed-brand", tags=["AI智能知识库训练模块"])
async def seed_brand_knowledge(
    x_role: str = Header(None, alias="X-Role"),
):
    """品牌基准知识种子(D-17, 幂等): 产品正确表述直接 published 入库

    本网产品事实: 竹笋/竹茎/竹叶+徂徕山富硒山泉水+专有菌群古法酿制,
    非浸泡/配制酒; 品牌禁忌表述由治理流水线拦截。
    """
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.seed_brand_knowledge()}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 统计(1) + 检索(1)
# ============================================================

@router.get("/api/knowledge/stats", tags=["AI智能知识库训练模块"])
async def stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """知识库统计概览(条目分布/命中率/缺口数)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok", "data": await _service.stats()}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/search", tags=["AI智能知识库训练模块"])
async def search(
    data: SearchRequest,
):
    """统一知识检索(公开; 消费方联调验证用, 不计入命中统计)"""
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.search(
                    query=data.query, category=data.category,
                    top_k=data.topK, record_hit=False)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# P1 三源接入: 教学(4)
# ============================================================

@router.post("/api/knowledge/teach/sessions", tags=["AI智能知识库训练模块"])
async def create_teach_session(
    data: CreateTeachSessionRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: int = Header(0, alias="X-Admin-Id"),
):
    """创建教学会话(对话式教学, P1)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.create_teach_session(
                    topic=data.topic, created_by=x_admin_id)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/teach/sessions", tags=["AI智能知识库训练模块"])
async def list_teach_sessions(
    limit: int = Query(50, ge=1, le=200),
    x_role: str = Header(None, alias="X-Role"),
):
    """教学会话列表"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.list_teach_sessions(limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/teach/sessions/{session_id}/ask",
             tags=["AI智能知识库训练模块"])
async def teach_ask(
    session_id: int,
    data: TeachAskRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """教学会话提问(检索已有知识作答; 未命中返回教学提示)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.teach_ask(
                    session_id, question=data.question)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/teach/sessions/{session_id}/teach",
             tags=["AI智能知识库训练模块"])
async def teach_submit(
    session_id: int,
    data: TeachSubmitRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """教学提交(Q+A 入库 pending + 自动闭环匹配知识缺口)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.teach_submit(
                    session_id, question=data.question,
                    answer=data.answer, keywords=data.keywords,
                    category=data.category)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 文档(2)
# ============================================================

@router.post("/api/knowledge/documents", tags=["AI智能知识库训练模块"])
async def ingest_document(
    data: IngestDocumentRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """文档上传解析分块入库(空行分段+超长句切, 批量 pending)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.ingest_document(
                    title=data.title, content=data.content,
                    fmt=data.format, category=data.category)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/documents", tags=["AI智能知识库训练模块"])
async def list_documents(
    limit: int = Query(50, ge=1, le=200),
    x_role: str = Header(None, alias="X-Role"),
):
    """文档列表(含分块入库统计)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.list_documents(limit=limit)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 多模态(2, D-14 rule 轨)
# ============================================================

@router.post("/api/knowledge/media/image", tags=["AI智能知识库训练模块"])
async def ingest_image(
    data: IngestImageRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """图片描述入库(rule 轨: 管理员配描述; llm 轨 P2 视觉大模型)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.ingest_image(
                    title=data.title, description=data.description,
                    url=data.url, tags=data.tags)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/media/video", tags=["AI智能知识库训练模块"])
async def ingest_video(
    data: IngestVideoRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """视频时间轴入库(分段=检索单元, 支持回答引用时间点; llm 轨 P2)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.ingest_video(
                    title=data.title, url=data.url,
                    segments=[s.model_dump() for s in data.segments])}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 全网抓取(4, D-15 白名单制)
# ============================================================

@router.post("/api/knowledge/crawl/sources", tags=["AI智能知识库训练模块"])
async def add_crawl_source(
    data: AddCrawlSourceRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """添加抓取种子源(白名单制, 绑定主题域)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.add_crawl_source(
                    name=data.name, url=data.url, topics=data.topics)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/crawl/sources", tags=["AI智能知识库训练模块"])
async def list_crawl_sources(
    limit: int = Query(50, ge=1, le=200),
    x_role: str = Header(None, alias="X-Role"),
):
    """种子源列表(含入库/拒绝统计)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.list_crawl_sources(limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/crawl/ingest", tags=["AI智能知识库训练模块"])
async def crawl_ingest(
    data: CrawlIngestRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """抓取内容接入(粘贴正文): 主题域过滤→医药加严→分块→批量 pending"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.crawl_ingest(
                    source_id=data.sourceId, title=data.title,
                    content=data.content)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/crawl/run", tags=["AI智能知识库训练模块"])
async def crawl_run(
    data: CrawlIngestRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """执行抓取(rule 轨: urllib 拉取 URL→提取正文→接入流程; llm 轨 P2)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.crawl_run(
                    source_id=data.sourceId)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# P2 智能进化: 质量(2) + 缺口摘要(1) + 自动过审(1) + 分发(1)
# ============================================================

@router.post("/api/knowledge/quality/sweep", tags=["AI智能知识库训练模块"])
async def quality_sweep(
    x_role: str = Header(None, alias="X-Role"),
):
    """质量淘汰扫描: 重算质量分, 低分+陈旧(分<30且超60天)降级退役"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.quality_sweep()}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/quality/report", tags=["AI智能知识库训练模块"])
async def quality_report(
    x_role: str = Header(None, alias="X-Role"),
):
    """质量报表: 高价值/低分清单+均分+分类分布"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.quality_report()}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/gaps/summary", tags=["AI智能知识库训练模块"])
async def gaps_summary(
    x_role: str = Header(None, alias="X-Role"),
):
    """缺口摘要: 高频缺口聚合+主题域归属, 驱动优先补知识"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.gaps_summary()}
    except Exception as exc:
        _handle(exc)


@router.post("/api/knowledge/auto-approve/run", tags=["AI智能知识库训练模块"])
async def auto_approve_run(
    x_role: str = Header(None, alias="X-Role"),
):
    """渐进信任自动过审(D-16): 高可信来源 pending 条目自动 approve"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.auto_approve_run()}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/distribution/suggest",
            tags=["AI智能知识库训练模块"])
async def distribution_suggest(
    consumer: str = Query(..., description="消费方: product/attract/chat"),
    limit: int = Query(10, ge=1, le=50),
    x_role: str = Header(None, alias="X-Role"),
):
    """跨模块知识分发建议: 高质量分 published 条目按消费方偏好加权"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.distribution_suggest(
                    consumer=consumer, limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/knowledge/quality/status", tags=["AI智能知识库训练模块"])
async def quality_scheduler_status(
    x_role: str = Header(None, alias="X-Role"),
):
    """质量进化调度器状态(运行中/周期/开关)"""
    _require_admin(x_role)
    from services.knowledge_quality_scheduler import (
        scheduler_enabled, scheduler_interval_seconds, scheduler_running)
    return {"code": 0, "msg": "ok", "data": {
        "enabled": scheduler_enabled(),
        "running": scheduler_running(),
        "intervalSeconds": scheduler_interval_seconds(),
    }}


def register_knowledge_routes(app) -> None:
    """注册路由(遵循项目注册模式)"""
    app.include_router(router)
