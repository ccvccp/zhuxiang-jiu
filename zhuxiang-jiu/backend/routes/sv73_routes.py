"""73号(sv)·短视频智能模型路由(P0 分镜剧本引擎)

端点(P0 3 个):
    POST /api/sv73/script/generate  生成分镜剧本(决策面 off=409, admin)
    GET  /api/sv73/script/{id}      剧本详情(观测面, admin)
    GET  /api/sv73/scripts          剧本清单(观测面, admin)

统一口径(71/73(member) 号范式):
    - 观测面 off 常开; 决策面 off=409(SV73_KILL 一键回退)
    - KeyError → 404 / ValueError → 409
    - 管理面 X-Role: admin(运营工具口径)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.sv73_script_service import (
    Sv73ScriptService,
    current_mode,
    is_kill,
)
from services.sv73_pipeline_service import (
    Sv73PipelineService,
    DEFAULT_PLATFORM,
    SV73_PLATFORMS,
)
from services.sv73_script_service import DEFAULT_TEMPLATE, TEMPLATES
from services.sv73_match_service import Sv73MatchService

router = APIRouter(
    prefix="/api/sv73",
    tags=["短视频智能模型(73号sv)"],
)

_service = Sv73ScriptService()
_pipeline = Sv73PipelineService()


class ScriptGenerateRequest(BaseModel):
    hotspot: dict = Field(..., description="36号雷达热点记录")
    category: str = Field("竹香型白酒", description="网站主推品类")
    persona: str = Field("zhuxiaomei", description="IP 人设注册表键")
    template: str = Field(DEFAULT_TEMPLATE,
                          description="视频模板(vertical/landscape/fast)")


class PipelineRunRequest(BaseModel):
    hotspot: dict = Field(..., description="36号雷达热点记录")
    category: str = Field("竹香型白酒",
                          description="主推品类; auto=匹配引擎推荐(P1)")
    platform: str = Field(DEFAULT_PLATFORM,
                          description="发布平台(wechat_channels/douyin)")
    persona: str = Field("zhuxiaomei", description="IP 人设注册表键")
    template: str = Field(DEFAULT_TEMPLATE,
                         description="视频模板(vertical/landscape/fast)")
    render_mode: str = Field("",
                             description="渲染步(空=SV73_RENDER_MODE 默认"
                                         " off 跳过; on=全链渲染)")


class RenderAttachRequest(BaseModel):
    scriptId: str = Field(..., description="剧本号(pipeline 产出)")
    video: str = Field(..., description="开发机本地 mp4 路径")
    pages: list = Field(default_factory=list, description="PNG 页路径")
    audioTrack: str = Field("", description="TTS 音频路径(可空)")
    sizeBytes: int = Field(0, description="mp4 字节数")


def _require_admin(x_role: str | None) -> None:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _mode_guard() -> None:
    """决策面门控(off=409; kill 一键回退)"""
    if is_kill():
        raise HTTPException(status_code=409,
                            detail="SV73_KILL=1(一键回退)")
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail=("SV73_MODE=off(默认 off——shadow=生成留痕不进"
                    "发布链, real=开放生成; 观测面不受影响)"))


@router.post("/script/generate")
async def generate_script(payload: ScriptGenerateRequest,
                          x_role: str | None = Header(None)):
    """生成分镜剧本(决策面: 热点+品类 → storyboard JSON)"""
    _require_admin(x_role)
    _mode_guard()
    try:
        sb = await _service.generate(
            payload.hotspot, payload.category, payload.persona,
            payload.template)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"code": 0, "data": sb}


@router.post("/match")
async def match_series(payload: ScriptGenerateRequest,
                       x_role: str | None = Header(None)):
    """热点×品类匹配(观测面: 纯确定性查询, off 常开)"""
    _require_admin(x_role)
    result = await Sv73MatchService().match(payload.hotspot)
    return {"code": 0, "data": result}


@router.get("/script/{script_id}")
async def get_script(script_id: str,
                     x_role: str | None = Header(None)):
    """剧本详情(观测面: off 常开)"""
    _require_admin(x_role)
    try:
        sb = _service.load(script_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from None
    return {"code": 0, "data": sb}


@router.get("/scripts")
async def list_scripts(x_role: str | None = Header(None)):
    """剧本清单(观测面: off 常开, mtime 倒序)"""
    _require_admin(x_role)
    return {"code": 0, "data": _service.list_storyboards()}


@router.post("/pipeline/run")
async def run_pipeline(payload: PipelineRunRequest,
                       x_role: str | None = Header(None)):
    """全链编排(决策面): 热点→剧本→渲染→合成→content 登记

    发布决策留 36号人工三审(三审闸门不动); real 态完整登记,
    shadow 态产物留痕不占归因通道。
    """
    _require_admin(x_role)
    _mode_guard()
    try:
        result = await _pipeline.run(
            payload.hotspot, payload.category, payload.platform,
            payload.persona, payload.template,
            payload.render_mode or None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"code": 0, "data": result}


@router.post("/render/attach")
async def attach_render(payload: RenderAttachRequest,
                        x_role: str | None = Header(None)):
    """开发机渲染产物挂载(决策面): 元数据回填 content.sv73

    分段语义(生产灰度实证后): 生产小机 ffmpeg 超时——渲染留
    开发机, mp4 为开发机本地路径(RPA 发布本在开发机执行)。
    """
    _require_admin(x_role)
    _mode_guard()
    try:
        result = await _pipeline.attach_render(
            payload.scriptId, payload.video, payload.pages,
            payload.audioTrack, payload.sizeBytes)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"code": 0, "data": result}


def register_sv73_routes(app):
    """注册73号(sv)·短视频智能模型路由"""
    app.include_router(router)
