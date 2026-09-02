"""41号·AI智能代驾模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 ride, 设计文档 §3):
    ride_driver_pool          司机池(轨道/资质快照/状态/评分/服务统计/当日负载)
    ride_driver_applications  注册审查流水(材料/AI评分/档位/复核记录)
    ride_coupons              代驾券(code/面值/来源订单/过期时间/状态)
    ride_coupon_packages      券包(用户维度, 持有计数/累计发放/累计核销)

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38号实机修复惯例)
    - Mock-first: 8 位种子司机(自营3/加盟3/直发2, 对齐 40号 8 博主惯例)
    - P0 范围: 券引擎 + 司机资格审查(派单/行程表 P1 落地)
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
# 种子司机(8 位: 自营3/加盟3/直发2)
# ============================================================

def _build_seed_drivers() -> dict[int, dict]:
    """表驱动种子(平台, 姓名, 电话, 车牌, 驾龄, 评分, 完单, 接单率, 取消率, 状态)"""
    seeds = [
        # 自营轨道(本站超级会员代驾员)
        (TRACK_SELF, "王师傅", "13900000001", "鲁J10001", 8, 4.9, 312, 0.98, 0.01, DRIVER_STATUS_ONLINE),
        (TRACK_SELF, "李师傅", "13900000002", "鲁J10002", 5, 4.7, 156, 0.95, 0.02, DRIVER_STATUS_ONLINE),
        (TRACK_SELF, "赵师傅", "13900000003", "鲁J10003", 12, 4.8, 489, 0.97, 0.01, DRIVER_STATUS_OFFLINE),
        # 加盟轨道(代驾加盟平台合作会员)
        (TRACK_PARTNER, "陈师傅", "13900000004", "鲁J20001", 6, 4.5, 203, 0.92, 0.04, DRIVER_STATUS_ONLINE),
        (TRACK_PARTNER, "刘师傅", "13900000005", "鲁J20002", 4, 4.3, 87, 0.90, 0.05, DRIVER_STATUS_ONLINE),
        (TRACK_PARTNER, "孙师傅", "13900000006", "鲁J20003", 9, 4.6, 275, 0.94, 0.03, DRIVER_STATUS_OFFLINE),
        # 平台直发轨道(外部代驾平台模拟运力, 展示口径)
        (TRACK_PLATFORM, "平台司机甲", "13900000007", "鲁J30001", 7, 4.4, 341, 0.91, 0.05, DRIVER_STATUS_ONLINE),
        (TRACK_PLATFORM, "平台司机乙", "13900000008", "鲁J30002", 3, 4.2, 64, 0.89, 0.06, DRIVER_STATUS_ONLINE),
    ]
    pool = {}
    for i, (track, name, phone, plate, years, rating, done,
            accept_rate, cancel_rate, status) in enumerate(seeds, start=1):
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

    _INT_FIELDS = ("driverId", "applicationId", "memberId",
                   "completedOrders", "todayOrders", "drivingYears",
                   "totalGranted", "totalUsed", "couponCount", "grantCount")
    _FLOAT_FIELDS = ("rating", "acceptRate", "cancelRate", "score",
                     "value", "amount", "consistency")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for key in (self.TABLE_POOL, self.TABLE_APPS,
                    self.TABLE_COUPONS, self.TABLE_PACKAGES):
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
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
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
