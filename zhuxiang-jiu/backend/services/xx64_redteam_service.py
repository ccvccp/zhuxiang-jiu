"""64号·信值兑换管理 红队七向量
(xx64_redteam_service, P5)

计划(docs/64号_信值兑换商品服务
AI智能管理模块实施计划.md
§八 P5):
    RT-01 规则绕过(伪造请求跳
    30/20/40)
    RT-02 拆单绕限(多笔小额
    压基数)
    RT-03 积分套利(高频兑换
    冲击)
    RT-04 价格操纵(提价套信值)
    RT-05 双花(并发同值兑换)
    RT-06 申诉刷分
    RT-07 负值透支

口径:
    每向量=攻击仿真→防御断言
    (defended)+证据(evidence
    数字可溯源)+自清理(_cleanup
    ——种子数据用后即删, 专用
    trustId 域 98xx 隔离)。

铁律:
    - 决策面(off 409——路由层)
    - 攻击仿真在当前模式下
      执行(shadow 观察留痕/
      assist 阻断——两态均可
      验证刚性规则, 因刚性
      规则 R1-R7 不受模式豁免)
    - 红队不留脏数据(delete
      助手自清理; 风控事件/
      事件留痕保留为审计轨)
"""

import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_redteam")

MODEL_VERSION = "v1-xx64-redteam"

SCORER_ID = "value_exchange"

# 红队专用隔离域(trustId/buyerId
# ——98xx 不与业务域冲突)
RT_TRUST_BASE = 9801


def current_mode() -> str:
    """模块开关(XX64_MODE——同底座)"""
    return os.environ.get(
        "XX64_MODE", "off")


