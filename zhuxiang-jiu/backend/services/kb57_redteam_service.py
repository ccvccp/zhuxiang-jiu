"""57号·AI智能知识库 红队验证
(kb57_redteam_service, P5)

计划(docs/57号_AI智能知识库模块实施计划.md §九 P5):
    红队七向量:
        RT-01 知识投毒(伪造缺口信号灌入——
               未知 eventType 不入白名单)
        RT-02 白名单逃逸(未注册源采集——
               版权关第一道阻断)
        RT-03 PII 泄漏(未脱敏内容入库——
               隐私关掩码+发布前拦截)
        RT-04 种子污染(非合规资源锻造——
               无指纹不入库铁律)
        RT-05 预算耗尽(缺口级超支硬测——
               halted 熔断不降级)
        RT-06 入口越权(他人学习记录推进——
               属主校验拒绝)
        RT-07 过期种子误导(有效期绕过——
               过期自动降权出推荐池)

设计(55/56号确定性红队范式——不依赖 LLM,
全部向量离线可复现):
    每向量: 构造攻击载荷 → 调用目标面 →
    断言防御行为(阻断/熔断/拒绝/降权) → 留痕。

前置: KB57_MODE=shadow/assist(决策面开放
——off 态无攻击面; P3 会员面向量需 assist)。
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_redteam_service")

MODEL_VERSION = "v1-kb57-redteam"

# 红队专用会员(向量隔离——不复用真实域)
RT_MEMBER = 9901
RT_MEMBER_OTHER = 9902


class Kb57RedteamService:
    """57号红队验证(七向量——确定性)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 红队入口(七向量全量)
    # ============================================================

    async def run_all(self) -> dict:
        """执行七向量红队全量(RT-01~07)

        前置: KB57_MODE=shadow/assist(决策面
        开放——off 态无攻击面; RT-03/06/07 需
        assist 会员面, 内部自行切换)。
        """
        mode = os.environ.get("KB57_MODE", "off")
        if mode == "off":
            raise ValueError(
                "红队需要 KB57_MODE=shadow/assist"
                "(决策面开放——off 态无攻击面)")

        vectors = {}
        vectors["RT-01"] = await \
            self._rt01_signal_poison()
        vectors["RT-02"] = await \
            self._rt02_whitelist_escape()
        vectors["RT-03"] = await \
            self._rt03_pii_leak()
        vectors["RT-04"] = await \
            self._rt04_seed_contamination()
        vectors["RT-05"] = await \
            self._rt05_budget_exhaust()
        vectors["RT-06"] = await \
            self._rt06_access_bypass()
        vectors["RT-07"] = await \
            self._rt07_expired_seed()

        defended = sum(
            1 for v in vectors.values()
            if v.get("defended"))
        return {
            "success": True,
            "vectors": vectors,
            "summary": {
                "total": len(vectors),
                "defended": defended,
                "allDefended":
                    defended == len(vectors),
            },
            "note": "红队七向量——确定性离线可复现",
            "ranAt": ts(),
        }

    # ============================================================
    # RT-01 知识投毒(伪造缺口信号灌入)
    # ============================================================

    async def _rt01_signal_poison(self) -> dict:
        """伪造信号源事件灌入(未知 eventType+
        伪造字段)→ scan 白名单纯读取不污染"""
        from core.helpers import ts as _ts
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        repo55 = Qr55Repository()
        forged_events = [
            {"eventType": "backdoor_pressure",
             "detail": {
                 "necessityScore": 999.0,
                 "hits": [{"signalId": "backdoor",
                           "weight": 1.0}]}},
            {"eventType": "poison_inject",
             "detail": {"metrics": {
                 "satisfactionScore": -999.0}}},
        ]
        for fe in forged_events:
            meid = await repo55.next_model_event_id()
            await repo55.save_model_event({
                "modelEventId": meid,
                "eventType": fe["eventType"],
                "detail": fe["detail"],
                "createdAt": _ts(),
            })

        # 攻击后扫描(白名单纯读取)
        from services.kb57_service import Kb57Service
        scan = await Kb57Service().scan_signals()

        from services.kb57_registry import (
            GAP_SIGNAL_REGISTRY,
        )
        whitelisted = set(
            GAP_SIGNAL_REGISTRY.keys())
        hits = scan.get("hits") or []
        results = [{
            "hitSignalIds": [
                h.get("signalId") for h in hits],
            "necessityScore":
                scan.get("necessityScore"),
        }]
        defended = all(
            h.get("signalId") in whitelisted
            for h in hits)
        return {
            "vector": "知识投毒(伪造缺口信号灌入)",
            "defended": defended,
            "results": results,
            "defense": "GAP_SIGNAL_REGISTRY 封闭白名单+"
                       "来源纯读取——注册表外通道"
                       "不入命中",
        }

    # ============================================================
    # RT-02 白名单逃逸(未注册源采集)
    # ============================================================

    async def _rt02_whitelist_escape(self) -> dict:
        """三路: 未注册源采集/白名单外指纹伪造/
        动态注册域外源——版权关全拒"""
        results = []

        # 路 ①: 采集器对未注册建议源跳过
        gap_id = await self._seed_gap(
            suggested=["darkweb_crawler",
                       "torrent_leak"],
            topic="rt02-gap")
        from services.kb57_collect_service import (
            Kb57CollectService,
        )
        r = await Kb57CollectService().run_collect(
            gap_id=gap_id)
        results.append({
            "path": "未注册建议源",
            "collected": r.get("collected")})
        path1_defended = \
            r.get("collected") == 0

        # 路 ②: 直建未注册源资源→版权关 blocked
        rid = await self._seed_resource(
            gap_id, source_id="darkweb_crawler",
            license_="")
        from services.\
            kb57_compliance_service import (
            Kb57ComplianceService,
        )
        comp = await (
            Kb57ComplianceService()
            .run_compliance(rid))
        results.append({
            "path": "未注册源鉴别",
            "verdict": comp.get("verdict")})
        path2_defended = \
            comp.get("verdict") == "blocked"

        # 路 ③: 白名单外源重复注册拒绝
        # (封闭域——已由 P0 测试覆盖, 此处
        # 校验动态注册后即可通过鉴别)
        from services.kb57_registry import (
            SOURCE_TYPES,
        )
        # 合法注册一个新源→通过(对照)
        reg_source = await self._register_source(
            "rt02_legit_source", "partner", 0.8)
        rid2 = await self._seed_resource(
            gap_id, source_id="rt02_legit_source",
            license_="测试授权")
        comp2 = await (
            Kb57ComplianceService()
            .run_compliance(rid2))
        results.append({
            "path": "注册后通过(对照)",
            "verdict": comp2.get("verdict")})
        # 注册域源默认可信度 0.8>=0.75——passed
        path3_ok = comp2.get("verdict") in (
            "passed", "quarantined")

        return {
            "vector": "白名单逃逸(未注册源)",
            "defended": path1_defended
            and path2_defended and path3_ok,
            "results": results,
            "defense": "SOURCE_REGISTRY 白名单+"
                       "版权关第一道阻断+admin "
                       "动态注册域(封闭)",
        }

    # ============================================================
    # RT-03 PII 泄漏(未脱敏内容入库)
    # ============================================================

    async def _rt03_pii_leak(self) -> dict:
        """PII 内容三路: 鉴别脱敏/隔离态不可浏览/
        发布态 maskedText 无明文"""
        results = []

        # PII 资源鉴别(脱敏)
        gap_id = await self._seed_gap(
            topic="rt03-gap")
        pii_text = ("contact 110101199001011234 "
                    "ph 13800138000 card "
                    "6222020200112233445")
        rid = await self._seed_resource(
            gap_id, content_text=pii_text)
        from services.\
            kb57_compliance_service import (
            Kb57ComplianceService,
        )
        comp = await (
            Kb57ComplianceService()
            .run_compliance(rid))
        masked = str(
            (comp.get("gates") or {})
            .get("privacy") or {})
        results.append({
            "path": "鉴别脱敏",
            "verdict": comp.get("verdict"),
            "maskedFields": len(
                comp.get("maskedFields") or [])})

        # 存储读回(maskedText 无明文)
        stored = await self.repo.get_resource(rid)
        masked_text = str(
            stored.get("maskedText") or "")
        leak = ("110101199001011234"
                in masked_text
                or "13800138000" in masked_text
                or "6222020200112233445"
                in masked_text)
        results.append({
            "path": "存储读回",
            "leaked": leak})

        # 隔离态资源伪造种子→浏览拒绝
        # (quarantined 永不暴露)
        seed_id = await self._seed_seed(
            gap_id, rid, status="sandbox",
            fingerprint="")
        os.environ["KB57_MODE"] = "assist"
        try:
            from services.kb57_feed_service import (
                Kb57FeedService,
            )
            try:
                await Kb57FeedService().view(
                    RT_MEMBER, seed_id)
                view_blocked = False
            except ValueError:
                view_blocked = True
        finally:
            os.environ["KB57_MODE"] = "shadow"
        results.append({
            "path": "隔离态浏览",
            "blocked": view_blocked})

        defended = (
            comp.get("verdict") == "passed"
            and len(comp.get("maskedFields")
                    or []) >= 3
            and not leak
            and view_blocked)
        return {
            "vector": "PII 泄漏(未脱敏入库)",
            "defended": defended,
            "results": results,
            "defense": "隐私关 lookaround 扫描+渐进"
                       "脱敏+隔离态永不暴露铁律",
        }

    # ============================================================
    # RT-04 种子污染(非合规资源锻造)
    # ============================================================

    async def _rt04_seed_contamination(self) -> dict:
        """三路: quarantined 资源锻造/无指纹资源
        锻造/rejected 资源锻造——无指纹不入库铁律"""
        gap_id = await self._seed_gap(
            topic="rt04-gap")
        results = []
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        ws = Kb57SeedService()

        # 路 ①: quarantined 资源
        rid1 = await self._seed_resource(
            gap_id, status="quarantined")
        try:
            await ws.craft(gap_id, rid1)
            r1 = False
        except ValueError:
            r1 = True
        results.append({"path": "quarantined 锻造",
                        "rejected": r1})

        # 路 ②: 无指纹 compliant 资源
        rid2 = await self._seed_resource(
            gap_id, status="compliant",
            fingerprint="")
        try:
            await ws.craft(gap_id, rid2)
            r2 = False
        except ValueError:
            r2 = True
        results.append({"path": "无指纹锻造",
                        "rejected": r2})

        # 路 ③: rejected 资源
        rid3 = await self._seed_resource(
            gap_id, status="rejected",
            fingerprint="sha256:valid")
        try:
            await ws.craft(gap_id, rid3)
            r3 = False
        except ValueError:
            r3 = True
        results.append({"path": "rejected 锻造",
                        "rejected": r3})

        return {
            "vector": "种子污染(非合规资源锻造)",
            "defended": r1 and r2 and r3,
            "results": results,
            "defense": "无指纹不入库铁律——craft "
                       "仅消费 compliant 态+指纹"
                       "前缀双重校验",
        }

    # ============================================================
    # RT-05 预算耗尽(缺口级超支硬测)
    # ============================================================

    async def _rt05_budget_exhaust(self) -> dict:
        """缺口 cap 已满→鉴别 halted 熔断+
        会员浏览预算不足拒绝"""
        # 路 ①: 缺口级封顶熔断
        gap_id = await self._seed_gap(
            topic="rt05-gap")
        gap = await self.repo.get_gap(gap_id)
        gap["budgetSpent"] = 0.1   # cap 已满
        await self.repo.save_gap(
            gap, create=False)
        rid = await self._seed_resource(
            gap_id)
        from services.\
            kb57_compliance_service import (
            Kb57ComplianceService,
        )
        comp = await (
            Kb57ComplianceService()
            .run_compliance(rid))
        results = [{
            "path": "缺口级熔断",
            "verdict": comp.get("verdict"),
            "fingerprint": bool(
                comp.get("fingerprint"))}]

        # 路 ②: 会员浏览预算耗尽
        # (49号账户用尽→view 拒绝)
        os.environ["KB57_MODE"] = "assist"
        try:
            from services.kb57_feed_service import (
                Kb57FeedService,
            )
            # 预耗尽账户(直建已用尽记录)
            from services.\
                xiaozhu_privacy_service import (
                XiaozhuPrivacyService,
            )
            privacy = XiaozhuPrivacyService()
            budget = await privacy._account(
                RT_MEMBER)
            budget["usedToday"] = 999.0
            await privacy.repo.\
                save_privacy_budget(budget)
            # 种发布态种子
            seed_id = await self._seed_seed(
                gap_id, rid, status="published",
                fingerprint="sha256:valid")
            try:
                await Kb57FeedService().view(
                    RT_MEMBER, seed_id)
                view_rejected = False
            except ValueError:
                view_rejected = True
            results.append({
                "path": "会员浏览预算",
                "rejected": view_rejected})
        finally:
            os.environ["KB57_MODE"] = "shadow"

        # 恢复预算账户(污染清理)
        try:
            budget2 = await privacy._account(
                RT_MEMBER)
            budget2["usedToday"] = 0.0
            await privacy.repo.\
                save_privacy_budget(budget2)
        except Exception:  # noqa: BLE001
            pass

        return {
            "vector": "预算耗尽(超支硬测)",
            "defended": comp.get("verdict")
            == "halted"
            and not comp.get("fingerprint")
            and view_rejected,
            "results": results,
            "defense": "预算关缺口级封顶熔断 halted"
                       "(不降级放行)+49号会员"
                       "浏览计量不足拒绝",
        }

    # ============================================================
    # RT-06 入口越权(他人学习记录)
    # ============================================================

    async def _rt06_access_bypass(self) -> dict:
        """三路: 他人路径推进/他人 my/learning
        (HTTP 层属主校验)/他人反馈伪造——
        属主校验拒绝"""
        results = []

        # 路 ①: 路径属主越权
        gap_id = await self._seed_gap(
            topic="rt06-gap")
        rid = await self._seed_resource(
            gap_id)
        os.environ["KB57_MODE"] = "assist"
        try:
            from services.kb57_feed_service import (
                Kb57FeedService,
            )
            fs = Kb57FeedService()
            seed_id = await self._seed_seed(
                gap_id, rid, status="published",
                fingerprint="sha256:valid")
            p = await fs.create_path(
                RT_MEMBER, seed_ids=[seed_id])
            path_id = p.get("pathId")
            try:
                await fs.advance_path(
                    RT_MEMBER_OTHER, path_id,
                    seed_id)
                r1 = False
            except ValueError:
                r1 = True
            results.append({"path": "他人路径推进",
                            "rejected": r1})

            # 路 ②: 会员学习记录属主隔离
            # (my_learning 按 member 域隔离——
            #  他人 header 读不到 RT_MEMBER 记录)
            mine = await fs.my_learning(
                RT_MEMBER_OTHER)
            r2 = len(
                mine.get("paths") or []) == 0
            results.append({"path": "他人学习记录",
                            "isolated": r2})

            # 路 ③: 反馈属主一致性
            # (feedback 以 member_id 落账——
            #  伪造他人 member 只污染自身域)
            await fs.feedback(
                RT_MEMBER_OTHER, seed_id,
                kind="positive")
            fb = await fs._member_history(
                RT_MEMBER_OTHER)
            r3 = all(
                int(h.get("seedId") or 0)
                == seed_id for h in fb)
            results.append({"path": "反馈落账域",
                            "selfDomain": r3})
        finally:
            os.environ["KB57_MODE"] = "shadow"

        return {
            "vector": "入口越权(他人学习记录)",
            "defended": r1 and r2 and r3,
            "results": results,
            "defense": "路径推进属主校验+学习记录"
                       "按会员域隔离+反馈落账"
                       "自域一致",
        }

    # ============================================================
    # RT-07 过期种子误导(有效期绕过)
    # ============================================================

    async def _rt07_expired_seed(self) -> dict:
        """三路: 过期种子自动降权/降权种子
        推荐池剔除/过期种子入路径拒绝"""
        from datetime import datetime, timedelta
        results = []

        gap_id = await self._seed_gap(
            topic="rt07-gap")
        rid = await self._seed_resource(
            gap_id)

        # 路 ①: 过期发布态种子→freshness 降权
        expired_seed = await self._seed_seed(
            gap_id, rid, status="published",
            fingerprint="sha256:valid")
        seed = await self.repo.get_seed(
            expired_seed)
        seed["validUntil"] = (
            datetime.utcnow()
            - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        await self.repo.save_seed(
            seed, create=False)
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        fresh = await (
            Kb57SeedService().freshness_check())
        stored = await self.repo.get_seed(
            expired_seed)
        results.append({
            "path": "过期自动降权",
            "status": stored.get("status"),
            "demoted": fresh.get("demoted")})
        r1 = stored.get("status") == "downgraded"

        # 路 ②: 降权种子推荐池剔除
        # (downgraded 不在 FEEDABLE_STATUSES)
        os.environ["KB57_MODE"] = "assist"
        try:
            from services.kb57_feed_service import (
                Kb57FeedService,
                FEEDABLE_STATUSES,
            )
            in_pool = "downgraded" \
                in FEEDABLE_STATUSES
            feed = await Kb57FeedService().feed(
                RT_MEMBER, role="citizen")
            seed_ids = [
                x.get("seedId") for x in
                (feed.get("recommendations")
                 or [])]
            results.append({
                "path": "推荐池剔除",
                "inFeed": expired_seed in seed_ids})
            r2 = not in_pool \
                and expired_seed not in seed_ids

            # 路 ③: 过期种子入路径拒绝
            # (状态机——仅发布态可入)
            fresh_seed = await self._seed_seed(
                gap_id, rid, status="published",
                fingerprint="sha256:valid")
            s2 = await self.repo.get_seed(
                fresh_seed)
            s2["validUntil"] = (
                datetime.utcnow()
                - timedelta(days=1)
            ).strftime("%Y-%m-%d")
            await self.repo.save_seed(
                s2, create=False)
            # 状态仍 published 但过期——
            # freshness 后置为 downgraded
            await (Kb57SeedService()
                   .freshness_check())
            try:
                await Kb57FeedService() \
                    .create_path(
                        RT_MEMBER,
                        seed_ids=[fresh_seed])
                r3 = False
            except ValueError:
                r3 = True
            results.append({
                "path": "过期入路径",
                "rejected": r3})
        finally:
            os.environ["KB57_MODE"] = "shadow"

        return {
            "vector": "过期种子误导(有效期绕过)",
            "defended": r1 and r2 and r3,
            "results": results,
            "defense": "有效期元数据+freshness 自动"
                       "降权+推荐池/路径状态机"
                       "双重过滤",
        }

    # --------------------------------------------------------
    # 种子辅助(最小直建——向量隔离)
    # --------------------------------------------------------

    async def _seed_gap(self, topic: str,
                        suggested: list = None
                        ) -> int:
        """最小缺口直建"""
        gap_id = await self.repo.next_gap_id()
        await self.repo.save_gap({
            "gapId": gap_id,
            "status": "open",
            "priority": "high",
            "topic": topic,
            "decision": "collect",
            "signalSnapshot": {
                "hits": [
                    {"signalId": "kb_gap_open",
                     "value": 1,
                     "evidence": "rt-seed"}],
                "necessityScore": 40.0,
                "sideCoverage": 0.2},
            "necessityScore": 40.0,
            "trustScore": 60.0,
            "suggestedSources": suggested
            or ["gov_policy_official"],
            "budgetCap": 0.1,
            "budgetSpent": 0.0,
            "llmCalls": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        return gap_id

    async def _seed_resource(
            self, gap_id: int,
            source_id: str = "gov_policy_official",
            content_text: str = None,
            license_: str = "公开政务(署名标注)",
            status: str = "quarantined",
            fingerprint: str = None) -> int:
        """最小资源直建(状态可控)"""
        rid = await self.repo.next_resource_id()
        text = content_text or (
            "step 1 apply; step 2 review; "
            "step 3 result")
        if fingerprint is None:
            fingerprint = (
                "sha256:" + hashlib.sha256(
                    f"rt-{rid}".encode(
                        "utf-8")).hexdigest()[:32])
        await self.repo.save_resource({
            "resourceId": rid,
            "gapId": gap_id,
            "sourceId": source_id,
            "sourceType": "authority",
            "sourceCredibility": 0.95,
            "license": license_,
            "title": "rt-resource",
            "contentText": text,
            "maskedText": "",
            "contentHash": "sha256:" + hashlib.sha256(
                f"ch-{rid}".encode(
                    "utf-8")).hexdigest()[:32],
            "status": status,
            "reviewRequired": False,
            "budgetHalted": False,
            "resourceVersion": 1,
            "complianceReports": [],
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        return rid

    async def _seed_seed(self, gap_id: int,
                         resource_id: int,
                         status: str = "sandbox",
                         fingerprint: str = None
                         ) -> int:
        """最小种子直建(状态可控)"""
        seed_id = await self.repo.next_seed_id()
        if fingerprint is None:
            fingerprint = (
                "sha256:" + hashlib.sha256(
                    f"rt-seed-{seed_id}".encode(
                        "utf-8")).hexdigest()[:32])
        await self.repo.save_seed({
            "seedId": seed_id,
            "seedVersion": 1,
            "type": "text",
            "title": "rt-seed",
            "content": {"text": "c",
                        "mediaRef": None,
                        "transcript": None,
                        "keyframes": None,
                        "alt": None},
            "contentHash": "sha256:x",
            "complianceFingerprint": fingerprint,
            "valueTags": ["policy"],
            "sourceId": "gov_policy_official",
            "sourceCredibility": 0.95,
            "privacyCost": 0.002,
            "knowledgeReason": "rt",
            "humanVerified": True,
            "validUntil": "2099-01-01",
            "abTest": {"active": False,
                       "variantOf": None},
            "status": status,
            "gapId": gap_id,
            "resourceId": resource_id,
            "viewCount": 0,
            "positiveCount": 0,
            "negativeCount": 0,
            "pooledFeedbackId": 0,
            "llmCalls": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        return seed_id

    async def _register_source(self, key: str,
                                source_type: str,
                                credibility: float
                                ) -> int:
        """动态注册源(红队对照)"""
        source_id = await self.repo.next_source_id()
        await self.repo.save_source({
            "sourceId": source_id,
            "sourceKey": key,
            "label": "rt-source",
            "sourceType": source_type,
            "credibility": credibility,
            "license": "测试授权",
            "reviewRequired":
                credibility < 0.75,
            "status": "active",
            "createdAt": ts(),
        })
        return source_id
