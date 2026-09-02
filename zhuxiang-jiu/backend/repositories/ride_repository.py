"""41号·AI智能代驾模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 ride, 设计文档 §3):
    ride_driver_pool          司机池(轨道/资质快照/状态/评分/服务统计/当日负载)
    ride_driver_applications  注册审查流水(材料/AI评分/档位/复核记录)
    ride_coupons              代驾券(code/面值/来源订单/过期时间/状态)
    ride_coupon_packages      券包(用户维度, 持有计数/累计发放/累计核销)
    ride_orders               行程订单(P1: 状态机/起终点/司机快照/计价明细)
    ride_settlements          结算单(P1: 本站支付流水/券抵扣/乘客补差)

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38号实机修复惯例)
    - Mock-first: 8 位种子司机(自营3/加盟3/直发2, 对齐 40号 8 博主惯例)
    - 种子司机内置经纬度(泰安市区), 支撑派单距离计算确定性测试
"""

import json
import os

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 三轨运力常量(设计文档 §1.2)
# ============================================================

TRACK_SELF = "self"          # 自营: 本站超级会员代驾员
TRACK_PARTNER = "partner"    # 加盟: 代驾加盟平台合作会员
TRACK_PLATFORM = "platform"  # 直发: 外部代驾平台运力(兜底)
TRACKS = (TRACK_SELF, TRACK_PARTNER, TRACK_PLATFORM)

TRACK_NAMES = {TRACK_SELF: "自营", TRACK_PARTNER: "加盟", TRACK_PLATFORM: "平台直发"}

# 司机生命周期(设计文档 §2.2, 资格审查通过后入池)
DRIVER_STATUS_ONLINE = "online"        # 接单中
DRIVER_STATUS_OFFLINE = "offline"      # 休息
DRIVER_STATUS_SUSPENDED = "suspended"  # 违规暂停(人工恢复)
DRIVER_STATUS_REVOKED = "revoked"      # 资格吊销
DRIVER_STATUSES = (DRIVER_STATUS_ONLINE, DRIVER_STATUS_OFFLINE,
                   DRIVER_STATUS_SUSPENDED, DRIVER_STATUS_REVOKED)

# 审查流水状态(AI 审查即时出档, manual_review 档转人工)
APP_STATUS_APPROVED = "approved"        # 自动通过
APP_STATUS_MANUAL_REVIEW = "manual_review"  # 人工复核队列
APP_STATUS_REJECTED = "rejected"        # 拒绝(留痕可申诉)
APP_STATUSES = (APP_STATUS_APPROVED, APP_STATUS_MANUAL_REVIEW,
                APP_STATUS_REJECTED)

# 审查三档阈值(沿用 36/40号阈值范式: ≥70 自动 / 50-70 人工 / <50 拒绝)
APP_AUTO_SCORE = 70.0
APP_MANUAL_SCORE = 50.0

# 硬门槛: 驾龄下限(年), 准驾车型
MIN_DRIVING_YEARS = 3
VALID_LICENSE_CLASSES = ("C1", "C2", "B1", "B2", "A1", "A2")

# ============================================================
# 券引擎常量(设计文档 §2.1, 环境变量可覆盖便于测试)
# ============================================================

# 满额赠券门槛(元)
COUPON_THRESHOLD = float(os.environ.get("DRIDE_COUPON_THRESHOLD", "500"))
# 券面值(元)
COUPON_VALUE = float(os.environ.get("DRIDE_COUPON_VALUE", "60"))
# 券有效期(天)
COUPON_VALID_DAYS = int(os.environ.get("DRIDE_COUPON_VALID_DAYS", "90"))
# 账户未核销上限(防囤积)
COUPON_HOLD_CAP = int(os.environ.get("DRIDE_COUPON_HOLD_CAP", "6"))

