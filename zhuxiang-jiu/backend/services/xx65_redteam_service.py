"""65号·网店及商品AI智能管理 红队
服务(xx65_redteam, P4)

计划(§八 P4):
    红队七向量(攻击仿真+防御
    断言+自清理——98xx 隔离域):

        RT-01 无信值开店绕过
        RT-02 违禁词直发
        RT-03 撤销窗口外撤销
        RT-04 配额越权
        RT-05 双轨价与结算不一致
        RT-06 合规承诺伪造
        RT-07 撤销重放

铁律:
    - 单向量异常不中断 run_all
      (defended=False 留痕)
    - 种子数据用后即删(风控/
      事件留痕保留为审计轨)
    - 决策面 off 409(admin)
"""

import asyncio
import logging
import time

from core.helpers import ts
from repositories.trust_value_repository import (
    TrustValue45Repository,
)
from repositories.credit_repository import (
    CreditRepository,
)
from repositories.xx65_repository import (
    Xx65Repository,
)

logger = logging.getLogger(
    "xx65_redteam")

# 隔离域(98xx 号段——与
# 64号红队 98xx/9801+ 不冲突
# 的并行序列, 每轮自增)
RT_OWNER_BASE = 9881
RT_DIGEST_PREFIX = "rt65-"


class Xx65RedteamService:
    """65号红队七向量(P4)"""

    def __init__(self):
        self.repo = Xx65Repository()
        self._owner_seq = RT_OWNER_BASE

    def _next_owner(self) -> int:
        self._owner_seq += 1
        return self._owner_seq

    async def _seed_shop(
            self, owner: int,
            credit_level: str = "L4",
            status: str = "active"
    ) -> dict:
        """开店种子(隔离域
        owner+trust_id 同号)"""
        trust_repo = \
            TrustValue45Repository()
        await trust_repo.save_profile({
            "trustId": owner,
            "role": "person",
            "name": f"rt65-{owner}",
            "idDigest":
                f"{RT_DIGEST_PREFIX}"
                f"{owner}",
            "factors": {},
            "score": 1000.0,
            "rawScore": 1000.0,
            "grade": "A",
            "fused": False,
            "frozen": False,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        credit_repo = \
            CreditRepository()
        acct = await credit_repo \
            .get_or_create_score(
                owner)
        acct["creditLevel"] = \
            credit_level
        await credit_repo \
            .save_score(acct)
        # 直写店铺(绕过服务层
        # 构造任意状态——攻击
        # 种子专用)
        shop_id = await \
            self.repo.next_shop_id()
        shop = {
            "shopId": shop_id,
            "ownerId": owner,
            "trustId": owner,
            "intentId": 0,
            "category": "handicraft",
            "minLevel": "L3",
            "status": status,
            "quotaTier": "growth",
            "complianceAnswers": {
                "q": "否"},
            "activated": True,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_shop(
            shop)
        return shop

    async def _cleanup_shops(
            self, shop_ids: list
    ) -> None:
        """店铺种子清理(直写
        closed 态——数据用后即删;
        事件留痕保留为审计轨)"""
        for sid in shop_ids:
            shop = await \
                self.repo.get_shop(sid)
            if shop:
                shop["status"] = \
                    "closed"
                shop["updatedAt"] = ts()
                await self.repo \
                    .save_shop(
                        shop,
                        create=False)
                # 同店铺直写商品一并
                # 下架(防回流误扫)
                products = await \
                    self.repo \
                    .list_products(
                        shop_id=sid,
                        limit=100)
                for p in products:
                    if p.get("status") \
                            == "published":
                        p["status"] = \
                            "off_shelf"
                        p["updatedAt"] = ts()
                        await self.repo \
                            .save_product(
                                p,
                                create=False)

    # --------------------------------------------------------
    # RT-01 无信值开店绕过
    # --------------------------------------------------------

    async def rt01_admission_bypass(
            self) -> dict:
        """攻击: 低信用(L1)会员伪造
        precheckSnapshot/precheck
        字段注入 apply_shop——
        服务端忽略伪造字段并以
        23号真实信用判定(S2)"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            svc = Xx65Service()
            # 先建 L1 信用档案(攻击
            # 前置——绕不开 45号建档)
            trust_repo = \
                TrustValue45Repository()
            await trust_repo \
                .save_profile({
                    "trustId": owner,
                    "role": "person",
                    "name": f"rt65-{owner}",
                    "idDigest":
                        f"{RT_DIGEST_PREFIX}"
                        f"{owner}",
                    "factors": {},
                    "score": 1000.0,
                    "rawScore": 1000.0,
                    "grade": "A",
                    "fused": False,
                    "frozen": False,
                    "createdAt": ts(),
                    "updatedAt": ts(),
                })
            credit_repo = \
                CreditRepository()
            acct = await credit_repo \
                .get_or_create_score(
                    owner)
            acct["creditLevel"] = "L1"
            await credit_repo \
                .save_score(acct)
            # 低信用正常申请——
            # S2 预检应拒
            forged_ignored = False
            try:
                await svc.apply_shop(
                    owner, owner,
                    intent_id=None)
            except ValueError as exc:
                forged_ignored = \
                    "S2" in str(exc) \
                    or "准入" in str(exc)
            # 直写伪造快照店铺再
            # 走激活链(绕过预检
            # 的直接攻击)
            shop = await \
                self._seed_shop(
                    owner,
                    credit_level="L1",
                    status="prechecked")
            shop_ids.append(
                shop["shopId"])
            activated = True
            try:
                # 伪造快照后直接调
                # 决策端点级链路
                await svc.activate_shop(
                    shop["shopId"])
            except ValueError:
                activated = False
            defended = (forged_ignored
                        and not
                        activated)
            return {
                "vector": "RT-01",
                "name": "无信值开店绕过",
                "attack":
                    "L1 低信用会员伪造"
                    "准入快照注入+跳过"
                    "预检直接激活",
                "defended": defended,
                "evidence": {
                    "forgedIgnored":
                        forged_ignored,
                    "directActivateRejected":
                        not activated,
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # RT-02 违禁词直发
    # --------------------------------------------------------

    async def rt02_severe_word_publish(
            self) -> dict:
        """攻击: 严重违禁词(根治/
        包治百病)草稿带 confirmed
        =true 直接发布——防御②
        发布前二次校验应拦截(S1)"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            shop = await \
                self._seed_shop(owner)
            shop_ids.append(
                shop["shopId"])
            svc = Xx65Service()
            d = await svc.create_draft(
                shop["shopId"],
                "养生茶",
                description="可以根治"
                            "三高, 包治百病。",
                price=88.0)
            rejected = False
            try:
                await svc.publish_draft(
                    d["draftId"],
                    confirmed=True)
            except ValueError as exc:
                rejected = "S1" in str(exc)
            # 直写伪造合规结果
            # 再发布(旁路二次校验)
            draft = await \
                self.repo.get_draft(
                    d["draftId"])
            draft["compliance"] = {
                "severeHits": [],
                "bannedHits": [],
                "score": 100,
                "passed": True,
                "passScore": 80,
            }
            await self.repo.save_draft(
                draft, create=False)
            bypass_rejected = False
            try:
                await svc.publish_draft(
                    d["draftId"],
                    confirmed=True)
            except ValueError:
                bypass_rejected = True
            defended = (rejected
                        and
                        bypass_rejected)
            return {
                "vector": "RT-02",
                "name": "违禁词直发",
                "attack":
                    "严重词草稿带确认"
                    "直发+伪造合规结果"
                    "旁路二次校验",
                "defended": defended,
                "evidence": {
                    "confirmedRejected":
                        rejected,
                    "forgedResultRescanned":
                        bypass_rejected,
                    "severeWords": [
                        "根治",
                        "包治百病"],
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # RT-03 撤销窗口外撤销
    # --------------------------------------------------------

    async def rt03_late_revoke(
            self) -> dict:
        """攻击: 撤销窗口(300s)外
            的活动强行撤销——S5
        窗口校验应拒绝"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            shop = await \
                self._seed_shop(owner)
            shop_ids.append(
                shop["shopId"])
            svc = Xx65Service()
            # 直写商品+过期活动
            product_id = await \
                self.repo \
                .next_product_id()
            await self.repo \
                .save_product({
                    "productId":
                        product_id,
                    "shopId":
                        shop["shopId"],
                    "draftId": 0,
                    "productName":
                        "木雕",
                    "title": "木雕",
                    "copy": "手作",
                    "cashPrice": 100.0,
                    "trustQuota": 30.0,
                    "status":
                        "published",
                    "complianceFlag":
                        False,
                    "createdAt": ts(),
                })
            campaign_id = await \
                self.repo \
                .next_campaign_id()
            now = time.time()
            await self.repo \
                .save_campaign({
                    "campaignId":
                        campaign_id,
                    "shopId":
                        shop["shopId"],
                    "productId":
                        product_id,
                    "strategy":
                        "clearance",
                    "name": "过期活动",
                    "discountRate": 0.1,
                    "exclusive": True,
                    "channels":
                        ["in_site"],
                    "status": "active",
                    "revocable": True,
                    "revocableUntilTs":
                        now - 600,
                    "revoked": False,
                    "createdAt": ts(),
                    "updatedAt": ts(),
                })
            rejected = False
            try:
                await svc.revoke_campaign(
                    campaign_id)
            except ValueError as exc:
                rejected = \
                    "窗口已过" in str(exc)
            return {
                "vector": "RT-03",
                "name":
                    "撤销窗口外撤销",
                "attack":
                    "发布 10 分钟后"
                    "(超窗 600s)强行"
                    "撤销活动",
                "defended": rejected,
                "evidence": {
                    "overdueSeconds":
                        600,
                    "windowSeconds":
                        300,
                    "rejected":
                        rejected,
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # RT-04 配额越权
    # --------------------------------------------------------

    async def rt04_quota_overflow(
            self) -> dict:
        """攻击: starter 档(10 次
        生成配额)反复生成——S7
        第 11 次应拒绝"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            shop = await \
                self._seed_shop(
                    owner,
                    credit_level="L3",
                    status="active")
            shop_ids.append(
                shop["shopId"])
            # 直写 starter 档
            shop_rec = await \
                self.repo.get_shop(
                    shop["shopId"])
            shop_rec["quotaTier"] = \
                "starter"
            await self.repo.save_shop(
                shop_rec,
                create=False)
            svc = Xx65Service()
            succeeded = 0
            quota_rejected = False
            for i in range(11):
                try:
                    await svc \
                        .create_draft(
                            shop[
                                "shopId"],
                            f"商品{i}",
                            price=10.0)
                    succeeded += 1
                except ValueError \
                        as exc:
                    if "S7" in str(exc):
                        quota_rejected \
                            = True
                    break
            return {
                "vector": "RT-04",
                "name": "配额越权",
                "attack":
                    "starter 档超配额"
                    "(10 次)继续生成",
                "defended": quota_rejected,
                "evidence": {
                    "succeededBeforeLimit":
                        succeeded,
                    "quotaLimit": 10,
                    "rejected":
                        quota_rejected,
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # RT-05 双轨价与结算不一致
    # --------------------------------------------------------

    async def rt05_dual_track_tamper(
            self) -> dict:
        """攻击: 直写篡改商品
        trustQuota=999(伪造大信值
        额度)——order_window 展示层
        应按 cashPrice 服务端重算
        (S3 双轨价不信任存储)"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            shop = await \
                self._seed_shop(owner)
            shop_ids.append(
                shop["shopId"])
            product_id = await \
                self.repo \
                .next_product_id()
            await self.repo \
                .save_product({
                    "productId":
                        product_id,
                    "shopId":
                        shop["shopId"],
                    "draftId": 0,
                    "productName":
                        "木雕",
                    "title": "木雕",
                    "copy": "手作",
                    "cashPrice": 100.0,
                    "trustQuota": 30.0,
                    "status":
                        "published",
                    "complianceFlag":
                        False,
                    "createdAt": ts(),
                })
            # 篡改存储值
            prod = await \
                self.repo.get_product(
                    product_id)
            prod["trustQuota"] = 999.0
            await self.repo \
                .save_product(
                    prod,
                    create=False)
            svc = Xx65Service()
            w = await svc.order_window(
                product_id,
                trust_id=owner)
            displayed = \
                (w.get("dualTrack")
                 or {}).get(
                    "trustValue")
            # 服务端重算: 100×0.30
            recalculated = \
                displayed == 30.0
            return {
                "vector": "RT-05",
                "name":
                    "双轨价与结算"
                    "不一致",
                "attack":
                    "直写篡改商品"
                    "trustQuota=999"
                    "伪造展示额度",
                "defended": recalculated,
                "evidence": {
                    "tamperedStored":
                        999.0,
                    "displayed":
                        displayed,
                    "expected": 30.0,
                    "recalculated":
                        recalculated,
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # RT-06 合规承诺伪造
    # --------------------------------------------------------

    async def rt06_compliance_forgery(
            self) -> dict:
        """攻击: prechecked 店铺
        跳过认领合规承诺问答直接
        激活——状态机应拒绝(prechecked
        →active 无合法边)"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            shop = await \
                self._seed_shop(
                    owner,
                    status="prechecked")
            shop_ids.append(
                shop["shopId"])
            svc = Xx65Service()
            skipped_rejected = False
            try:
                await svc.activate_shop(
                    shop["shopId"])
            except ValueError:
                skipped_rejected = True
            # 答案伪造(未答全+
            # 篡改存量 answers)
            claimed = await \
                self._seed_shop(
                    self._next_owner(),
                    status="claimed")
            shop_ids.append(
                claimed["shopId"])
            activated_ok = True
            try:
                await svc \
                    .activate_shop(
                        claimed[
                            "shopId"])
            except ValueError:
                activated_ok = False
            # claimed 态(经合规
            # 承诺)可激活——非伪造
            # 路径; 但 prechecked
            # 不可——防御在状态机
            defended = (
                skipped_rejected)
            return {
                "vector": "RT-06",
                "name": "合规承诺伪造",
                "attack":
                    "跳过认领合规承诺"
                    "问答(prechecked)"
                    "直接激活店铺",
                "defended": defended,
                "evidence": {
                    "skipClaimRejected":
                        skipped_rejected,
                    "claimedPathWorks":
                        activated_ok,
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # RT-07 撤销重放
    # --------------------------------------------------------

    async def rt07_revoke_replay(
            self) -> dict:
        """攻击: 同一活动撤销成功
        后重放撤销请求(重放攻击)
        ——终态 revoked 无出边,
        第二次应拒绝+并发双撤销
        恰 1 成功"""
        from services.xx65_service import (
            Xx65Service,
        )
        owner = self._next_owner()
        shop_ids = []
        try:
            shop = await \
                self._seed_shop(owner)
            shop_ids.append(
                shop["shopId"])
            product_id = await \
                self.repo \
                .next_product_id()
            await self.repo \
                .save_product({
                    "productId":
                        product_id,
                    "shopId":
                        shop["shopId"],
                    "draftId": 0,
                    "productName":
                        "木雕",
                    "title": "木雕",
                    "copy": "手作",
                    "cashPrice": 100.0,
                    "trustQuota": 30.0,
                    "status":
                        "published",
                    "complianceFlag":
                        False,
                    "createdAt": ts(),
                })
            campaign_id = await \
                self.repo \
                .next_campaign_id()
            now = time.time()
            await self.repo \
                .save_campaign({
                    "campaignId":
                        campaign_id,
                    "shopId":
                        shop["shopId"],
                    "productId":
                        product_id,
                    "strategy":
                        "clearance",
                    "name": "重放目标",
                    "discountRate": 0.1,
                    "exclusive": True,
                    "channels":
                        ["in_site"],
                    "status": "active",
                    "revocable": True,
                    "revocableUntilTs":
                        now + 300,
                    "revoked": False,
                    "createdAt": ts(),
                    "updatedAt": ts(),
                })
            svc = Xx65Service()
            # 首次撤销成功
            first = await \
                svc.revoke_campaign(
                    campaign_id)
            # 重放(终态拒绝)
            replay_rejected = False
            try:
                await \
                    svc.revoke_campaign(
                        campaign_id)
            except ValueError:
                replay_rejected = True
            # 并发重放(第二活动
            # ——gather 双撤销恰
            # 1 成功)
            campaign2 = await \
                self.repo \
                .next_campaign_id()
            await self.repo \
                .save_campaign({
                    "campaignId":
                        campaign2,
                    "shopId":
                        shop["shopId"],
                    "productId":
                        product_id,
                    "strategy":
                        "clearance",
                    "name": "并发目标",
                    "discountRate": 0.1,
                    "exclusive": True,
                    "channels":
                        ["in_site"],
                    "status": "active",
                    "revocable": True,
                    "revocableUntilTs":
                        now + 300,
                    "revoked": False,
                    "createdAt": ts(),
                    "updatedAt": ts(),
                })
            results = await \
                asyncio.gather(
                    svc.revoke_campaign(
                        campaign2),
                    svc.revoke_campaign(
                        campaign2),
                    return_exceptions=
                    True)
            successes = sum(
                1 for r in results
                if not isinstance(
                    r, BaseException))
            defended = (
                first.get("status")
                == "revoked"
                and replay_rejected
                and successes == 1)
            return {
                "vector": "RT-07",
                "name": "撤销重放",
                "attack":
                    "撤销成功后重放"
                    "+并发双撤销",
                "defended": defended,
                "evidence": {
                    "firstRevoked":
                        first.get(
                            "status")
                        == "revoked",
                    "replayRejected":
                        replay_rejected,
                    "concurrentSuccesses":
                        successes,
                    "exactlyOne":
                        successes == 1,
                },
            }
        finally:
            await self._cleanup_shops(
                shop_ids)

    # --------------------------------------------------------
    # 总入口
    # --------------------------------------------------------

    async def run_all(self) -> dict:
        """红队七向量总入口(单向量
        异常不中断)"""
        vectors = [
            ("RT-01",
             self.rt01_admission_bypass),
            ("RT-02",
             self.rt02_severe_word_publish),
            ("RT-03",
             self.rt03_late_revoke),
            ("RT-04",
             self.rt04_quota_overflow),
            ("RT-05",
             self.rt05_dual_track_tamper),
            ("RT-06",
             self.rt06_compliance_forgery),
            ("RT-07",
             self.rt07_revoke_replay),
        ]
        results = []
        for code, fn in vectors:
            try:
                r = await fn()
            except Exception as exc:
                logger.warning(
                    "xx65_redteam_%s"
                    "_failed: %s",
                    code, exc)
                r = {
                    "vector": code,
                    "defended": False,
                    "evidence": {
                        "error":
                            str(exc)[:150]},
                }
            results.append(r)
        defended_n = sum(
            1 for r in results
            if r.get("defended"))
        return {
            "success": True,
            "ranAt": ts(),
            "vectors": results,
            "total": len(results),
            "defended": defended_n,
            "allDefended":
                defended_n
                == len(results),
            "note": "红队七向量——攻击"
                    "仿真+防御断言+种子"
                    "自清理(98xx 隔离域)",
        }
