"""智酿运通 P4a 智能API调配引擎·统一语义层(zw_semantic_service)

创新规划 §三 P4a: 多源异构运力的"翻译官"与"指挥官"
    - 字段映射注册表: 五物流商 API 字段方言(weight vs gross_weight /
      area_code vs district_id / 状态方言) → 平台标准数据模型
    - 配置化新运力接入: register_carrier 以映射配置注册, 零代码改动
    - 四维订单画像: 品类+包装规格+时效要求+风险等级(确定性推断)
    - 物流知识图谱: 品类→包装→运输条件→渠道能力→区域政策(五维注册表)
    - 特征路由策略表: 优先策略+备选策略+进化反馈信号(确定性映射)

铁律: 全链确定性; 新运力注册为建议书制(上线须人工确认); LLM 禁入。
"""

import logging

from services.zw_fabric_service import _ZwStore, _now_iso

logger = logging.getLogger(__name__)

# ============================================================
# 平台标准数据模型字段(统一语义层的目标 schema)
# ============================================================
PLATFORM_MODEL_FIELDS = (
    "waybillNo",    # 运单号
    "carrier",      # 渠道代码
    "status",       # 平台状态枚(pending/booked/picked/transporting/
                    #           delivering/signed/failed/returned)
    "weight",       # 重量 kg
    "insuredValue", # 保价货值
    "receiverName", # 收件人
    "receiverPhone",# 收件电话
    "receiverArea", # 收件区域(省/市)
    "pickedAt",     # 揽收时间
    "signedAt",     # 签收时间
)

# 内置五渠道字段方言映射(渠道原始字段 → 平台标准字段)
# 实践原型: 顺丰 bill_no/京东 logisticsId/货拉拉 order_no/
#          德邦 gross_weight/圆通 area_code 等(确定性映射)
BUILTIN_CARRIER_FIELD_MAPS = {
    "SF": {
        "bill_no": "waybillNo",          "company": "carrier",
        "state": "status",                "parcel_weight": "weight",
        "declare_value": "insuredValue", "recv_name": "receiverName",
        "recv_mobile": "receiverPhone",  "recv_district": "receiverArea",
        "pick_time": "pickedAt",         "sign_time": "signedAt",
    },
    "JD": {
        "logisticsId": "waybillNo",      "vender_code": "carrier",
        "orderStatus": "status",         "grossWeight": "weight",
        "insureAmount": "insuredValue",  "consignee": "receiverName",
        "consigneeTel": "receiverPhone", "district_id": "receiverArea",
        "pickupTime": "pickedAt",        "completeTime": "signedAt",
    },
    "LLL": {
        "order_no": "waybillNo",          "platform_code": "carrier",
        "order_state": "status",          "cargo_weight": "weight",
        "goods_value": "insuredValue",    "contact_name": "receiverName",
        "contact_phone": "receiverPhone", "area_code": "receiverArea",
        "accept_time": "pickedAt",        "finish_time": "signedAt",
    },
    "DB": {
        "mailNo": "waybillNo",            "channel": "carrier",
        "statusName": "status",           "gross_weight": "weight",
        "insuranceFee": "insuredValue",   "receiveMan": "receiverName",
        "receivePhone": "receiverPhone",  "province_city": "receiverArea",
        "pickUpTime": "pickedAt",         "arriveTime": "signedAt",
    },
    "YT": {
        "waybill_code": "waybillNo",      "branch_code": "carrier",
        "trace_status": "status",         "weight_kg": "weight",
        "baogia_value": "insuredValue",   "to_name": "receiverName",
        "to_mobile": "receiverPhone",     "area_code": "receiverArea",
        "get_time": "pickedAt",           "ok_time": "signedAt",
    },
}

# 状态方言: 渠道状态词 → 平台状态枚(确定性翻译表)
STATUS_DIALECTS = {
    "SF": {"已揽收": "picked", "运输中": "transporting", "派送中": "delivering",
           "已签收": "signed", "投递失败": "failed", "已下单": "booked"},
    "JD": {"PACKED": "booked", "PICKED_UP": "picked", "IN_TRANSIT": "transporting",
           "OUT_DELIVERY": "delivering", "SIGNED": "signed", "FAILED": "failed"},
    "LLL": {"10": "booked", "20": "picked", "30": "transporting",
            "40": "delivering", "50": "signed", "60": "failed"},
    "DB": {"已发货": "booked", "运输中": "transporting", "派送中": "delivering",
           "已签收": "signed", "签收失败": "failed"},
    "YT": {"GOT": "picked", "ARRIVE": "transporting", "SENT": "delivering",
           "SIGNED": "signed", "FAILED": "failed"},
}

