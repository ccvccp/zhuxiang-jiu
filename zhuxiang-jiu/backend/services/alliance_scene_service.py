"""37号·AI智能网站同盟模块·P2 场景服务与线下核销

核心职责(设计文档 §2.7):
    - 酒友小聚编排: 选酒(自营/同盟)→配菜(私厨)→订境(酒店/会所)
      → 一单三子单合并 → 线下核销码 → 核销完成分润起算
    - 定制服务: 需求单状态机 demand→quoted→confirmed→producing
      →delivered(酒具刻字/私宴/封坛)
    - hub 能力注册(P2 流量统筹): "同盟场景"意图入口

对接:
    - alliance_service: place_order/settle_order(子单复用交易分润链)
    - alliance_geo_service: 订境/配菜商户就近推荐

异常约定: KeyError → 404 / ValueError → 409
"""

import logging
import secrets
import string
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.alliance_repository import (
    AllianceRepository,
    CATEGORY_WINE, CATEGORY_DISH, CATEGORY_VENUE, CATEGORY_VESSEL,
    PRODUCT_STATUS_ACTIVE,
    SCENE_ITEM_WINE, SCENE_ITEM_DISH, SCENE_ITEM_VENUE,
    SCENE_STATUS_CREATED, SCENE_STATUS_REDEEMED,
    REDEEM_CODE_TTL_HOURS,
    CUSTOM_STATUS_DEMAND, CUSTOM_STATUS_QUOTED,
    CUSTOM_STATUS_CONFIRMED, CUSTOM_STATUS_PRODUCING,
    CUSTOM_STATUS_DELIVERED, CUSTOM_STATUS_CANCELLED,
    CUSTOM_TRANSITIONS,
    STATUS_ACTIVE,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AllianceSceneService:
    """酒友小聚场景编排 + 线下核销 + 定制服务"""

    def __init__(self, repo: AllianceRepository = AllianceRepository()):
        self.repo = repo

    # ============================================================
    # 1. 酒友小聚编排出单(一单三子单)
    # ============================================================

    async def create_gathering(self, user_id: int, party_size: int,
                               wine_product_id: int,
                               dish_merchant_id: int,
                               venue_merchant_id: int,
                               gathering_time: str = "") -> dict:
        """酒友小聚: 选酒→配菜→订境 合并出单("好酒配好菜配好境")

        编排规则(设计文档 §2.7):
            - 酒: 自营或同盟 wine 类目商品(直接 productId)
            - 菜: dish 类目商户按人数配单(P0 取该商户在售商品组合)
            - 境: venue 类目商户时段包间(P0 以商户商品承载)
            - 三个子单各自走 place_order 交易链(分润独立),
              合并单 sceneId 统一编排 + 单一核销码线下核销

        Raises:
            ValueError: 人数非法/类目不匹配
            KeyError: 商品/商户不存在
        """
        if party_size < 1 or party_size > 50:
            raise ValueError("聚会人数须为 1-50")
        from services.alliance_service import AllianceService
        svc = AllianceService(repo=self.repo)

        # 校验三类主体类目
        dish_merchant = await svc.get_merchant(dish_merchant_id)
        venue_merchant = await svc.get_merchant(venue_merchant_id)
        if dish_merchant["category"] != CATEGORY_DISH:
            raise ValueError("配菜商户须为好菜类目(alliance_dish)")
        if venue_merchant["category"] != CATEGORY_VENUE:
            raise ValueError("订境商户须为好境类目(alliance_venue)")

        # 子单①: 酒(自营商品走同盟交易口径: 须同盟在售商品)
        wine_order = await svc.place_order(wine_product_id, user_id,
                                           quantity=1)
        # 子单②: 菜(取配菜商户在售商品, 按人数配量: P0 每人一份)
        dish_products = await self.repo.list_products(
            merchant_id=dish_merchant_id, status=PRODUCT_STATUS_ACTIVE)
        if not dish_products:
            raise ValueError("配菜商户无在售商品")
        dish_product = dish_products[0]
        dish_order = await svc.place_order(
            dish_product["productId"], user_id, quantity=party_size)
        # 子单③: 境(取订境商户在售商品, 按场次 1 份)
        venue_products = await self.repo.list_products(
            merchant_id=venue_merchant_id, status=PRODUCT_STATUS_ACTIVE)
        if not venue_products:
            raise ValueError("订境商户无在售商品(包间/场次)")
        venue_order = await svc.place_order(
            venue_products[0]["productId"], user_id, quantity=1)

        scene_id = await self.repo.next_id("scene")
        total = round(wine_order["amount"] + dish_order["amount"]
                      + venue_order["amount"], 2)
        scene = {
            "sceneId": scene_id,
            "type": "gathering",
            "userId": user_id,
            "partySize": party_size,
            "gatheringTime": gathering_time,
            "items": [
                {"type": SCENE_ITEM_WINE, "orderId": wine_order["orderId"],
                 "productId": wine_product_id,
                 "amount": wine_order["amount"]},
                {"type": SCENE_ITEM_DISH, "orderId": dish_order["orderId"],
                 "productId": dish_product["productId"],
                 "amount": dish_order["amount"]},
                {"type": SCENE_ITEM_VENUE, "orderId": venue_order["orderId"],
                 "productId": venue_products[0]["productId"],
                 "amount": venue_order["amount"]},
            ],
            "totalAmount": total,
            "status": SCENE_STATUS_CREATED,
            "redeemCode": "",
            "redeemedAt": "",
            "createdAt": _now_iso(),
        }
        scene = await self.repo.save_scene(scene)
        # 生成核销码(TTL 72h)
        scene["redeemCode"] = self._generate_redeem_code(scene_id)
        return await self.repo.save_scene(scene)

    @staticmethod
    def _generate_redeem_code(scene_id: int) -> str:
        alphabet = string.ascii_uppercase + string.digits
        suffix = "".join(secrets.choice(alphabet) for _ in range(8))
        return f"RP-{scene_id:05d}-{suffix}"

    # ============================================================
    # 2. 线下核销
    # ============================================================

    async def redeem(self, code: str) -> dict:
        """线下核销(到店扫码; 幂等 + TTL 72h)

        核销动作: 场景单 → redeemed; 三个子单立即触发结算分润
        (线下履约完成 = 分润起算点, 设计文档 §2.7)。

        Raises:
            KeyError: 核销码不存在
            ValueError: 已核销/已过期
        """
        async with get_lock(f"alliance:redeem:{code}"):
            record = await self.repo.get_redeem_code(code)
            if record is None:
                # 兼容: 核销码记录未落 redeem 表时回查场景单
                scene = await self._find_scene_by_code(code)
                if scene is None:
                    raise KeyError(f"核销码不存在(code={code})")
                record = {"code": code, "sceneId": scene["sceneId"],
                          "expiresAt": (datetime.now(UTC) + timedelta(
                              hours=REDEEM_CODE_TTL_HOURS)).isoformat(),
                          "redeemed": False}
            if record.get("redeemed"):
                raise ValueError("核销码已使用(幂等)")
            expires_at = record.get("expiresAt", "")
            if expires_at and expires_at < _now_iso():
                raise ValueError(f"核销码已过期({expires_at[:19]})")
            scene = await self.repo.get_scene(record["sceneId"])
            if scene is None:
                raise KeyError(f"场景单不存在(sceneId={record['sceneId']})")
            if scene["status"] != SCENE_STATUS_CREATED:
                raise ValueError(
                    f"场景单状态非法(当前{scene['status']})")

            # 三子单立即结算(分润起算)
            from services.alliance_service import AllianceService
            svc = AllianceService(repo=self.repo)
            settlements = []
            for item in scene["items"]:
                try:
                    settlements.append(await svc.settle_order(
                        item["orderId"]))
                except (KeyError, ValueError) as exc:
                    # 已结算等幂等冲突不阻断核销
                    logger.info("alliance_scene_settle_skip order=%s: %s",
                                item["orderId"], exc)

            scene.update({"status": SCENE_STATUS_REDEEMED,
                          "redeemedAt": _now_iso()})
            await self.repo.save_scene(scene)
            record.update({"redeemed": True, "redeemedAt": _now_iso()})
            await self.repo.save_redeem_code(code, record)
            logger.info("alliance_scene_redeemed scene=%s code=%s",
                        scene["sceneId"], code)
            return {"scene": scene, "settlements": settlements}

    async def _find_scene_by_code(self, code: str) -> dict | None:
        scenes = await self.repo.list_scenes(limit=1000)
        for scene in scenes:
            if scene.get("redeemCode") == code:
                return scene
        return None

    async def list_scenes(self, user_id: int = None,
                          status: str = None) -> list[dict]:
        return await self.repo.list_scenes(user_id=user_id, status=status)

    async def get_scene(self, scene_id: int) -> dict:
        scene = await self.repo.get_scene(scene_id)
        if scene is None:
            raise KeyError(f"场景单不存在(sceneId={scene_id})")
        return scene

    # ============================================================
    # 3. 定制服务状态机(demand→quoted→confirmed→producing→delivered)
    # ============================================================

    async def create_custom_demand(self, user_id: int, merchant_id: int,
                                   demand_type: str, description: str,
                                   budget: float = 0.0) -> dict:
        """提交定制需求(酒具刻字/私宴定制/封坛定制)

        Raises:
            KeyError: 商户不存在
            ValueError: 需求类型非法/描述为空/商户非在营
        """
        from services.alliance_service import AllianceService
        merchant = await AllianceService(
            repo=self.repo).get_merchant(merchant_id)
        if merchant["status"] not in (STATUS_ACTIVE, "probation"):
            raise ValueError(f"商户非在营状态({merchant['status']})")
        # P0: 定制类型限定酒具/私宴/封坛(设计文档 §2.7)
        allowed_types = ("engraving", "private_feast", "sealing")
        if demand_type not in allowed_types:
            raise ValueError(
                f"定制类型无效({demand_type}, 须为{'/'.join(allowed_types)})")
        if not (description or "").strip():
            raise ValueError("定制需求描述不能为空")
        demand_id = await self.repo.next_id("custom")
        demand = {
            "demandId": demand_id,
            "userId": user_id,
            "merchantId": merchant_id,
            "demandType": demand_type,
            "description": description.strip()[:1000],
            "budget": round(float(budget or 0), 2),
            "quotedPrice": 0.0,
            "status": CUSTOM_STATUS_DEMAND,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        return await self.repo.save_custom_demand(demand)

    async def quote_custom_demand(self, demand_id: int,
                                  quoted_price: float) -> dict:
        """商户报价(demand→quoted)

        Raises:
            KeyError: 需求不存在 / ValueError: 状态非法/价格非法
        """
        demand = await self._require_demand(demand_id)
        self._transition(demand, CUSTOM_STATUS_QUOTED)
        if quoted_price <= 0:
            raise ValueError("报价必须大于 0")
        demand.update({"status": CUSTOM_STATUS_QUOTED,
                       "quotedPrice": round(float(quoted_price), 2),
                       "updatedAt": _now_iso()})
        return await self.repo.save_custom_demand(demand)

    async def confirm_custom_demand(self, demand_id: int,
                                    user_id: int) -> dict:
        """用户确认报价(quoted→confirmed; 须本人)

        Raises:
            KeyError: 需求不存在 / ValueError: 状态非法/非本人
        """
        demand = await self._require_demand(demand_id)
        if demand.get("userId") != user_id:
            raise ValueError("仅需求提交人可确认")
        self._transition(demand, CUSTOM_STATUS_CONFIRMED)
        demand.update({"status": CUSTOM_STATUS_CONFIRMED,
                       "updatedAt": _now_iso()})
        return await self.repo.save_custom_demand(demand)

    async def advance_custom_demand(self, demand_id: int,
                                    target: str) -> dict:
        """推进定制(producing/delivered/cancelled; 商户/管理侧)

        Raises:
            KeyError: 需求不存在 / ValueError: 状态转移非法
        """
        demand = await self._require_demand(demand_id)
        self._transition(demand, target)
        demand.update({"status": target, "updatedAt": _now_iso()})
        return await self.repo.save_custom_demand(demand)

    async def _require_demand(self, demand_id: int) -> dict:
        demand = await self.repo.get_custom_demand(demand_id)
        if demand is None:
            raise KeyError(f"定制需求不存在(demandId={demand_id})")
        return demand

    @staticmethod
    def _transition(demand: dict, target: str) -> None:
        allowed = CUSTOM_TRANSITIONS.get(demand["status"], ())
        if target not in allowed:
            raise ValueError(
                f"定制状态转移非法({demand['status']}→{target}, "
                f"允许:{'/'.join(allowed) or '终态'})")

    async def list_custom_demands(self, merchant_id: int = None,
                                  user_id: int = None,
                                  status: str = None) -> list[dict]:
        return await self.repo.list_custom_demands(
            merchant_id=merchant_id, user_id=user_id, status=status)
