"""县（区）网店模块业务逻辑层

核心业务(市级网店规则改造为县区网店——分店定位):
    - 开店申请(SVIP 资格校验 + 区县独占校验 + 身份证/签名确认
      + 保证金预存锁定(钱包 1000 元一年期) + 90 天冷静期)
    - 确认开业(轻审核: 平台一键确认开业/驳回; 驳回全额退保证金)
    - 保证金结算(满一年按年任务完成度退还/中途取消按已运营期
      折算/驳回全额退——不可提前取现)
    - 月度考核(进货/销售达标 + 连续不达标 + 折扣调整)
    - 状态流转(运营 → 预警/暂停 → 取消)
    - 订单关联(销售额统计)

锁保护(单向嵌套, 无死锁):
    - 申请: lock:citystore:apply:{memberId}  (防重复申请)
    - 区县独占: lock:citystore:city:{districtCode}  (防并发申请同一区县)
    - 保证金: lock:citystore:margin:{marginNo}  (防并发结算)
    - 钱包: lock:wallet:{userId}  (资金 RMW——最深一层)
    - 考核: lock:citystore:assessment:{storeCode}:{month}  (防重复考核)
    - 状态流转: lock:citystore:status:{storeCode}  (防并发状态变更)

异常约定:
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 资格不符/区县被占/状态非法等)
"""

import logging
from datetime import date, timedelta
from typing import ClassVar

from core.locks import get_lock
from core.helpers import ts
from repositories.citystore_repository import (
    CityStoreRepository,
    # 网店状态
    STORE_STATUS_PENDING, STORE_STATUS_OPERATING, STORE_STATUS_WARNING,
    STORE_STATUS_SUSPENDED, STORE_STATUS_CANCELLED,
    STORE_STATUS_NAMES, STORE_STATUS_FLOW,
    QUAL_STATUS_NORMAL, QUAL_STATUS_WARNING, QUAL_STATUS_YELLOW_CARD, QUAL_STATUS_CANCELLED,
    QUAL_STATUS_NAMES,
    # 阶梯折扣与考核
    DISCOUNT_UNQUALIFIED,
    PURCHASE_TARGET, SALES_TARGET, MAX_CONSECUTIVE_BELOW, COOLDOWN_DAYS,
    # 保证金
    MARGIN_AMOUNT, ANNUAL_PURCHASE_TARGET,
    MARGIN_STATUS_LOCKED, MARGIN_STATUS_SETTLED, MARGIN_SETTLE_REASONS,
    # 销售渠道
    CHANNEL_MINIPROGRAM, calc_discount,
)
from repositories.wallet_repository import WalletRepository


logger = logging.getLogger(__name__)

# 保证金一年期天数
MARGIN_TERM_DAYS = 365


def _validate_id_number(id_number: str) -> None:
    """身份证号校验(GB 11643-1999, 18 位)

    Raises:
        ValueError: 格式/出生日期/校验码不符
    """
    id_number = (id_number or "").strip().upper()
    if len(id_number) != 18:
        raise ValueError("身份证号须为 18 位")
    body, check = id_number[:17], id_number[17]
    if not body.isdigit() or check not in "0123456789X":
        raise ValueError("身份证号格式无效")
    # 出生日期段合法性
    try:
        birth = date(int(body[6:10]), int(body[10:12]), int(body[11:13]))
    except ValueError:
        raise ValueError("身份证号出生日期段无效") from None
    if not (date(1900, 1, 1) <= birth <= date.today()):
        raise ValueError("身份证号出生日期段无效")
    # MOD 11-2 校验码
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    mapping = "10X98765432"
    total = sum(int(c) * w for c, w in zip(body, weights, strict=True))
    if mapping[total % 11] != check:
        raise ValueError("身份证号校验码不符")