# ============================================================
# 物流知识图谱(五维注册表, 确定性)
# 品类 → 包装规格/运输条件; 渠道能力; 区域政策
# ============================================================
WINE_CATEGORY_KNOWLEDGE = {
    "premium_gift": {
        "name": "高端礼盒",
        "packaging": "礼盒+防震内衬+保价",
        "transportConditions": ["fragile", "shockproof", "insured"],
        "categoryExplain": "高货值礼盒装, 须保价+签收验证",
    },
    "classic_retail": {
        "name": "经典系列",
        "packaging": "标准纸箱+气泡柱",
        "transportConditions": ["fragile"],
        "categoryExplain": "常规零售瓶装, 标准防震",
    },
    "bulk_case": {
        "name": "整箱大宗",
        "packaging": "整箱托盘+缠绕膜",
        "transportConditions": ["palletized", "fragile"],
        "categoryExplain": "团购/批量整箱, 托盘化运输",
    },
    "base_liquor": {
        "name": "基酒原料",
        "packaging": "吨桶/罐车",
        "transportConditions": ["hazmat", "qualified_line"],
        "categoryExplain": "散装基酒属液体运输限制, 须资质专线",
    },
}

# 渠道能力矩阵(渠道 → 支持的运输条件标签)
CARRIER_CAPABILITY = {
    "SF": {"name": "顺丰", "conditions": {"fragile", "shockproof", "insured"},
           "note": "高货值保价+特快"},
    "JD": {"name": "京东物流", "conditions": {"fragile", "insured"},
           "note": "备选高价值渠道"},
    "LLL": {"name": "货拉拉", "conditions": {"palletized", "same_city"},
            "note": "同城整车/托盘"},
    "DB": {"name": "德邦", "conditions": {"palletized", "fragile"},
           "note": "大件零担主力"},
    "YT": {"name": "圆通", "conditions": {"remote_cover"},
           "note": "经济覆盖偏远乡镇"},
}

# 区域政策(复用既有 REMOTE_PROVINCES 语义, 确定性)
REGION_POLICY_NOTE = "偏远省份(新疆/西藏/青海/内蒙古/甘肃/宁夏)走经济覆盖渠道"

# 特征路由策略表(订单特征 → 优先/备选/进化反馈信号)
FEATURE_ROUTE_TABLE = {
    "premium_gift": {
        "priority": {"carrier": "SF", "service": "特快+保价+签收验证"},
        "backup": {"carrier": "JD", "service": "高价值备选"},
        "feedbackSignals": ["签收好评率", "破损率"],
    },
    "bulk_case": {
        "priority": {"carrier": "DB", "service": "零担/托盘运输"},
        "backup": {"carrier": "LLL", "service": "区域整车"},
        "feedbackSignals": ["单件物流成本", "准时率"],
    },
    "remote_area": {
        "priority": {"carrier": "YT", "service": "经济快递覆盖乡镇"},
        "backup": {"carrier": "EMS", "service": "邮政兜底(未注册时配置接入)"},
        "feedbackSignals": ["妥投率", "投诉率"],
    },
    "same_city_urgent": {
        "priority": {"carrier": "LLL", "service": "同城车队急送"},
        "backup": {"carrier": "SF", "service": "顺丰同城备选"},
        "feedbackSignals": ["响应时长", "客户满意度"],
    },
    "base_liquor": {
        "priority": {"carrier": "DB", "service": "资质专线(须核验资质)"},
        "backup": {"carrier": "LLL", "service": "签约车队"},
        "feedbackSignals": ["合规检查通过率", "损耗率"],
    },
    "classic_retail": {
        "priority": {"carrier": "SF", "service": "标快(≤5件特快)"},
        "backup": {"carrier": "YT", "service": "经济备选"},
        "feedbackSignals": ["签收率", "时效达标率"],
    },
}


