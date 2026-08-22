"""推广码矩阵获利模块路由(18 端点)

鉴权:
    - 用户端(9 接口): X-Member-Id 头(领取/绑定/领酒/奖励购买/统计)
    - 管理端(9 接口): X-Role: admin 头(参数配置/关系/奖励/领酒/推广码管理)
    - 公开(1 接口): 活动酒池查询

异常映射(遵循项目约定):
    - KeyError → 404(会员/产品/记录不存在)
    - ValueError → 409(重复绑定/成环/余额不足/参数非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services import ai_feedback_hooks as ai_hooks
from services.promotion_service import PromotionService


router = APIRouter()
_service = PromotionService()


# ============================================================
# 鉴权与异常映射辅助(对齐 points/citystore 风格)
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 头须为会员ID数字")


def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射: KeyError → 404, ValueError → 409"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ClaimCodeRequest(PydBaseModel):
    channel: str = Field("wechat_miniprogram",
                         description="投放渠道: wechat_miniprogram/douyin/kuaishou/"
                                     "xiaohongshu/bilibili/taobao/direct")


class BindCodeRequest(PydBaseModel):
    code: str = Field(..., min_length=4, max_length=16,
                      description="推广码(ZXBJ-XXXXXX)")
    inviteeMemberId: int = Field(..., gt=0, description="被邀请(新注册)会员ID")


class ClaimWineRequest(PydBaseModel):
    productId: str = Field(..., description="活动池内的竹奕酒产品ID")
    address: str = Field(..., min_length=5, description="收货地址")


class RewardPurchaseRequest(PydBaseModel):
    productId: str = Field(..., description="本站产品ID")
    quantity: int = Field(1, ge=1, le=99, description="购买数量")


class UpdateSettingsRequest(PydBaseModel):
    enabled: Optional[bool] = Field(None, description="模块总开关")
    level1Threshold: Optional[int] = Field(None, ge=1, description="一级奖励人数阈值")
    level1RewardAmount: Optional[float] = Field(None, ge=0,
                                                description="一级奖励金额(元/轮)")
    level2SubPromoterCount: Optional[int] = Field(None, ge=1,
                                                  description="二级达标所需下线数")
    level2SubThreshold: Optional[int] = Field(None, ge=1,
                                              description="每个下线需推广人数")
    wineMinPrice: Optional[float] = Field(None, ge=0, description="奖励酒最低价")
    eligibleProductIds: Optional[list] = Field(
        None, description="活动酒池产品ID数组(null=自动按价格筛选)")


class GrantRewardRequest(PydBaseModel):
    memberId: int = Field(..., gt=0, description="会员ID")
    rewardType: str = Field(..., description="wallet(奖励余额)/wine_qualify(领酒资格)")
    amount: float = Field(0, ge=0, description="wallet 类型发放金额(元)")
    detail: str = Field("管理端手动补发", description="备注")


# ============================================================
# 用户端: 专属推广码
# ============================================================

@router.post("/api/promotion/code/claim", tags=["推广码矩阵模块"])
async def claim_promo_code(
    data: ClaimCodeRequest,
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """领取专属推广码(ZXBJ 竹奕标识, 同渠道幂等, 附各平台分享文案)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.claim_promo_code(member_id, data.channel)
        return result
    except Exception as exc:
        _handle(exc)