# 满额档位梯度: (订单实付下限, 赠券张数), 从高到低匹配
GRANT_TIERS = (
    (3000.0, 3),
    (1000.0, 2),
    (500.0, 1),
)


def grant_tier_count(amount: float) -> int:
    """订单实付 → 赠券张数(未达门槛 0)"""
    for floor, count in GRANT_TIERS:
        if amount >= floor:
            return count
    return 0


# 券状态机: granted → used / expired / revoked
COUPON_STATUS_GRANTED = "granted"
COUPON_STATUS_USED = "used"
COUPON_STATUS_EXPIRED = "expired"
COUPON_STATUS_REVOKED = "revoked"
COUPON_STATUSES = (COUPON_STATUS_GRANTED, COUPON_STATUS_USED,
                   COUPON_STATUS_EXPIRED, COUPON_STATUS_REVOKED)

# P1 预留常量(设计文档 §2.3/§2.4, 先行定义供配置口径统一)
CITY_RADIUS_KM = float(os.environ.get("DRIDE_CITY_RADIUS_KM", "40"))
DISPATCH_RADIUS_KM = float(os.environ.get("DRIDE_DISPATCH_RADIUS_KM", "5"))
FREE_CANCEL_SECONDS = int(os.environ.get("DRIDE_FREE_CANCEL_SECONDS", "180"))

# 司机准入最低服务评分(派单硬过滤, P1 用)
MIN_DRIVER_RATING = 4.0

# ============================================================
# 行程状态机(设计文档 §2.4, P1)
# ============================================================

RIDE_STATUS_REQUESTED = "requested"          # 已叫单(选券完成)
RIDE_STATUS_DISPATCHED = "dispatched"        # 已派单(含司机信息)
RIDE_STATUS_ARRIVING = "driver_arriving"     # 司机已出发/到位
RIDE_STATUS_STARTED = "trip_started"         # 行程开始(乘客上车)
RIDE_STATUS_COMPLETED = "trip_completed"     # 行程结束
RIDE_STATUS_SETTLING = "settling"            # AI 结算中
RIDE_STATUS_SETTLED = "settled"              # 本站已支付(终态)
RIDE_STATUS_CANCELLED = "cancelled"          # 取消(免责窗口判定)
RIDE_STATUS_NO_DRIVER = "no_driver"          # 全轨无运力(券退回, 罕见)
RIDE_STATUSES = (RIDE_STATUS_REQUESTED,
                 RIDE_STATUS_DISPATCHED, RIDE_STATUS_ARRIVING,
                 RIDE_STATUS_STARTED, RIDE_STATUS_COMPLETED,
                 RIDE_STATUS_SETTLING, RIDE_STATUS_SETTLED,
                 RIDE_STATUS_CANCELLED, RIDE_STATUS_NO_DRIVER)

# 行程非终态(活跃中: 选券时需排除这些行程已占用的券)
RIDE_ACTIVE_STATUSES = (RIDE_STATUS_REQUESTED, RIDE_STATUS_DISPATCHED,
                        RIDE_STATUS_ARRIVING, RIDE_STATUS_STARTED,
                        RIDE_STATUS_COMPLETED, RIDE_STATUS_SETTLING)

# ============================================================
# 计价常量(设计文档 §2.5, 市内代驾, 环境变量可覆盖便于测试)
# ============================================================

RIDE_BASE_FARE = float(os.environ.get("DRIDE_BASE_FARE", "35"))   # 起步价(含 BASE_KM)
RIDE_BASE_KM = float(os.environ.get("DRIDE_BASE_KM", "5"))        # 起步里程
RIDE_PER_KM = float(os.environ.get("DRIDE_PER_KM", "5"))          # 超里程单价
RIDE_PER_MIN = float(os.environ.get("DRIDE_PER_MIN", "1"))        # 超时单价
RIDE_FREE_MINUTES = float(os.environ.get("DRIDE_FREE_MINUTES", "40"))  # 免费时长
RIDE_NIGHT_SURGE = float(os.environ.get("DRIDE_NIGHT_SURGE", "0.2"))  # 夜间加成 20%
RIDE_NIGHT_START = 22   # 夜间起始(时, 含)
RIDE_NIGHT_END = 6       # 夜间结束(时, 不含)

