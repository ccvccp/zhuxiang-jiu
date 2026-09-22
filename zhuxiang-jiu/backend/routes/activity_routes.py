"""活动管理模块路由(20 端点)

鉴权:
    - 用户端(6接口): X-Member-Id 头标识当前会员(报名/取消/抽奖
      要求头与 body.userId 一致——仅本人可操作)
    - 管理端(8接口): 管理员(role=admin) 或 持有活动后台授权
      (权限模块 /api/perm/* 的 activity.operate/approve/manage
      任一生效且已签责任书的授权——管理员可授权后台角色
      设计发布活动, 权责共存/限时/审计留痕)
    - 抽奖发奖(6, P1-12): 奖品池配置/查询公示(公开)/抽奖/我的奖品/
      实物发货登记(admin)/签收确认(中奖人)/管理端发奖记录列表

异常映射:
    - KeyError → 404(活动/报名不存在)
    - ValueError → 409(业务冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(20个):
    - 用户端(6): 查询列表/查询详情/报名/取消报名/擂台赛排名/活动统计
    - 管理端(8): 创建活动/编辑活动/活动状态流转/活动审核/管理端列表/
      提交擂台赛分数/查询报名列表/管理端发奖记录列表
    - 抽奖发奖(6): 配置奖品池/奖品池公示/抽奖执行/我的奖品/发货登记/签收确认
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.activity_service import ActivityService


router = APIRouter()
_service = ActivityService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


# 活动后台授权链: 权限模块 activity 域操作级及以上任一生效授权
# (含已签责任书校验)即可管理活动模块——管理员授权后台角色
# 设计发布活动; 授权/吊销本身仍走权限模块(仅超管)
ACTIVITY_ADMIN_NODE_CODES = (
    "activity.operate", "activity.approve", "activity.manage",
)


async def _has_activity_grant(member_id: int) -> bool:
    """会员是否持有活动后台授权(生效+未过期+已签责任书)

    走 perm 静默持有校验(has_any_grant)——网关级探测不触发
    deny_access 审计与 AI 越权升级, 防逐码尝试误伤冻结授权。
    """
    from services.perm_service import PermService

    try:
        return await PermService().has_any_grant(
            member_id, ACTIVITY_ADMIN_NODE_CODES)
    except Exception:
        return False


async def _require_activity_admin(x_role: str | None,
                                  x_member_id: str | None):
    """活动管理鉴权: 管理员直通, 或活动后台授权链"""
    if x_role == "admin":
        return
    if x_member_id:
        try:
            mid = int(x_member_id)
        except (TypeError, ValueError):
            mid = 0
        if mid and await _has_activity_grant(mid):
            return
    raise HTTPException(
        status_code=403,
        detail="需要管理员权限或活动后台授权(activity.operate及以上)")


def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class CreateActivityRequest(PydBaseModel):
    name: str = Field(..., description="活动名称")
    type: str = Field(..., description="活动类型: promotion/lottery/competition/arena/interactive/groupbuy/seckill/presale")
    subType: str = Field("", description="子类型(如擂台赛L01-L08)")
    description: str = Field("", description="活动描述")
    startTime: str = Field("", description="开始时间")
    endTime: str = Field("", description="结束时间")
    budget: float = Field(0.0, ge=0, description="活动预算")
    rules: dict = Field(default_factory=dict, description="活动规则(JSON)")
    applicableScope: dict = Field(default_factory=dict, description="适用范围(JSON)")
    createdBy: int = Field(0, description="创建人ID")


class UpdateActivityRequest(PydBaseModel):
    """编辑活动(仅草稿可编辑; 未传字段保持原值)

    注意: 不含 type/status/usedBudget 等字段——模型级防误改
    (type 不可改: draft 抽奖活动可能已配奖品池, 改 type 产生孤儿数据)
    """
    name: str = Field(None, description="活动名称")
    subType: str = Field(None, description="子类型(如擂台赛L01-L08)")
    description: str = Field(None, description="活动描述")
    startTime: str = Field(None, description="开始时间")
    endTime: str = Field(None, description="结束时间")
    budget: float = Field(None, ge=0, description="活动预算")
    rules: dict = Field(None, description="活动规则(JSON)")
    applicableScope: dict = Field(None, description="适用范围(JSON)")


class RegisterRequest(PydBaseModel):
    activityId: int = Field(..., description="活动ID")
    userId: int = Field(..., description="会员ID")
    participateData: dict = Field(default_factory=dict, description="参与数据(JSON)")


class CancelRegistrationRequest(PydBaseModel):
    activityId: int = Field(..., description="活动ID")
    userId: int = Field(..., description="会员ID")


class TransitionStatusRequest(PydBaseModel):
    targetStatus: str = Field(..., description="目标状态: registering/ongoing/ended/cancelled")
    operator: int = Field(0, description="操作人ID")


class AuditActivityRequest(PydBaseModel):
    approve: bool = Field(..., description="是否通过")
    auditor: int = Field(0, description="审核人ID")
    reason: str = Field("", description="审核理由")


class SubmitArenaScoreRequest(PydBaseModel):
    activityId: int = Field(..., description="活动ID")
    userId: int = Field(..., description="会员ID")
    score: float = Field(..., ge=0, description="擂台赛分数")
    realName: str = Field("", description="实名认证姓名")


# ============================================================
# P0 接口(12 个) — 静态路径优先于动态路径
# ============================================================

# --- 用户端接口 ---

@router.get("/api/activity/list", tags=["活动管理模块"])
async def list_activities(
    status: str = Query(None, description="按状态筛选: draft/registering/ongoing/ended/cancelled"),
    type: str = Query(None, description="按类型筛选: promotion/lottery/competition/arena/interactive/groupbuy/seckill/presale"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
):
    """查询活动列表(默认仅查非草稿状态)"""
    try:
        result = await _service.list_activities(status=status, type_=type, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/stats/{activity_id}", tags=["活动管理模块"])
async def get_stats(
    activity_id: int,
):
    """活动统计(报名数/预算使用等)"""
    try:
        result = await _service.get_stats(activity_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/leaderboard/{activity_id}", tags=["活动管理模块"])
async def get_leaderboard(
    activity_id: int,
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询擂台赛排名(按 rank 升序)"""
    try:
        result = await _service.get_leaderboard(activity_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/registrations/{activity_id}", tags=["活动管理模块"])
async def list_registrations(
    activity_id: int,
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询活动报名列表(管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.repo.list_registrations(activity_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/my-registrations", tags=["活动管理模块"])
async def my_registrations(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的报名列表(用户端: 含已结束活动, 仅有效报名)

    注: 必须注册在 /{activity_id} 之前, 否则会被路径参数吞掉
    """
    user_id = int(_require_member_id(x_member_id))
    try:
        result = await _service.list_my_registrations(user_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/{activity_id}", tags=["活动管理模块"])
async def get_activity(
    activity_id: int,
):
    """查询活动详情"""
    try:
        result = await _service.get_activity(activity_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/register", tags=["活动管理模块"])
async def register(
    data: RegisterRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """活动报名(幂等防重, 同一用户对同一活动仅可报名一次)"""
    member_id = _require_member_id(x_member_id)
    if str(member_id) != str(data.userId):
        raise HTTPException(status_code=403, detail="仅本人可报名")
    try:
        result = await _service.register(
            activity_id=data.activityId,
            user_id=data.userId,
            participate_data=data.participateData,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/cancel", tags=["活动管理模块"])
async def cancel_registration(
    data: CancelRegistrationRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """取消报名(报名中/进行中状态可取消)"""
    member_id = _require_member_id(x_member_id)
    if str(member_id) != str(data.userId):
        raise HTTPException(status_code=403, detail="仅本人可取消报名")
    try:
        result = await _service.cancel_registration(data.activityId, data.userId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/arena/score", tags=["活动管理模块"])
async def submit_arena_score(
    data: SubmitArenaScoreRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交擂台赛分数(自动排名)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.submit_arena_score(
            activity_id=data.activityId,
            user_id=data.userId,
            score=data.score,
            real_name=data.realName,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 管理端接口 ---

@router.post("/api/activity/admin/create", tags=["活动管理模块"])
async def create_activity(
    data: CreateActivityRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建活动(初始状态: 草稿; 管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.create_activity(
            name=data.name,
            type_=data.type,
            sub_type=data.subType,
            description=data.description,
            start_time=data.startTime,
            end_time=data.endTime,
            budget=data.budget,
            rules=data.rules,
            applicable_scope=data.applicableScope,
            created_by=data.createdBy,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/activity/admin/update/{activity_id}", tags=["活动管理模块"])
async def update_activity(
    activity_id: int,
    data: UpdateActivityRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """编辑活动(仅草稿可编辑; 部分更新, 未传字段保持原值)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.update_activity(
            activity_id=activity_id,
            updates=data.dict(exclude_unset=True),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/admin/list", tags=["活动管理模块"])
async def list_admin_activities(
    status: str = Query(None, description="按状态筛选"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """管理端查询活动列表(含草稿; 管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.list_admin_activities(status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/admin/transition/{activity_id}", tags=["活动管理模块"])
async def transition_status(
    activity_id: int,
    data: TransitionStatusRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """活动状态流转(草稿→报名中→进行中→已结束; 管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.transition_status(
            activity_id=activity_id,
            target_status=data.targetStatus,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/admin/audit/{activity_id}", tags=["活动管理模块"])
async def audit_activity(
    activity_id: int,
    data: AuditActivityRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """活动审核(草稿→报名中 or 拒绝; 管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.audit_activity(
            activity_id=activity_id,
            approve=data.approve,
            auditor=data.auditor,
            reason=data.reason,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 抽奖发奖(P1-12, 设计文档 §3.3/§7.3)
# ============================================================

class PrizeItemRequest(PydBaseModel):
    """单个奖品配置"""
    prizeName: str = Field(..., min_length=1, description="奖品名称")
    prizeType: str = Field("coupon", description="奖品类型: coupon/points/product/cash/benefit/banquet_wine/mascot")
    prizeValue: float = Field(0.0, ge=0, description="奖品价值(元/积分值)")
    probability: float = Field(..., ge=0, le=100, description="中奖概率(%)")
    dailyLimit: int = Field(0, ge=0, description="日限量(0=不限)")
    totalLimit: int = Field(..., ge=1, description="总限量")


class ConfigurePrizesRequest(PydBaseModel):
    """配置奖品池(管理端)"""
    prizes: list[PrizeItemRequest] = Field(..., min_items=1,
                                           description="奖品列表")


class DrawLotteryRequest(PydBaseModel):
    """抽奖执行"""
    activityId: int = Field(..., description="抽奖活动ID")
    userId: int = Field(..., description="会员ID")


class DeliverPrizeRequest(PydBaseModel):
    """实物奖品发货登记"""
    waybillNo: str = Field(..., min_length=1, description="物流运单号")


@router.post("/api/activity/admin/prizes/{activity_id}", tags=["活动管理模块"])
async def configure_prizes(
    activity_id: int,
    data: ConfigurePrizesRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """配置抽奖奖品池(管理端, 概率总和≤100%, 单奖≤¥5000 合规红线)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.configure_prizes(
            activity_id, [p.dict() for p in data.prizes])
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/lottery/{activity_id}/prizes", tags=["活动管理模块"])
async def get_prize_pool(activity_id: int):
    """查询奖品池(概率公示, 合规要求)"""
    try:
        result = await _service.get_prize_pool(activity_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/lottery/draw", tags=["活动管理模块"])
async def draw_lottery(
    data: DrawLotteryRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """抽奖执行(服务端概率计算, 每日 3 次; 中奖自动发奖分派)"""
    member_id = _require_member_id(x_member_id)
    if str(member_id) != str(data.userId):
        raise HTTPException(status_code=403, detail="仅本人可抽奖")
    try:
        result = await _service.draw_lottery(data.activityId, data.userId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/prizes/mine", tags=["活动管理模块"])
async def list_my_prizes(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的奖品(按状态分组: 待发放/已发放/已发货/已签收)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_my_prizes(int(member_id))
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/admin/prize-records", tags=["活动管理模块"])
async def list_admin_prize_records(
    status: str = Query(None, description="按状态筛选: pending/issued/shipped/signed/expired"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """管理端查询全量发奖记录(发货登记面板, 默认查全部; 管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.list_prize_records(status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/admin/prize/{record_no}/deliver", tags=["活动管理模块"])
async def deliver_prize(
    record_no: str,
    data: DeliverPrizeRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """实物奖品发货登记(待发放 → 已发货; 管理员/活动后台授权)"""
    await _require_activity_admin(x_role, x_member_id)
    try:
        result = await _service.deliver_prize(record_no, data.waybillNo)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/prize/{record_no}/confirm", tags=["活动管理模块"])
async def confirm_prize_received(
    record_no: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """用户签收确认(已发货 → 已签收, 仅中奖人)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.confirm_prize_received(record_no, int(member_id))
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_activity_routes(app):
    """注册活动管理模块路由"""
    app.include_router(router)
