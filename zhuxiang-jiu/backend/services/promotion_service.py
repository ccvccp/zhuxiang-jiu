"""推广码矩阵获利模块业务逻辑层

核心规则(参数可在管理端动态调整):
    一级(直推奖): 每直接推广满 level1Threshold(默认10)人
        → 发放 level1RewardAmount(默认¥20)钱包奖励余额(可叠加, 仅可购物不可提现)
    二级(裂变奖): 直推下线中每有 level2SubPromoterCount(默认6)人
        各自完成推广 level2SubThreshold(默认5)人
        → 发放 level2RewardAmount(默认¥15)钱包奖励余额(仅可购物不可提现)

防刷: 一人仅可绑定一次 / 禁自绑 / 祖先链防环 / 撤销码失效 / 无效关系不计业绩
"""

import logging
import secrets

from core.locks import get_lock
from repositories.promotion_repository import (
    PromotionRepository, CODE_PREFIX, CHANNELS,
)
from repositories.member_repository import MemberRepository
from repositories.product_repository import ProductRepository
from services.wallet_service import WalletService
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


class PromotionService:
    """推广码矩阵获利模块业务逻辑层"""

    def __init__(self, store: dict = None):
        self.promo_repo = PromotionRepository(store)
        self.member_repo = MemberRepository(store)
        self.product_repo = ProductRepository(store)
        # WalletService 首参是 wallet_repo 而非 store, 需显式构造注入
        from repositories.wallet_repository import WalletRepository
        self.wallet_service = WalletService(
            wallet_repo=WalletRepository(store),
            member_repo=self.member_repo,
        )

    # ============================================================
    # 用户端: 专属推广码
    # ============================================================

    async def claim_promo_code(self, member_id: int, channel: str) -> dict:
        """领取专属推广码(同渠道幂等: 已有生效码直接返回)

        推广码带竹奕品牌标识 ZXBJ 前缀, 可投放于微信小程序/抖音/快手等成熟平台。

        Raises:
            KeyError: 会员不存在
            ValueError: 渠道非法
        """
        if channel not in CHANNELS:
            raise ValueError(f"渠道非法: {channel}, 可选: {', '.join(CHANNELS)}")
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")

        async with get_lock(f"promotion:code:{member_id}:{channel}"):
            existing = await self.promo_repo.find_active_code(member_id, channel)
            if existing:
                return {
                    "success": True,
                    "code": existing["code"],
                    "channel": channel,
                    "boundCount": existing.get("boundCount", 0),
                    "shareTip": self._share_tip(channel, existing["code"]),
                    "reclaimed": True,
                }
            code = self._generate_code()
            record = {
                "code": code,
                "ownerMemberId": member_id,
                "channel": channel,
                "status": "active",
                "boundCount": 0,
                "createdAt": self._now(),
                "updatedAt": self._now(),
            }
            await self.promo_repo.save_code(record)
            logger.info("promo_code_claimed member=%s channel=%s code=%s",
                        member_id, channel, code)
            return {
                "success": True,
                "code": code,
                "channel": channel,
                "boundCount": 0,
                "shareTip": self._share_tip(channel, code),
                "reclaimed": False,
            }

    async def list_my_codes(self, member_id: int) -> list[dict]:
        """我的推广码列表(附分享文案)"""
        codes = await self.promo_repo.list_codes_by_owner(member_id)
        for c in codes:
            c["shareTip"] = self._share_tip(c.get("channel", "direct"),
                                            c["code"])
        return codes

    @staticmethod
    def _generate_code() -> str:
        """生成推广码: ZXBJ-{6位大写字母数字}"""
        import string
        alphabet = string.ascii_uppercase + string.digits
        suffix = "".join(secrets.choice(alphabet) for _ in range(6))
        return f"{CODE_PREFIX}-{suffix}"

    @staticmethod
    def _share_tip(channel: str, code: str) -> str:
        """各平台分享文案(利用成熟平台流量)"""
        tips = {
            "wechat_miniprogram": f"微信小程序搜索「竹奕酒」, 输入推广码 {code} 注册享新人礼",
            "douyin": f"抖音评论区/私信发送推广码 {code}, 好酒等你来",
            "kuaishou": f"快手粉丝群发布推广码 {code}, 一起喝竹奕",
            "xiaohongshu": f"小红书笔记置顶推广码 {code}, 种草竹奕酒",
            "bilibili": f"B站视频简介挂推广码 {code}, 三连喝好酒",
            "taobao": f"淘宝店铺收藏截图+推广码 {code} 找客服领券",
            "direct": f"直达链接带推广码 {code}, 注册即绑定",
        }
        return tips.get(channel, f"推广码 {code}")

    # ============================================================
    # 用户端: 绑定推广码(新用户)
    # ============================================================

    async def bind_relation(self, code: str, invitee_member_id: int) -> dict:
        """新用户绑定推广码, 建立矩阵关系并触发上级奖励检查

        新人注册原则: 仅"新注册会员"(注册后 24 小时内)的绑定计入推广业绩
        并触发奖励; 老会员绑定成功但不计业绩(关系 status=invalid)、不触发奖励。

        Raises:
            KeyError: 会员不存在
            ValueError: 推广码无效/重复绑定/自绑/成环
        """
        async with get_lock(f"promotion:relation:{invitee_member_id}"):
            invitee = await self.member_repo.get_by_id(invitee_member_id)
            if not invitee:
                raise KeyError(f"会员 {invitee_member_id} 不存在")

            code_record = await self.promo_repo.get_code(code.strip().upper())
            if not code_record:
                raise ValueError(f"推广码不存在: {code}")
            if code_record.get("status") != "active":
                raise ValueError("推广码已失效")
            inviter_id = code_record["ownerMemberId"]
            if inviter_id == invitee_member_id:
                raise ValueError("不能绑定自己的推广码")

            existing = await self.promo_repo.get_relation(invitee_member_id)
            if existing:
                raise ValueError(
                    f"会员 {invitee_member_id} 已绑定过推广码(一人仅可绑定一次)")

            # 祖先链防环: 沿 inviter 向上遍历, 若遇 invitee 则成环
            await self._assert_no_cycle(inviter_id, invitee_member_id)

            # 新人注册原则: 仅新注册会员(24h内)绑定计入业绩并触发奖励
            is_new = self._is_new_member(invitee)

            relation = {
                "inviteeMemberId": invitee_member_id,
                "inviterMemberId": inviter_id,
                "code": code_record["code"],
                "channel": code_record.get("channel", "direct"),
                "status": "valid" if is_new else "invalid",
                "createdAt": self._now(),
            }
            await self.promo_repo.save_relation(relation)
            await self.promo_repo.incr_code_bound(code_record["code"])

            if is_new:
                # 触发上级奖励检查(直推达标 → 上线裂变达标), 最多上溯 2 级
                triggered = [inviter_id]
                parent = await self.promo_repo.get_relation(inviter_id)
                if parent and parent.get("status") == "valid":
                    triggered.append(parent["inviterMemberId"])
                for member_id in triggered:
                    await self._check_rewards(member_id)

            return {
                "success": True,
                "inviteeMemberId": invitee_member_id,
                "inviterMemberId": inviter_id,
                "code": code_record["code"],
                "counted": is_new,
                "countedNote": "" if is_new
                else "老会员绑定不计入推广业绩(新人注册原则)",
            }

    _NEW_MEMBER_WINDOW = timedelta(hours=24)

    @classmethod
    def _is_new_member(cls, invitee: dict) -> bool:
        """新人注册原则: 注册后 24 小时内视为新人(无注册时间的旧数据不计)"""
        created = invitee.get("created_at") or invitee.get("createdAt")
        if not created:
            return False
        try:
            created_dt = datetime.fromisoformat(
                str(created).replace("Z", "+00:00"))
        except ValueError:
            return False
        now = datetime.now(created_dt.tzinfo) if created_dt.tzinfo \
            else datetime.now()
        return (now - created_dt) <= cls._NEW_MEMBER_WINDOW

    async def _assert_no_cycle(self, inviter_id: int, invitee_member_id: int,
                               max_depth: int = 50):
        """沿 inviter 祖先链上溯, invitee 已在链上则拒绝(防 A→B→A 环)"""
        current = inviter_id
        for _ in range(max_depth):
            relation = await self.promo_repo.get_relation(current)
            if not relation:
                return
            current = relation["inviterMemberId"]
            if current == invitee_member_id:
                raise ValueError("绑定失败: 推广关系成环")

    # ============================================================
    # 核心: 矩阵奖励检查(绑定触发)
    # ============================================================

    async def _check_rewards(self, member_id: int):
        """检查会员的两级奖励达成情况(在 relation 锁内调用,再加会员锁防并发)"""
        async with get_lock(f"promotion:reward:{member_id}"):
            settings = await self.promo_repo.get_settings()
            if not settings.get("enabled", True):
                return

            # ---------- 一级: 直推奖(每满 N 人发一轮钱包奖励) ----------
            l1_threshold = int(settings.get("level1Threshold", 10))
            l1_amount = float(settings.get("level1RewardAmount", 20))
            team = await self.promo_repo.list_team(member_id)
            n1 = len(team)
            c1 = await self.promo_repo.count_rewards(member_id, "wallet")
            if l1_threshold > 0 and l1_amount > 0 and n1 >= l1_threshold * (c1 + 1):
                cycle = c1 + 1
                reward = {
                    "rewardId": await self.promo_repo.next_reward_id(),
                    "memberId": member_id,
                    "rewardType": "wallet",
                    "cycle": cycle,
                    "amount": l1_amount,
                    "status": "issued",
                    "detail": f"直推满{l1_threshold}人第{cycle}轮",
                    "createdAt": self._now(),
                }
                await self.promo_repo.save_reward(reward)
                await self.wallet_service.deposit_reward(
                    member_id, l1_amount,
                    description=f"推广矩阵奖励(直推第{cycle}轮)")
                logger.info("promo_l1_reward member=%s cycle=%s amount=%.2f",
                            member_id, cycle, l1_amount)

            # ---------- 二级: 裂变奖(每 M 个达标下线发一轮钱包现金) ----------
            l2_count = int(settings.get("level2SubPromoterCount", 6))
            l2_threshold = int(settings.get("level2SubThreshold", 5))
            l2_amount = float(settings.get("level2RewardAmount", 15))
            qualified = 0
            for relation in team:
                sub_team = await self.promo_repo.list_team(
                    relation["inviteeMemberId"])
                if len(sub_team) >= l2_threshold:
                    qualified += 1
            c2 = await self.promo_repo.count_rewards(member_id, "wallet_l2")
            if (l2_count > 0 and l2_threshold > 0 and l2_amount > 0
                    and qualified >= l2_count * (c2 + 1)):
                cycle = c2 + 1
                reward = {
                    "rewardId": await self.promo_repo.next_reward_id(),
                    "memberId": member_id,
                    "rewardType": "wallet_l2",
                    "cycle": cycle,
                    "amount": l2_amount,
                    "status": "issued",
                    "detail": (f"{l2_count}个下线各推广满{l2_threshold}人"
                               f"第{cycle}轮"),
                    "createdAt": self._now(),
                }
                await self.promo_repo.save_reward(reward)
                await self.wallet_service.deposit_reward(
                    member_id, l2_amount,
                    description=f"推广矩阵奖励(裂变第{cycle}轮)")
                logger.info("promo_l2_reward member=%s cycle=%s qualified=%s "
                            "amount=%.2f", member_id, cycle, qualified,
                            l2_amount)

    # ============================================================
    # 用户端: 推广统计/团队/奖励
    # ============================================================

    async def get_my_stats(self, member_id: int) -> dict:
        """我的推广统计: 下线数/达标下线数/奖励/奖励余额"""
        settings = await self.promo_repo.get_settings()
        l2_threshold = int(settings.get("level2SubThreshold", 100))
        l2_count = int(settings.get("level2SubPromoterCount", 50))

        team = await self.promo_repo.list_team(member_id)
        qualified = 0
        for relation in team:
            sub_team = await self.promo_repo.list_team(
                relation["inviteeMemberId"])
            if len(sub_team) >= l2_threshold:
                qualified += 1

        wallet_reward = 0.0
        account = await self.wallet_service.wallet_repo.get_account(member_id)
        if account:
            wallet_reward = float(account.get("rewardBalance", 0))

        wine_available = await self.promo_repo.list_rewards(
            member_id=member_id, reward_type="wine_qualify", status="issued")

        return {
            "memberId": member_id,
            "directCount": len(team),
            "level1Threshold": int(settings.get("level1Threshold", 10)),
            "level1RewardAmount": float(settings.get("level1RewardAmount", 20)),
            "qualifiedSubCount": qualified,
            "level2SubPromoterCount": l2_count,
            "level2SubThreshold": l2_threshold,
            "level2RewardAmount": float(
                settings.get("level2RewardAmount", 15)),
            "wineMinPrice": float(settings.get("wineMinPrice", 200)),
            "rewardBalance": wallet_reward,
            "rewardBalanceNote": "仅可购买本站产品,不可提现",
            "wineQualifyAvailable": len(wine_available),
            "walletRewardCycles": await self.promo_repo.count_rewards(
                member_id, "wallet"),
        }

    async def list_my_team(self, member_id: int) -> list[dict]:
        """我的下线列表(附各自推广数)"""
        team = await self.promo_repo.list_team(member_id)
        result = []
        for relation in team:
            sub_count = len(await self.promo_repo.list_team(
                relation["inviteeMemberId"]))
            invitee = await self.member_repo.get_by_id(
                relation["inviteeMemberId"])
            result.append({
                "inviteeMemberId": relation["inviteeMemberId"],
                "nickname": (invitee or {}).get("nickname", ""),
                "channel": relation.get("channel"),
                "subCount": sub_count,
                "boundAt": relation.get("createdAt"),
            })
        return result

    async def list_my_rewards(self, member_id: int) -> list[dict]:
        """我的奖励记录(钱包轮次+领酒资格)"""
        return await self.promo_repo.list_rewards(member_id=member_id)

    # ============================================================
    # 用户端: 领酒(二级奖励核销)
    # ============================================================

    async def claim_wine(self, member_id: int, product_id: str,
                         address: str) -> dict:
        """领取奖励酒: 核销一个领酒资格, 从活动酒池选 1 瓶(价格≥wineMinPrice)

        Raises:
            KeyError: 会员不存在
            ValueError: 无可用资格/产品不在池/地址为空
        """
        if not address or len(address.strip()) < 5:
            raise ValueError("请填写完整收货地址")
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")

        async with get_lock(f"promotion:claim:{member_id}"):
            available = await self.promo_repo.list_rewards(
                member_id=member_id, reward_type="wine_qualify",
                status="issued")
            if not available:
                raise ValueError("暂无可领取的奖励酒资格")

            product = await self._get_eligible_product(product_id)
            if not product:
                raise ValueError(
                    f"产品 {product_id} 不在活动酒池或价格未达标")

            reward = available[0]  # 先到先核销(最早一轮)
            await self.promo_repo.update_reward_status(
                reward["rewardId"], "used")
            claim = {
                "claimId": await self.promo_repo.next_claim_id(),
                "memberId": member_id,
                "rewardId": reward["rewardId"],
                "productId": product["product_id"],
                "productName": product.get("name", ""),
                "address": address.strip(),
                "status": "pending_shipped",
                "createdAt": self._now(),
                "shippedAt": "",
            }
            await self.promo_repo.save_wine_claim(claim)
            logger.info("promo_wine_claimed member=%s product=%s reward=%s",
                        member_id, product_id, reward["rewardId"])
            return {"success": True, **claim}

    async def list_eligible_products(self) -> list[dict]:
        """活动酒池: 价格 ≥ wineMinPrice 的竹奕酒"""
        settings = await self.promo_repo.get_settings()
        min_price = float(settings.get("wineMinPrice", 200))
        configured = settings.get("eligibleProductIds")
        products = await self.product_repo.list_products()
        result = []
        for p in products:
            price = float(p.get("price", 0))
            if configured is not None:
                if p["product_id"] in configured and price >= min_price:
                    result.append(self._brief_product(p))
            elif price >= min_price:
                result.append(self._brief_product(p))
        return result

    async def _get_eligible_product(self, product_id: str) -> dict | None:
        """校验产品在活动池且价格达标, 返回产品或 None"""
        settings = await self.promo_repo.get_settings()
        min_price = float(settings.get("wineMinPrice", 200))
        configured = settings.get("eligibleProductIds")
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            return None
        price = float(product.get("price", 0))
        if price < min_price:
            return None
        if configured is not None and product_id not in configured:
            return None
        return product

    @staticmethod
    def _brief_product(p: dict) -> dict:
        return {
            "productId": p["product_id"],
            "name": p.get("name", ""),
            "price": float(p.get("price", 0)),
            "brand": p.get("brand", "竹奕"),
        }

    # ============================================================
    # 用户端: 奖励余额购买本站产品
    # ============================================================

    async def reward_purchase(self, member_id: int, product_id: str,
                              quantity: int = 1) -> dict:
        """奖励余额购买本站产品(不可提现的钱包奖励的唯一出口)

        Raises:
            KeyError: 产品不存在
            ValueError: 数量非法/奖励余额不足
        """
        if quantity < 1:
            raise ValueError("购买数量至少为 1")
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        amount = round(float(product.get("price", 0)) * quantity, 2)

        order_id = f"ZXBJ-RP-{self._now().replace(':', '').replace('-', '').replace('.', '').replace('+', '')[:17]}"
        pay = await self.wallet_service.pay_with_reward(
            member_id, amount, order_id=order_id,
            description=f"奖励余额购买 {product.get('name', product_id)} x{quantity}")
        return {
            "success": True,
            "orderId": order_id,
            "productId": product_id,
            "productName": product.get("name", ""),
            "quantity": quantity,
            "amount": amount,
            "rewardBalanceAfter": pay["rewardBalanceAfter"],
            "txNo": pay["txNo"],
        }

    # ============================================================
    # 管理端: 参数配置
    # ============================================================

    async def get_settings(self) -> dict:
        return await self.promo_repo.get_settings()

    async def update_settings(self, fields: dict, admin: str = "admin") -> dict:
        """管理端修改参数(校验合法性), 新绑定即时按新参数计算奖励

        Raises:
            ValueError: 参数非法
        """
        allowed = ("enabled", "level1Threshold", "level1RewardAmount",
                   "level2SubPromoterCount", "level2SubThreshold",
                   "level2RewardAmount", "wineMinPrice", "eligibleProductIds")
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValueError(f"无可更新字段, 支持: {', '.join(allowed)}")

        if "level1Threshold" in updates:
            v = int(updates["level1Threshold"])
            if v < 1:
                raise ValueError("一级阈值须 ≥ 1")
            updates["level1Threshold"] = v
        if "level1RewardAmount" in updates:
            v = float(updates["level1RewardAmount"])
            if v < 0:
                raise ValueError("一级奖励金额须 ≥ 0")
            updates["level1RewardAmount"] = round(v, 2)
        if "level2SubPromoterCount" in updates:
            v = int(updates["level2SubPromoterCount"])
            if v < 1:
                raise ValueError("二级达标下线数须 ≥ 1")
            updates["level2SubPromoterCount"] = v
        if "level2SubThreshold" in updates:
            v = int(updates["level2SubThreshold"])
            if v < 1:
                raise ValueError("下线推广阈值须 ≥ 1")
            updates["level2SubThreshold"] = v
        if "level2RewardAmount" in updates:
            v = float(updates["level2RewardAmount"])
            if v < 0:
                raise ValueError("二级奖励金额须 ≥ 0")
            updates["level2RewardAmount"] = round(v, 2)
        if "wineMinPrice" in updates:
            v = float(updates["wineMinPrice"])
            if v < 0:
                raise ValueError("奖励酒最低价须 ≥ 0")
            updates["wineMinPrice"] = round(v, 2)
        if "enabled" in updates:
            updates["enabled"] = bool(updates["enabled"])
        if "eligibleProductIds" in updates and updates["eligibleProductIds"] is not None:
            if not isinstance(updates["eligibleProductIds"], list):
                raise ValueError("eligibleProductIds 须为产品ID数组或 null(自动)")
            valid_ids = []
            for pid in updates["eligibleProductIds"]:
                product = await self.product_repo.get_by_id(pid)
                if not product:
                    raise ValueError(f"产品不存在: {pid}")
                if float(product.get("price", 0)) < float(
                        (await self.promo_repo.get_settings()).get(
                            "wineMinPrice", 200)):
                    raise ValueError(
                        f"产品 {pid} 价格低于 wineMinPrice, 不可入池")
                valid_ids.append(pid)
            updates["eligibleProductIds"] = valid_ids

        updates["updatedAt"] = self._now()
        updates["updatedBy"] = admin
        result = await self.promo_repo.update_settings(updates)
        logger.info("promo_settings_updated by=%s fields=%s", admin,
                    list(updates.keys()))
        return result

    # ============================================================
    # 管理端: 关系/奖励/领酒管理
    # ============================================================

    async def admin_list_relations(self, inviter_member_id: int = None,
                                   status: str = None,
                                   limit: int = 200) -> list[dict]:
        return await self.promo_repo.list_relations(
            inviter_member_id=inviter_member_id, status=status, limit=limit)

    async def admin_list_rewards(self, member_id: int = None,
                                 reward_type: str = None,
                                 status: str = None,
                                 limit: int = 200) -> list[dict]:
        return await self.promo_repo.list_rewards(
            member_id=member_id, reward_type=reward_type, status=status,
            limit=limit)

    async def admin_list_wine_claims(self, member_id: int = None,
                                     status: str = None,
                                     limit: int = 200) -> list[dict]:
        return await self.promo_repo.list_wine_claims(
            member_id=member_id, status=status, limit=limit)

    async def admin_ship_wine(self, claim_id: int) -> dict:
        """领酒发货流转: pending_shipped → shipped → done

        Raises:
            KeyError: 记录不存在
            ValueError: 状态不可流转
        """
        claim = await self.promo_repo.get_wine_claim(claim_id)
        if not claim:
            raise KeyError(f"领酒记录 {claim_id} 不存在")
        if claim["status"] == "pending_shipped":
            fields = {"status": "shipped", "shippedAt": self._now()}
        elif claim["status"] == "shipped":
            fields = {"status": "done"}
        else:
            raise ValueError(f"记录已是终态(当前: {claim['status']})")
        return await self.promo_repo.update_wine_claim(claim_id, fields)

    async def admin_revoke_code(self, code: str) -> dict:
        """撤销推广码(撤销后不可再绑定, 已建立关系不受影响)"""
        code = code.strip().upper()
        record = await self.promo_repo.get_code(code)
        if not record:
            raise KeyError(f"推广码 {code} 不存在")
        result = await self.promo_repo.update_code_status(code, "revoked")
        return {"success": True, "code": code, "status": result.get("status")}

    async def admin_invalidate_relation(self, invitee_member_id: int) -> dict:
        """作废绑定关系(不计入上级业绩; 再次调用可恢复)"""
        relation = await self.promo_repo.get_relation(invitee_member_id)
        if not relation:
            raise KeyError(f"会员 {invitee_member_id} 无绑定关系")
        new_status = "invalid" if relation.get("status") == "valid" else "valid"
        result = await self.promo_repo.update_relation_status(
            invitee_member_id, new_status)
        return {"success": True, "inviteeMemberId": invitee_member_id,
                "status": result.get("status")}

    async def admin_grant_reward(self, member_id: int, reward_type: str,
                                 amount: float = 0,
                                 detail: str = "管理端手动补发") -> dict:
        """手动补发奖励(钱包奖励余额或领酒资格)

        Raises:
            KeyError: 会员不存在
            ValueError: 类型/金额非法
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        if reward_type not in ("wallet", "wine_qualify"):
            raise ValueError("rewardType 须为 wallet / wine_qualify")
        reward = {
            "rewardId": await self.promo_repo.next_reward_id(),
            "memberId": member_id,
            "rewardType": reward_type,
            "cycle": 0,  # 手动补发不计轮次
            "amount": 0,
            "status": "issued",
            "detail": detail,
            "createdAt": self._now(),
        }
        if reward_type == "wallet":
            amount = round(float(amount), 2)
            if amount <= 0:
                raise ValueError("钱包奖励金额须 > 0")
            reward["amount"] = amount
            await self.promo_repo.save_reward(reward)
            await self.wallet_service.deposit_reward(
                member_id, amount, description=detail)
        else:
            await self.promo_repo.save_reward(reward)
        return {"success": True, "reward": reward}

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now(UTC).isoformat()
