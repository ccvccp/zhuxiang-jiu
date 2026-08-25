"""市级网店模块业务逻辑层

核心业务:
    - 开店申请(SVIP 资格校验 + 城市独占校验 + 资质校验)
    - 审核流程(待审核 → 运营中/已取消)
    - 月度考核(进货/销售达标 + 连续不达标 + 折扣调整)
    - 状态流转(运营 → 预警/暂停 → 取消)
    - 订单关联(销售额统计)

锁保护:
    - 申请: lock:citystore:apply:{memberId}  (防重复申请)
    - 城市独占: lock:citystore:city:{cityCode}  (防并发申请同一城市)
    - 考核: lock:citystore:assessment:{storeCode}:{month}  (防重复考核)
    - 状态流转: lock:citystore:status:{storeCode}  (防并发状态变更)

异常约定:
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 资格不符/城市被占/状态非法等)
"""

import logging

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
    # 阶梯折扣
    DISCOUNT_UNQUALIFIED,
    PURCHASE_TARGET, SALES_TARGET, MAX_CONSECUTIVE_BELOW,
    # 销售渠道
    CHANNEL_MINIPROGRAM, calc_discount,
)


logger = logging.getLogger(__name__)


class CityStoreService:
    """市级网店业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: CityStoreRepository = CityStoreRepository()):
        self.repo = repo

    # ============================================================
    # 开店申请
    # ============================================================

    async def apply(self, member_id: int, member_level: int,
                    store_name: str, city_code: str, city_name: str,
                    province_code: str, province_name: str,
                    business_license: str = "", food_license: str = "",
                    tax_reg_no: str = "") -> dict:
        """申请开店(含 SVIP 资格校验 + 城市独占校验 + 资质校验)

        Args:
            member_id: 会员ID
            member_level: 会员等级(必须为 5 = SVIP)
            store_name: 网店名称
            city_code: 地级市行政区划码
            city_name: 城市名称
            province_code: 省份码
            province_name: 省份名称
            business_license: 营业执照号
            food_license: 食品经营许可证号
            tax_reg_no: 税务登记号

        Returns:
            网店详情(含 storeCode)

        Raises:
            ValueError: 资格不符/城市被占/重复申请/资质缺失
        """
        # 1. 资格校验: 仅 SVIP(L5) 可申请
        if member_level != 5:
            raise ValueError("市级网店为 SVIP 专属权益, 请先开通 SVIP 会员")

        # 2. 资质校验
        if not business_license:
            raise ValueError("营业执照号必填")
        if not food_license:
            raise ValueError("食品经营许可证号必填")

        # 3. 防重复申请(同一会员有非取消状态的网店)
        async with get_lock(f"citystore:apply:{member_id}"):
            existing = await self.repo.get_by_member(member_id)
            if existing:
                raise ValueError("您已有一家网店, 不可重复开店")

            # 4. 城市独占校验(一城一店)
            async with get_lock(f"citystore:city:{city_code}"):
                city_store = await self.repo.get_by_city(city_code)
                if city_store:
                    raise ValueError(f"城市 {city_name} 已有网店, 不可重复开店")

                # 5. 生成网店编号
                store_code = await self.repo.next_store_code(city_code)
                now = ts()
                store = {
                    "storeCode": store_code,
                    "storeName": store_name,
                    "memberId": member_id,
                    "cityCode": city_code,
                    "cityName": city_name,
                    "provinceCode": province_code,
                    "provinceName": province_name,
                    "businessLicense": business_license,
                    "foodLicense": food_license,
                    "taxRegNo": tax_reg_no,
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

    async def list_available_cities(self) -> dict:
        """查询可用城市列表(未被独占的城市)"""
        # 预定义城市列表(实际应从位置地图模块获取)
        all_cities = self._get_predefined_cities()
        occupied = set(await self.repo.list_occupied_cities())
        available = [c for c in all_cities if c["cityCode"] not in occupied]
        return {
            "cities": available,
            "count": len(available),
            "occupiedCount": len(occupied),
        }

    def _get_predefined_cities(self) -> list[dict]:
        """预定义城市列表(简化版, 实际应从位置地图模块获取)"""
        return [
            {"cityCode": "370100", "cityName": "济南市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370200", "cityName": "青岛市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370300", "cityName": "淄博市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370400", "cityName": "枣庄市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370500", "cityName": "东营市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370600", "cityName": "烟台市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370700", "cityName": "潍坊市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370800", "cityName": "济宁市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "370900", "cityName": "泰安市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "371000", "cityName": "威海市", "provinceCode": "370000", "provinceName": "山东省"},
            {"cityCode": "110100", "cityName": "北京市", "provinceCode": "110000", "provinceName": "北京市"},
            {"cityCode": "310100", "cityName": "上海市", "provinceCode": "310000", "provinceName": "上海市"},
            {"cityCode": "440100", "cityName": "广州市", "provinceCode": "440000", "provinceName": "广东省"},
            {"cityCode": "440300", "cityName": "深圳市", "provinceCode": "440000", "provinceName": "广东省"},
            {"cityCode": "330100", "cityName": "杭州市", "provinceCode": "330000", "provinceName": "浙江省"},
        ]

    # ============================================================
    # 下单入口决策(市级网店优先原则)
    # ============================================================

    # 可下单的市店状态(运营中/预警: 预警仅考核警示仍在营业)
    ORDERABLE_STORE_STATUSES = {STORE_STATUS_OPERATING, STORE_STATUS_WARNING}

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
            city_code_converted = self._adcode_to_city_code(adcode)
            resolved = (city_code_converted, "", "", "adcode")
            logger.info("[下单入口决策] 城市判定走 adcode 路径: %s → 市级码 %s",
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
        """按城市名匹配市店(去"市"后缀宽松比对; 省份一致优先)"""
        def normalize(name: str) -> str:
            return (name or "").strip().rstrip("市")

        target = normalize(city_name)
        if not target:
            return None
        candidates = []
        for s in stores:
            if normalize(s.get("cityName", "")) == target:
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
    # 审核流程
    # ============================================================

    async def audit_store(self, store_code: str, auditor: str,
                           approved: bool, remark: str = "") -> dict:
        """审核开店申请(待审核 → 运营中/已取消)

        Args:
            store_code: 网店编号
            auditor: 审核人
            approved: 是否通过(True 通过/False 驳回)
            remark: 审核备注

        Returns:
            审核后的网店详情

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
                    f"网店状态非法, 当前 {STORE_STATUS_NAMES.get(store['status'], '')}, 仅待审核网店可审核"
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
