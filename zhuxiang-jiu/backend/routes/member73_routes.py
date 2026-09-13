"""73号·AI智能会员体验大模型路由(P1 视野与唤醒)

端点(P1 8 个):
    GET  /api/member73/horizon/{member_id}     升级视野(本人 X-Member-Id/ admin, 观测面)
    GET  /api/member73/mentor/dict             触发分字典公示(admin, 观测面)
    POST /api/member73/mentor/decide           时机决策(本人/ admin, 快环——呈现位随 MODE 门控)
    GET  /api/member73/mentor/moments          时机留痕(admin, 观测面)
    POST /api/member73/mentor/{id}/respond     响应回流(本人/ admin, 快环)
    GET  /api/member73/benefits/preview/{member_id}  升级前后权益对比(本人/ admin, 观测面)
    POST /api/member73/benefits/reveal         升级完成实时告知(admin, 决策面 off 409)
    GET  /api/member73/benefits/reveals        告知留痕(admin, 观测面)

鉴权:
    - 管理面 X-Role: admin(71号同款口径)
    - 会员面 X-Member-Id(本人——member_routes
      Mock 惯例); 越权访问他人数据 403

统一口径(71号范式):
    - 视野/字典/留痕/对比卡为观测面——off 常开
    - 时机决策为快环(计算+留痕); 呈现位
      rendered 随 MODE(off/shadow=False)
    - 告知 reveal 为决策面 off=409
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.member73_p1_service import (
    Member73P1Service,
)

router = APIRouter(
    prefix="/api/member73",
    tags=["AI智能会员体验大模型(73号)"],
)

_service = Member73P1Service()


def _require_admin(x_role: str | None) -> None:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _parse_member_id(
        x_member_id: str | None) -> int:
    """会员头解析(member_routes Mock 惯例)"""
    if not x_member_id:
        raise HTTPException(
            status_code=401,
            detail="未登录: 请提供 "
                   "X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="X-Member-Id 格式不正确"
        ) from None


def _require_self_or_admin(
        x_role: str | None,
        x_member_id: str | None,
        member_id: int) -> None:
    """本人或 admin(越权 403)"""
    if x_role == "admin":
        return
    mid = _parse_member_id(x_member_id)
    if mid != member_id:
        raise HTTPException(
            status_code=403,
            detail="仅可访问本人数据"
                   "(需 X-Member-Id 匹配"
                   "或 X-Role: admin)")


def _map(exc: Exception) -> HTTPException:
    """统一异常映射(71号口径)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        return HTTPException(status_code=404,
                             detail=msg)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409,
                             detail=str(exc))
    return HTTPException(status_code=500,
                         detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class MentorDecideRequest(BaseModel):
    memberId: int = Field(..., gt=0,
                          description="会员 ID")
    momentType: str = Field(
        ..., max_length=30,
        description="时刻类型: order_done/achieved/"
                    "points_changed/profile_gap")
    entry: str = Field(
        "order_page", max_length=30,
        description="入口: order_page/profile_page/"
                    "points_page/home")
    now: str = Field(
        "", max_length=30,
        description="决策时刻(ISO 8601, 空=当前——演示口径)")


class MomentRespondRequest(BaseModel):
    responseType: str = Field(
        ..., max_length=20,
        description="响应类型: click/upgrade/ignore")


class BenefitRevealRequest(BaseModel):
    memberId: int = Field(..., gt=0,
                          description="会员 ID")
    fromLevel: int = Field(..., ge=1, le=4,
                           description="升级前等级")
    toLevel: int = Field(..., ge=2, le=5,
                        description="升级后等级(须=from+1)")


class EffortlessObserveRequest(BaseModel):
    memberId: int = Field(..., gt=0,
                          description="会员 ID")
    steps: int = Field(..., ge=0, le=100,
                       description="操作步骤数")
    formFields: int = Field(..., ge=0, le=100,
                           description="表单字段数(认知负荷)")
    waitSeconds: float = Field(..., ge=0, le=3600,
                                description="接口平均等待秒数")
    now: str = Field("", max_length=30,
                    description="观测时刻(ISO 8601, 空=当前——演示口径)")


class AdaptRequest(BaseModel):
    hour: int = Field(-1, ge=-1, le=23,
                     description="当前小时(0-23; -1=按 now 解析)")
    now: str = Field("", max_length=30,
                     description="查询时刻(ISO 8601, 空=当前——演示口径)")


class MuteRequest(BaseModel):
    memberId: int = Field(0, ge=0,
                          description="会员 ID(0=按 X-Member-Id; admin 可指定)")
    silenced: bool = Field(...,
                          description="夜间静默开关(注册默认开启)")


class DelegateActionRequest(BaseModel):
    memberId: int = Field(0, ge=0,
                          description="会员 ID(0=按 X-Member-Id; admin 可指定)")
    action: str = Field(..., max_length=40,
                        description="代办动作(白名单五动作)")


# ============================================================
# ① 升级视野(观测面)
# ============================================================

@router.get("/horizon/{member_id}")
async def horizon(
        member_id: int,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """个体升级视野(缺口/外推/保级窗/影子位)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        return {"code": 0,
                "data": await _service.horizon(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ② 情境引导时机(快环+观测)
# ============================================================

@router.get("/mentor/dict")
async def mentor_dict(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """触发分字典公示(观测面)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": _service.mentor_dict()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/mentor/decide")
async def mentor_decide(
        body: MentorDecideRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """时机决策(触发分×三态×静默×封顶×
    影子——快环, 呈现位随 MODE)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, body.memberId)
        return {"code": 0,
                "data": await _service.decide(
                    member_id=body.memberId,
                    moment_type=body.momentType,
                    entry=body.entry,
                    now=body.now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/mentor/moments")
async def list_moments(
        memberId: int = 0,
        decision: str = "",
        limit: int = 100,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """时机留痕(观测面)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await
                _service.list_moments(
                    member_id=memberId or None,
                    decision=decision or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/mentor/{moment_id}/respond")
async def moment_respond(
        moment_id: int,
        body: MomentRespondRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """响应回流(快环——形式效果学习源)"""
    try:
        if x_role != "admin":
            mid = _parse_member_id(x_member_id)
            moment = await _service.repo \
                .get_moment(moment_id)
            if moment is None or \
                    moment.get("memberId") \
                    != mid:
                raise HTTPException(
                    status_code=403,
                    detail="仅可响应本人时刻")
        return {"code": 0,
                "data": await
                _service.respond(
                    moment_id,
                    body.responseType)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ④ 权益对比告知(观测+决策面)
# ============================================================

@router.get("/benefits/preview/{member_id}")
async def benefits_preview(
        member_id: int,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """升级前后权益对比卡片
    (zk 权益矩阵只读——零编造)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        return {"code": 0,
                "data": await
                _service.benefits_preview(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/benefits/reveal")
async def benefits_reveal(
        body: BenefitRevealRequest,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """升级完成实时告知(决策面 off 409
    ——即效/需领取清单确定性分类)"""
    try:
        _require_admin(x_role)
        from services.member73_registry import (
            current_mode,
        )
        if current_mode() == "off":
            raise HTTPException(
                status_code=409,
                detail="MEMBER73_MODE=off"
                      "(默认 off——触达面"
                      "关闭, 观测面不受影响)")
        return {"code": 0,
                "data": await
                _service.benefits_reveal(
                    member_id=body.memberId,
                    from_level=body.fromLevel,
                    to_level=body.toLevel)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/benefits/reveals")
async def list_reveals(
        memberId: int = 0,
        limit: int = 100,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """告知留痕(观测面)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await
                _service.list_reveals(
                    member_id=memberId or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P2 ⑤ 无感度量(观测/快环/用户面——
# 不受 MODE/KILL 影响)
# ============================================================

@router.post("/effortless/observe")
async def effortless_observe(
        body: EffortlessObserveRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """交互观测上报(步骤/字段/等待
    ——快环, 同日 upsert 合并)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, body.memberId)
        from services.member73_p2_service import (
            Member73P2Service,
        )
        return {"code": 0,
                "data": await
                Member73P2Service().observe(
                    member_id=body.memberId,
                    steps=body.steps,
                    form_fields=body.formFields,
                    wait_seconds=body.waitSeconds,
                    now=body.now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/effortless/{member_id}")
async def effortless(
        member_id: int,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """无感度得分(四维公式——进化
    核心奖励函数)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        from services.member73_p2_service import (
            Member73P2Service,
        )
        return {"code": 0,
                "data": await
                Member73P2Service().effortless(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/adapt/{member_id}")
async def adapt(
        member_id: int,
        hour: int = -1,
        now: str = "",
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """自适应参数下发(情境档位规则表
    查表——静默优先>高峰>常规)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        from services.member73_p2_service import (
            Member73P2Service,
        )
        return {"code": 0,
                "data": await
                Member73P2Service().adapt(
                    member_id=member_id,
                    hour=None if hour < 0
                    else hour,
                    now=now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/notify/mute")
async def set_mute(
        body: MuteRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """静默时段设置(用户面——本人,
    注册默认开启, 即时生效)"""
    try:
        member_id = _parse_member_id(
            x_member_id)
        if x_role != "admin" \
                or body.memberId <= 0:
            body.memberId = member_id
        from services.member73_p2_service import (
            Member73P2Service,
        )
        return {"code": 0,
                "data": await
                Member73P2Service().set_mute(
                    member_id=body.memberId,
                    silenced=body.silenced)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P3 ⑥ 预判代办(预判/授权=观测/用户面;
# 执行=决策面 off 409——代办域永远
# assist 语义: 显式调用+白名单+授权)
# ============================================================

@router.post("/delegate/predict")
async def delegate_predict(
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """下一步操作预判(行为序列频率
    确定性排序 top1——快环)"""
    try:
        member_id = _parse_member_id(
            x_member_id)
        from services.member73_p3_service import (
            Member73P3Service,
        )
        return {"code": 0,
                "data": await
                Member73P3Service().predict(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/delegate/grants/{member_id}")
async def delegate_grants(
        member_id: int,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """授权台账(有效授权位图——观测面)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        from services.member73_p3_service import (
            Member73P3Service,
        )
        return {"code": 0,
                "data": await
                Member73P3Service()
                .list_grants(
                    member_id=member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/delegate/grant")
async def delegate_grant(
        body: DelegateActionRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """授权(用户显式——per-action
    位图级; 资金类永不授权)"""
    try:
        member_id = _parse_member_id(
            x_member_id)
        if x_role != "admin" \
                or body.memberId <= 0:
            body.memberId = member_id
        from services.member73_p3_service import (
            Member73P3Service,
        )
        return {"code": 0,
                "data": await
                Member73P3Service().grant(
                    member_id=body.memberId,
                    action=body.action)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/delegate/revoke")
async def delegate_revoke(
        body: DelegateActionRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """撤回授权(用户即否决权——
    即时生效留痕)"""
    try:
        member_id = _parse_member_id(
            x_member_id)
        if x_role != "admin" \
                or body.memberId <= 0:
            body.memberId = member_id
        from services.member73_p3_service import (
            Member73P3Service,
        )
        return {"code": 0,
                "data": await
                Member73P3Service().revoke(
                    member_id=body.memberId,
                    action=body.action)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/delegate/execute")
async def delegate_execute(
        body: DelegateActionRequest,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """代办执行(决策面 off 409——
    白名单+授权双重校验四态留痕)"""
    try:
        member_id = _parse_member_id(
            x_member_id)
        if x_role != "admin" \
                or body.memberId <= 0:
            body.memberId = member_id
        from services.member73_registry import (
            current_mode,
        )
        if current_mode() == "off":
            raise HTTPException(
                status_code=409,
                detail="MEMBER73_MODE=off"
                      "(默认 off——代办面"
                      "关闭, 观测面不受影响)")
        from services.member73_p3_service import (
            Member73P3Service,
        )
        return {"code": 0,
                "data": await
                Member73P3Service().execute(
                    member_id=body.memberId,
                    action=body.action)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/delegate/logs")
async def delegate_logs(
        memberId: int = 0,
        limit: int = 100,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """代办执行留痕(观测面)"""
    try:
        _require_admin(x_role)
        from services.member73_p3_service import (
            Member73P3Service,
        )
        return {"code": 0,
                "data": await
                Member73P3Service()
                .list_delegate_logs(
                    member_id=memberId or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P4 ⑦ 信任共生(用户面/观测面——
# 四可面板/遗忘/报告)
# ============================================================

@router.get("/trust/panel/{member_id}")
async def trust_panel(
        member_id: int,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """四可面板("AI 为我做了什么"——
    动作流+可撤回授权+验证/遗忘入口)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        from services.member73_p4_service import (
            Member73P4Service,
        )
        return {"code": 0,
                "data": await
                Member73P4Service().panel(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/trust/forget")
async def trust_forget(
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """画像遗忘(用户面——五表硬删除
    +seq 留痕; member 账户本体保留)"""
    try:
        member_id = _parse_member_id(
            x_member_id)
        from services.member73_p4_service import (
            Member73P4Service,
        )
        return {"code": 0,
                "data": await
                Member73P4Service().forget(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/trust/report/{member_id}")
async def trust_report(
        member_id: int,
        x_role: str = Header(default=None,
                             alias="X-Role"),
        x_member_id: str = Header(
            default=None,
            alias="X-Member-Id")):
    """个人信任报告(周期统计——用户侧/
    admin 数字同源)"""
    try:
        _require_self_or_admin(
            x_role, x_member_id, member_id)
        from services.member73_p4_service import (
            Member73P4Service,
        )
        return {"code": 0,
                "data": await
                Member73P4Service().report(
                    member_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P5 ⑧ 元认知收官(观测/快环/保护面/
# 决策面——漂移/免疫/红队/进化日志)
# ============================================================

@router.get("/model/status")
async def model_status(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """模型状态(mode/kill/免疫/版本
    ——观测面)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .model_status()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/meta/dict")
async def metacog_dict(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """元认知字典公示(观测面)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data":
                    Member73P5Service()
                    .metacog_dict()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/meta/drift")
async def drift_detect(
        day: str = "",
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """漂移检测(三信号——快环,
    不受 MODE 影响)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .drift_detect(day=day)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/immunity")
async def immunity_view(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """免疫看板(观测面)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .immunity_view()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/immunity/monitor")
async def immunity_monitor(
        day: str = "",
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """分布监控+自动冻结(快环——
    信号数≥规则线自动冻结[保护方向])"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .monitor_immunity(
                    day=day)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/immunity/freeze")
async def immunity_freeze(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """人工冻结(保护面——不受 MODE)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .freeze_manual(
                    by="admin")}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/immunity/unfreeze")
async def immunity_unfreeze(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """解冻(人工专属+环境变量双保险
    ——MEMBER73_IMMUNITY=1)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .unfreeze(by="admin")}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/redteam")
async def redteam(
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """红队四向量执行(决策面 off 409
    ——构造→断言→留痕; 失守→冻结)"""
    try:
        _require_admin(x_role)
        from services.member73_registry import (
            current_mode,
        )
        if current_mode() == "off":
            raise HTTPException(
                status_code=409,
                detail="MEMBER73_MODE=off"
                      "(默认 off——决策面"
                      "关闭, 观测面不受影响)")
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .redteam()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/redteam/runs")
async def redteam_runs(
        limit: int = 50,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """红队批次历史(观测面)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .list_redteams(
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/evolution/log")
async def evolution_log(
        kind: str = "",
        limit: int = 100,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """进化日志(观测面——六类留痕)"""
    try:
        _require_admin(x_role)
        from services.member73_p5_service import (
            Member73P5Service,
        )
        return {"code": 0,
                "data": await
                Member73P5Service()
                .evolution_log(
                    kind=kind or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


def register_member73_routes(app) -> None:
    """路由注册(main.py 挂载)"""
    app.include_router(router)
