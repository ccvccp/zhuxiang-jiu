"""40号 P7a/P7b·雷达2.0 路由(感知聚合+三维价值评估,
设计文档《40号 P7 规划方案》§3/§4/§9)

端点(6):
    POST /api/radar/channels        频道入库(admin 增量扩展)
    GET  /api/radar/channels        频道池列表(种子惰性灌入)
    POST /api/radar/events/collect  流式采集触发(mock 15min 槽位)
    GET  /api/radar/events          事件流查询(分级/生命周期过滤)
    GET  /api/radar/events/{id}     事件详情(多模态)
    POST /api/radar/events/score    三维评分批次执行(P7b)
    GET  /api/radar/scores          评分快照查询(P7b)

鉴权: X-Role: admin(管理决策面)
异常映射: KeyError→404 / ValueError→409(项目约定)
"""

from fastapi import APIRouter, Header, HTTPException

from pydantic import BaseModel as PydBaseModel, Field

router = APIRouter()


def _require_admin(x_role: str):
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 admin 权限(X-Role)")


def _handle(e: Exception):
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=409, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def _service():
    from services.radar_hub_service import RadarHubService
    return RadarHubService()


def _score_service():
    from services.radar_score_service import RadarScoreService
    return RadarScoreService()


class ChannelRequest(PydBaseModel):
    platform: str = Field("douyin", max_length=30)
    name: str = Field(..., min_length=1, max_length=60,
                      description="频道唯一名")
    displayName: str = Field(..., min_length=1, max_length=60)
    category: str = Field(..., max_length=20,
                          description="politics/military/finance/"
                                      "history/current")


class CollectRequest(PydBaseModel):
    channelId: int = Field(None,
                           description="指定频道(空=全量批次)")


class ScoreRequest(PydBaseModel):
    eventIds: list = Field(None,
                           description="指定事件 ID 列表(空=全量)")


@router.get("/api/radar/channels",
            tags=["雷达2.0全网侦测中枢"])
async def radar_channels_list(
        x_role: str = Header(None, alias="X-Role")):
    """频道池列表(12 种子频道惰性灌入——时政军事构成合规测试集)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _service().seed_channels()}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/channels",
             tags=["雷达2.0全网侦测中枢"])
async def radar_channels_add(
        req: ChannelRequest,
        x_role: str = Header(None, alias="X-Role")):
    """频道增量入库(类别驱动预期合规分级)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _service().register_channel(
                    req.platform, req.name,
                    req.displayName, req.category)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/events/collect",
             tags=["雷达2.0全网侦测中枢"])
async def radar_events_collect(
        req: CollectRequest,
        x_role: str = Header(None, alias="X-Role")):
    """流式采集触发(15min 槽位确定性 mock; 聚类去重+情绪场域+
    刷量过滤; 同槽位重复采集幂等跳过)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _service().collect_events(
                    channel_id=req.channelId)}
    except Exception as e:
        _handle(e)


@router.get("/api/radar/events",
            tags=["雷达2.0全网侦测中枢"])
async def radar_events_list(
        category: str = None, lifecycle: str = None,
        grade: str = None, limit: int = 50,
        x_role: str = Header(None, alias="X-Role")):
    """事件流查询(热度降序; 分级/生命周期/类别过滤;
    弹幕原文即用即弃不返回)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _service().list_events(
                    category=category, lifecycle=lifecycle,
                    grade=grade, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/radar/events/{event_id}",
            tags=["雷达2.0全网侦测中枢"])
async def radar_events_detail(
        event_id: int,
        x_role: str = Header(None, alias="X-Role")):
    """事件详情(ASR/OCR/BGM 指纹多模态字段+槽位时序)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _service().event_detail(event_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/events/score",
             tags=["雷达2.0全网侦测中枢"])
async def radar_events_score(
        req: ScoreRequest,
        x_role: str = Header(None, alias="X-Role")):
    """三维评分批次执行(契合×安全×转化→L1-L4 分级;
    安全<0.6 硬闸短路 L4 屏蔽留痕——无论热度多高)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _score_service().score_events(
                    event_ids=req.eventIds)}
    except Exception as e:
        _handle(e)


@router.get("/api/radar/scores",
            tags=["雷达2.0全网侦测中枢"])
async def radar_scores_list(
        grade: str = None, category: str = None,
        event_id: int = None, limit: int = 50,
        x_role: str = Header(None, alias="X-Role")):
    """评分快照查询(最新优先; 漏斗字段供归因回流核对)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _score_service().list_scores(
                    event_id=event_id, category=category,
                    grade=grade, limit=limit)}
    except Exception as e:
        _handle(e)


def register_radar_routes(app) -> None:
    """注册雷达2.0路由(main.py startup 调用)"""
    app.include_router(router)
