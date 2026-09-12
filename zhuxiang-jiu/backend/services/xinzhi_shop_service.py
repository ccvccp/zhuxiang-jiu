"""68号 P6·信值·臻选——角色店铺服务(平台化升级)

依据:《68号 创新规划方案》§三 P6 + 任务书《信值·臻选 68号平台化升级》

核心命题:**信值体系驱动的角色店铺**——与 37号同盟入驻(超级会员
Lv4+信用分≥80)差异化: 68号店铺准入锚定信值雷达五维总分(≥60) +
会员等级(≥L3); 店铺等级复用 P4 商家评级 S→旗舰店/A→优选店/
B→标准店/C→新锐店(D 不可开店)。

九态状态机(结构对齐 37号入驻范式, 门槛口径不同):
    pending → ai_reviewing → manual_reviewing → signed →
    probation(30天观察) → active ⇄ suspended → terminated/rejected

AI 预审三档(确定性规则, LLM 禁入; 对齐 37号三档范式):
    雷达总分≥80 → 快车道: pending→signed 直接(正向激励自动生效
        留痕——对齐 P4"升级自动生效"口径, disposition 记录)
    60-79      → 人工审核(manual_reviewing)
    <60/材料不全 → 拒绝建议(仅标记+建议书, 永不自动执行——
        处罚类决策须 admin 确认, 宪法域; <60 已被准入门槛拦截,
        此档实际作用于"签名材料不全"类标记)

红线(宪法域):
    - 全链路确定性: 准入门槛/预审/等级映射全为规则, 同输入同输出
    - 建议书模式: 审核/暂停/预警类响应含 disposition 字段;
      雷达分跌破 40 仅生成预警建议书, 永不自动 suspend
    - 一人一铺: memberId 唯一(在途/有效状态均拦截, 重复 409)
    - LLM 禁入判定链

存储: _XzStore(xinzhi_repository 范式——双模式内存/Redis,
    键前缀 zhuxiang:xz:, 表 xz_shops/xz_listings, P6/P7 共用)
"""

import json
import logging
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xinzhi-shop"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# P6 常量(任务书口径)
# ============================================================

# 店铺类目(37号"酒水不分家"域的角色铺面子集)
SHOP_CATEGORY_WINE = "wine"       # 好酒(主站白酒臻选)
SHOP_CATEGORY_VESSEL = "vessel"   # 酒具
SHOP_CATEGORY_VENUE = "venue"     # 好境(酒友聚会场所)
SHOP_CATEGORIES = (SHOP_CATEGORY_WINE, SHOP_CATEGORY_VESSEL,
                   SHOP_CATEGORY_VENUE)
SHOP_CATEGORY_LABELS = {
    SHOP_CATEGORY_WINE: "好酒", SHOP_CATEGORY_VESSEL: "酒具",
    SHOP_CATEGORY_VENUE: "好境",
}

# 准入门槛(与 37号差异化——信值体系口径)
SHOP_RADAR_MIN = 60            # 信值雷达五维总分门槛
SHOP_MEMBER_LEVEL_MIN = 3      # 会员等级门槛(≥L3)
SHOP_NAME_MAX = 20             # 店铺名称上限(字)
SHOP_INTRO_MAX = 100           # 店铺简介上限(字)

# AI 预审三档(确定性——雷达总分口径)
AI_FAST_LANE_SCORE = 80        # ≥80 快车道(pending→signed 直接)
AI_MANUAL_SCORE = 60           # 60-79 人工审核

# 观察期与雷达预警线
PROBATION_DAYS = 30            # 签约后观察期(天)
RADAR_WARN_LINE = 40           # 雷达分跌破 → 预警建议书(永不自动)
RADAR_SNAPSHOT_KEEP = 90       # 店铺雷达快照保留条数(90 日口径)

# 店铺等级映射(复用 P4 商家评级 S-A-B-C-D)
SHOP_LEVELS = {
    "S": "旗舰店", "A": "优选店", "B": "标准店", "C": "新锐店",
}

