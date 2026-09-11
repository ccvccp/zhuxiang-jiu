"""68号 P3·信值·臻选——互助购物生态服务

依据:《信值·臻选大模型》文档"邻里臻选/15分钟信值生活圈"+
《68号 创新规划方案》§三 P3/§六差异化护城河

核心命题(文档差异化最大点——巨头无此资产):
    "购物+互助一体: 买酒可叫邻里代驾/求购可唤邻里互助/
     消费累积碳积分"——67号 LBS 互助大厅零成本联动。

三大能力(全确定性, LLM 禁入):
    ① 邻里臻选频道: 同城高信值用户购买聚合展示
       ——零个体数据红线(P6f-3 口径): 仅平台×品类×漏斗
       聚合, 个体购买永不暴露
    ② 邻里求购: 需求发布→邻里响应(67号范式复用:
       三单上限/紧急→距离→新单排序)
    ③ 碳积分联动: 互助购物碳折算(67号口径只读引用:
       公益单 100%/有偿 50%/接力均摊, 不可交易)

红线(宪法域):
    - 邻里频道零个体数据: 聚合粒度=品类×城市; 单品类
      消费者 <K 匿名门槛不展示(防群体反推个体)
    - 求购响应仅计数+昵称脱敏: 不留个人联系方式
    - LLM 禁入: 聚合=统计公式/排序=加权/折算=系数
"""

import logging
from datetime import datetime, UTC

