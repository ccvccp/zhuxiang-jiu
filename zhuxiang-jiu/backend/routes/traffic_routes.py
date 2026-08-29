"""流量管理模块路由(21 端点)

鉴权:
    - 用户端(9接口): X-Member-Id 头标识当前会员
    - 管理端(12接口): X-Role: admin 头(创建推广员/流量来源/管理端统计/流量分发等)

异常映射:
    - KeyError → 404(推广员不存在)
    - ValueError → 409(业务冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(21个):
    - 推广员(4):  创建推广员/列表/详情/等级查询
    - 引流线索(2): 线索列表/线索上报
    - 统计(1):    流量统计
    - 佣金(1):    佣金计算
    - 裂变(1):    裂变关系查询
    - 流量来源(2): 创建/列表
    - 分发(1):    流量分发
    - 管理统计(1): admin/stats
    - 达人(8):    创建/列表/详情/平台绑定/平台同步/专属推广码/归因查询/归因上报
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services import ai_feedback_hooks as ai_hooks
from services.traffic_service import TrafficService


router = APIRouter()
_service = TrafficService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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

class CreatePromoterRequest(PydBaseModel):
    userId: int = Field(..., description="关联用户ID")
    name: str = Field("", description="推广员姓名")
    level: str = Field("trainee", description="初始等级: trainee/junior/intermediate/senior/gold")
    parentPromoterId: int = Field(0, description="上级推广员ID(用于裂变)")


class RecordLeadRequest(PydBaseModel):
    promoterId: int = Field(..., description="推广员ID")
    userId: int = Field(..., description="访客用户ID")
    source: str = Field("direct", description="来源平台: douyin/kuaishou/wechat/xiaohongshu/bilibili/taobao/direct")
    medium: str = Field("share", description="引流方式: video/live/share/ad")
    utmParams: str = Field("", description="UTM 参数")
    isEffective: int = Field(1, description="是否有效流量: 1有效 0无效")
    status: str = Field("pending", description="状态: pending/registered/ordered/invalid")


class UpdateLeadStatusRequest(PydBaseModel):
    status: str = Field(..., description="新状态: pending/registered/ordered/invalid")
    isEffective: int = Field(None, description="是否有效(可选)")


class CalculateCommissionRequest(PydBaseModel):
    promoterId: int = Field(..., description="推广员ID")
    orderId: str = Field(..., description="关联订单号")
    orderAmount: float = Field(..., gt=0, description="订单金额")
    userId: int = Field(0, description="下单用户ID")


class CreateSourceRequest(PydBaseModel):
    code: str = Field(..., description="来源编码: douyin/kuaishou/wechat")
    name: str = Field(..., description="来源名称")
    description: str = Field("", description="来源描述")


class DistributeTrafficRequest(PydBaseModel):
    totalTraffic: int = Field(..., ge=0, description="待分发流量总数")
    strategy: str = Field("proportional", description="分发策略: proportional/average/weighted")


# ============================================================
# P0 接口(10 个) — 静态路径优先于动态路径
# ============================================================

# --- 用户端接口 ---

@router.post("/api/traffic/promoter/create", tags=["流量管理模块"])
async def create_promoter(
    data: CreatePromoterRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建推广员(初始等级见习, 5%佣金)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.create_promoter(
            user_id=data.userId,
            name=data.name,
            level=data.level,
            parent_promoter_id=data.parentPromoterId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/promoter/list", tags=["流量管理模块"])
async def list_promoters(
    status: str = Query(None, description="按状态筛选: active/paused/banned"),
    level: str = Query(None, description="按等级筛选: trainee/junior/intermediate/senior/gold"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询推广员列表(支持按状态/等级筛选)"""
    try:
        result = await _service.list_promoters(status=status, level=level, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/promoter/level/{promoter_id}", tags=["流量管理模块"])
async def get_promoter_level(
    promoter_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询推广员等级与升级条件"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_promoter_level(promoter_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/promoter/{promoter_id}", tags=["流量管理模块"])
async def get_promoter(
    promoter_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询推广员详情"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_promoter(promoter_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/leads", tags=["流量管理模块"])
async def list_leads(
    promoter_id: int = Query(None, description="按推广员筛选"),
    source: str = Query(None, description="按来源筛选: douyin/kuaishou/wechat"),
    status: str = Query(None, description="按状态筛选: pending/registered/ordered/invalid"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询引流记录(支持多条件筛选)"""
    try:
        result = await _service.list_leads(promoter_id=promoter_id, source=source,
                                            status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/stats", tags=["流量管理模块"])
async def get_stats(
    promoter_id: int = Query(None, description="指定推广员ID查询其统计"),
):
    """流量统计(按推广员或全局)"""
    try:
        result = await _service.get_stats(promoter_id=promoter_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/lead/record", tags=["流量管理模块"])
async def record_lead(
    data: RecordLeadRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """记录引流(推广员带来的流量)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.record_lead(
            promoter_id=data.promoterId,
            user_id=data.userId,
            source=data.source,
            medium=data.medium,
            utm_params=data.utmParams,
            is_effective=data.isEffective,
            status=data.status,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 管理端接口 ---

@router.post("/api/traffic/commission/calculate", tags=["流量管理模块"])
async def calculate_commission(
    data: CalculateCommissionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """计算佣金(按订单金额×等级佣金比例)"""
    _require_admin(x_role)
    try:
        result = await _service.calculate_commission(
            promoter_id=data.promoterId,
            order_id=data.orderId,
            order_amount=data.orderAmount,
            user_id=data.userId,
        )
        # v7.6 自动反馈: 佣金计算 → 防作弊观察评分+配对(正常计佣期望 pass)
        await ai_hooks.on_traffic_commission(
            f"{data.promoterId}:{data.orderId}")
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/fission/{promoter_id}", tags=["流量管理模块"])
async def get_fission_tree(
    promoter_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询推广员裂变关系树(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_fission_tree(promoter_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/source/create", tags=["流量管理模块"])
async def create_source(
    data: CreateSourceRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建流量来源(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_source(
            code=data.code,
            name=data.name,
            description=data.description,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/source/list", tags=["流量管理模块"])
async def list_sources(
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询流量来源列表"""
    try:
        result = await _service.list_sources(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/distribute", tags=["流量管理模块"])
async def distribute_traffic(
    data: DistributeTrafficRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """流量分发(按策略分配到各推广员)"""
    _require_admin(x_role)
    try:
        result = await _service.distribute_traffic(
            total_traffic=data.totalTraffic,
            strategy=data.strategy,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/admin/stats", tags=["流量管理模块"])
async def get_admin_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """管理端统计(全局)"""
    _require_admin(x_role)
    try:
        result = await _service.get_admin_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 博主(KOL)管理接口
# ============================================================

class CreateInfluencerRequest(PydBaseModel):
    userId: int = Field(..., description="关联用户ID")
    name: str = Field(..., min_length=1, max_length=50, description="博主名称")
    level: str = Field("C", description="博主等级: S/A/B/C")
    avatar: str = Field("", description="头像URL")
    commissionRate: float | None = Field(None, gt=0, le=0.30, description="佣金比例(0~0.30)")
    contractStart: str = Field("", description="合作开始日期")
    contractEnd: str = Field("", description="合作结束日期")


class AddInfluencerPlatformRequest(PydBaseModel):
    platform: str = Field(..., description="平台: douyin/kuaishou/wechat/xiaohongshu/bilibili")
    platformUid: str = Field(..., description="平台账号ID")
    platformName: str = Field("", description="平台昵称")
    profileUrl: str = Field("", description="主页链接")
    followerCount: int = Field(0, ge=0, description="粉丝数")
    verified: bool = Field(False, description="是否认证")


class CreatePromoCodeRequest(PydBaseModel):
    platform: str = Field(..., description="来源平台")
    expiresAt: str = Field("", description="过期时间")


class SyncPlatformRequest(PydBaseModel):
    followerCount: int | None = Field(None, ge=0, description="新粉丝数")
    verified: bool | None = Field(None, description="认证状态")


class AttributeTrafficRequest(PydBaseModel):
    promoCode: str = Field(..., description="博主推广码")
    isClick: bool = Field(False, description="是否点击事件")
    isLead: bool = Field(False, description="是否注册事件")
    orderAmount: float = Field(0, ge=0, description="订单金额(下单事件)")


@router.post("/api/traffic/influencer/create", tags=["流量管理模块"])
async def create_influencer(
    data: CreateInfluencerRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建博主(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_influencer(
            user_id=data.userId,
            name=data.name,
            level=data.level,
            avatar=data.avatar,
            commission_rate=data.commissionRate,
            contract_start=data.contractStart,
            contract_end=data.contractEnd,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/influencer/list", tags=["流量管理模块"])
async def list_influencers(
    status: str | None = Query(None, description="合作状态: cooperating/suspended/ended"),
    level: str | None = Query(None, description="博主等级: S/A/B/C"),
    limit: int = Query(100, ge=1, le=500, description="返回数量"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """博主列表(按等级/状态筛选)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_influencers(status=status, level=level, limit=limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/influencer/{influencer_id}", tags=["流量管理模块"])
async def get_influencer(
    influencer_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询博主详情(含平台+推广码)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_influencer(influencer_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/influencer/{influencer_id}/platform", tags=["流量管理模块"])
async def add_influencer_platform(
    influencer_id: int,
    data: AddInfluencerPlatformRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """为博主关联平台账号(唯一约束: 博主+平台)"""
    _require_admin(x_role)
    try:
        result = await _service.add_influencer_platform(
            influencer_id=influencer_id,
            platform=data.platform,
            platform_uid=data.platformUid,
            platform_name=data.platformName,
            profile_url=data.profileUrl,
            follower_count=data.followerCount,
            verified=data.verified,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/influencer/platform/{platform_id}/sync", tags=["流量管理模块"])
async def sync_influencer_platform(
    platform_id: int,
    data: SyncPlatformRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """同步平台数据(粉丝数/认证状态)"""
    _require_admin(x_role)
    try:
        result = await _service.sync_influencer_platform(
            platform_id=platform_id,
            follower_count=data.followerCount,
            verified=data.verified,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/influencer/{influencer_id}/promo-code", tags=["流量管理模块"])
async def create_influencer_promo_code(
    influencer_id: int,
    data: CreatePromoCodeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """为博主生成专属推广码(按平台区分)"""
    _require_admin(x_role)
    try:
        result = await _service.create_influencer_promo_code(
            influencer_id=influencer_id,
            platform=data.platform,
            expires_at=data.expiresAt,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/traffic/influencer/{influencer_id}/attribution", tags=["流量管理模块"])
async def get_influencer_attribution(
    influencer_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询博主归因数据(各平台流量/订单/GMV)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_influencer_attribution(influencer_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/traffic/influencer/attribute", tags=["流量管理模块"])
async def attribute_traffic(
    data: AttributeTrafficRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """流量归因(通过推广码追溯到博主, 可点击/注册/下单事件)"""
    _require_admin(x_role)
    try:
        result = await _service.attribute_traffic(
            promo_code=data.promoCode,
            is_click=data.isClick,
            is_lead=data.isLead,
            order_amount=data.orderAmount,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_traffic_routes(app):
    """注册流量管理模块路由"""
    app.include_router(router)