# ============================================================
# 九态状态机(结构对齐 37号)
# ============================================================

SHOP_STATUS_PENDING = "pending"                # 已申请(待 AI 预审)
SHOP_STATUS_AI_REVIEWING = "ai_reviewing"      # AI 预审中
SHOP_STATUS_MANUAL_REVIEWING = "manual_reviewing"  # 人工审核中
SHOP_STATUS_SIGNED = "signed"                  # 已签约
SHOP_STATUS_PROBATION = "probation"            # 观察期(30 天)
SHOP_STATUS_ACTIVE = "active"                  # 正式营业
SHOP_STATUS_SUSPENDED = "suspended"            # 暂停整改
SHOP_STATUS_TERMINATED = "terminated"          # 已关店(终态)
SHOP_STATUS_REJECTED = "rejected"              # 审核拒绝(终态)
SHOP_STATUSES = (
    SHOP_STATUS_PENDING, SHOP_STATUS_AI_REVIEWING,
    SHOP_STATUS_MANUAL_REVIEWING, SHOP_STATUS_SIGNED,
    SHOP_STATUS_PROBATION, SHOP_STATUS_ACTIVE,
    SHOP_STATUS_SUSPENDED, SHOP_STATUS_TERMINATED,
    SHOP_STATUS_REJECTED,
)

# 状态转移表(对齐 37号 §2.1)
SHOP_STATUS_TRANSITIONS = {
    SHOP_STATUS_PENDING: (SHOP_STATUS_AI_REVIEWING,
                          SHOP_STATUS_REJECTED),
    SHOP_STATUS_AI_REVIEWING: (SHOP_STATUS_MANUAL_REVIEWING,
                               SHOP_STATUS_SIGNED,
                               SHOP_STATUS_REJECTED),
    SHOP_STATUS_MANUAL_REVIEWING: (SHOP_STATUS_SIGNED,
                                   SHOP_STATUS_REJECTED),
    SHOP_STATUS_SIGNED: (SHOP_STATUS_PROBATION,
                         SHOP_STATUS_TERMINATED),
    SHOP_STATUS_PROBATION: (SHOP_STATUS_ACTIVE,
                            SHOP_STATUS_SUSPENDED,
                            SHOP_STATUS_TERMINATED),
    SHOP_STATUS_ACTIVE: (SHOP_STATUS_SUSPENDED,
                         SHOP_STATUS_TERMINATED),
    SHOP_STATUS_SUSPENDED: (SHOP_STATUS_ACTIVE,
                            SHOP_STATUS_TERMINATED),
    SHOP_STATUS_TERMINATED: (),   # 终态
    SHOP_STATUS_REJECTED: (),     # 终态
}

SHOP_STATUS_LABELS = {
    SHOP_STATUS_PENDING: "已申请(待AI预审)",
    SHOP_STATUS_AI_REVIEWING: "AI预审中(确定性规则)",
    SHOP_STATUS_MANUAL_REVIEWING: "人工审核中",
    SHOP_STATUS_SIGNED: "已签约",
    SHOP_STATUS_PROBATION: "观察期(30天)",
    SHOP_STATUS_ACTIVE: "正式营业",
    SHOP_STATUS_SUSPENDED: "暂停整改",
    SHOP_STATUS_TERMINATED: "已关店",
    SHOP_STATUS_REJECTED: "审核拒绝",
}

# 一人一铺拦截域(在途/有效状态; 终态可重新申请)
SHOP_OCCUPYING_STATUSES = (
    SHOP_STATUS_PENDING, SHOP_STATUS_AI_REVIEWING,
    SHOP_STATUS_MANUAL_REVIEWING, SHOP_STATUS_SIGNED,
    SHOP_STATUS_PROBATION, SHOP_STATUS_ACTIVE,
    SHOP_STATUS_SUSPENDED,
)


# ============================================================
# _XzStore: P6/P7 轻量存储(xinzhi_repository 范式)
# ============================================================