# 派单决策阈值(设计文档 §2.3: ≥70 直接派 / 50-70 次优+备选 / <50 溢出直发)
DISPATCH_AUTO_SCORE = 70.0
DISPATCH_BACKUP_SCORE = 50.0

# 直发通道模式(对齐 36号三态: mock/real/mock_fallback)
DRIDE_CHANNEL_MODE = os.environ.get("DRIDE_CHANNEL_MODE", "mock")
DRIDE_PARTNER_URL = os.environ.get("DRIDE_PARTNER_URL", "")

# 直发平台真实接入凭证(待办清单 §二/§三; 均缺省空 = 不带鉴权头)
DRIDE_PARTNER_APP_ID = os.environ.get("DRIDE_PARTNER_APP_ID", "")
DRIDE_PARTNER_APP_SECRET = os.environ.get("DRIDE_PARTNER_APP_SECRET", "")
DRIDE_PARTNER_TOKEN = os.environ.get("DRIDE_PARTNER_TOKEN", "")   # Bearer 备选风格
# 回调签名令牌(待办清单 §四; 缺省空 = 回调端点不校验, 生产必须配置)
DRIDE_PARTNER_CALLBACK_TOKEN = os.environ.get(
    "DRIDE_PARTNER_CALLBACK_TOKEN", "")

# ============================================================
# P2 安全监控常量(设计文档 §2.4)
# ============================================================

# 饮酒场景 POI 词表(上车点命中 → 合规叫单场景, 不产生风控信号)
RIDE_POI_DRINKING_WORDS = ("餐厅", "饭店", "酒", "吧", "烧烤", "火锅",
                           "宴", "夜市", "KTV", "排档", "私房菜")
# 非饮酒 POI 高频叫单风控: 24h 窗口内次数阈值(含当次)
RIDE_POI_FREQ_WINDOW_HOURS = 24
RIDE_POI_FREQ_THRESHOLD = 3
# 行程超时预警: 市内行程 > 3h 未结束
RIDE_TIMEOUT_HOURS = 3
# 里程异常: 实际里程超预估倍数
RIDE_MILEAGE_ANOMALY_RATIO = 2.0

# 风险事件类型(POI 高频/行程超时/里程异常)
RISK_EVENT_POI = "poi_high_frequency"    # 行前: 非饮酒场景 POI 高频叫单
RISK_EVENT_TIMEOUT = "trip_timeout"      # 行中: 行程超时未结束
RISK_EVENT_MILEAGE = "mileage_anomaly"   # 行后: 实际里程超预估 2 倍
RISK_EVENT_TYPES = (RISK_EVENT_POI, RISK_EVENT_TIMEOUT, RISK_EVENT_MILEAGE)

# 平台直发回调事件(五态生命周期: 接单/到达/开始/完成/取消)
PARTNER_EVENT_ACCEPTED = "accepted"
PARTNER_EVENT_ARRIVED = "driver_arrived"   # 4.3: 司机到达(映射 arriving)
PARTNER_EVENT_STARTED = "started"
PARTNER_EVENT_COMPLETED = "completed"
PARTNER_EVENT_CANCELLED = "cancelled"
PARTNER_EVENTS = (PARTNER_EVENT_ACCEPTED, PARTNER_EVENT_ARRIVED,
                 PARTNER_EVENT_STARTED, PARTNER_EVENT_COMPLETED,
                 PARTNER_EVENT_CANCELLED)

# ============================================================
# P3 双向评价常量(设计文档 §2.4 行后)
# ============================================================

