"""Self-Play 对抗压测路由(小竹蓝图 P4-10 第二批·对抗面, 2 端点)

鉴权:
    - 管理端(2): 手动触发一轮 / 压测统计(X-Role: admin——对抗结果
      只留痕+告警管理员, 铁律「LLM 不自动外发」)

异常映射(对齐 knowledge_routes / live_qa_routes 约定):
    - ValueError → 409(参数非法)
    - 权限校验 → 403

响应包裹形态对齐 live_qa_routes: {code: 0, msg: "ok", data: ...}。
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services.selfplay_service import get_selfplay_service

router = APIRouter()


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403(对齐 live_qa_routes 写法)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


class SelfPlayRunRequest(PydBaseModel):
    """手动触发一轮对抗请求(batch 缺省走 SELFPLAY_BATCH env)"""
    batch: int = Field(20, ge=1, le=200,
                       description="本轮抽样条数上限(1-200)")


@router.post("/api/selfplay/run", tags=["Self-Play 对抗压测"])
async def run_round(
    data: SelfPlayRunRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """手动触发一轮对抗(admin; 红队模板六类 × 现有防线 × 确定性
    判定——单条 fail-soft 不断轮; A1-A5 失守走安全告警, A6 失守走
    缺口登记 L2 闭环; 同 seed 同 batch 抽样可复现)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok",
            "data": await get_selfplay_service().run_round(
                batch=data.batch)}


@router.get("/api/selfplay/stats", tags=["Self-Play 对抗压测"])
async def stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """Self-Play 压测统计(admin; 六类命中率/失守清单(最近 N 条)/
    新增缺口数/最近一轮时间/defense_score 加权命中率)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok",
            "data": get_selfplay_service().stats()}


def register_selfplay_routes(app) -> None:
    """注册路由(遵循项目注册模式)"""
    app.include_router(router)