class CityStoreService:
    """县（区）网店业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: CityStoreRepository = CityStoreRepository()):
        self.repo = repo
        self.wallet_repo = WalletRepository()

    # ============================================================
    # 开店申请
    # ============================================================

    async def apply(self, member_id: int, member_level: int,
                    store_name: str, district_code: str,
                    id_name: str = "", id_number: str = "",
                    signature_confirm: bool = False,
                    city_code: str = "", city_name: str = "",
                    province_code: str = "", province_name: str = "") -> dict:
        """申请开县（区）网店(分店定位——本站为总店, 网站备案即为分店备案)

        申请材料: 身份证(姓名+号码) + 确认签名 + 保证金预存
        (取代原营业执照+食品卫生许可证审核)。

        Args:
            member_id: 会员ID
            member_level: 会员等级(必须为 5 = SVIP)
            store_name: 网店名称
            district_code: 区县行政区划码(6 位, 须从 districts/available 选择)
            id_name: 身份证姓名(2-30 位)
            id_number: 身份证号(18 位 GB 11643 校验)
            signature_confirm: 确认签名(必须 True——已阅读并同意保证金协议)
            city_code/city_name/province_code/province_name:
                上级市/省信息(以区划册覆盖, 可不传)

        Returns:
            网店详情(含 storeCode)

        Raises:
            ValueError: 资格不符/区县被占/重复申请/身份证不符/
                签名未确认/保证金不足
        """
        # 1. 资格校验: 仅 SVIP(L5) 可申请
        if member_level != 5:
            raise ValueError("县（区）网店为 SVIP 专属权益, 请先开通 SVIP 会员")

        # 2. 区县合法性校验(districtCode 须在全国区县区划册)
        from services.citystore_districts import get_district
        district_ref = get_district(district_code)
        if district_ref is None:
            raise ValueError(
                f"区县行政区划码无效: {district_code}"
                "(须从 GET /api/citystore/districts/available 选择)")
        # 名码一致性(防脏数据——以区划册为准)
        district_name = district_ref["districtName"]
        city_code = district_ref["cityCode"]
        city_name = district_ref["cityName"]
        province_code = district_ref["provinceCode"]
        province_name = district_ref["provinceName"]

        # 3. 上级市店拦截(存量市级网店独占地级市, 其下区县不再开放)
        city_store = await self.repo.get_by_city(city_code)
        if city_store:
            raise ValueError(
                f"城市 {city_name} 已由市级网店覆盖"
                f"({city_store.get('storeName', '')}), 不可在其区县开店")

        # 4. 身份证与签名确认(取代营业执照/食品卫生许可证审核)
        id_name = (id_name or "").strip()
        if not (2 <= len(id_name) <= 30):
            raise ValueError("身份证姓名必填(2-30 位)")
        _validate_id_number(id_number)
        if not signature_confirm:
            raise ValueError("须勾选确认签名(已阅读并同意县（区）网店保证金协议)")

        # 5. 保证金前置校验(锁外快速失败——防恶意申请空跑锁)
        account = await self.wallet_repo.get_account(member_id)
        if account is None:
            raise ValueError(
                "县（区）网店须预存保证金 ¥1000, 请先开通钱包账户")
        if account.get("status") != "active":
            raise ValueError("钱包账户状态异常, 无法预存保证金")
        if float(account.get("balance", 0)) < MARGIN_AMOUNT:
            raise ValueError(
                f"钱包余额不足 ¥{MARGIN_AMOUNT:.0f}, 请先充值预存保证金"
                "(一年期满按年任务完成度退还, 不可提前取现)")

        # 6. 防重复申请(同一会员有非取消状态的网店)
        async with get_lock(f"citystore:apply:{member_id}"):
            existing = await self.repo.get_by_member(member_id)
            if existing:
                raise ValueError("您已有一家网店, 不可重复开店")

            # 6b. 90 天冷静期校验(P1-11, 设计文档 8.3)
            await self._check_cooldown(member_id)

            # 7. 区县独占校验(一区县一店)
            async with get_lock(f"citystore:city:{district_code}"):
                district_store = await self.repo.get_by_city(district_code)
                if district_store:
                    raise ValueError(
                        f"区县 {district_name} 已有网店, 不可重复开店")

                # 8. 保证金锁定(钱包扣款 1000 → 保证金记录)
                margin_no = await self.repo.next_margin_no()
                async with get_lock(f"wallet:{member_id}"):
                    new_balance = await self.wallet_repo.add_balance(
                        member_id, -MARGIN_AMOUNT)
                    now = ts()
                    margin = {
                        "marginNo": margin_no,
                        "userId": member_id,
                        "storeCode": "",  # storeCode 在下一步回填
                        "amount": MARGIN_AMOUNT,
                        "annualTarget": ANNUAL_PURCHASE_TARGET,
                        "startDate": "",   # 确认开业时补
                        "endDate": "",     # 确认开业时补(=startDate+365 天)
                        "status": MARGIN_STATUS_LOCKED,
                        "settledAt": None,
                        "refundAmount": None,
                        "deductedAmount": None,
                        "completionRate": None,
                        "annualPurchased": None,
                        "settleReason": None,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                    await self.wallet_repo.save_transaction({
                        "txNo": await self.wallet_repo.next_tx_no(),
                        "userId": member_id,
                        "type": "citystore_margin_lock",
                        "direction": "OUT",
                        "amount": MARGIN_AMOUNT,
                        "balanceAfter": new_balance,
                        "payChannel": "",
                        "orderId": "",
                        "depositNo": "",
                        "withdrawNo": "",
                        "status": "success",
                        "description": "县（区）网店保证金预存锁定"
                                       f" ¥{MARGIN_AMOUNT:.2f}"
                                       "(一年期, 不可提前取现)",
                        "createdAt": now,
                    })

                # 9. 生成网店编号(CS-{区县码}-{3位序号})
                store_code = await self.repo.next_store_code(district_code)
                margin["storeCode"] = store_code
                await self.repo.save_margin(margin)
                store = {
                    "storeCode": store_code,
                    "storeName": store_name,
                    "memberId": member_id,
                    "districtCode": district_code,
                    "districtName": district_name,
                    "cityCode": city_code,
                    "cityName": city_name,
                    "provinceCode": province_code,
                    "provinceName": province_name,
                    "idName": id_name,
                    "idNumber": id_number,
                    "signatureConfirm": True,
                    "signedAt": now,
                    "marginNo": margin_no,
                    "annualTarget": ANNUAL_PURCHASE_TARGET,
                    "status": STORE_STATUS_PENDING,
                    "openDate": None,
                    "closeDate": None,
                    "currentDiscount": DISCOUNT_UNQUALIFIED,  # 新店默认 90 折
                    "consecutiveBelowPurchase": 0,
                    "consecutiveBelowSales": 0,
                    "createdAt": now,
                    "updatedAt": now,
                }
                await self.repo.save_store(store)
                return await self.get_store_detail(store_code)

    async def _check_cooldown(self, member_id: int) -> None:
        """90 天冷静期校验(P1-11, 设计文档 8.3)

        规则:
            - 运营后被取消的网店(openDate+closeDate 均非空),
              自 closeDate 起 90 天内不可重新申请
            - 审核驳回(从未运营, openDate 为空)不触发冷静期
              (驳回后重新提交申请+资质审核, 与文档表述一致)

        Raises:
            ValueError: 冷静期内(提示剩余天数)
        """
        from datetime import date

        history = await self.repo.list_history_stores_by_member(member_id)
        for store in history:
            if store.get("status") != STORE_STATUS_CANCELLED:
                continue
            if not store.get("openDate") or not store.get("closeDate"):
                continue  # 未运营即取消(审核驳回), 不触发
            try:
                closed = date.fromisoformat(str(store["closeDate"])[:10])
            except ValueError:
                continue  # 日期异常数据保守跳过
            days = (date.today() - closed).days
            if days < COOLDOWN_DAYS:
                remain = COOLDOWN_DAYS - days
                raise ValueError(
                    f"网店资格取消后须 90 天冷静期方可重新申请"
                    f"(自 {store['closeDate']} 起, 剩余 {remain} 天)")

    # ============================================================
    # 网店查询
    # ============================================================

    async def get_store_detail(self, store_code: str) -> dict:
        """查询网店详情"""
        store = await self.repo.get_store(store_code)
        if store is None:
            raise KeyError(f"网店不存在: {store_code}")

        result = dict(store)
        result["statusName"] = STORE_STATUS_NAMES.get(store["status"], "")
        return result

    async def list_stores(self, member_id: int = None, status: int = None,
                          limit: int = 50) -> dict:
        """查询网店列表"""
        stores = await self.repo.list_stores(member_id=member_id, status=status, limit=limit)
        for s in stores:
            s["statusName"] = STORE_STATUS_NAMES.get(s.get("status", ""), "")
        return {
            "stores": stores,
            "count": len(stores),
        }

    async def list_available_cities(
            self, province_code: str = None) -> dict:
        """查询可用城市列表(未被独占的城市; 可按省筛选)

        城市源: 全国省级行政区+地级行政区全量
        (citystore_regions, 34 省 344 市——GB/T 2260)。
        """
        from services import citystore_regions
        if province_code:
            if province_code not in \
                    citystore_regions.PROVINCES:
                raise ValueError(
                    f"省份码无效: {province_code}")
            all_cities = citystore_regions \
                .cities_by_province(province_code)
        else:
            all_cities = citystore_regions.all_cities()
        occupied = set(await self.repo.list_occupied_cities())
        available = [c for c in all_cities
                     if c["cityCode"] not in occupied]
        return {
            "cities": available,
            "count": len(available),
            "totalCount": len(all_cities),
            "occupiedCount": len(occupied),
        }

    def _get_predefined_cities(self) -> list[dict]:
        """预定义城市列表(全国全量——GB/T 2260 口径)"""
        from services.citystore_regions import all_cities
        return all_cities()

    # ============================================================
    # 下单入口决策(市级网店优先原则)
    # ============================================================

    # 可下单的市店状态(运营中/预警: 预警仅考核警示仍在营业)
    ORDERABLE_STORE_STATUSES: ClassVar[set] = {STORE_STATUS_OPERATING, STORE_STATUS_WARNING}

    async def decide_order_entry(
        self,
        city_code: str = None,
        adcode: str = None,
        city_name: str = None,
        province_name: str = None,
        longitude: float = None,
        latitude: float = None,
        member_id: int = None,
        nearby_radius_km: float = 50.0,
    ) -> dict:
        """下单入口决策: 所在城市有营业中的市级网店 → 市店入口, 否则 → 本站入口

        市级网店优先原则。城市判定优先级:
            1. cityCode(地级市行政区划码, 精确)
            2. adcode(区县级码, 截前4位+"00" 转市级码)
            3. cityName(城市名匹配市店表)
            4. longitude+latitude(附近 loc_stores 门店推断城市, 限 radius_km 内)
            5. memberId 默认收货地址的 city/adcode
            6. 全部缺失 → 本站入口(未获取到位置)

        Args:
            city_code: 地级市行政区划码(如 "110100")
            adcode: 区县级行政区划码(如 "110105" → 自动转 "110100")
            city_name: 城市名(如 "北京市")
            province_name: 省份名(配合城市名兜底)
            longitude/latitude: 地图定位经纬度
            member_id: 会员ID(取默认收货地址兜底)
            nearby_radius_km: 经纬度模式附近门店搜索半径(km)

        Returns:
            {
                entry: "citystore" | "site",
                reason: 决策原因,
                city: {...} | null,
                store: {...} | null,   # entry=citystore 时市店详情(含折扣)
                orderEntry: {type, url, params},
                nearbyStores: [...],   # 经纬度模式附带
            }
        """
        nearby_stores = []

        logger.info(
            "[下单入口决策] 收到请求: cityCode=%r, adcode=%r, cityName=%r, "
            "provinceName=%r, longitude=%r, latitude=%r, memberId=%r, radiusKm=%s",
            city_code, adcode, city_name, province_name,
            longitude, latitude, member_id, nearby_radius_km)

        # ---------- 1. 城市判定(按优先级) ----------
        # 输入归一化(strip + 空串视为未提供, 抵御前端脏数据)
        city_code = (str(city_code).strip() or None) if city_code else None
        adcode = (str(adcode).strip() or None) if adcode else None
        city_name = (str(city_name).strip() or None) if city_name else None
        province_name = (str(province_name).strip() or None) if province_name else None

        logger.info(
            "[下单入口决策] 归一化后有效输入: cityCode=%r, adcode=%r, cityName=%r, "
            "provinceName=%r", city_code, adcode, city_name, province_name)

        resolved = None       # (city_code, city_name, province_name, source)
        if city_code:
            resolved = (city_code, city_name or "", province_name or "", "cityCode")
            logger.info("[下单入口决策] 城市判定走 cityCode 路径: %s", city_code)
        elif adcode:
            # 区县级码: 先精确命中区县店, miss 后转市级码回退存量市店
            district_store = await self.repo.get_by_city(adcode)
            if district_store:
                resolved = (adcode,
                            district_store.get("cityName", ""),
                            district_store.get("provinceName", ""), "adcode")
                logger.info(
                    "[下单入口决策] 城市判定走 adcode 路径(区县店精确命中): %s",
                    adcode)
            else:
                city_code_converted = self._adcode_to_city_code(adcode)
                resolved = (city_code_converted, "", "", "adcode")
                logger.info(
                    "[下单入口决策] 城市判定走 adcode 路径: %s → 市级码 %s",
                    adcode, city_code_converted)
        elif city_name:
            resolved = ("", city_name, province_name or "", "cityName")
            logger.info("[下单入口决策] 城市判定走 cityName 路径: %r (省份=%r)",
                        city_name, province_name)

        # 经纬度: 查附近门店推断城市(附带 nearbyStores 返回)
        if resolved is None and longitude is not None and latitude is not None:
            from repositories.location_repository import LocationRepository
            loc_repo = LocationRepository()
            nearby_stores = await loc_repo.list_nearby_stores(
                longitude, latitude, radius_km=nearby_radius_km, limit=10)
            if nearby_stores:
                nearest = nearby_stores[0]
                resolved = ("", nearest.get("city", ""), "", "location")
                logger.info(
                    "[下单入口决策] 城市判定走经纬度路径: (%s, %s) 半径%skm 内"
                    "找到 %d 家门店, 最近=%s(%.2fkm), 推断城市=%r",
                    longitude, latitude, nearby_radius_km, len(nearby_stores),
                    nearest.get("storeName", ""), nearest.get("distance", -1.0),
                    nearest.get("city", ""))
            else:
                logger.info(
                    "[下单入口决策] 经纬度路径: (%s, %s) 半径 %skm 内无门店, "
                    "无法推断城市", longitude, latitude, nearby_radius_km)
        elif resolved is None and (longitude is None) != (latitude is None):
            logger.warning(
                "[下单入口决策] 经纬度半缺(longitude=%r, latitude=%r), "
                "跳过定位路径", longitude, latitude)

        # 会员默认收货地址兜底
        if resolved is None and member_id is not None:
            resolved = await self._resolve_city_from_default_address(member_id)
            if resolved:
                logger.info(
                    "[下单入口决策] 城市判定走会员默认地址兜底: memberId=%s → "
                    "cityCode=%r, cityName=%r", member_id, resolved[0], resolved[1])
            else:
                logger.info(
                    "[下单入口决策] 会员默认地址兜底失败: memberId=%s 无可用地址"
                    "(无地址或地址缺 adcode/city)", member_id)

        # ---------- 2. 无城市信息 → 本站入口 ----------
        if resolved is None:
            logger.info(
                "[下单入口决策] 所有城市判定路径均未命中 → 本站入口"
                "(原因: 未获取到位置信息)")
            return self._entry_site(reason="未获取到位置信息, 已为你展示本站下单入口")

        r_code, r_name, r_province, source = resolved
        logger.info(
            "[下单入口决策] 城市判定完成: cityCode=%r, cityName=%r, "
            "provinceName=%r, 来源=%s", r_code, r_name, r_province, source)

        # ---------- 3. 匹配市级网店 ----------
        store = None
        if r_code:
            store = await self.repo.get_by_city(r_code)
            logger.info(
                "[下单入口决策] 按市级码 %s 匹配市店: %s",
                r_code, store.get("storeName", "") if store else "未匹配")
        if store is None and r_name:
            # 城市名匹配(规范化去"市"后缀比对)
            all_stores = await self.repo.list_stores(limit=500)
            store = self._match_store_by_name(all_stores, r_name, r_province)
            logger.info(
                "[下单入口决策] 按城市名 %r 匹配市店: %s (共检索 %d 家市店)",
                r_name, store.get("storeName", "") if store else "未匹配",
                len(all_stores))

        # ---------- 4. 决策 ----------
        if store is None:
            city_info = self._city_info(r_code, r_name, r_province,
                                        next((s for s in nearby_stores), None))
            city_label = (city_info or {}).get("cityName") or r_code
            logger.info(
                "[下单入口决策] 城市 %r (来源=%s) 无市级网店 → 本站入口",
                city_label, source)
            result = self._entry_site(
                reason=f"所在城市{city_label}暂无市级网店, 已为你展示本站下单入口")
            result["city"] = city_info
            result["citySource"] = source
            return result

        store_detail = dict(store)
        store_detail["statusName"] = STORE_STATUS_NAMES.get(store["status"], "")
        city_info = {
            "cityCode": store.get("cityCode", ""),
            "cityName": store.get("cityName", ""),
            "provinceCode": store.get("provinceCode", ""),
            "provinceName": store.get("provinceName", ""),
        }

        if store["status"] in self.ORDERABLE_STORE_STATUSES:
            logger.info(
                "[下单入口决策] 命中市店「%s」(storeCode=%s, 状态=%s, 折扣=%s) "
                "→ 市级网店下单入口 (来源=%s)",
                store.get("storeName", ""), store.get("storeCode", ""),
                store_detail["statusName"], store.get("currentDiscount"), source)
            return {
                "entry": "citystore",
                "reason": f"所在城市有市级网店「{store.get('storeName', '')}」"
                          f"({store_detail['statusName']}), 已为你展示市级网店下单入口",
                "city": city_info,
                "citySource": source,
                "store": store_detail,
                "orderEntry": {
                    "type": "citystore",
                    "url": "/api/citystore/order",
                    "params": {"storeCode": store.get("storeCode", "")},
                    "storeCode": store.get("storeCode", ""),
                    "currentDiscount": store.get("currentDiscount"),
                },
                "nearbyStores": nearby_stores,
            }

        # 有市店但不可下单(待审核/暂停/已取消)
        logger.info(
            "[下单入口决策] 命中市店「%s」(storeCode=%s) 但状态为「%s」不可下单 "
            "→ 本站入口 (来源=%s)",
            store.get("storeName", ""), store.get("storeCode", ""),
            store_detail["statusName"], source)
        return {
            "entry": "site",
            "reason": f"所在城市市级网店「{store.get('storeName', '')}」"
                      f"当前状态为「{store_detail['statusName']}」, "
                      "暂不可下单, 已为你展示本站下单入口",
            "city": city_info,
            "citySource": source,
            "store": store_detail,
            "orderEntry": {
                "type": "site",
                "url": "/api/order/create",
                "params": {},
            },
            "nearbyStores": nearby_stores,
        }

    @staticmethod
    def _adcode_to_city_code(adcode: str) -> str:
        """区县级码转地级市码: 前4位+"00"(110105 → 110100)"""
        adcode = adcode.strip()
        if len(adcode) < 4 or not adcode[:4].isdigit():
            return adcode
        return adcode[:4] + "00"

    async def _resolve_city_from_default_address(self, member_id: int):
        """取会员默认收货地址解析城市(无默认取最新一条)"""
        from repositories.location_repository import LocationRepository
        loc_repo = LocationRepository()
        addresses = await loc_repo.list_addresses(member_id)
        if not addresses:
            return None
        address = next((a for a in addresses if a.get("isDefault")), addresses[-1])
        adcode = address.get("adcode")
        if adcode:
            return (self._adcode_to_city_code(str(adcode)), "", "", "defaultAddress")
        if address.get("city"):
            return ("", str(address["city"]), address.get("province", ""),
                    "defaultAddress")
        return None

    @staticmethod
    def _match_store_by_name(stores: list[dict], city_name: str,
                             province_name: str = None) -> dict | None:
        """按城市/区县名匹配网店(去"市"后缀宽松比对; 省份一致优先)

        区县店优先比对 districtName(前端定位可能传区县名),
        再比对 cityName(市级口径)。
        """
        def normalize(name: str) -> str:
            return (name or "").strip().rstrip("市")

        target = normalize(city_name)
        if not target:
            return None
        candidates = []
        for s in stores:
            if (normalize(s.get("districtName", "")) == target
                    or normalize(s.get("cityName", "")) == target):
                if province_name and normalize(s.get("provinceName", "")) == \
                        normalize(province_name):
                    return s  # 省市都一致, 直接命中
                candidates.append(s)
        return candidates[0] if candidates else None

    @staticmethod
    def _entry_site(reason: str) -> dict:
        """本站下单入口"""
        return {
            "entry": "site",
            "reason": reason,
            "city": None,
            "store": None,
            "orderEntry": {
                "type": "site",
                "url": "/api/order/create",
                "params": {},
            },
            "nearbyStores": [],
        }

    @staticmethod
    def _city_info(city_code: str, city_name: str, province_name: str,
                   nearby_store: dict = None) -> dict | None:
        """构造城市信息(取值自参数或附近门店)"""
        if not any((city_code, city_name)):
            return None
        return {
            "cityCode": city_code or "",
            "cityName": city_name or (nearby_store or {}).get("city", ""),
            "provinceCode": "",
            "provinceName": province_name or (nearby_store or {}).get("province", ""),
        }

    # ============================================================
    # 确认开业(轻审核: 取代原营业执照/食品卫生许可证材料审核)
    # ============================================================

    async def audit_store(self, store_code: str, auditor: str,
                           approved: bool, remark: str = "") -> dict:
        """平台确认开业/驳回(待审核 → 运营中/已取消)

        轻审核语义: 防恶意注册的一键确认(非材料审核)。
            - approved=True: 确认开业 → 运营中, 保证金锁定期自开业日起算
            - approved=False: 驳回 → 已取消, 保证金全额退还(rejected)

        Args:
            store_code: 网店编号
            auditor: 操作人
            approved: True 确认开业 / False 驳回
            remark: 备注

        Returns:
            网店详情

        Raises:
            KeyError: 网店不存在
            ValueError: 状态非待审核
        """
        async with get_lock(f"citystore:status:{store_code}"):
            store = await self.repo.get_store(store_code)
            if store is None:
                raise KeyError(f"网店不存在: {store_code}")

            if store["status"] != STORE_STATUS_PENDING:
                raise ValueError(
                    f"网店状态非法, 当前 {STORE_STATUS_NAMES.get(store['status'], '')}, 仅待确认网店可操作"
                )

            # 确定新状态
            new_status = STORE_STATUS_OPERATING if approved else STORE_STATUS_CANCELLED
            now = ts()
            today = now[:10]  # YYYY-MM-DD

            store["status"] = new_status
            store["updatedAt"] = now
            if approved:
                store["openDate"] = today
            else:
                store["closeDate"] = today
            await self.repo.save_store(store)

            # 保证金联动: 确认开业补锁定期起止 / 驳回全额退还
            margin = await self.repo.get_margin_by_store(store_code)
            if margin and margin.get("status") == MARGIN_STATUS_LOCKED:
                if approved:
                    margin["startDate"] = today
                    margin["endDate"] = (
                        date.fromisoformat(today)
                        + timedelta(days=MARGIN_TERM_DAYS)).isoformat()
                    margin["updatedAt"] = now
                    await self.repo.save_margin(margin)
                else:
                    await self.settle_margin(store_code, "rejected")

            return await self.get_store_detail(store_code)

    # ============================================================
    # 状态流转
    # ============================================================

    async def update_status(self, store_code: str, new_status: int,
                              operator: str = "") -> dict:
        """更新网店状态(含状态机校验)

        Args:
            store_code: 网店编号
            new_status: 新状态
            operator: 操作人

        Returns:
            更新后的网店详情

        Raises:
            KeyError: 网店不存在
            ValueError: 状态流转非法
        """
        async with get_lock(f"citystore:status:{store_code}"):
            store = await self.repo.get_store(store_code)
            if store is None:
                raise KeyError(f"网店不存在: {store_code}")

            current = store["status"]
            self._validate_status_transition(current, new_status)

            now = ts()
            today = now[:10]
            store["status"] = new_status
            store["updatedAt"] = now
            if new_status == STORE_STATUS_CANCELLED:
                store["closeDate"] = today
            await self.repo.save_store(store)

            # 取消时联动保证金中途结算(按已运营期折算目标)
            if new_status == STORE_STATUS_CANCELLED:
                await self.settle_margin(store_code, "cancelled")

            return await self.get_store_detail(store_code)

    def _validate_status_transition(self, current: int, new_status: int) -> None:
        """状态机校验"""
        if current not in STORE_STATUS_FLOW:
            raise ValueError(f"未知状态: {current}")

        allowed = STORE_STATUS_FLOW[current]
        if new_status not in allowed:
            if not allowed:
                raise ValueError(
                    f"当前状态 {STORE_STATUS_NAMES.get(current, current)} 为终态, 不可变更"
                )
            allowed_names = "、".join(
                STORE_STATUS_NAMES.get(s, str(s)) for s in allowed
            )
            raise ValueError(
                f"状态流转非法: {STORE_STATUS_NAMES.get(current, current)} 不可直接流转到 "
                f"{STORE_STATUS_NAMES.get(new_status, new_status)}, 允许: {allowed_names}"
            )

    # ============================================================
    # 保证金(预存钱包 1000 元一年期, 不可提前取现)
    # ============================================================

    async def get_margin(self, store_code: str) -> dict:
        """查询网店保证金(含年任务进度)

        Raises:
            KeyError: 保证金不存在
        """
        margin = await self.repo.get_margin_by_store(store_code)
        if margin is None:
            raise KeyError(f"保证金记录不存在: 网店 {store_code}")
        result = dict(margin)
        # 实时年任务进度(运营中)
        if result.get("status") == MARGIN_STATUS_LOCKED and result.get("startDate"):
            purchased = await self.repo.sum_purchase_between(
                store_code, result["startDate"], result.get("endDate") or "9999-12-31")
            result["annualPurchasedLive"] = round(purchased, 2)
            target = float(result.get("annualTarget") or ANNUAL_PURCHASE_TARGET)
            result["completionRateLive"] = round(
                min(1.0, purchased / target) if target > 0 else 1.0, 4)
        return result

    async def settle_margin(self, store_code: str, reason: str) -> dict | None:
        """结算保证金(幂等——非 locked 直接返回当前记录)

        结算口径:
            - rejected(平台驳回, 未开业): 全额退还
            - expired(满一年): 按年任务完成度退还
              rate = min(1, 年进货额 / 50000), refund = round(1000 × rate, 2)
            - cancelled(中途取消资格): 按已运营期折算目标退还
              rate = min(1, 年进货额 / (50000 × 已运营天数 / 365))

        资金路径: wallet:{userId} 锁内 add_balance(+refund) + 流水
        (citystore_margin_settle, IN——WALLET_MODE 豁免面外, 资金退出红线)

        Args:
            store_code: 网店编号
            reason: expired | cancelled | rejected

        Raises:
            ValueError: reason 非法
        """
        if reason not in MARGIN_SETTLE_REASONS:
            raise ValueError(f"保证金结算原因非法: {reason}")

        margin = await self.repo.get_margin_by_store(store_code)
        if margin is None:
            return None
        margin_no = margin["marginNo"]

        # 锁内双重检查(防调度器与手动结算并发双重退款)
        async with get_lock(f"citystore:margin:{margin_no}"):
            margin = await self.repo.get_margin(margin_no)
            if margin is None or margin.get("status") != MARGIN_STATUS_LOCKED:
                return margin

            amount = float(margin.get("amount") or MARGIN_AMOUNT)
            target = float(margin.get("annualTarget") or ANNUAL_PURCHASE_TARGET)
            user_id = margin.get("userId")
            today = ts()[:10]

            start = margin.get("startDate") or ""
            if reason == "rejected" or not start:
                # 未开业(驳回/异常未开业取消): 全额退还
                rate = 1.0
                annual_purchased = 0.0
            else:
                end = margin.get("endDate") or today
                annual_purchased = await self.repo.sum_purchase_between(
                    store_code, start, end)
                if reason == "expired":
                    # 满一年: 按年任务完成度
                    rate = (min(1.0, annual_purchased / target)
                            if target > 0 else 1.0)
                else:
                    # 中途取消: 按已运营期折算目标
                    end_d = date.fromisoformat(min(today, end))
                    elapsed = max((end_d - date.fromisoformat(start)).days, 0)
                    prorated = target * elapsed / 365
                    rate = (min(1.0, annual_purchased / prorated)
                            if prorated > 0 else 1.0)

            refund = round(amount * rate, 2)
            deducted = round(amount - refund, 2)
            now = ts()

            # 资金退还(钱包锁内; refund=0 时不产生流水——全额扣除)
            if refund > 0:
                async with get_lock(f"wallet:{user_id}"):
                    new_balance = await self.wallet_repo.add_balance(
                        user_id, refund)
                    await self.wallet_repo.save_transaction({
                        "txNo": await self.wallet_repo.next_tx_no(),
                        "userId": user_id,
                        "type": "citystore_margin_settle",
                        "direction": "IN",
                        "amount": refund,
                        "balanceAfter": new_balance,
                        "payChannel": "",
                        "orderId": "",
                        "depositNo": "",
                        "withdrawNo": "",
                        "status": "success",
                        "description": (
                            "县（区）网店保证金驳回全额退还 ¥"
                            f"{refund:.2f}" if reason == "rejected" else
                            f"县（区）网店保证金结算退还 ¥{refund:.2f}"
                            f"(年任务完成率 {rate * 100:.1f}%, 扣除 ¥{deducted:.2f})"),
                        "createdAt": now,
                    })

            margin.update({
                "status": MARGIN_STATUS_SETTLED,
                "settledAt": now,
                "refundAmount": refund,
                "deductedAmount": deducted,
                "completionRate": round(rate, 4),
                "annualPurchased": round(annual_purchased, 2),
                "settleReason": reason,
                "updatedAt": now,
            })
            await self.repo.save_margin(margin)
            logger.info(
                "[保证金结算] %s reason=%s 退还 ¥%.2f 扣除 ¥%.2f "
                "完成率 %.4f 年进货 ¥%.2f",
                margin_no, reason, refund, deducted, rate, annual_purchased)
            return margin

    async def run_margin_settlement(self) -> dict:
        """到期保证金批量结算(调度器小时级轮询, 幂等)

        扫描 status=locked 且 endDate 非空且 ≤ today 的保证金逐个结算。
        """
        from core.helpers import ts as _ts
        today = _ts()[:10]
        due = await self.repo.list_due_margins(today)
        settled, skipped = [], []
        for m in due:
            result = await self.settle_margin(m["storeCode"], "expired")
            if result and result.get("status") == MARGIN_STATUS_SETTLED:
                settled.append(result["marginNo"])
            else:
                skipped.append(m["marginNo"])
        return {"due": len(due), "settled": settled, "skipped": skipped}

    # ============================================================
    # 区县列表(县区网店三级联动数据源)
    # ============================================================

    async def list_available_districts(self, city_code: str = None) -> dict:
        """查询可开网店区县(未被独占; 可按市筛选)

        Args:
            city_code: 市码(可选——为空返回全量, 前端三级联动一次性预载)

        Raises:
            ValueError: 市码无效
        """
        from services import citystore_districts
        if city_code:
            all_districts = citystore_districts.districts_by_city(city_code)
            if not all_districts:
                raise ValueError(
                    f"市码无效或该市无区县数据: {city_code}"
                    "(港澳台暂不开放区县级网店)")
        else:
            all_districts = citystore_districts.all_districts()
        occupied = set(await self.repo.list_occupied_cities())
        available = [d for d in all_districts
                     if d["districtCode"] not in occupied]
        # 上级市店拦截标记(该市被存量市级网店覆盖时前端提示)
        blocked_cities = set()
        for d in all_districts:
            if d["cityCode"] in occupied and d["districtCode"] not in occupied:
                blocked_cities.add(d["cityCode"])
        return {
            "districts": available,
            "count": len(available),
            "totalCount": len(all_districts),
            "blockedCityCount": len(blocked_cities),
        }

    # ============================================================
    # 月度考核
    # ============================================================

    async def run_assessment(self, store_code: str, month: str) -> dict:
        """执行月度考核(进货/销售达标 + 连续不达标 + 折扣调整)

        Args:
            store_code: 网店编号
            month: 考核月份(YYYY-MM)

        Returns:
            考核结果

        Raises:
            KeyError: 网店不存在
            ValueError: 重复考核
        """
        async with get_lock(f"citystore:assessment:{store_code}:{month}"):
            store = await self.repo.get_store(store_code)
            if store is None:
                raise KeyError(f"网店不存在: {store_code}")

            # 检查是否已考核
            existing = await self.repo.get_assessment(store_code, month)
            if existing:
                raise ValueError(f"网店 {store_code} 在 {month} 已完成考核")

            # 统计月度数据
            monthly_purchase = await self.repo.sum_monthly_purchase(store_code, month)
            monthly_sales = await self.repo.sum_monthly_sales(store_code, month)

            # AI 决策门(全站批次四——25号市级网店 AI 升级)
            # citystore_health 考核门: observe 模式仅评分快照
            # (双达标硬规则不变——行为 100% 兼容); enforce 模式
            # high 拦截异常考核。assessment:{store}:{month}
            # 贯穿快照与考核终态——回流配对键一致。
            try:
                from services.ai_enforcement_governance import (
                    enrich_citystore_health,
                    enforce_citystore_assessment,
                )
                ctx = await enrich_citystore_health(
                    store, monthly_purchase,
                    monthly_sales)
                await enforce_citystore_assessment(
                    store_code, month, ctx)
            except ValueError:
                raise  # AI 拦截(enforce 模式)
            except Exception:
                pass  # fail-open: 富化异常不阻塞考核

            # 达标判定
            purchase_qualified = 1 if monthly_purchase >= PURCHASE_TARGET else 0
            sales_qualified = 1 if monthly_sales >= SALES_TARGET else 0

            # 计算次月折扣
            next_month_discount = calc_discount(monthly_sales)

            # 连续不达标月数(基于上月数据累加)
            prev_consecutive_purchase = store.get("consecutiveBelowPurchase", 0)
            prev_consecutive_sales = store.get("consecutiveBelowSales", 0)
            new_consecutive_purchase = prev_consecutive_purchase + 1 if not purchase_qualified else 0
            new_consecutive_sales = prev_consecutive_sales + 1 if not sales_qualified else 0

            # 资格状态判定
            max_consecutive = max(new_consecutive_purchase, new_consecutive_sales)
            if max_consecutive >= MAX_CONSECUTIVE_BELOW:
                qualification_status = QUAL_STATUS_CANCELLED
            elif max_consecutive >= 2:
                qualification_status = QUAL_STATUS_YELLOW_CARD
            elif max_consecutive >= 1:
                qualification_status = QUAL_STATUS_WARNING
            else:
                qualification_status = QUAL_STATUS_NORMAL

            # 当前月折扣(基于上月销售额)
            current_month_discount = store.get("currentDiscount", DISCOUNT_UNQUALIFIED)

            now = ts()
            assessment = {
                "storeCode": store_code,
                "assessmentMonth": month,
                "monthlyPurchaseAmount": round(monthly_purchase, 2),
                "purchaseTarget": PURCHASE_TARGET,
                "purchaseQualified": purchase_qualified,
                "monthlySalesAmount": round(monthly_sales, 2),
                "salesTarget": SALES_TARGET,
                "salesQualified": sales_qualified,
                "priceViolationCount": 0,
                "regionViolationCount": 0,
                "currentMonthDiscount": current_month_discount,
                "nextMonthDiscount": next_month_discount,
                "consecutiveBelowPurchase": new_consecutive_purchase,
                "consecutiveBelowSales": new_consecutive_sales,
                "qualificationStatus": qualification_status,
                "assessedAt": now,
                "createdAt": now,
            }
            await self.repo.save_assessment(assessment)

            # 更新网店状态
            store["currentDiscount"] = next_month_discount
            store["consecutiveBelowPurchase"] = new_consecutive_purchase
            store["consecutiveBelowSales"] = new_consecutive_sales
            store["updatedAt"] = now

            # 连续3月不达标 → 自动取消资格
            if qualification_status == QUAL_STATUS_CANCELLED:
                store["status"] = STORE_STATUS_CANCELLED
                store["closeDate"] = now[:10]
            # 连续2月不达标 → 暂停
            elif qualification_status == QUAL_STATUS_YELLOW_CARD:
                if store["status"] == STORE_STATUS_OPERATING:
                    store["status"] = STORE_STATUS_SUSPENDED
            # 连续1月不达标 → 预警
            elif qualification_status == QUAL_STATUS_WARNING:
                if store["status"] == STORE_STATUS_OPERATING:
                    store["status"] = STORE_STATUS_WARNING

            await self.repo.save_store(store)

            # 取消资格时联动保证金中途结算(按已运营期折算目标)
            if qualification_status == QUAL_STATUS_CANCELLED:
                await self.settle_margin(store_code, "cancelled")

            # 回流钩子(25号网店健康决策门——考核终态自动反馈)
            try:
                from services.ai_feedback_hooks import (
                    on_store_assessed,
                )
                await on_store_assessed(
                    store_code, month,
                    qualification_status)
            except Exception:
                pass

            # 返回考核结果(含状态名称)
            result = dict(assessment)
            result["qualificationStatusName"] = QUAL_STATUS_NAMES.get(qualification_status, "")
            result["storeStatus"] = store["status"]
            result["storeStatusName"] = STORE_STATUS_NAMES.get(store["status"], "")
            return result

    async def get_assessment(self, store_code: str, month: str) -> dict:
        """查询月度考核结果"""
        assessment = await self.repo.get_assessment(store_code, month)
        if assessment is None:
            raise KeyError(f"考核记录不存在: 网店 {store_code}, 月份 {month}")

        result = dict(assessment)
        result["qualificationStatusName"] = QUAL_STATUS_NAMES.get(
            assessment.get("qualificationStatus", 0), ""
        )
        return result

    async def list_assessments(self, store_code: str) -> dict:
        """查询网店所有考核记录"""
        assessments = await self.repo.list_assessments(store_code)
        for a in assessments:
            a["qualificationStatusName"] = QUAL_STATUS_NAMES.get(a.get("qualificationStatus", 0), "")
        return {
            "assessments": assessments,
            "count": len(assessments),
        }

    # ============================================================
    # 网店订单关联
    # ============================================================

    async def add_order(self, store_code: str, order_no: str,
                        product_id: str, product_name: str, quantity: int,
                        retail_price: float, total_amount: float,
                        customer_phone: str = "", delivery_city_code: str = "",
                        sales_channel: int = CHANNEL_MINIPROGRAM) -> dict:
        """关联订单到网店(用于销售额统计)

        Args:
            store_code: 网店编号
            order_no: 订单号
            product_id: 商品ID
            product_name: 商品名称
            quantity: 数量
            retail_price: 零售单价
            total_amount: 订单总金额
            customer_phone: 消费者手机(脱敏)
            delivery_city_code: 收货城市码
            sales_channel: 销售渠道

        Returns:
            订单关联记录

        Raises:
            KeyError: 网店不存在
            ValueError: 网店非运营状态
        """
        store = await self.repo.get_store(store_code)
        if store is None:
            raise KeyError(f"网店不存在: {store_code}")

        if store["status"] != STORE_STATUS_OPERATING:
            raise ValueError(
                f"网店状态非运营中, 当前 {STORE_STATUS_NAMES.get(store['status'], '')}, 不可关联订单"
            )

        order = {
            "storeCode": store_code,
            "orderNo": order_no,
            "customerPhone": customer_phone,
            "deliveryCityCode": delivery_city_code,
            "productId": product_id,
            "productName": product_name,
            "quantity": quantity,
            "retailPrice": retail_price,
            "totalAmount": total_amount,
            "salesChannel": sales_channel,
            "createdAt": ts(),
        }
        await self.repo.add_order(order)
        return order

    async def list_orders(self, store_code: str, month: str = None) -> dict:
        """查询网店订单"""
        orders = await self.repo.list_orders(store_code, month)
        return {
            "orders": orders,
            "count": len(orders),
            "totalAmount": round(sum(float(o.get("totalAmount", 0)) for o in orders), 2),
        }

    # ============================================================
    # 管理端查询
    # ============================================================

    async def list_pending_stores(self, limit: int = 50) -> dict:
        """待审核网店列表(管理端)"""
        stores = await self.repo.list_stores(status=STORE_STATUS_PENDING, limit=limit)
        for s in stores:
            s["statusName"] = STORE_STATUS_NAMES.get(s.get("status", ""), "")
        return {
            "stores": stores,
            "count": len(stores),
        }

    async def get_stats(self) -> dict:
        """网店统计(管理端)"""
        all_stores = await self.repo.list_stores(limit=10000)
        total = len(all_stores)
        pending = sum(1 for s in all_stores if s.get("status") == STORE_STATUS_PENDING)
        operating = sum(1 for s in all_stores if s.get("status") == STORE_STATUS_OPERATING)
        warning = sum(1 for s in all_stores if s.get("status") == STORE_STATUS_WARNING)
        suspended = sum(1 for s in all_stores if s.get("status") == STORE_STATUS_SUSPENDED)
        cancelled = sum(1 for s in all_stores if s.get("status") == STORE_STATUS_CANCELLED)
        occupied_cities = sum(1 for s in all_stores if s.get("status") != STORE_STATUS_CANCELLED)
        return {
            "totalStores": total,
            "pendingStores": pending,
            "operatingStores": operating,
            "warningStores": warning,
            "suspendedStores": suspended,
            "cancelledStores": cancelled,
            "occupiedCities": occupied_cities,
        }
