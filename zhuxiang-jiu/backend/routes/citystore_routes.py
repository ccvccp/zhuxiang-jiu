"""县（区）网店模块路由(17 端点)

市级网店规则改造为县（区）网店(分店定位——本站为总店, 网站备案即为分店备案)。

鉴权:
    - 用户端(10 接口): X-Member-Id 头标识当前会员(申请/可用区县/可用城市/
      列表/详情/考核记录/订单关联/保证金查询)
    - 管理端(6 接口): X-Role: admin 头(确认开业/状态流转/考核/待确认列表/
      统计/保证金手动结算)
    - 公开(1 接口): 下单入口决策(县区网店优先原则; 可选 X-Member-Id
      仅增强默认收货地址兜底, 未登录不影响决策)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 资格不符/区县被占/状态非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 开店(3):      apply / detail / list
    - 确认开业(1):   audit(轻审核: 一键确认开业/驳回)
    - 状态流转(1):   status
    - 月度考核(3):   assessment-run / assessment-get / assessment-list
    - 订单关联(2):   orders-add / orders-list
    - 保证金(2):     margin-get(用户查进度) / margin-settle(管理端兜底)
    - 管理端(2):     pending / stats
    - 区县(1):       可用区县查询(县区店三级联动数据源)
    - 城市(1):       可用城市查询(三级联动第 1/2 列数据源)
    - 入口决策(1):   order-entry/decide(地图定位→县区店/市店入口/本站入口)
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.citystore_service import CityStoreService
from repositories.citystore_repository import (
    # 网店状态
    CHANNEL_MINIPROGRAM,
)


router = APIRouter()
_service = CityStoreService()


# ============================================================
# 鉴权与异常映射辅助(对齐 wallet/payment 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _map_key_error(exc: KeyError) -> HTTPException:
    """KeyError → 404"""
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    """ValueError → 409"""
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ApplyRequest(PydBaseModel):
    memberId: int = Field(..., description="会员ID")
    memberLevel: int = Field(..., description="会员等级(5 = SVIP)")
    storeName: str = Field(..., description="网店名称")
    districtCode: str = Field(..., description="区县行政区划码(6位, 须从 districts/available 选择)")
    idName: str = Field(..., description="身份证姓名(2-30位)")
    idNumber: str = Field(..., description="身份证号(18位 GB 11643 校验)")
    signatureConfirm: bool = Field(..., description="确认签名(须 True——已阅读并同意县（区）网店保证金协议)")
    cityCode: str = Field("", description="上级市码(以区划册覆盖, 可不传)")
    cityName: str = Field("", description="上级市名(以区划册覆盖, 可不传)")
    provinceCode: str = Field("", description="省份码(以区划册覆盖, 可不传)")
    provinceName: str = Field("", description="省份名(以区划册覆盖, 可不传)")


class AuditRequest(PydBaseModel):
    auditor: str = Field(..., description="审核人")
    approved: bool = Field(..., description="是否通过")
    remark: str = Field("", description="审核备注")


class StatusRequest(PydBaseModel):
    status: int = Field(..., description="新状态: 1运营 2预警 3暂停 4取消")
    operator: str = Field("", description="操作人")


class AssessmentRequest(PydBaseModel):
    month: str = Field(..., description="考核月份(YYYY-MM)")


class AddOrderRequest(PydBaseModel):
    orderNo: str = Field(..., description="订单号")
    productId: str = Field(..., description="商品ID")
    productName: str = Field("", description="商品名称")
    quantity: int = Field(..., ge=1, description="数量")
    retailPrice: float = Field(..., ge=0, description="零售单价")
    totalAmount: float = Field(..., ge=0, description="订单总金额")
    customerPhone: str = Field("", description="消费者手机(脱敏)")
    deliveryCityCode: str = Field("", description="收货城市码")
    salesChannel: int = Field(CHANNEL_MINIPROGRAM, description="销售渠道: 1直播 2小程序 3社群 4H5 5抖音")


# ============================================================
# P0 接口(12 个)
# ============================================================

@router.post("/api/citystore/apply", tags=["县区网店模块"])
async def apply(
    data: ApplyRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """申请开县（区）网店(身份证+签名确认+保证金预存 ¥1000 一年期)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.apply(
            member_id=data.memberId,
            member_level=data.memberLevel,
            store_name=data.storeName,
            district_code=data.districtCode,
            id_name=data.idName,
            id_number=data.idNumber,
            signature_confirm=data.signatureConfirm,
            city_code=data.cityCode,
            city_name=data.cityName,
            province_code=data.provinceCode,
            province_name=data.provinceName,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/districts/available", tags=["县区网店模块"])
async def list_available_districts(
    city: str | None = Query(
        None, max_length=6,
        description="市码筛选(如 370100=济南市; 缺省全量——前端三级联动一次性预载)"),
):
    """查询可开网店区县(未被独占; 一区县一店)

    区县源: 全国县级行政区 3028 条(GB/T 2260, 挂靠 344 市——
    直辖市并挂单市条目/省直辖县级市自身单条目/港澳台不收录)。
    上级市被存量市级网店覆盖时该市区县仍返回但 apply 拦截。

    游客可用(公开白名单——申请开店页/情景切换城市同源数据)。
    """
    try:
        result = await _service.list_available_districts(city_code=city)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/cities/available", tags=["县区网店模块"])
async def list_available_cities(
    province: str | None = Query(
        None, max_length=6,
        description="省份码筛选(如 370000=山东省; 缺省全量)"),
):
    """查询可用城市列表(未被独占的城市)

    城市源: 全国 34 省级行政区 + 344 地级行政区全量
    (GB/T 2260)——申请开店城市选择数据源。

    游客可用(公开白名单——申请开店页/情景切换城市同源数据)。
    """
    try:
        result = await _service.list_available_cities(
            province_code=province)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/list", tags=["县区网店模块"])
async def list_stores(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    status: int | None = Query(None, description="按状态筛选: 0待审核 1运营 2预警 3暂停 4取消"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """我的网店列表"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_stores(
            member_id=int(user_id),
            status=status,
            limit=limit,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/pending", tags=["县区网店模块"])
async def list_pending_stores(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """待审核网店列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_pending_stores(limit=limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/stats", tags=["县区网店模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """网店统计(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/margin/overview", tags=["县区网店模块"])
async def get_margin_overview(
    x_role: str = Header(None, alias="X-Role"),
):
    """保证金治理总览(管理端): 统计 + 到期预警 + 滞留监控 + 对账恒等式"""
    _require_admin(x_role)
    try:
        result = await _service.margin_admin_overview()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/margin/list", tags=["县区网店模块"])
async def get_margin_list(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="筛选: locked|settled(缺省全部)"),
    days: int = Query(None, ge=0, le=365,
                      description="仅 locked: 剩余天数 ≤ N 的即将到期清单"),
):
    """保证金治理清单(管理端): 含实时进度/剩余天数/已提醒档位"""
    _require_admin(x_role)
    try:
        result = await _service.margin_admin_list(status=status, days=days)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


class OrderEntryDecideRequest(PydBaseModel):
    """下单入口决策请求(市级网店优先原则)"""
    cityCode: str | None = Field(None, description="地级市行政区划码(如 110100)")
    adcode: str | None = Field(None, description="区县级码(如 110105, 自动转市级)")
    cityName: str | None = Field(None, description="城市名(如 北京市)")
    provinceName: str | None = Field(None, description="省份名(配合城市名)")
    longitude: float | None = Field(None, ge=-180, le=180, description="经度")
    latitude: float | None = Field(None, ge=-90, le=90, description="纬度")
    nearbyRadiusKm: float = Field(50.0, ge=1, le=500,
                                  description="附近门店搜索半径(km)")


@router.post("/api/citystore/order-entry/decide", tags=["县区网店模块"])
async def decide_order_entry(
    data: OrderEntryDecideRequest,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """下单入口决策(市级网店优先原则)

    根据地图位置判定: 所在城市有营业中的市级网店 → 返回市店下单入口
    (含 storeCode/折扣), 无市店或市店未营业 → 返回本站下单入口。
    城市判定优先级: cityCode > adcode > cityName > 经纬度附近门店 > 默认收货地址。
    """
    try:
        try:
            member_id = int(x_member_id) if x_member_id else None
        except (TypeError, ValueError):
            member_id = None  # 非数字头视为未登录(仅失去地址兜底, 不影响决策)
        result = await _service.decide_order_entry(
            city_code=data.cityCode,
            adcode=data.adcode,
            city_name=data.cityName,
            province_name=data.provinceName,
            longitude=data.longitude,
            latitude=data.latitude,
            member_id=member_id,
            nearby_radius_km=data.nearbyRadiusKm,
        )
        return {"success": True, **result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}", tags=["县区网店模块"])
async def get_store_detail(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """网店详情"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_store_detail(store_code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/audit", tags=["县区网店模块"])
async def audit_store(
    store_code: str,
    data: AuditRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """审核开店申请(后台, 待审核 → 运营中/已取消)"""
    _require_admin(x_role)
    try:
        result = await _service.audit_store(
            store_code=store_code,
            auditor=data.auditor,
            approved=data.approved,
            remark=data.remark,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/citystore/{store_code}/status", tags=["县区网店模块"])
async def update_status(
    store_code: str,
    data: StatusRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新网店状态(运营/预警/暂停/取消)"""
    _require_admin(x_role)
    try:
        result = await _service.update_status(
            store_code=store_code,
            new_status=data.status,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/assessment", tags=["县区网店模块"])
async def run_assessment(
    store_code: str,
    data: AssessmentRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """触发月度考核(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.run_assessment(
            store_code=store_code,
            month=data.month,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}/assessment/{month}", tags=["县区网店模块"])
async def get_assessment(
    store_code: str,
    month: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询月度考核结果"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_assessment(store_code, month)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}/assessments", tags=["县区网店模块"])
async def list_assessments(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询网店所有考核记录"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_assessments(store_code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/orders", tags=["县区网店模块"])
async def add_order(
    store_code: str,
    data: AddOrderRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """关联订单到网店(用于销售额统计)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.add_order(
            store_code=store_code,
            order_no=data.orderNo,
            product_id=data.productId,
            product_name=data.productName,
            quantity=data.quantity,
            retail_price=data.retailPrice,
            total_amount=data.totalAmount,
            customer_phone=data.customerPhone,
            delivery_city_code=data.deliveryCityCode,
            sales_channel=data.salesChannel,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}/orders", tags=["县区网店模块"])
async def list_orders(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    month: str | None = Query(None, description="按月份筛选(YYYY-MM)"),
):
    """查询网店订单"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_orders(store_code, month)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P0 接口(保证金 2 个——用户查进度 + 管理端手动补结算)
# ============================================================

@router.get("/api/citystore/{store_code}/margin", tags=["县区网店模块"])
async def get_margin(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询网店保证金(预存 ¥1000 一年期, 含年任务实时进度)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_margin(store_code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/margin/settle", tags=["县区网店模块"])
async def settle_margin(
    store_code: str,
    x_role: str = Header(None, alias="X-Role"),
    reason: str = Query("expired", description="结算原因: expired满一年 | cancelled中途取消 | rejected驳回"),
):
    """手动结算保证金(管理端——调度器自动结算的幂等兜底入口)

    资金红线豁免面: 不受 WALLET_MODE 门控, 保证金退还在任何档位可用。
    """
    _require_admin(x_role)
    try:
        result = await _service.settle_margin(store_code, reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_citystore_routes(app):
    """注册县（区）网店模块路由"""
    app.include_router(router)