# 评价方向(双向)
REVIEW_BY_PASSENGER = "passenger_to_driver"   # 乘客评司机(回写司机评分)
REVIEW_BY_DRIVER = "driver_to_passenger"      # 司机评乘客(留档观察)
REVIEW_DIRECTIONS = (REVIEW_BY_PASSENGER, REVIEW_BY_DRIVER)

# AI 审评处置动作(对齐 37号评价审评范式)
REVIEW_ACTION_SHOW = "show"    # 正常展示
REVIEW_ACTION_WATCH = "watch"  # 观察
REVIEW_ACTION_FOLD = "fold"    # 折叠(垃圾评价, 不回写评分)
REVIEW_ACTIONS = (REVIEW_ACTION_SHOW, REVIEW_ACTION_WATCH,
                  REVIEW_ACTION_FOLD)

# 评价星级边界
REVIEW_SCORE_MIN = 1
REVIEW_SCORE_MAX = 5

# 行程评价状态(双向各自独立标记)
RIDE_REVIEW_PENDING = "pending"    # 待评价
RIDE_REVIEW_DONE = "done"          # 已评价
RIDE_REVIEW_STATUSES = (RIDE_REVIEW_PENDING, RIDE_REVIEW_DONE)

# ============================================================
# P4 日结对账常量(物流结算单模式平移, 设计文档 §2.5 对账)
# ============================================================

# 对账单状态机: 主链 pending → reconciling → confirmed → paid;
# 差异分支 diff → investigating → resolved → confirmed
RECON_STATUS_PENDING = "pending"
RECON_STATUS_RECONCILING = "reconciling"
RECON_STATUS_CONFIRMED = "confirmed"
RECON_STATUS_PAID = "paid"
RECON_STATUS_DIFF = "diff"
RECON_STATUS_INVESTIGATING = "investigating"
RECON_STATUS_RESOLVED = "resolved"

# 差异类型(三方: 本站结算单 vs 平台账单 vs 券核销)
RECON_DIFF_AMOUNT = "amount_mismatch"       # 金额不符
RECON_DIFF_MISSING = "order_missing"       # 单据缺失(本站有, 平台无)
RECON_DIFF_EXTRA = "extra_order"            # 多余单据(平台有, 本站无)
RECON_DIFF_COUPON = "coupon_unredeemed"    # 结算单的券未核销


# ============================================================
# 种子司机(8 位: 自营3/加盟3/直发2)
# ============================================================