from repositories.location_repository import (
    haversine_km,
)
from repositories.xinzhi_repository import (
    XinzhiRepository,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xinzhi-neighbor"

# ============================================================
# P3 常量(文档口径)
# ============================================================

# 求购在途上限(67号叫帮同款——每人待响应最多 3 单)
GROUPBUY_PUBLISHING_LIMIT = 3

# 求购响应半径(公里——67号 LBS 大厅 20km 同口径)
GROUPBUY_RADIUS_KM = 20.0

# 求购排序半径分档(距离加分用)
NEAR_KM = 5.0

# 匿名门槛(零个体数据红线: 单品类聚合人数 <5 不展示)
ANONYMITY_K = 5

# 状态机(67号求购域: published → responded → closed)
GB_STATUS_PUBLISHED = "published"
GB_STATUS_RESPONDED = "responded"
GB_STATUS_CLOSED = "closed"
GB_STATUS_CANCELLED = "cancelled"
GB_STATUSES = (GB_STATUS_PUBLISHED, GB_STATUS_RESPONDED,
               GB_STATUS_CLOSED, GB_STATUS_CANCELLED)
GB_STATUS_NAMES = {
    GB_STATUS_PUBLISHED: "待响应",
    GB_STATUS_RESPONDED: "已有人响应",
    GB_STATUS_CLOSED: "已解决",
    GB_STATUS_CANCELLED: "已取消",
}

# 互助购物碳积分折算(克/单——文档"消费累积碳积分";
# 邻里同单聚合配送的环境正外部性, 确定性系数)
CARBON_GRAMS_PER_GROUPBUY = 500.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mask_name(member_id: int) -> str:
    """发起人昵称脱敏(邻里频道零个体数据红线)"""
    return f"邻里{str(member_id)[-2:]}**"


class XinzhiNeighborService:
    """68号 P3·互助购物生态(邻里臻选+求购+碳积分)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # ① 邻里臻选频道(零个体数据聚合)
    # ============================================================

    async def neighbor_shelf(self,
                            city: str = None) -> dict:
        """邻里臻选频道(同城市民在买什么——品类聚合)

        口径(P6f-3 白皮书同款):
            - 聚合源: 订单库(items→series 计数)
            - 匿名门槛: 单品类消费者 <5 人不展示
            - 零个体: 无会员 ID/无昵称/无个体金额

        Args:
            city: 城市过滤(空=全站)
        """
        from repositories.order_repository import (
            OrderRepository)
        orders = await OrderRepository().list_all()
        # 品类计数(下单人数+单量——聚合层唯一口径)
        series_buyers: dict = {}
        series_orders: dict = {}
        for o in orders:
            addr = o.get("address") or {}
            if city and str(addr.get("city") or "") != city:
                continue
            member = o.get("memberId")
            for it in (o.get("items") or []):
                pid = it.get("productId")
                series = await self._series_of(pid)
                if series is None:
                    continue
                series_buyers.setdefault(series, set()).add(
                    member)
                series_orders[series] = \
                    series_orders.get(series, 0) + 1
        # 匿名门槛过滤(K 人以下不展示——零个体数据红线)
        rows = []
        for series, buyers in series_buyers.items():
            if len(buyers) < ANONYMITY_K:
                continue
            rows.append({
                "series": series,
                "buyerCount": len(buyers),
                "orderCount": series_orders.get(series, 0),
            })
        rows.sort(key=lambda x: (-x["buyerCount"],
                                -x["orderCount"]))
        return {
            "city": city or "全站",
            "categories": rows[:20],
            "anonymityK": ANONYMITY_K,
            "scope": ("平台×品类聚合口径, 零个体数据"
                      "(<K 人品类不展示)"),
            "modelVersion": MODEL_VERSION,
        }

    async def _series_of(self, product_id: str):
        """商品→系列(品类)映射(查询层直出)"""
        from repositories.product_repository import (
            ProductRepository)
        p = await ProductRepository().get_by_id(product_id)
        return (p or {}).get("series")

    # ============================================================
    # ② 邻里求购(67号叫帮范式复用)
    # ============================================================

    async def publish_groupbuy(self, member_id: int,
                              title: str,
                              product_id: str = "",
                              quantity: int = 1,
                              urgency: str = "normal",
                              longitude: float = 0.0,
                              latitude: float = 0.0,
                              address: str = "") -> dict:
        """发布邻里求购(三单上限+安全预检——67号范式)

        Raises:
            KeyError: 会员不存在
            ValueError: 参数非法/在途超限/违禁词
        """
        from repositories.member_repository import (
            MemberRepository)
        member = await MemberRepository().get_by_id(member_id)
        if member is None:
            raise KeyError(f"会员不存在(memberId={member_id})")
        if not (title or "").strip():
            raise ValueError("求购标题不能为空")
        if quantity < 1 or quantity > 99:
            raise ValueError("数量须为 1-99")
        if urgency not in ("normal", "urgent"):
            raise ValueError("紧急度非法(normal/urgent)")
        # 违禁词预检(67号 BANNED_KEYWORDS 复用)
        from services.help_service import BANNED_KEYWORDS
        banned = next((w for w in BANNED_KEYWORDS
                       if w in title), None)
        if banned:
            raise ValueError(f"求购含违禁内容({banned})")
        # 在途上限(67号三单上限同款)
        publishing = await self.repo.list_groupbuys(
            status=GB_STATUS_PUBLISHED,
            member_id=member_id, limit=100)
        if len(publishing) >= GROUPBUY_PUBLISHING_LIMIT:
            raise ValueError(
                "待响应求购已达上限(3 单), "
                "请等待邻里响应后再发布")
        # 商品联动(可选: 指定商品求购)
        series = ""
        if product_id:
            series = (await self._series_of(product_id)
                      or "")
        gb_id = await self.repo.next_id("groupbuy")
        record = {
            "groupbuyId": gb_id,
            "memberId": member_id,
            "publisherMasked": _mask_name(member_id),
            "title": title.strip()[:60],
            "productId": product_id,
            "series": series,
            "quantity": int(quantity),
            "urgency": urgency,
            "longitude": float(longitude or 0),
            "latitude": float(latitude or 0),
            "address": (address or "").strip()[:100],
            "status": GB_STATUS_PUBLISHED,
            "responderCount": 0,
            "responders": [],   # 仅脱敏昵称计数(红线)
            "closed": False,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        await self.repo.save_groupbuy(record)
        logger.info("xinzhi_p3_groupbuy_pub id=%s m=%s",
                    gb_id, member_id)
        return record

    async def groupbuy_hall(self, longitude: float,
                            latitude: float,
                            limit: int = 50) -> list[dict]:
        """求购大厅(紧急→距离→新单——67号排序范式)"""
        orders = await self.repo.list_groupbuys(
            status=GB_STATUS_PUBLISHED, limit=500)
        out = []
        for o in orders:
            dist = haversine_km(
                longitude, latitude,
                o.get("longitude", 0), o.get("latitude", 0))
            if dist > GROUPBUY_RADIUS_KM:
                continue
            item = dict(o)
            item["distanceKm"] = round(dist, 2)
            out.append(item)
        # 排序(67号范式: 新单→距离→紧急最高优先)
        out.sort(key=lambda o: o.get("createdAt", ""),
                 reverse=True)
        out.sort(key=lambda o: o.get("distanceKm", 0))
        out.sort(key=lambda o: (
            0 if o.get("urgency") == "urgent" else 1,))
        return out[:limit]

    async def respond_groupbuy(self, groupbuy_id: int,
                              responder_id: int) -> dict:
        """邻里响应求购(published → responded; 仅计数)

        红线: 响应不留个人明细——responderCount+1 与
        脱敏昵称; 发起人不得响应自己的求购。

        Raises:
            KeyError: 求购不存在
            ValueError: 状态非法/自响应
        """
        gb = await self.repo.get_groupbuy(groupbuy_id)
        if gb is None:
            raise KeyError(
                f"求购不存在(groupbuyId={groupbuy_id})")
        if gb.get("closed"):
            raise ValueError("求购已关闭, 不可响应")
        if gb.get("memberId") == responder_id:
            raise ValueError("不能响应自己发布的求购")
        responders = list(gb.get("responders") or [])
        responders.append(_mask_name(responder_id))
        fields = {
            "responderCount": int(
                gb.get("responderCount") or 0) + 1,
            "responders": responders,
            "status": GB_STATUS_RESPONDED,
            "updatedAt": _now_iso(),
        }
        return await self.repo.update_groupbuy(groupbuy_id,
                                              fields)

    async def close_groupbuy(self, groupbuy_id: int,
                            operator_id: int,
                            carbon_grams: float = None
                            ) -> dict:
        """关闭求购(发起人确认解决; 67号 cancel 范式)

        碳积分联动: 关闭时按响应人数折算邻里聚合配送
        碳减排(观测口径——不可交易, 67号宪法同款)。

        Raises:
            KeyError: 求购不存在
            ValueError: 非发起人/已关闭
        """
        gb = await self.repo.get_groupbuy(groupbuy_id)
        if gb is None:
            raise KeyError(
                f"求购不存在(groupbuyId={groupbuy_id})")
        if gb.get("closed"):
            raise ValueError("求购已关闭")
        if gb.get("memberId") != operator_id:
            raise ValueError("仅发起人可关闭求购")
        # 碳折算(确定性: 单笔基准 × 响应人数——邻里
        # 拼单的环境正外部性; 观测留痕不可交易)
        if carbon_grams is None:
            n = int(gb.get("responderCount") or 0)
            carbon_grams = round(
                CARBON_GRAMS_PER_GROUPBUY
                * (1 + n), 1)
        fields = {
            "closed": True,
            "status": GB_STATUS_CLOSED,
            "carbonGrams": round(float(carbon_grams), 1),
            "closedAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        result = await self.repo.update_groupbuy(
            groupbuy_id, fields)
        logger.info("xinzhi_p3_groupbuy_closed id=%s "
                    "carbon=%sg",
                    groupbuy_id, fields["carbonGrams"])
        return result

    # ============================================================
    # ③ 碳积分联动(67号只读引用+互助购物增量)
    # ============================================================

    async def carbon_profile(self, member_id: int) -> dict:
        """邻里购物碳档案(67号互助碳+68号求购碳合并视图)

        口径(67号 carbon_profile 只读引用——不重复计算):
            互助碳: 时长×类别系数(公益100%/有偿50%)
            求购碳: 关闭的求购单碳折算(发起人名下)
        不可交易(宪法口径防金融化)。
        """
        from services.help_service import HelpService
        help_carbon = await HelpService().carbon_profile(
            member_id)
        gb_carbon = 0.0
        gb_count = 0
        for gb in await self.repo.list_groupbuys(
                member_id=member_id, limit=1000):
            if gb.get("closed"):
                gb_carbon += float(gb.get("carbonGrams")
                                   or 0)
                gb_count += 1
        total = float(help_carbon["carbonGrams"]) \
            + gb_carbon
        return {
            "memberId": member_id,
            "carbonGrams": round(total, 1),
            "carbonKg": round(total / 1000, 2),
            "helpOrders": help_carbon["completedOrders"],
            "helpCarbonGrams":
                help_carbon["carbonGrams"],
            "groupbuys": gb_count,
            "groupbuyCarbonGrams": round(gb_carbon, 1),
            "methodology": ("67号互助碳(时长×类别×"
                            "公益100%/有偿50%/接力均摊)"
                            "+68号求购碳(500g×参与人数); "
                            "只读观测不可交易"),
        }
