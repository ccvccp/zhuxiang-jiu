"""40号 P7a-P7e·雷达2.0 路由(五引擎全链, 设计文档《40号 P7 规划方案》§3-§7/§9)

端点(14):
    POST /api/radar/channels        频道入库(admin 增量扩展)
    GET  /api/radar/channels        频道池列表(种子惰性灌入)
    POST /api/radar/events/collect  流式采集触发(mock 15min 槽位)
    GET  /api/radar/events          事件流查询(分级/生命周期过滤)
    GET  /api/radar/events/{id}     事件详情(多模态)
    POST /api/radar/events/score    三维评分批次执行(P7b)
    GET  /api/radar/scores          评分快照查询(P7b)
    POST /api/radar/events/{id}/predict   演化预测+跨平台关联(P7c)
    POST /api/radar/events/{id}/rehearse  合规预演沙盘(P7c, L1 专用)
    GET  /api/radar/tasks           L1 任务包队列(P7d)
    POST /api/radar/tasks           任务包补建(P7d 延迟创建轨)
    POST /api/radar/tasks/{id}/confirm  L1 人工确认(P7d, 46号轨)
    POST /api/radar/efficiency/weekly 效能周报生成(P7e)
    GET  /api/radar/efficiency      效能记录查询(P7e)
    POST /api/radar/threshold/tighten 阈值收紧(P7e, 只紧不松)
    GET  /api/radar/dashboard       雷达中枢看板(P7e 四区聚合)

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


def _forecast_service():
    from services.radar_forecast_service import (
        RadarForecastService)
    return RadarForecastService()


def _task_service():
    from services.radar_task_service import RadarTaskService
    return RadarTaskService()


def _govern_service():
    from services.radar_govern_service import RadarGovernService
    return RadarGovernService()


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


class ConfirmRequest(PydBaseModel):
    approve: bool = Field(..., description="确认/否决")
    reviewer: str = Field("admin", max_length=60)
    note: str = Field("", max_length=500)


class TightenRequest(PydBaseModel):
    reason: str = Field("", max_length=500)


class TaskRequest(PydBaseModel):
    eventId: int = Field(..., description="L1 事件 ID(已过预演)")


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


@router.post("/api/radar/events/{event_id}/predict",
             tags=["雷达2.0全网侦测中枢"])
async def radar_events_predict(
        event_id: int,
        x_role: str = Header(None, alias="X-Role")):
    """事件演化预测(生命周期分段+峰值拐点预警+早期信号+
    跨平台关联: 机会窗/叙事变异/联动造势——全确定性规则)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _forecast_service().predict_event(event_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/events/{event_id}/rehearse",
             tags=["雷达2.0全网侦测中枢"])
async def radar_events_rehearse(
        event_id: int,
        x_role: str = Header(None, alias="X-Role")):
    """合规预演沙盘(L1 候选专用——预案四件套: 推荐角度/
    禁用表述/素材建议(仅授权库)/钩子方向; 过合规校验方入执行队列)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _forecast_service().rehearse_event(event_id)}
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


@router.get("/api/radar/tasks",
            tags=["雷达2.0全网侦测中枢"])
async def radar_tasks_list(
        status: str = None, limit: int = 50,
        x_role: str = Header(None, alias="X-Role")):
    """L1 任务包队列(预演通过自动入队; 决策依据+预案四件套
    随任务返回——确认界面数据面)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _task_service().list_tasks(
                    status=status, limit=limit)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/tasks",
             tags=["雷达2.0全网侦测中枢"])
async def radar_tasks_ensure(
        req: TaskRequest,
        x_role: str = Header(None, alias="X-Role")):
    """任务包创建/补建(幂等——46号审批串行约束下的延迟创建轨;
    L1+预演通过门槛)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _task_service().ensure_task(req.eventId)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/tasks/{task_id}/confirm",
             tags=["雷达2.0全网侦测中枢"])
async def radar_tasks_confirm(
        task_id: int, req: ConfirmRequest,
        x_role: str = Header(None, alias="X-Role")):
    """L1 人工确认(46号审批总线轨——永不自动执行;
    确认后触发 P6b 创作轨派发, 派发失败 fail-soft 留痕不回滚)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _task_service().confirm_task(
                    task_id, req.approve, req.reviewer,
                    req.note)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/efficiency/weekly",
             tags=["雷达2.0全网侦测中枢"])
async def radar_efficiency_weekly(
        x_role: str = Header(None, alias="X-Role")):
    """效能周报生成(触发数/命中率/误报率/漏报案例库——
    确定性聚合, LLM 禁入)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _govern_service().generate_weekly_report()}
    except Exception as e:
        _handle(e)


@router.get("/api/radar/efficiency",
            tags=["雷达2.0全网侦测中枢"])
async def radar_efficiency_list(
        kind: str = None, limit: int = 20,
        x_role: str = Header(None, alias="X-Role")):
    """效能记录查询(weekly 周报/threshold 阈值留痕)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _govern_service().list_reports(
                    kind=kind, limit=limit)}
    except Exception as e:
        _handle(e)


@router.post("/api/radar/threshold/tighten",
             tags=["雷达2.0全网侦测中枢"])
async def radar_threshold_tighten(
        req: TightenRequest,
        x_role: str = Header(None, alias="X-Role")):
    """L1 阈值自适应收紧(违规率 ×3 且样本≥20 触发;
    +10 封顶 95——只紧不松, 放宽须 46号建议书)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _govern_service().tighten_threshold(
                    reason=req.reason)}
    except Exception as e:
        _handle(e)


@router.get("/api/radar/dashboard",
            tags=["雷达2.0全网侦测中枢"])
async def radar_dashboard(
        x_role: str = Header(None, alias="X-Role")):
    """雷达中枢看板(事件/分级/任务/效能四区聚合——纯读取)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _govern_service().dashboard()}
    except Exception as e:
        _handle(e)


def register_radar_routes(app) -> None:
    """注册雷达2.0路由(main.py startup 调用)"""
    app.include_router(router)