def _build_seed_drivers() -> dict[int, dict]:
    """表驱动种子(平台, 姓名, 电话, 车牌, 驾龄, 评分, 完单, 接单率,
    取消率, 状态, 纬度, 经度)

    位置锚点: 泰安市区中心 ≈ (36.19, 117.13), 0.01° 纬度 ≈ 1.11km,
    0.01° 经度 ≈ 0.9km——种子分布覆盖 0.2-2.8km 派单半径带, 支撑
    派单距离因子与半径过滤的确定性测试。
    """
    seeds = [
        # 自营轨道(本站超级会员代驾员)
        (TRACK_SELF, "王师傅", "13900000001", "鲁J10001", 8, 4.9, 312, 0.98, 0.01, DRIVER_STATUS_ONLINE, 36.192, 117.130),
        (TRACK_SELF, "李师傅", "13900000002", "鲁J10002", 5, 4.7, 156, 0.95, 0.02, DRIVER_STATUS_ONLINE, 36.190, 117.135),
        (TRACK_SELF, "赵师傅", "13900000003", "鲁J10003", 12, 4.8, 489, 0.97, 0.01, DRIVER_STATUS_OFFLINE, 36.188, 117.128),
        # 加盟轨道(代驾加盟平台合作会员)
        (TRACK_PARTNER, "陈师傅", "13900000004", "鲁J20001", 6, 4.5, 203, 0.92, 0.04, DRIVER_STATUS_ONLINE, 36.200, 117.130),
        (TRACK_PARTNER, "刘师傅", "13900000005", "鲁J20002", 4, 4.3, 87, 0.90, 0.05, DRIVER_STATUS_ONLINE, 36.190, 117.160),
        (TRACK_PARTNER, "孙师傅", "13900000006", "鲁J20003", 9, 4.6, 275, 0.94, 0.03, DRIVER_STATUS_OFFLINE, 36.185, 117.140),
        # 平台直发轨道(外部代驾平台模拟运力, 展示口径)
        (TRACK_PLATFORM, "平台司机甲", "13900000007", "鲁J30001", 7, 4.4, 341, 0.91, 0.05, DRIVER_STATUS_ONLINE, 36.190, 117.130),
        (TRACK_PLATFORM, "平台司机乙", "13900000008", "鲁J30002", 3, 4.2, 64, 0.89, 0.06, DRIVER_STATUS_ONLINE, 36.190, 117.131),
    ]
    pool = {}
    for i, (track, name, phone, plate, years, rating, done,
            accept_rate, cancel_rate, status, lat, lng) in enumerate(seeds, start=1):
        pool[i] = {
            "driverId": i,
            "track": track,
            "trackName": TRACK_NAMES[track],
            "platform": "本站" if track == TRACK_SELF else (
                "e代驾mock" if track == TRACK_PLATFORM else "代驾联盟"),
            "name": name,
            "phone": phone,
            "plateNo": plate,
            "drivingYears": years,
            "licenseClass": "C1",
            "rating": rating,
            "completedOrders": done,
            "acceptRate": accept_rate,
            "cancelRate": cancel_rate,
            "status": status,
            "city": "泰安",
            "lat": lat,
            "lng": lng,
            "currentRideId": "",   # 在忙行程(派单占用/完成后释放)
            "todayOrders": 0,
            "memberId": 0,          # 关联超级会员(自营轨道审查通过后回填)
            "applicationId": 0,     # 关联审查流水
            "suspendedReason": "",
            "createdAt": "2026-08-01T00:00:00+00:00",
            "updatedAt": "2026-08-01T00:00:00+00:00",
        }
    return pool