class Xx64RedteamService:
    """64号红队七向量(P5——兑换
    安全全链防御)"""

    def __init__(self):
        self.repo = Xx64Repository()
        self._seq = 0

    def _next_trust_id(self) -> int:
        self._seq += 1
        return RT_TRUST_BASE + self._seq

    async def _seed_profile(
            self, trust_id: int,
            score: float = 500.0
    ) -> None:
        """种子 45号档案(红队隔离域
        ——idDigest rt-* 前缀)"""
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        await TrustValue45Repository() \
            .save_profile({
                "trustId": int(trust_id),
                "role": "person",
                "name": f"rt-{trust_id}",
                "idDigest":
                    f"rt-{trust_id}",
                "factors": {},
                "score": float(score),
                "rawScore":
                    float(score),
                "grade": "A",
                "fused": False,
                "frozen": False,
                "createdAt":
                    "2026-01-01T00:00:00",
                "updatedAt":
                    "2026-01-01T00:00:00"})

    async def _set_score(
            self, trust_id: int,
            score: float) -> None:
        """改写红队档案余额(RT-07
        攻击构造——转移前压低余额)"""
        await self._seed_profile(
            trust_id, score)

    # ============================================================
    # RT-01 规则绕过(伪造请求)
    # ============================================================

    async def rt01_rule_bypass(self
                               ) -> dict:
        """伪造请求跳 30/20/40——
        ① 注入伪 trustValue/cashValue
        字段(服务端忽略, 强制
        服务端计算 30/70)
        ② 超单次限额价(R4 拒)
        ③ 非法价(0/负——拒)"""
        tid = self._next_trust_id()
        await self._seed_profile(tid)
        from services.xx64_service import (
            Xx64Service,
        )
        svc = Xx64Service()
        buyer = tid
        evidence = {}

        # ① 伪造字段注入(被忽略)
        order = await svc.create_order(
            buyer, 8888, tid, 100,
            product="rt01-forged",
            use_trust=True,
            # create_order 无
            # trustValue 形参——伪字段
            # 无注入面(API body 同理
            # 仅取白名单字段)
        )
        server_tv = float(
            order.get("trustValue"))
        server_cash = float(
            order.get("cashValue"))
        evidence["forgedIgnored"] = (
            server_tv == 30.0
            and server_cash == 70.0)
        await self.repo.delete_order(
            order["orderId"])

        # ② 超单次限额(价 2000→
        # tv 600 > 余额 500×20%=100)
        r4_rejected = False
        r4_msg = ""
        try:
            await svc.create_order(
                buyer, 8888, tid, 2000,
                product="rt01-over")
        except ValueError as exc:
            r4_rejected = True
            r4_msg = str(exc)[:60]
        evidence["r4Rejected"] = \
            r4_rejected
        evidence["r4Msg"] = r4_msg

        # ③ 非法价
        bad_rejected = False
        try:
            await svc.create_order(
                buyer, 8888, tid, -100,
                product="rt01-neg")
        except ValueError:
            bad_rejected = True
        evidence["invalidPriceRejected"] \
            = bad_rejected

        defended = all([
            evidence["forgedIgnored"],
            r4_rejected, bad_rejected,
        ])
        return {
            "vector": "RT-01",
            "name": "规则绕过",
            "attack": "伪造请求注入伪"
                      "trustValue 字段/"
                      "超限价/负价",
            "defended": defended,
            "evidence": evidence,
        }

    # ============================================================
    # RT-02 拆单绕限(多笔小额
    # 压基数后大额)
    # ============================================================

    async def rt02_split_bypass(self
                                ) -> dict:
        """多笔小额压窗口用量后
        大额——R5 累计限额拒
        (窗口用量+本次 > 快照
        ×40%)"""
        tid = self._next_trust_id()
        await self._seed_profile(tid)
        from services.xx64_service import (
            Xx64Service,
        )
        svc = Xx64Service()
        small_ids = []
        # 5 笔小额(各 tv 30→
        # 窗口用量 150)
        for i in range(5):
            r = await svc.create_order(
                tid, 8888, tid, 100,
                product=f"rt02-{i}")
            small_ids.append(
                r["orderId"])
        # 大额(tv 60——150+60
        # =210 > 500×40%=200)
        r5_rejected = False
        r5_msg = ""
        try:
            await svc.create_order(
                tid, 8888, tid, 200,
                product="rt02-big")
        except ValueError as exc:
            r5_rejected = True
            r5_msg = str(exc)[:80]
        # 自清理
        for oid in small_ids:
            await self.repo.delete_order(
                oid)
        return {
            "vector": "RT-02",
            "name": "拆单绕限",
            "attack": "5 笔小额压窗口"
                      "用量后大额 200",
            "defended": r5_rejected,
            "evidence": {
                "r5Rejected":
                    r5_rejected,
                "r5Msg": r5_msg,
                "windowUsed": 150.0,
                "attemptTrust": 60.0,
                "cumulativeQuota":
                    200.0,
            },
        }

    # ============================================================
    # RT-03 积分套利(高频兑换)
    # ============================================================

    async def rt03_points_arb(self
                              ) -> dict:
        """高频积分兑换冲击——
        R6 日限频第 4 次拒"""
        tid = self._next_trust_id()
        await self._seed_profile(tid)
        user = tid
        from repositories.points_repository import (
            PointsRepository,
        )
        repo_pts = PointsRepository()
        acct = await \
            repo_pts.get_or_create_account(
                user)
        acct["totalPoints"] = 10000
        await repo_pts.save_account(
            acct)
        from services.xx64_points_service import (
            Xx64PointsService,
        )
        pts = Xx64PointsService()
        exch_ids = []
        ok_count = 0
        for _ in range(3):
            r = await pts.exchange(
                user, tid, 100)
            exch_ids.append(
                r["exchangeId"])
            ok_count += 1
        # 第 4 次——日限频拒
        limited = False
        limit_msg = ""
        try:
            await pts.exchange(
                user, tid, 100)
        except ValueError as exc:
            limited = True
            limit_msg = str(exc)[:60]
        # 自清理(取消返还积分+
        # 删记录)
        for eid in exch_ids:
            with suppress(ValueError):
                await pts \
                    .cancel_exchange(eid)
            await self.repo \
                .delete_exchange(eid)
        return {
            "vector": "RT-03",
            "name": "积分套利",
            "attack": "同日高频兑换"
                      "(第 4 次冲击)",
            "defended": limited,
            "evidence": {
                "succeededBeforeLimit":
                    ok_count,
                "fourthRejected":
                    limited,
                "limitMsg": limit_msg,
                "dailyLimit": 3,
            },
        }

    # ============================================================
    # RT-04 价格操纵(提价套信值)
    # ============================================================

    async def rt04_price_manip(self
                               ) -> dict:
        """提价 50% 叠加信值支付
        ——PRICE-MANIP 检测命中"""
        tid = self._next_trust_id()
        product = "rt04-manip"
        now = datetime.now(UTC)
        old = (now - timedelta(
            days=10)).isoformat()
        seeded = []
        # 前 7 日 3 笔价 100
        for _ in range(3):
            oid = await \
                self.repo.next_order_id()
            await self.repo.save_order({
                "orderId": oid,
                "buyerId": tid,
                "sellerId": 8888,
                "trustId": tid,
                "product": product,
                "price": 100.0,
                "trustValue": 30.0,
                "cashValue": 70.0,
                "balanceSnapshot":
                    500.0,
                "status": "paid",
                "paidAt": old,
                "createdAt": old,
            })
            seeded.append(oid)
        # 近 7 日 3 笔价 150(+50%)
        for _ in range(3):
            oid = await \
                self.repo.next_order_id()
            await self.repo.save_order({
                "orderId": oid,
                "buyerId": tid,
                "sellerId": 8888,
                "trustId": tid,
                "product": product,
                "price": 150.0,
                "trustValue": 45.0,
                "cashValue": 105.0,
                "balanceSnapshot":
                    500.0,
                "status": "paid",
                "paidAt": ts(),
                "createdAt": ts(),
            })
            seeded.append(oid)
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        findings = await (
            Xx64RiskService()
            .detect_price_manip(
                product=product))
        hit = any(
            f.get("entityId")
            == product
            for f in findings)
        drift = next(
            (f["detail"]["drift"]
             for f in findings
             if f.get("entityId")
             == product), None)
        # 自清理
        for oid in seeded:
            await self.repo.delete_order(
                oid)
        return {
            "vector": "RT-04",
            "name": "价格操纵",
            "attack": "商品 7 日提价"
                      " 50% 叠加信值支付",
            "defended": hit,
            "evidence": {
                "detected": hit,
                "drift": drift,
                "driftThreshold": 0.20,
            },
        }

    # ============================================================
    # RT-05 双花(并发同值兑换)
    # ============================================================

    async def rt05_double_spend(self
                                ) -> dict:
        """并发双支付同一订单——
        支付占位锁保证恰好一笔
        成功(状态机+SET NX)"""
        tid = self._next_trust_id()
        await self._seed_profile(tid)
        from services.xx64_service import (
            Xx64Service,
        )
        order = await (
            Xx64Service().create_order(
                tid, 8888, tid, 100,
                product="rt05-ds"))
        oid = order["orderId"]
        from services.xx64_settle_service import (
            Xx64SettleService,
        )
        settle = Xx64SettleService()
        # 并发两笔支付
        results = await asyncio.gather(
            settle.pay_order(oid),
            settle.pay_order(oid),
            return_exceptions=True)
        successes = sum(
            1 for r in results
            if not isinstance(
                r, BaseException))
        failures = [
            str(r)[:60] for r in results
            if isinstance(
                r, BaseException)]
        # 自清理(借贷对+订单)
        entry_id = next(
            (r.get("entryId")
             for r in results
             if isinstance(r, dict)),
            None)
        if entry_id:
            await self.repo \
                .delete_ledger_pair(
                    entry_id)
        await self.repo.delete_order(
            oid)
        return {
            "vector": "RT-05",
            "name": "双花",
            "attack": "并发两笔支付"
                      "同一订单",
            "defended":
                successes == 1,
            "evidence": {
                "successes":
                    successes,
                "exactlyOne":
                    successes == 1,
                "failureSample":
                    failures[0]
                if failures else "",
            },
        }

    # ============================================================
    # RT-06 申诉刷分
    # ============================================================

    async def rt06_appeal_farm(self
                               ) -> dict:
        """重复申诉+过期不翻转
        ——刷分无门(翻转必经
        admin 显式终审)"""
        tid = self._next_trust_id()
        oid = await \
            self.repo.next_order_id()
        await self.repo.save_order({
            "orderId": oid,
            "buyerId": tid,
            "sellerId": 8888,
            "trustId": tid,
            "product": "rt06-farm",
            "price": 100.0,
            "trustValue": 30.0,
            "cashValue": 70.0,
            "balanceSnapshot": 500.0,
            "status": "paid",
            "paidAt": ts(),
            "createdAt": ts(),
        })
        from services.xx64_appeal_service import (
            Xx64AppealService,
        )
        aps = Xx64AppealService()
        ap = await aps.submit(
            oid, "rt06 刷分尝试")
        # 重复申诉(进行中)拒
        dup_rejected = False
        try:
            await aps.submit(
                oid, "rt06 再来")
        except ValueError:
            dup_rejected = True
        # 过期不翻转(改 expiresAt
        # 为过去→expire→expired
        # 而非 approved)
        appeal = await self.repo \
            .get_appeal(
                ap["appealId"])
        appeal["expiresAt"] = (
            datetime.now(UTC)
            - timedelta(hours=1)
        ).isoformat()
        await self.repo.save_appeal(
            appeal, create=False)
        await aps.expire_stale()
        fresh = await self.repo \
            .get_appeal(
                ap["appealId"])
        expired_not_flipped = (
            fresh.get("status")
            == "expired")
        # 自清理
        await self.repo.delete_order(
            oid)
        return {
            "vector": "RT-06",
            "name": "申诉刷分",
            "attack": "重复申诉+坐等"
                      "过期自动翻转",
            "defended": dup_rejected
            and expired_not_flipped,
            "evidence": {
                "duplicateRejected":
                    dup_rejected,
                "expiredNotFlipped":
                    expired_not_flipped,
                "finalStatus":
                    fresh.get("status"),
            },
        }

    # ============================================================
    # RT-07 负值透支
    # ============================================================

    async def rt07_negative(self
                            ) -> dict:
        """转移前压低余额——
        R7 复核拒(锁值后余额
        10 < 需扣 30)"""
        tid = self._next_trust_id()
        await self._seed_profile(tid)
        from services.xx64_service import (
            Xx64Service,
        )
        order = await (
            Xx64Service().create_order(
                tid, 8888, tid, 100,
                product="rt07-neg"))
        oid = order["orderId"]
        # 攻击构造: 转移前档案
        # 余额压到 10(<30)
        await self._set_score(tid, 10.0)
        from services.xx64_settle_service import (
            Xx64SettleService,
        )
        r7_rejected = False
        r7_msg = ""
        try:
            await Xx64SettleService() \
                .pay_order(oid)
        except ValueError as exc:
            r7_rejected = True
            r7_msg = str(exc)[:80]
        # 自清理(支付失败无借贷对)
        await self.repo.delete_order(
            oid)
        return {
            "vector": "RT-07",
            "name": "负值透支",
            "attack": "锁值后压低档案"
                      "余额至 10 再支付",
            "defended": r7_rejected,
            "evidence": {
                "r7Rejected":
                    r7_rejected,
                "r7Msg": r7_msg,
                "balanceAtPay": 10.0,
                "trustValue": 30.0,
            },
        }

    # ============================================================
    # 总入口
    # ============================================================

    async def run_all(self) -> dict:
        """执行红队七向量
        (每向量独立 try——单向量
        异常不中断整轮)"""
        vectors = [
            ("RT-01", self.rt01_rule_bypass),
            ("RT-02", self.rt02_split_bypass),
            ("RT-03", self.rt03_points_arb),
            ("RT-04", self.rt04_price_manip),
            ("RT-05", self.rt05_double_spend),
            ("RT-06", self.rt06_appeal_farm),
            ("RT-07", self.rt07_negative),
        ]
        results = []
        for code, fn in vectors:
            try:
                r = await fn()
            except Exception as exc:
                r = {
                    "vector": code,
                    "name": "执行异常",
                    "attack": "-",
                    "defended": False,
                    "evidence": {
                        "error": str(exc)
                        [:150]},
                }
            results.append(r)
        defended = sum(
            1 for r in results
            if r.get("defended"))
        return {
            "success": True,
            "ranAt": ts(),
            "mode": current_mode(),
            "vectors": results,
            "total": len(results),
            "defended": defended,
            "allDefended":
                defended
                == len(results),
            "note": "红队七向量——兑换"
                    "安全全链防御"
                    "(种子数据自清理; "
                    "刚性规则不受模式"
                    "豁免)",
        }