# 序列化类型清单(bool 陷阱还原——P6g-4 实机教训)
_XZ_INT_FIELDS = ("shopId", "memberId", "listingId", "warningId",
                  "batchId")
_XZ_FLOAT_FIELDS = ("radarTotal", "score", "sourcePrice", "basePrice",
                    "afterThreeFactor", "xinzhiAlpha", "xinzhiCredit",
                    "finalPrice", "trustDiscount",
                    "contributionDiscount", "promoFactor")
_XZ_BOOL_FIELDS = ("materialsComplete", "gatesPassed", "executed",
                   "approved", "requiresAdmin", "certified")
_XZ_LIST_FIELDS = ("radarSnapshots", "warnings", "reviews",
                   "missingChecks", "allowedTransitions")


class _XzStore:
    """68号 P6/P7 数据访问层(双模式: 内存 + Redis)

    表清单:
        xz_shops:    角色店铺(九态状态机/AI 预审/雷达快照/预警留痕)
        xz_listings: 店铺铺货(四门禁留痕/信值价快照/上下架流转)

    键口径: Redis zhuxiang:xz:shops:{id} / zhuxiang:xz:listings:{id}
    (xinzhi_repository 同构——hset 整记录 + bool/list 序列化清单)
    """

    TABLE_SHOPS = "xz_shops"
    TABLE_LISTINGS = "xz_listings"

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # --------------------------------------------------------
    # 序列化(口径对齐 xinzhi_repository)
    # --------------------------------------------------------

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
            if k in _XZ_INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _XZ_FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _XZ_BOOL_FIELDS:
                if v in ("0", 0):
                    record[k] = False
                elif v in ("1", 1):
                    record[k] = True
                else:
                    record[k] = bool(v)
            elif isinstance(v, str) and (
                    k in _XZ_LIST_FIELDS
                    or v.startswith(("{", "["))):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SHOPS, {})
        self.store.setdefault(self.TABLE_LISTINGS, {})

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("xz", kind, "seq"))
        self._ensure_store()
        seq_key = f"_xz_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _save(self, table: str, record_id,
                    record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("xz", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("xz", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("xz", table, "*"))
            result = []
            for key in keys:
                if str(key).endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    # --------------------------------------------------------
    # 角色店铺(P6)
    # --------------------------------------------------------

    async def save_shop(self, shop: dict) -> dict:
        return await self._save(self.TABLE_SHOPS,
                                shop["shopId"], shop)

    async def get_shop(self, shop_id: int) -> dict | None:
        return await self._get(self.TABLE_SHOPS, shop_id)

    async def list_shops(self, status: str = None,
                         member_id: int = None,
                         limit: int = 200) -> list[dict]:
        """店铺查询(最新优先; 状态/会员过滤)"""
        result = []
        for s in await self._list(self.TABLE_SHOPS, limit * 10):
            if status and s.get("status") != status:
                continue
            if member_id is not None \
                    and s.get("memberId") != member_id:
                continue
            result.append(s)
        return sorted(result,
                      key=lambda x: -int(x.get("shopId") or 0)
                      )[:limit]

    async def find_shop_by_member(
            self, member_id: int,
            statuses: tuple = None) -> dict | None:
        """按会员查店铺(最新优先; statuses 过滤——一人一铺锚)"""
        for s in await self._list(self.TABLE_SHOPS, 5000):
            if s.get("memberId") != member_id:
                continue
            if statuses is not None \
                    and s.get("status") not in statuses:
                continue
            return s
        return None

    # --------------------------------------------------------
    # 店铺铺货(P7)
    # --------------------------------------------------------

    async def save_listing(self, listing: dict) -> dict:
        return await self._save(self.TABLE_LISTINGS,
                                listing["listingId"], listing)

    async def get_listing(self, listing_id: int) -> dict | None:
        return await self._get(self.TABLE_LISTINGS, listing_id)

    async def list_listings(self, shop_id: int = None,
                            member_id: int = None,
                            product_id: str = None,
                            status: str = None,
                            limit: int = 200) -> list[dict]:
        """铺货查询(最新优先; 店铺/会员/商品/状态过滤)"""
        result = []
        for l in await self._list(self.TABLE_LISTINGS,
                                  limit * 10):
            if shop_id is not None \
                    and l.get("shopId") != shop_id:
                continue
            if member_id is not None \
                    and l.get("memberId") != member_id:
                continue
            if product_id is not None \
                    and l.get("productId") != product_id:
                continue
            if status and l.get("status") != status:
                continue
            result.append(l)
        return sorted(result,
                      key=lambda x: -int(x.get("listingId") or 0)
                      )[:limit]

    async def find_listing_by_shop_product(
            self, shop_id: int, product_id: str,
            include_removed: bool = False) -> dict | None:
        """按店铺+商品查铺货(幂等锚——每店每商品一条)"""
        for l in await self._list(self.TABLE_LISTINGS, 5000):
            if l.get("shopId") != shop_id \
                    or l.get("productId") != product_id:
                continue
            if not include_removed \
                    and l.get("status") == "removed":
                continue
            return l
        return None


# ============================================================
# P6 角色店铺服务
# ============================================================

class XinzhiShopService:
    """68号 P6·角色店铺(准入门槛/AI 预审/九态状态机/雷达预警)"""

    def __init__(self, store: _XzStore = None):
        self.store = store if store is not None else _XzStore()

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    @staticmethod
    def _transition(shop: dict, target: str) -> None:
        """状态转移校验(转移表外一律 ValueError→409)"""
        current = shop.get("status")
        allowed = SHOP_STATUS_TRANSITIONS.get(current, ())
        if target not in allowed:
            raise ValueError(
                f"状态转移非法({current}→{target}, "
                f"允许:{'/'.join(allowed) or '终态'})")

    async def _current_radar(self, member_id: int) -> dict:
        """会员当前雷达(最新快照; 无快照即时计算——P0 口径)"""
        from services.xinzhi_radar_service import (
            XinzhiRadarService)
        return await XinzhiRadarService().get_radar(member_id)

    async def _grade_of_member(self, member_id: int,
                               radar_total: float) -> tuple:
        """店铺等级来源(P4 商家档案评级优先, 无档案回退雷达等级)

        Returns:
            (grade, source 说明)——grade 为 S/A/B/C/D
        """
        from repositories.xinzhi_repository import (
            XinzhiRepository)
        merchant = await XinzhiRepository(
        ).find_merchant_by_member(member_id)
        if merchant is not None:
            grade = str(merchant.get("grade") or "D")
            return grade, "商家档案评级(P4)"
        from services.xinzhi_radar_service import grade_of
        return grade_of(float(radar_total)), "雷达等级(无商家档案)"

    @staticmethod
    def _ai_pre_review(radar_total: float,
                       materials_complete: bool) -> str:
        """AI 预审三档(确定性规则, LLM 禁入)

        Returns:
            fast(≥80 快车道) / manual(60-79 人工) /
            reject(<60 或签名材料不全——仅标记, 拒绝须人工确认)
        """
        if not materials_complete:
            return "reject"
        if radar_total >= AI_FAST_LANE_SCORE:
            return "fast"
        if radar_total >= AI_MANUAL_SCORE:
            return "manual"
        return "reject"

    # --------------------------------------------------------
    # ① 开店申请(门槛校验 + AI 预审)
    # --------------------------------------------------------

    async def apply(self, member_id: int, shop_name: str,
                    category: str, intro: str) -> dict:
        """角色店铺申请(准入门槛→AI 预审三档→状态机流转)

        门槛(确定性, 与 37号差异化): 信值雷达总分≥60 + 会员等级
        ≥L3 + 一人一铺 + 商家评级非 D。

        Raises:
            KeyError: 会员不存在
            ValueError: 名称/类目/简介非法/门槛不达/重复申请/
                评级 D 不可开店
        """
        from repositories.member_repository import (
            MemberRepository)
        member = await MemberRepository().get_by_id(member_id)
        if member is None:
            raise KeyError(f"会员不存在(memberId={member_id})")
        name = (shop_name or "").strip()
        if not name:
            raise ValueError("店铺名称不能为空")
        if len(name) > SHOP_NAME_MAX:
            raise ValueError(
                f"店铺名称超限({len(name)}>{SHOP_NAME_MAX}字)")
        if category not in SHOP_CATEGORIES:
            raise ValueError(
                f"类目无效({category}, "
                f"须为{'/'.join(SHOP_CATEGORIES)})")
        intro = (intro or "").strip()
        if len(intro) > SHOP_INTRO_MAX:
            raise ValueError(
                f"店铺简介超限({len(intro)}>{SHOP_INTRO_MAX}字)")

        # 准入门槛①: 信值雷达总分
        radar = await self._current_radar(member_id)
        radar_total = float(radar.get("totalScore") or 0)
        if radar_total < SHOP_RADAR_MIN:
            raise ValueError(
                f"信值雷达总分不足({radar_total}<{SHOP_RADAR_MIN}, "
                "68号店铺准入须信值体系达标)")
        # 准入门槛②: 会员等级
        level = int(member.get("level") or 1)
        if level < SHOP_MEMBER_LEVEL_MIN:
            raise ValueError(
                f"会员等级不足(L{level}<L{SHOP_MEMBER_LEVEL_MIN})")
        # 准入门槛③: 一人一铺
        existing = await self.store.find_shop_by_member(
            member_id, statuses=SHOP_OCCUPYING_STATUSES)
        if existing is not None:
            raise ValueError(
                f"一人一铺: 会员已有在途或有效店铺(shopId="
                f"{existing.get('shopId')}, "
                f"状态{existing.get('status')})")
        # 准入门槛④: 商家评级 D 不可开店
        grade, grade_source = await self._grade_of_member(
            member_id, radar_total)
        if grade == "D" or grade not in SHOP_LEVELS:
            raise ValueError(
                f"商家评级 D 不可开店({grade_source}={grade}, "
                "店铺等级须 S/A/B/C)")

        materials_complete = bool(intro)
        shop_id = await self.store.next_id("shop")
        now = _now_iso()
        shop = {
            "shopId": shop_id,
            "memberId": member_id,
            "shopName": name,
            "category": category,
            "intro": intro,
            "status": SHOP_STATUS_PENDING,
            "shopLevel": SHOP_LEVELS[grade],
            "merchantGrade": grade,
            "gradeSource": grade_source,
            "radarTotal": round(radar_total, 1),
            "materialsComplete": materials_complete,
            "aiReview": {},
            "radarSnapshots": [],
            "warnings": [],
            "reviews": [],
            "probationStartedAt": "",
            "suspendedAt": "",
            "suspendedReason": "",
            "terminatedAt": "",
            "closedReason": "",
            "createdAt": now,
            "updatedAt": now,
        }

        # AI 预审(确定性三档): pending → ai_reviewing → 后续档位
        self._transition(shop, SHOP_STATUS_AI_REVIEWING)
        shop["status"] = SHOP_STATUS_AI_REVIEWING
        track = self._ai_pre_review(radar_total,
                                    materials_complete)
        shop["aiReview"] = {
            "score": round(radar_total, 1),
            "track": track,
            "materialsComplete": materials_complete,
            "checkedAt": now,
        }
        disposition = None
        if track == "fast":
            # 快车道: pending→signed 直接(正向激励自动生效留痕)
            self._transition(shop, SHOP_STATUS_SIGNED)
            shop["status"] = SHOP_STATUS_SIGNED
            disposition = {
                "kind": "ai_fast_lane",
                "track": "fast",
                "recommendation": "signed",
                "executed": True,
                "requiresAdmin": False,
                "note": (f"雷达总分{round(radar_total, 1)}"
                         f"≥{AI_FAST_LANE_SCORE} 快车道自动签约"
                         "(确定性规则留痕; 30 天观察期保障)"),
                "at": now,
            }
        elif track == "manual":
            self._transition(shop, SHOP_STATUS_MANUAL_REVIEWING)
            shop["status"] = SHOP_STATUS_MANUAL_REVIEWING
            disposition = {
                "kind": "ai_pre_review",
                "track": "manual",
                "recommendation": "manual_review",
                "executed": False,
                "requiresAdmin": True,
                "note": (f"雷达总分{round(radar_total, 1)}在"
                         f"{AI_MANUAL_SCORE}-{AI_FAST_LANE_SCORE - 1}"
                         " 分档, 转人工审核"),
                "at": now,
            }
        else:
            # 拒绝建议: 仅标记+建议书, 永不自动执行(宪法域)
            self._transition(shop, SHOP_STATUS_MANUAL_REVIEWING)
            shop["status"] = SHOP_STATUS_MANUAL_REVIEWING
            disposition = {
                "kind": "reject_proposal",
                "track": "reject",
                "recommendation": "reject",
                "executed": False,
                "requiresAdmin": True,
                "note": ("签名材料不全(店铺简介缺失)——AI 预审"
                         "拒绝建议, 永不自动执行, 须 admin 确认"),
                "at": now,
            }
        shop["updatedAt"] = now
        await self.store.save_shop(shop)
        logger.info("xz_p6_apply m=%s shop=%s track=%s "
                    "status=%s level=%s",
                    member_id, shop_id, track, shop["status"],
                    shop["shopLevel"])
        return {**shop, "disposition": disposition}

    # --------------------------------------------------------
    # ② 查询视图
    # --------------------------------------------------------

    async def get_my_shop(self, member_id: int) -> dict | None:
        """我的店铺(最新一条, 含全部内部字段)"""
        return await self.store.find_shop_by_member(member_id)

    async def get_shop(self, shop_id: int) -> dict:
        """店铺主页公开信息(零个体数据——不含 memberId/审核明细)

        Raises:
            KeyError: 店铺不存在
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        return {
            "shopId": shop.get("shopId"),
            "shopName": shop.get("shopName"),
            "category": shop.get("category"),
            "categoryLabel": SHOP_CATEGORY_LABELS.get(
                shop.get("category"), ""),
            "intro": shop.get("intro"),
            "status": shop.get("status"),
            "statusLabel": SHOP_STATUS_LABELS.get(
                shop.get("status"), ""),
            "shopLevel": shop.get("shopLevel"),
            "merchantGrade": shop.get("merchantGrade"),
            "radarTotal": shop.get("radarTotal"),
            "createdAt": shop.get("createdAt"),
        }

    async def list_shops(self, status: str = None,
                         limit: int = 200) -> list[dict]:
        """店铺列表(admin; 可按状态过滤)"""
        return await self.store.list_shops(status=status,
                                           limit=limit)

    def statuses(self) -> dict:
        """状态字典(九态+转移表+门槛——公开口径)"""
        return {
            "statuses": [
                {"code": s, "label": SHOP_STATUS_LABELS[s],
                 "allowedTransitions": list(
                     SHOP_STATUS_TRANSITIONS[s])}
                for s in SHOP_STATUSES],
            "categories": [
                {"code": c, "label": SHOP_CATEGORY_LABELS[c]}
                for c in SHOP_CATEGORIES],
            "shopLevels": dict(SHOP_LEVELS),
            "thresholds": {
                "radarMin": SHOP_RADAR_MIN,
                "memberLevelMin": SHOP_MEMBER_LEVEL_MIN,
                "aiFastLaneScore": AI_FAST_LANE_SCORE,
                "aiManualScore": AI_MANUAL_SCORE,
                "probationDays": PROBATION_DAYS,
                "radarWarnLine": RADAR_WARN_LINE,
            },
        }

    # --------------------------------------------------------
    # ③ 人工审核(admin——建议书模式)
    # --------------------------------------------------------

    async def review_shop(self, shop_id: int, approved: bool,
                          note: str = "",
                          reviewer: str = "admin") -> dict:
        """店铺人工审核(manual_reviewing→signed/rejected)

        审核结果带 disposition(人工执行留痕, 非系统自动)。

        Raises:
            KeyError: 店铺不存在
            ValueError: 状态非法(非人工审核中)
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        target = (SHOP_STATUS_SIGNED if approved
                  else SHOP_STATUS_REJECTED)
        self._transition(shop, target)
        now = _now_iso()
        decision = "approve" if approved else "reject"
        review = {"reviewer": reviewer, "decision": decision,
                  "note": (note or "")[:200], "at": now}
        shop["reviews"] = list(shop.get("reviews") or [])
        shop["reviews"].append(review)
        shop["status"] = target
        shop["updatedAt"] = now
        await self.store.save_shop(shop)
        disposition = {
            "kind": "shop_review",
            "decision": decision,
            "executed": True,
            "executedBy": reviewer,
            "note": review["note"] or (
                "人工审核通过, 转签约" if approved
                else "人工审核拒绝(终态)"),
            "at": now,
        }
        logger.info("xz_p6_review shop=%s %s by=%s",
                    shop_id, decision, reviewer)
        return {**shop, "disposition": disposition}

    # --------------------------------------------------------
    # ④ 暂停/恢复/激活/关店
    # --------------------------------------------------------

    async def suspend_shop(self, shop_id: int, reason: str = "",
                           operator: str = "admin") -> dict:
        """暂停整改(admin; active/probation→suspended)

        处罚类操作——admin 确认后执行, disposition 留痕。

        Raises:
            KeyError: 店铺不存在
            ValueError: 状态非法
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        self._transition(shop, SHOP_STATUS_SUSPENDED)
        now = _now_iso()
        shop["status"] = SHOP_STATUS_SUSPENDED
        shop["suspendedAt"] = now
        shop["suspendedReason"] = (reason or "")[:200]
        shop["updatedAt"] = now
        await self.store.save_shop(shop)
        disposition = {
            "kind": "shop_suspend",
            "decision": "suspend",
            "executed": True,
            "executedBy": operator,
            "note": shop["suspendedReason"] or "暂停整改(admin 确认)",
            "at": now,
        }
        logger.warning("xz_p6_suspend shop=%s by=%s reason=%s",
                       shop_id, operator, reason)
        return {**shop, "disposition": disposition}

    async def activate_shop(self, shop_id: int,
                            operator: str = "admin") -> dict:
        """激活/恢复(admin; 三路径: 签约激活/观察期满转正/解除暂停)

        - signed→probation: 进入 30 天观察期
        - probation→active: 观察期满 30 天转正(确定性时间校验)
        - suspended→active: 解除暂停

        Raises:
            KeyError: 店铺不存在
            ValueError: 状态非法/观察期未满
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        now = _now_iso()
        status = shop.get("status")
        if status == SHOP_STATUS_SIGNED:
            self._transition(shop, SHOP_STATUS_PROBATION)
            shop["status"] = SHOP_STATUS_PROBATION
            shop["probationStartedAt"] = now
            action = "probation_started"
            note = f"签约激活, 进入{PROBATION_DAYS}天观察期"
        elif status == SHOP_STATUS_PROBATION:
            started = str(shop.get("probationStartedAt") or "")
            days = self._days_since(started)
            if days < PROBATION_DAYS:
                raise ValueError(
                    f"观察期未满({days:.1f}/{PROBATION_DAYS}天, "
                    f"始于{started[:10]})")
            self._transition(shop, SHOP_STATUS_ACTIVE)
            shop["status"] = SHOP_STATUS_ACTIVE
            action = "activated"
            note = f"观察期满({days:.1f}天), 转正式营业"
        elif status == SHOP_STATUS_SUSPENDED:
            self._transition(shop, SHOP_STATUS_ACTIVE)
            shop["status"] = SHOP_STATUS_ACTIVE
            action = "resumed"
            note = "解除暂停, 恢复营业"
        else:
            raise ValueError(
                f"状态转移非法({status}→active, "
                "仅 signed/probation/suspended 可激活)")
        shop["updatedAt"] = now
        await self.store.save_shop(shop)
        logger.info("xz_p6_activate shop=%s action=%s by=%s",
                    shop_id, action, operator)
        return {**shop, "action": action, "note": note}

    @staticmethod
    def _days_since(iso: str) -> float:
        """距今天数(空/非法→大数)"""
        if not (iso or "").strip():
            return 999.0
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return max(0.0, (
                datetime.now(UTC) - dt).total_seconds() / 86400.0)
        except (TypeError, ValueError):
            return 999.0

    async def close_shop(self, shop_id: int, member_id: int,
                         reason: str = "") -> dict:
        """商家自关店(仅 active→terminated——任务书"商家自关, 仅
        active"口径; 签约/观察/暂停期的退出须走 admin 终止轨)

        Raises:
            KeyError: 店铺不存在
            PermissionError: 非本店资源
            ValueError: 状态非法(仅营业中可自关)
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        if shop.get("memberId") != member_id:
            raise PermissionError(
                f"非本店资源(shopId={shop_id} 归属会员"
                f"{shop.get('memberId')})")
        if shop.get("status") != SHOP_STATUS_ACTIVE:
            raise ValueError(
                f"仅营业中店铺可自关(当前{shop.get('status')}, "
                "签约/观察/暂停期退出须管理员终止轨)")
        self._transition(shop, SHOP_STATUS_TERMINATED)
        now = _now_iso()
        shop["status"] = SHOP_STATUS_TERMINATED
        shop["terminatedAt"] = now
        shop["closedReason"] = (reason or "")[:200]
        shop["updatedAt"] = now
        await self.store.save_shop(shop)
        logger.info("xz_p6_close shop=%s by=m%s",
                    shop_id, member_id)
        return shop

    # --------------------------------------------------------
    # ⑤ 雷达快照与预警建议书(永不自动 suspend)
    # --------------------------------------------------------

    async def radar_check(self, shop_id: int) -> dict:
        """店铺雷达定期快照 + 跌破预警(建议书, 永不自动执行)

        每次调用记录一条快照(90 条滚动); 雷达分跌破 40 → 生成
        预警建议书(proposedAction=suspend, executed=False)——
        店铺状态不变, 须 admin 确认后经 suspend 端点执行。

        Raises:
            KeyError: 店铺不存在
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        radar = await self._current_radar(shop.get("memberId"))
        score = round(float(radar.get("totalScore") or 0), 1)
        grade = str(radar.get("grade") or "")
        now = _now_iso()
        snapshots = list(shop.get("radarSnapshots") or [])
        snapshots.append({"score": score, "grade": grade,
                          "at": now})
        shop["radarSnapshots"] = snapshots[-RADAR_SNAPSHOT_KEEP:]
        warning = None
        if score < RADAR_WARN_LINE:
            warning_id = await self.store.next_id("warning")
            warning = {
                "kind": "radar_warning",
                "warningId": warning_id,
                "proposedAction": "suspend",
                "executed": False,
                "requiresAdmin": True,
                "score": score,
                "line": RADAR_WARN_LINE,
                "note": (f"雷达分跌破{RADAR_WARN_LINE}"
                         f"(当前{score})——建议暂停整改; "
                         "预警永不自动执行, 须 admin 确认"),
                "at": now,
            }
            warnings = list(shop.get("warnings") or [])
            warnings.append(warning)
            shop["warnings"] = warnings
            logger.warning("xz_p6_radar_warn shop=%s score=%s",
                           shop_id, score)
        shop["updatedAt"] = now
        await self.store.save_shop(shop)
        return {
            "shopId": shop_id,
            "status": shop.get("status"),
            "current": {"score": score, "grade": grade},
            "snapshots": shop["radarSnapshots"],
            "warnings": shop.get("warnings") or [],
            "warning": warning,
            "warnLine": RADAR_WARN_LINE,
        }