class RideRepository:
    """41号代驾模块仓储(双模式, blogger_repository 四原语模式平移)"""

    TABLE_POOL = "ride_driver_pool"
    TABLE_APPS = "ride_driver_applications"
    TABLE_COUPONS = "ride_coupons"
    TABLE_PACKAGES = "ride_coupon_packages"
    TABLE_RIDES = "ride_orders"
    TABLE_SETTLEMENTS = "ride_settlements"
    TABLE_RISK = "ride_risk_events"
    TABLE_REVIEWS = "ride_reviews"
    TABLE_RECON = "ride_reconciliations"

    _INT_FIELDS = ("driverId", "applicationId", "memberId",
                   "completedOrders", "todayOrders", "drivingYears",
                   "totalGranted", "totalUsed", "couponCount", "grantCount",
                   "settlementId", "rideSeq", "riskId", "reviewId",
                   "reviewScore", "reviewsToday", "totalOrders",
                   "diffCount")
    _FLOAT_FIELDS = ("rating", "acceptRate", "cancelRate", "score",
                     "value", "amount", "consistency",
                     "lat", "lng", "distanceKm", "estimatedKm",
                     "totalAmount", "couponDeduction", "extraCharge",
                     "payoutAmount", "dispatchScore", "channelQuotedAmount")
    # bool 字段(Redis 序列化为 1/0, 读回须恢复 bool 否则 "0" 为 truthy)
    _BOOL_FIELDS = ("dispatchFed", "cancelWindowFree", "mileageAnomaly",
                    "ratingApplied", "resolved", "appFed", "reviewFed")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for key in (self.TABLE_POOL, self.TABLE_APPS,
                    self.TABLE_COUPONS, self.TABLE_PACKAGES,
                    self.TABLE_RIDES, self.TABLE_SETTLEMENTS,
                    self.TABLE_RISK, self.TABLE_REVIEWS,
                    self.TABLE_RECON):
            self.store.setdefault(key, {})
        # 种子司机(内存模式惰性灌入; Redis 模式由 _ensure_pool_seeded 兜底)
        if not self.store[self.TABLE_POOL]:
            for did, driver in _build_seed_drivers().items():
                self.store[self.TABLE_POOL][did] = dict(driver)

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("ride", kind, "seq"))
        self._ensure_store()
        seq_key = f"_ride_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in RideRepository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in RideRepository._FLOAT_FIELDS:
                # None 序列化为 "" → 恢复 None(金额类空值口径)
                if v == "" or v is None:
                    record[k] = None
                else:
                    try:
                        record[k] = float(v)
                    except (TypeError, ValueError):
                        record[k] = v
            elif k in RideRepository._BOOL_FIELDS:
                record[k] = v in (1, "1", True, "True", "true")
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("ride", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("ride", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("ride", table, "*"))
            result = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    async def _delete(self, table: str, record_id) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("ride", table, record_id))
        else:
            self._ensure_store()
            self.store[table].pop(record_id, None)

    async def _ensure_pool_seeded(self) -> None:
        """司机池种子惰性灌入(幂等, 双模式)"""
        existing = await self._list(self.TABLE_POOL, limit=1000)
        if existing:
            return
        for did, driver in _build_seed_drivers().items():
            await self._save(self.TABLE_POOL, did, driver)

    # --------------------------------------------------------
    # 司机池
    # --------------------------------------------------------

    async def save_driver(self, driver: dict) -> dict:
        return await self._save(self.TABLE_POOL, driver["driverId"], driver)

    async def get_driver(self, driver_id: int) -> dict | None:
        await self._ensure_pool_seeded()
        return await self._get(self.TABLE_POOL, driver_id)

    async def list_drivers(self, track: str = None, status: str = None,
                           limit: int = 200) -> list[dict]:
        await self._ensure_pool_seeded()
        drivers = await self._list(self.TABLE_POOL, limit=1000)
        if track:
            drivers = [d for d in drivers if d.get("track") == track]
        if status:
            drivers = [d for d in drivers if d.get("status") == status]
        return drivers[:limit]

    async def next_driver_id(self) -> int:
        await self._ensure_pool_seeded()
        drivers = await self._list(self.TABLE_POOL, limit=10000)
        return max((int(d.get("driverId") or 0) for d in drivers),
                   default=0) + 1

    async def get_driver_by_member(self, member_id: int) -> dict | None:
        drivers = await self._list(self.TABLE_POOL, limit=1000)
        for d in drivers:
            if int(d.get("memberId") or 0) == int(member_id):
                return d
        return None

    # --------------------------------------------------------
    # 审查流水
    # --------------------------------------------------------

    async def save_application(self, app: dict) -> dict:
        return await self._save(self.TABLE_APPS,
                                app["applicationId"], app)

    async def get_application(self, application_id: int) -> dict | None:
        return await self._get(self.TABLE_APPS, application_id)

    async def list_applications(self, status: str = None,
                                limit: int = 200) -> list[dict]:
        apps = await self._list(self.TABLE_APPS, limit=1000)
        if status:
            apps = [a for a in apps if a.get("status") == status]
        return apps[:limit]

    async def get_application_by_member(self, member_id: int) -> dict | None:
        apps = await self._list(self.TABLE_APPS, limit=1000)
        for a in apps:
            if int(a.get("memberId") or 0) == int(member_id):
                return a
        return None

    # --------------------------------------------------------
    # 券与券包
    # --------------------------------------------------------

    async def save_coupon(self, coupon: dict) -> dict:
        await self._save(self.TABLE_COUPONS, coupon["code"], coupon)
        return coupon

    async def get_coupon(self, code: str) -> dict | None:
        return await self._get(self.TABLE_COUPONS, code)

    async def list_coupons(self, member_id: int = None,
                           status: str = None,
                           order_id: str = None,
                           limit: int = 500) -> list[dict]:
        coupons = await self._list(self.TABLE_COUPONS, limit=2000)
        if member_id is not None:
            coupons = [c for c in coupons
                       if int(c.get("memberId") or 0) == int(member_id)]
        if status:
            coupons = [c for c in coupons if c.get("status") == status]
        if order_id:
            coupons = [c for c in coupons
                       if c.get("orderId") == order_id]
        return coupons[:limit]

    async def get_package(self, member_id: int) -> dict | None:
        return await self._get(self.TABLE_PACKAGES, member_id)

    async def save_package(self, package: dict) -> dict:
        return await self._save(self.TABLE_PACKAGES,
                                package["memberId"], package)

    async def ensure_package(self, member_id: int) -> dict:
        """券包惰性创建(用户维度聚合容器)"""
        package = await self.get_package(member_id)
        if package is None:
            package = {
                "memberId": member_id,
                "holdCount": 0,      # 当前未核销持有数
                "totalGranted": 0,   # 累计发放
                "totalUsed": 0,      # 累计核销
                "totalRevoked": 0,   # 累计冲正作废
                "createdAt": None,
                "updatedAt": None,
            }
            await self.save_package(package)
        return package

    # --------------------------------------------------------
    # 行程订单(P1)
    # --------------------------------------------------------

    async def save_ride(self, ride: dict) -> dict:
        return await self._save(self.TABLE_RIDES, ride["rideId"], ride)

    async def get_ride(self, ride_id: str) -> dict | None:
        return await self._get(self.TABLE_RIDES, ride_id)

    async def list_rides(self, member_id: int = None, driver_id: int = None,
                        status: str = None, limit: int = 200) -> list[dict]:
        rides = await self._list(self.TABLE_RIDES, limit=2000)
        if member_id is not None:
            rides = [r for r in rides
                     if int(r.get("memberId") or 0) == int(member_id)]
        if driver_id is not None:
            rides = [r for r in rides
                     if int(r.get("driverId") or 0) == int(driver_id)]
        if status:
            rides = [r for r in rides if r.get("status") == status]
        return rides[:limit]

    async def next_ride_id(self) -> str:
        """行程号: RD + 8 位零填充自增(如 RD00000001)"""
        seq = await self.next_id("ride")
        return f"RD{seq:08d}"

    # --------------------------------------------------------
    # 结算单(P1)
    # --------------------------------------------------------

    async def save_settlement(self, settlement: dict) -> dict:
        return await self._save(self.TABLE_SETTLEMENTS,
                                 settlement["settlementId"], settlement)

    async def get_settlement(self, settlement_id: int) -> dict | None:
        return await self._get(self.TABLE_SETTLEMENTS, settlement_id)

    async def get_settlement_by_ride(self, ride_id: str) -> dict | None:
        settlements = await self._list(self.TABLE_SETTLEMENTS, limit=2000)
        for s in settlements:
            if s.get("rideId") == ride_id:
                return s
        return None

    async def list_settlements(self, driver_id: int = None,
                               track: str = None,
                               payout_status: str = None,
                               limit: int = 200) -> list[dict]:
        settlements = await self._list(self.TABLE_SETTLEMENTS, limit=2000)
        if driver_id is not None:
            settlements = [s for s in settlements
                           if int(s.get("driverId") or 0) == int(driver_id)]
        if track:
            settlements = [s for s in settlements if s.get("track") == track]
        if payout_status:
            settlements = [s for s in settlements
                           if s.get("payoutStatus") == payout_status]
        return settlements[:limit]

    async def next_settlement_id(self) -> int:
        return await self.next_id("settlement")

    # --------------------------------------------------------
    # 风险事件(P2 安全监控)
    # --------------------------------------------------------

    async def save_risk_event(self, event: dict) -> dict:
        return await self._save(self.TABLE_RISK, event["riskId"], event)

    async def list_risk_events(self, ride_id: str = None, type: str = None,
                               resolved: bool = None,
                               limit: int = 200) -> list[dict]:
        events = await self._list(self.TABLE_RISK, limit=2000)
        if ride_id:
            events = [e for e in events if e.get("rideId") == ride_id]
        if type:
            events = [e for e in events if e.get("type") == type]
        if resolved is not None:
            events = [e for e in events
                      if bool(e.get("resolved")) == bool(resolved)]
        return events[:limit]

    async def next_risk_id(self) -> int:
        return await self.next_id("risk")

    async def get_ride_by_partner_order(self,
                                        partner_order_id: str) -> dict | None:
        """按平台直发单号查行程(回调入口)"""
        rides = await self._list(self.TABLE_RIDES, limit=2000)
        for r in rides:
            snap = r.get("driverSnapshot") or {}
            if snap.get("partnerOrderId") == str(partner_order_id):
                return r
        return None

    # --------------------------------------------------------
    # 双向评价(P3)
    # --------------------------------------------------------

    async def save_review(self, review: dict) -> dict:
        return await self._save(self.TABLE_REVIEWS,
                                review["reviewId"], review)

    async def get_review(self, review_id: int) -> dict | None:
        return await self._get(self.TABLE_REVIEWS, review_id)

    async def get_review_by_ride(self, ride_id: str,
                                 direction: str) -> dict | None:
        """按行程+方向查评价(幂等: 一行程一方向一评价)"""
        reviews = await self._list(self.TABLE_REVIEWS, limit=2000)
        for r in reviews:
            if (r.get("rideId") == ride_id
                    and r.get("direction") == direction):
                return r
        return None

    async def list_reviews(self, driver_id: int = None,
                          member_id: int = None, action: str = None,
                          direction: str = None,
                          annotated: bool = None,
                          limit: int = 200) -> list[dict]:
        reviews = await self._list(self.TABLE_REVIEWS, limit=2000)
        if driver_id is not None:
            reviews = [r for r in reviews
                      if int(r.get("driverId") or 0) == int(driver_id)]
        if member_id is not None:
            reviews = [r for r in reviews
                       if int(r.get("memberId") or 0) == int(member_id)]
        if action:
            reviews = [r for r in reviews if r.get("action") == action]
        if direction:
            reviews = [r for r in reviews
                       if r.get("direction") == direction]
        if annotated is not None:
            reviews = [r for r in reviews
                       if bool(r.get("annotatedAction"))
                       == bool(annotated)]
        return reviews[:limit]

    async def next_review_id(self) -> int:
        return await self.next_id("review")

    # --------------------------------------------------------
    # 日结对账单(P4, 物流结算单模式平移)
    # --------------------------------------------------------

    async def save_reconciliation(self, recon: dict) -> dict:
        return await self._save(self.TABLE_RECON,
                                 recon["reconNo"], recon)

    async def get_reconciliation(self, recon_no: str) -> dict | None:
        return await self._get(self.TABLE_RECON, recon_no)

    async def list_reconciliations(self, track: str = None,
                                   status: str = None,
                                   period: str = None,
                                   limit: int = 200) -> list[dict]:
        recons = await self._list(self.TABLE_RECON, limit=1000)
        if track:
            recons = [r for r in recons if r.get("track") == track]
        if status:
            recons = [r for r in recons if r.get("status") == status]
        if period:
            recons = [r for r in recons
                      if r.get("period") == period]
        return recons[:limit]

    async def update_reconciliation(self, recon_no: str,
                                    fields: dict) -> dict:
        recon = await self.get_reconciliation(recon_no)
        if recon is None:
            raise KeyError(recon_no)
        recon.update(fields)
        return await self.save_reconciliation(recon)