class ZwSemanticService:
    """P4a: 统一语义层 + 四维画像 + 特征路由"""

    def __init__(self, store: _ZwStore = None):
        self.store = store or _ZwStore()

    # ============================================================
    # 统一语义层: 异构回单归一化
    # ============================================================

    async def _field_map_of(self, carrier: str) -> dict:
        """渠道字段映射(内置 + 注册表, 注册优先)"""
        registered = await self.store.list("carrier_registry")
        for r in registered:
            if r.get("carrier") == carrier:
                return r.get("fieldMap", {})
        return BUILTIN_CARRIER_FIELD_MAPS.get(carrier, {})

    async def _status_map_of(self, carrier: str) -> dict:
        registered = await self.store.list("carrier_registry")
        for r in registered:
            if r.get("carrier") == carrier:
                return r.get("statusMap", {})
        return STATUS_DIALECTS.get(carrier, {})

    async def normalize_payload(self, carrier: str, payload: dict) -> dict:
        """异构渠道回单 → 平台标准数据模型(翻译官)

        - 字段映射: 渠道方言 → 平台 schema; 未映射字段列入 unmapped 报告
        - 状态翻译: 渠道状态词 → 平台状态枚
        未知渠道 → ValueError(须先 register_carrier 配置接入)。
        """
        if not isinstance(payload, dict) or not payload:
            raise ValueError("回单载荷不可为空")
        field_map = await self._field_map_of(carrier)
        if not field_map:
            raise ValueError(f"未知渠道({carrier}): 须先经 register-carrier "
                             f"配置字段映射后接入(建议书制)")
        status_map = await self._status_map_of(carrier)

        normalized, unmapped = {}, []
        raw_status = ""
        for raw_key, value in payload.items():
            key = str(raw_key).strip()
            if key in field_map:
                platform_key = field_map[key]
                if platform_key == "status":
                    raw_status = str(value)
                    continue
                normalized[platform_key] = value
            else:
                unmapped.append(key)
        if raw_status:
            normalized["status"] = status_map.get(
                raw_status, status_map.get(str(raw_status), raw_status))
        normalized["carrier"] = normalized.get("carrier") or carrier

        return {
            "carrier": carrier,
            "normalized": normalized,
            "mappedFields": len([k for k in normalized
                                 if k in PLATFORM_MODEL_FIELDS]),
            "unmappedFields": unmapped,
            "note": "统一语义层: 渠道方言→平台标准模型(确定性映射)",
            "normalizedAt": _now_iso(),
        }

    # ============================================================
    # 配置化新运力接入(建议书制)
    # ============================================================

    async def register_carrier(self, carrier: str, carrier_name: str,
                               field_map: dict, status_map: dict,
                               conditions: list = None) -> dict:
        """新运力注册: 映射配置入库, 零代码改动接入

        铁律: 注册即生效于语义层翻译; 上线投产须人工确认(建议书模式)。
        """
        if not carrier or len(carrier) > 10:
            raise ValueError("渠道代码无效(1-10字符)")
        if not carrier_name or len(carrier_name) > 30:
            raise ValueError("渠道名称无效")
        required = {"waybillNo", "status"}
        missing_platform_keys = required - set(field_map.values()) \
            if field_map else required
        if missing_platform_keys:
            raise ValueError(
                f"字段映射缺失平台必需键({sorted(missing_platform_keys)})")
        if carrier in BUILTIN_CARRIER_FIELD_MAPS:
            raise ValueError(f"内置渠道({carrier})不可重复注册")
        registered = await self.store.list("carrier_registry")
        for r in registered:
            if r.get("carrier") == carrier:
                raise ValueError(f"渠道({carrier})已注册(不可重复)")

        record = {
            "registryId": await self.store.next_id("carrier_registry"),
            "carrier": carrier, "carrierName": carrier_name,
            "fieldMap": field_map, "statusMap": status_map,
            "conditions": conditions or [],
            "disposition": "建议书制: 注册即接入语义翻译, "
                           "投产须人工确认",
            "registeredAt": _now_iso(),
        }
        await self.store.save("carrier_registry",
                              record["registryId"], record)
        return record

    async def carriers(self) -> list[dict]:
        """渠道注册表总览(内置+扩展)"""
        rows = [{
            "carrier": c, "carrierName": CARRIER_CAPABILITY.get(
                c, {}).get("name", c),
            "builtin": True,
            "conditions": sorted(CARRIER_CAPABILITY.get(
                c, {}).get("conditions", set())),
            "note": CARRIER_CAPABILITY.get(c, {}).get("note", ""),
        } for c in BUILTIN_CARRIER_FIELD_MAPS]
        for r in await self.store.list("carrier_registry"):
            rows.append({
                "carrier": r["carrier"],
                "carrierName": r["carrierName"],
                "builtin": False,
                "conditions": r.get("conditions", []),
                "note": "配置化接入(建议书制)",
            })
        return rows

    # ============================================================
    # 四维订单画像(品类+包装+时效+风险, 确定性推断)
    # ============================================================

    def order_profile(self, order_type: str, weight: float,
                      piece_count: int, insured_value: float,
                      urgent: bool = False,
                      receiver: dict = None) -> dict:
        """订单四维语义画像

        品类推断(确定性):
            - insuredValue ≥ 10000 或 order=retail 且件数≤2 → premium_gift
            - order=groupbuy(瓶数≥50) → bulk_case
            - order=return 且 weight ≥ 50 → base_liquor(散装回转)
            - 其余 → classic_retail
        特征覆盖: remote/same_city(复用既有判定语义)
        """
        if order_type not in ("retail", "groupbuy", "return"):
            raise ValueError(f"订单类型无效({order_type})")
        if weight < 0 or piece_count <= 0 or insured_value < 0:
            raise ValueError("重量/件数/货值不可为负(件数须为正)")

        receiver = receiver or {}
        province = str(receiver.get("province", "") or "")
        remote = any(p in province for p in
                     ("新疆", "西藏", "青海", "内蒙古", "甘肃", "宁夏"))
        # 同城判定: 发货仓固定成都(与既有 smart_route 同口径)
        city = str(receiver.get("city", "") or "").strip()
        same_city = city == "成都"

        bottles = piece_count * 6
        if insured_value >= 10000:
            category = "premium_gift"
        elif order_type == "groupbuy" and bottles >= 50:
            category = "bulk_case"
        elif order_type == "return" and weight >= 50:
            category = "base_liquor"
        else:
            category = "classic_retail"

        knowledge = WINE_CATEGORY_KNOWLEDGE[category]
        risk_level = ("high" if insured_value >= 10000
                      else "medium" if "hazmat" in knowledge["transportConditions"]
                      else "low")
        return {
            "category": category,
            "categoryName": knowledge["name"],
            "packaging": knowledge["packaging"],
            "transportConditions": knowledge["transportConditions"],
            "urgency": "urgent" if urgent else "standard",
            "riskLevel": risk_level,
            "flags": {"remote": remote, "sameCity": same_city,
                      "insured": insured_value >= 10000},
            "bottles": bottles,
            "knowledge": {
                "categoryExplain": knowledge["categoryExplain"],
                "regionPolicy": REGION_POLICY_NOTE if remote else "",
            },
            "note": "四维画像为确定性推断(品类/包装/时效/风险)",
        }

    # ============================================================
    # 特征路由策略表(优先+备选+进化反馈信号)
    # ============================================================

    async def feature_route(self, profile: dict) -> dict:
        """特征路由决策留痕(策略表确定性映射)"""
        if not isinstance(profile, dict) or not profile.get("category"):
            raise ValueError("画像无效(须含 category, 先调 order-profile)")
        category = profile["category"]
        flags = profile.get("flags", {})

        feature = category
        if flags.get("remote"):
            feature = "remote_area"
        elif flags.get("sameCity") and profile.get("urgency") == "urgent":
            feature = "same_city_urgent"
        strategy = FEATURE_ROUTE_TABLE.get(feature)
        if not strategy:
            raise ValueError(f"无特征路由策略(特征={feature})")

        priority = strategy["priority"]
        record = {
            "feature": feature,
            "priority": {**priority, "carrierName": CARRIER_CAPABILITY.get(
                priority["carrier"], {}).get("name", priority["carrier"])},
            "backup": {**strategy["backup"], "carrierName": CARRIER_CAPABILITY.get(
                strategy["backup"]["carrier"], {}).get(
                "name", strategy["backup"]["carrier"])},
            "feedbackSignals": strategy["feedbackSignals"],
            "inputCategory": category,
            "formula": "特征路由: 画像特征 → 策略表映射(确定性)",
            "disposition": "决策为推荐, 实际渠道由下单方确认",
            "decidedAt": _now_iso(),
        }
        record_id = await self.store.next_id("feature_routes")
        record["featureRouteId"] = record_id
        await self.store.save("feature_routes", record_id, record)
        return record

    async def feature_routes(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("feature_routes")
        return sorted(rows, key=lambda r: r.get("decidedAt", ""),
                      reverse=True)[:limit]
