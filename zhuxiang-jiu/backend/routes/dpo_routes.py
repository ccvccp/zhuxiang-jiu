# -*- coding: utf-8 -*-
"""DPO 偏好对路由(P4-10 第一批·数据面 + 第三批·导出, 4 端点)

小竹蓝图 P4-10: DPO 偏好对人工评审环(常闭铁律——approved 必经
人工 operator 必填, 判别器/规则只产 pending)。

端点(/api/dpo/*, 全部 admin):
    - GET  /api/dpo/pairs                 偏好对列表(status 过滤+分页)
    - POST /api/dpo/pairs/{pair_id}/review  评审三操作
          (approve/reject/flip, operator 必填——Pydantic 422 强制)
    - GET  /api/dpo/stats                 统计面板(口径对齐
          repurchase stats: 总量/五源占比/评审转化率/intent 分布/
          周增趋势)
    - GET  /api/dpo/export                数据集导出(第三批, 标准
          DPO JSONL 流: 逐行 {"prompt","chosen","rejected","meta"},
          meta 含 datasetVersion; 响应头 X-Dataset-Version/
          X-Dataset-Count; 导出即快照可重复导出不打 status 标记)

异常映射: KeyError → 404 / ValueError → 409
"""

import json

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as PydBaseModel, Field

from services.dpo_pair_service import (
    DpoPairService, REVIEW_ACTIONS, SOURCES)

router = APIRouter()
_service = DpoPairService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


class ReviewRequest(PydBaseModel):
    action: str = Field(..., description="评审操作: approve/reject/flip")
    operator: str = Field(..., min_length=1, max_length=64,
                          description="评审操作人(人工评审环必填留痕)")


@router.get("/api/dpo/pairs", tags=["DPO偏好对数据面"])
async def list_pairs(
        status: str = Query(None,
                            description="状态过滤: pending/approved/"
                                        "rejected(空=全量)"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        x_role: str = Header(None, alias="X-Role")):
    """偏好对列表(admin 分页; pairId 倒序=最近优先)"""
    _require_admin(x_role)
    try:
        rows = await _service.list_pairs(status=status, limit=limit,
                                         offset=offset)
        return {"success": True, "data": rows,
                "pagination": {"limit": limit, "offset": offset,
                               "count": len(rows)}}
    except Exception as e:
        _handle(e)


@router.post("/api/dpo/pairs/{pair_id}/review",
             tags=["DPO偏好对数据面"])
async def review_pair(pair_id: int, body: ReviewRequest,
                      x_role: str = Header(None, alias="X-Role")):
    """人工评审三操作(评审环常闭: operator 必填; flip 互换
    chosen/rejected, 翻转权在人)"""
    _require_admin(x_role)
    try:
        record = await _service.review(
            pair_id, body.action, body.operator)
        return {"success": True, "data": record}
    except Exception as e:
        _handle(e)


@router.get("/api/dpo/stats", tags=["DPO偏好对数据面"])
async def stats(x_role: str = Header(None, alias="X-Role")):
    """统计面板(总量/五源占比/评审转化率/intent 分布/周增趋势)"""
    _require_admin(x_role)
    try:
        data = await _service.stats()
        data["sources"] = list(SOURCES)
        data["actions"] = list(REVIEW_ACTIONS)
        return {"success": True, "data": data}
    except Exception as e:
        _handle(e)


@router.get("/api/dpo/export", tags=["DPO偏好对数据面"])
async def export_pairs(
        source: str = Query(
            None, description="来源过滤: S1/S2/S3/S4/S5(空=全源)"),
        intent: str = Query(
            None, description="intent 过滤(空=全意图)"),
        min_status: str = Query(
            "approved",
            description="状态推进度下限: pending/rejected/approved"
                        "(默认 approved——approved 才是数据集)"),
        x_role: str = Header(None, alias="X-Role")):
    """数据集导出(admin; 标准 DPO JSONL 流)

    实现形态: StreamingResponse 逐行流式(对齐项目 xiaozhu_routes
    SSE 流式先例——大导出不占内存); 每行
    {"prompt", "chosen", "rejected", "meta": {source, intent,
    track, pairId, reviewedAt, datasetVersion}}。
    导出即快照: 不打 status 标记, 可重复导出(可复现快照仅供
    未来训练框架消费——本站自身不消费)。
    """
    _require_admin(x_role)
    try:
        rows, version = await _service.export_pairs(
            source=source, intent=intent, min_status=min_status)
    except Exception as e:
        _handle(e)

    def _lines():
        # 逐行序列化生成器(响应体流式——不一次性构建大字符串)
        for r in rows:
            yield json.dumps({
                "prompt": r.get("question", ""),
                "chosen": r.get("chosen", ""),
                "rejected": r.get("rejected", ""),
                "meta": {
                    "source": r.get("source", ""),
                    "intent": r.get("intent", ""),
                    "track": r.get("track", ""),
                    "pairId": r.get("pairId"),
                    "reviewedAt": r.get("reviewedAt", ""),
                    "datasetVersion": version,
                },
            }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _lines(), media_type="application/x-ndjson",
        headers={
            "X-Dataset-Version": version,
            "X-Dataset-Count": str(len(rows)),
        })


def register_dpo_routes(app) -> None:
    """注册 DPO 偏好对路由"""
    app.include_router(router)
