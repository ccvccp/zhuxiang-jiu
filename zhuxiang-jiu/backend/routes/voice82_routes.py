"""82号·小竹语音对话逻辑学习进化模型路由(P0 观测面+清除)

守卫: X-Role: admin(同 48/81号惯例)
分段: P0 全观测面无模式门槛(看得见永远先于管得着);
      rebuild 为只读聚合(写画像三表, 不触语音链参数);
      参数生效在 P2(VOICE82_MODE 三态), P0 params 仅展示
"""

from fastapi import APIRouter, Header, HTTPException

from services.voice82_dialog_service import (
    Voice82DialogService, voice82_mode,
)

router = APIRouter(prefix="/api/voice82",
                   tags=["小竹对话逻辑学习进化(82号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/status")
async def voice82_status(x_role: str = Header(default="",
                                              alias="X-Role")):
    """模块状态(P0: 模式/白名单/冷启动口径)"""
    _require_admin(x_role)
    from services.voice82_dialog_service import (
        COLD_START_SESSIONS, PARAM_DEFAULTS, PARAM_KEYS,
    )
    return {"success": True, "data": {
        "module": "voice82-dialog-evolution", "phase": "P0",
        "mode": voice82_mode(),
        "paramWhitelist": list(PARAM_KEYS),
        "paramDefaults": PARAM_DEFAULTS,
        "coldStartSessions": COLD_START_SESSIONS,
        "note": "P0 零侵入(48号表离线聚合); 参数生效于 P2 三态",
    }}


@router.get("/profile/{member_id}")
async def voice82_profile(member_id: int,
                          rebuild: int = 0,
                          x_role: str = Header(default="",
                                               alias="X-Role")):
    """画像三表+参数映射(观测面; ?rebuild=1 即时重建)"""
    _require_admin(x_role)
    svc = Voice82DialogService()
    result = await (svc.rebuild_profile(member_id)
                    if rebuild else svc.get_profile(member_id))
    if not result.get("success"):
        raise HTTPException(status_code=404,
                            detail=result.get("error") or "无画像")
    return result


@router.delete("/profile/{member_id}")
async def voice82_profile_clear(
        member_id: int,
        x_role: str = Header(default="", alias="X-Role")):
    """一键清除画像(隐私红线: 用户可清除)"""
    _require_admin(x_role)
    return await Voice82DialogService().clear_profile(member_id)


# ============================================================
# P1: 意图转移矩阵 + 目标预判(观测面干跑——preload 仅决策预览,
# 实际接线于 P2/P3 由语音主链消费)
# ============================================================

@router.get("/matrix")
async def voice82_matrix(x_role: str = Header(default="",
                                              alias="X-Role")):
    """转移矩阵摘要(全局+阈值口径)"""
    _require_admin(x_role)
    from services.voice82_predict_service import Voice82PredictService
    return await Voice82PredictService().matrix_view()


@router.post("/matrix/rebuild")
async def voice82_matrix_rebuild(
        x_role: str = Header(default="", alias="X-Role")):
    """全量重建矩阵(后台数据加工, 不触语音链参数)"""
    _require_admin(x_role)
    from services.voice82_predict_service import Voice82PredictService
    return await Voice82PredictService().rebuild_matrices()


@router.post("/predict")
async def voice82_predict(payload: dict,
                          x_role: str = Header(default="",
                                               alias="X-Role")):
    """目标预判干跑 {memberId?, intent} → Top3+置信度+预载预览"""
    _require_admin(x_role)
    from services.voice82_predict_service import Voice82PredictService
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        raise HTTPException(status_code=422, detail="intent 必填")
    member_id = payload.get("memberId")
    return await Voice82PredictService().predict(member_id, intent)


# ============================================================
# P2: 在线强化(信号观测无门槛; challenger/promote/discard 决策面
# —— off 409; promote 恒人工, auto_apply=False 铁律)
# ============================================================

@router.post("/signals")
async def voice82_signals_record(payload: dict,
                                 x_role: str = Header(
                                     default="", alias="X-Role")):
    """会话信号采集(观测面——off 也可采): {sessionId, memberId, summary}"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    sid = payload.get("sessionId")
    if sid is None:
        raise HTTPException(status_code=422, detail="sessionId 必填")
    return await Voice82EvolutionService().record_signals(
        sid, payload.get("memberId"), payload.get("summary") or {})


@router.get("/signals")
async def voice82_signals_view(memberId: int = None, limit: int = 20,
                               x_role: str = Header(
                                   default="", alias="X-Role")):
    """信号观测(最近 N 条/按会员过滤)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().signals_view(
        member_id=memberId, limit=max(1, min(limit, 100)))


@router.post("/signals/collect")
async def voice82_signals_collect(x_role: str = Header(
        default="", alias="X-Role")):
    """手动采集已结束会话信号(观测面——与调度器同源, 幂等)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().collect_ended_sessions()


@router.post("/challenger/{member_id}")
async def voice82_challenger_build(member_id: int,
                                   x_role: str = Header(
                                       default="", alias="X-Role")):
    """生成参数候选(决策面: off → 409; auto_apply=False)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    r = await Voice82EvolutionService().build_challenger(member_id)
    if not r.get("success") and "off" in str(r.get("error")):
        raise HTTPException(status_code=409, detail=r.get("error"))
    return r


@router.post("/challenger/{member_id}/promote")
async def voice82_challenger_promote(member_id: int,
                                     x_role: str = Header(
                                         default="",
                                         alias="X-Role")):
    """人工 promote: 候选 → effective(仅 assist 消费)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().promote(member_id)


@router.post("/challenger/{member_id}/discard")
async def voice82_challenger_discard(member_id: int,
                                     x_role: str = Header(
                                         default="",
                                         alias="X-Role")):
    """一键回滚(弃候选+撤 effective)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().discard(member_id)


@router.get("/effective/{member_id}")
async def voice82_effective(member_id: int,
                            x_role: str = Header(
                                default="", alias="X-Role")):
    """生效参数(shadow 不生效/assist 才覆盖)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().effective_params(member_id)


@router.get("/shadow-status")
async def voice82_shadow_status(x_role: str = Header(
        default="", alias="X-Role")):
    """灰度门槛观测(≥200 会话且正反馈占优 → ready)"""
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().shadow_status()


# ============================================================
# P4: 开闸决策数据面 + 按比例放量(assist 生效条件收紧为
# mode==assist 且 _in_rollout; 默认 0=未开闸零行为变化)
# ============================================================

@router.get("/rollout")
async def voice82_rollout_view(x_role: str = Header(
        default="", alias="X-Role")):
    """开闸决策数据面(门槛+放量+信号概览+advice——零 LLM)

    开闸决策 runbook: 先 GET 本端点看门槛 → POST 小比例 →
    观察 → 逐步放量; 回滚 = POST pct=0
    """
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService,
    )
    return await Voice82EvolutionService().rollout_dashboard()


@router.post("/rollout")
async def voice82_rollout_set(payload: dict,
                              x_role: str = Header(
                                  default="", alias="X-Role")):
    """调整放量比例 {pct: 0-100}(写 Redis 运行时键, 返回 dashboard 刷新值)

    开闸决策 runbook: 先 GET /api/voice82/rollout 看门槛 →
    POST 小比例(如 {"pct": 5}) → 观察 → 逐步放量
    """
    _require_admin(x_role)
    from services.voice82_evolution_service import (
        Voice82EvolutionService, set_assist_rollout,
    )
    try:
        pct = int(payload.get("pct"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail="pct 必填且为整数(0-100)")
    if not 0 <= pct <= 100:
        raise HTTPException(status_code=422,
                            detail="pct 需在 0-100 之间")
    try:
        await set_assist_rollout(pct)
    except ValueError as exc:
        # Redis 运行时键不可用 → 提示走 env 静态回退面
        raise HTTPException(status_code=409, detail=str(exc))
    return await Voice82EvolutionService().rollout_dashboard()


def register_voice82_routes(app):
    """路由注册(routes/__init__ register 函数族惯例)"""
    app.include_router(router)