@router.get("/api/promotion/my/codes", tags=["推广码矩阵模块"])
async def list_my_codes(
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """我的推广码列表(各渠道)"""
    member_id = _require_member_id(x_member_id)
    try:
        codes = await _service.list_my_codes(member_id)
        return {"success": True, "codes": codes}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 用户端: 绑定推广码(新用户)
# ============================================================

@router.post("/api/promotion/bind", tags=["推广码矩阵模块"])
async def bind_promo_code(data: BindCodeRequest):
    """新用户绑定推广码, 建立矩阵关系并触发上级奖励检查

    一人仅可绑定一次; 禁止自绑; 祖先链防环。
    """
    try:
        result = await _service.bind_relation(data.code, data.inviteeMemberId)
        return result
    except Exception as exc:
        _handle(exc)


# ============================================================
# 用户端: 统计/团队/奖励查询
# ============================================================

@router.get("/api/promotion/my/stats", tags=["推广码矩阵模块"])
async def get_my_stats(
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """我的推广统计: 下线数/达标下线数/奖励余额(不可提现)/可领酒资格"""
    member_id = _require_member_id(x_member_id)
    try:
        stats = await _service.get_my_stats(member_id)
        return {"success": True, **stats}
    except Exception as exc:
        _handle(exc)


@router.get("/api/promotion/my/team", tags=["推广码矩阵模块"])
async def list_my_team(
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """我的下线列表(含各自推广数, 判定裂变达标进度)"""
    member_id = _require_member_id(x_member_id)
    try:
        team = await _service.list_my_team(member_id)
        return {"success": True, "team": team}
    except Exception as exc:
        _handle(exc)


@router.get("/api/promotion/my/rewards", tags=["推广码矩阵模块"])
async def list_my_rewards(
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """我的奖励记录(钱包轮次 + 领酒资格)"""
    member_id = _require_member_id(x_member_id)
    try:
        rewards = await _service.list_my_rewards(member_id)
        return {"success": True, "rewards": rewards}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 用户端: 奖励酒池与领取
# ============================================================

@router.get("/api/promotion/products/eligible", tags=["推广码矩阵模块"])
async def list_eligible_products():
    """活动酒池: 价格 ≥ wineMinPrice 的竹奕酒(公开, 领取前浏览)"""
    try:
        products = await _service.list_eligible_products()
        return {"success": True, "products": products}
    except Exception as exc:
        _handle(exc)


@router.post("/api/promotion/wine/claim", tags=["推广码矩阵模块"])
async def claim_wine(
    data: ClaimWineRequest,
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """领取奖励酒: 核销领酒资格, 从活动池选 1 瓶(200元以上竹奕酒)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.claim_wine(member_id, data.productId,
                                           data.address)
        return result
    except Exception as exc:
        _handle(exc)


# ============================================================
# 用户端: 奖励余额购买本站产品
# ============================================================

@router.post("/api/promotion/reward/purchase", tags=["推广码矩阵模块"])
async def reward_purchase(
    data: RewardPurchaseRequest,
    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
):
    """奖励余额购买本站产品(奖励余额唯一出口, 不可提现)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.reward_purchase(member_id, data.productId,
                                                data.quantity)
        return result
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理端: 参数配置(静态路径在前, 动态路径在后)
# ============================================================

@router.get("/api/promotion/admin/settings", tags=["推广码矩阵模块"])
async def admin_get_settings(
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """读取推广参数配置(阈值/奖励金额/酒池等)"""
    _require_admin(x_role)
    try:
        settings = await _service.get_settings()
        return {"success": True, "settings": settings}
    except Exception as exc:
        _handle(exc)


@router.put("/api/promotion/admin/settings", tags=["推广码矩阵模块"])
async def admin_update_settings(
    data: UpdateSettingsRequest,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """修改推广参数配置(新绑定即时按新参数计算奖励)"""
    _require_admin(x_role)
    try:
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        settings = await _service.update_settings(fields, admin="admin")
        return {"success": True, "settings": settings}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理端: 关系/奖励/领酒/推广码管理
# ============================================================

@router.get("/api/promotion/admin/relations", tags=["推广码矩阵模块"])
async def admin_list_relations(
    x_role: Optional[str] = Header(None, alias="X-Role"),
    inviterMemberId: int = Query(None, description="按上级筛选"),
    status: str = Query(None, description="valid/invalid"),
    limit: int = Query(200, ge=1, le=1000),
):
    """绑定关系列表(管理端审计)"""
    _require_admin(x_role)
    try:
        relations = await _service.admin_list_relations(
            inviter_member_id=inviterMemberId, status=status, limit=limit)
        return {"success": True, "relations": relations}
    except Exception as exc:
        _handle(exc)


@router.get("/api/promotion/admin/rewards", tags=["推广码矩阵模块"])
async def admin_list_rewards(
    x_role: Optional[str] = Header(None, alias="X-Role"),
    memberId: int = Query(None, description="按会员筛选"),
    rewardType: str = Query(None, description="wallet/wine_qualify"),
    status: str = Query(None, description="issued/used"),
    limit: int = Query(200, ge=1, le=1000),
):
    """奖励发放记录列表"""
    _require_admin(x_role)
    try:
        rewards = await _service.admin_list_rewards(
            member_id=memberId, reward_type=rewardType, status=status,
            limit=limit)
        return {"success": True, "rewards": rewards}
    except Exception as exc:
        _handle(exc)


@router.get("/api/promotion/admin/wine-claims", tags=["推广码矩阵模块"])
async def admin_list_wine_claims(
    x_role: Optional[str] = Header(None, alias="X-Role"),
    memberId: int = Query(None, description="按会员筛选"),
    status: str = Query(None, description="pending_shipped/shipped/done"),
    limit: int = Query(200, ge=1, le=1000),
):
    """领酒记录列表"""
    _require_admin(x_role)
    try:
        claims = await _service.admin_list_wine_claims(
            member_id=memberId, status=status, limit=limit)
        return {"success": True, "claims": claims}
    except Exception as exc:
        _handle(exc)


@router.post("/api/promotion/admin/rewards/grant", tags=["推广码矩阵模块"])
async def admin_grant_reward(
    data: GrantRewardRequest,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """手动补发奖励(钱包奖励余额或领酒资格, 客诉补偿等场景)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_grant_reward(
            data.memberId, data.rewardType, data.amount, data.detail)
        # v7.6 自动反馈: 奖励发放 → 防作弊观察评分+配对(正常发放期望 pay)
        await ai_hooks.on_promotion_reward(f"grant:{data.memberId}")
        return result
    except Exception as exc:
        _handle(exc)


@router.post("/api/promotion/admin/codes/{code}/revoke", tags=["推广码矩阵模块"])
async def admin_revoke_code(
    code: str,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """撤销推广码(撤销后不可再绑定, 已建立关系不受影响)"""
    _require_admin(x_role)
    try:
        return await _service.admin_revoke_code(code)
    except Exception as exc:
        _handle(exc)


@router.post("/api/promotion/admin/relations/{invitee_id}/invalidate",
             tags=["推广码矩阵模块"])
async def admin_invalidate_relation(
    invitee_id: int,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """作废/恢复绑定关系(作废后不计入上级业绩, 再调用一次恢复)"""
    _require_admin(x_role)
    try:
        return await _service.admin_invalidate_relation(invitee_id)
    except Exception as exc:
        _handle(exc)


@router.put("/api/promotion/admin/wine-claims/{claim_id}/ship",
            tags=["推广码矩阵模块"])
async def admin_ship_wine(
    claim_id: int,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """领酒发货流转: 待发货 → 已发货 → 已完成"""
    _require_admin(x_role)
    try:
        result = await _service.admin_ship_wine(claim_id)
        return {"success": True, **result}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 路由注册
# ============================================================

def register_promotion_routes(app) -> None:
    """向 FastAPI 应用注册推广码矩阵模块路由"""
    app.include_router(router)
