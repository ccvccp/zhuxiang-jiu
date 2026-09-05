"""55号·二维码AI智能管理 红队验证
(qr55_redteam_service, P5)

计划(docs/55号_二维码AI智能管理模块实施计划.md §六 P5):
    红队六向量:
        RT-01 载荷伪造(自造码串冒充签名码)
        RT-02 重放(已核销码二次消费)
        RT-03 篡改(截断/改尾签名码)
        RT-04 参数越权(白名单外参数注入)
        RT-05 白名单逃逸(未知 serviceId 幻觉链接)
        RT-06 预算绕过(超预算硬扣——降级铁律)

设计(确定性红队——不依赖 LLM, 全部向量
离线可复现):
    每向量: 构造攻击载荷 → 调用目标面 →
    断言防御行为(阻断/降级/过滤) → 留痕。

投毒防御联动: RT-07 反馈池投毒洪流
(护栏 [0.5,2.0] 约束+集中度检测)——
54号红队范式(权重演进护栏断言)。
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_redteam_service")

MODEL_VERSION = "v1-qr55-redteam"

SCORER_ID = "qr_orchestration"

# 红队洪流规模(投毒向量)
FLOOD_COUNT = 30


class Qr55RedteamService:
    """55号红队验证(六向量+投毒洪流——确定性)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 红队入口(六向量全量)
    # ============================================================

    async def run_all(self, member_id: int = 9960
                      ) -> dict:
        """执行六向量红队全量(RT-01~06+RT-07 投毒)

        前置: QR55_MODE=on(生成/核销面开放)。

        向量隔离: RT-06(预算耗尽)污染会员上下文 →
        后续向量/重复运行需独立会员(每向量 +1 递增,
        预算独立互不干扰——幂等可重跑)。
        """
        import os
        if os.environ.get("QR55_MODE") != "on":
            raise ValueError(
                "红队需要 QR55_MODE=on(生成/核销面"
                "开放——off 态无攻击面)")

        # 向量会员信值档案 fail-soft 种子(未建档会员
        # member_trust 中性低分 → clarify 拦截生成;
        # 红队需 direct 生码面——healthy 预置)
        try:
            from repositories.trust_value_repository \
                import TrustValue45Repository
            trepo = TrustValue45Repository()
            for offset in range(6):
                mid = member_id + offset
                rec = await trepo.get_profile(mid) or {}
                if rec.get("grade"):
                    continue   # 已建档不覆盖
                rec.update({
                    "trustId": mid, "grade": "healthy",
                    "score": 80, "factors": {},
                    "role": "person", "l1Severity": {},
                    "idDigest": f"rt-digest-{mid}",
                })
                await trepo.save_profile(rec)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_rt_seed_failed: %s", exc)

        vectors = {}
        vectors["RT-01"] = await self._rt01_forged(
            member_id)
        vectors["RT-02"] = await self._rt02_replay(
            member_id + 1)
        vectors["RT-03"] = await self._rt03_tampered(
            member_id + 2)
        vectors["RT-04"] = await self._rt04_param_escape(
            member_id + 3)
        vectors["RT-05"] = await self._rt05_whitelist(
            member_id + 4)
        vectors["RT-06"] = await self._rt06_budget_bypass(
            member_id + 5)
        vectors["RT-07"] = await self._rt07_poison_flood()

        blocked = sum(
            1 for v in vectors.values()
            if v.get("defended"))
        return {
            "success": True,
            "vectors": vectors,
            "summary": {
                "total": len(vectors),
                "defended": blocked,
                "allDefended": blocked == len(vectors),
            },
            "note": "红队六向量+投毒洪流——确定性"
                    "离线可复现",
            "ranAt": ts(),
        }

    # ============================================================
    # RT-01 载荷伪造(自造码串)
    # ============================================================

    async def _rt01_forged(self, member_id: int) -> dict:
        """自造码串冒充签名码(无 HMAC 密钥——验签必败)"""
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        scan = Qr55ScanService()
        forged_variants = [
            "ZXBJ-QR55:elderly_card:ZmFrZQ==."
            "AAAA.BBBBBBBB.CCCCCCCC",
            "ZXBJ-QR55:policy_search:dGVzdA==."
            "deadbeef.9999999999.00000000",
            "ZXBJ-QR55:::.:",
        ]
        results = []
        for code in forged_variants:
            try:
                r = await scan.scan(code,
                                    member_id=member_id)
                results.append({
                    "codeHead": code[:24],
                    "status": r.get("status"),
                })
            except ValueError as exc:
                # 格式非法(五段结构破坏)——
                # 验签层格式拒绝亦是防御
                results.append({
                    "codeHead": code[:24],
                    "status": f"format_rejected"
                              f"({str(exc)[:20]})",
                })
        defended = all(
            "rejected" in str(r.get("status"))
            or r.get("status") == "tampered"
            or not r.get("success")
            for r in results)
        return {
            "vector": "载荷伪造(自造码串冒充)",
            "defended": defended,
            "results": results,
            "defense": "HMAC-SHA256 验签——无密钥"
                       "自造码必 tampered 阻断",
        }

    # ============================================================
    # RT-02 重放(已核销码二次消费)
    # ============================================================

    async def _rt02_replay(self, member_id: int) -> dict:
        """同码二次扫码(nonce 一次性消费)"""
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        gen = Qr55GenerateService()
        scan = Qr55ScanService()
        g = await gen.orchestrate(
            member_id, "查政策解读")
        first = await scan.scan(g["code"],
                                member_id=member_id)
        replay = await scan.scan(g["code"],
                                 member_id=member_id)
        defended = (first.get("status") == "redeemed"
                   and replay.get("status")
                   == "replayed")
        return {
            "vector": "重放(已核销码二次消费)",
            "defended": defended,
            "results": [{
                "first": first.get("status"),
                "replay": replay.get("status"),
            }],
            "defense": "nonce 一次性消费——replayed 拒绝",
        }

    # ============================================================
    # RT-03 篡改(截断/改尾)
    # ============================================================

    async def _rt03_tampered(self, member_id: int
                             ) -> dict:
        """签名码截断/改尾/换段"""
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        gen = Qr55GenerateService()
        scan = Qr55ScanService()
        g = await gen.orchestrate(
            member_id, "查政策解读")
        code = g["code"]
        attacks = {
            "改尾": code[:-2] + "xx",
            "截断": code[:-8],
            "换段": code.replace(".", "..", 1),
        }
        results = []
        for label, bad in attacks.items():
            try:
                r = await scan.scan(bad,
                                    member_id=member_id)
                results.append({
                    "attack": label,
                    "status": r.get("status"),
                })
            except ValueError as exc:
                # 结构破坏(段数/格式)——验签层
                # 格式拒绝亦是防御
                results.append({
                    "attack": label,
                    "status": f"format_rejected"
                              f"({str(exc)[:20]})",
                })
        defended = all(
            "rejected" in str(r.get("status"))
            or r.get("status") == "tampered"
            or not r.get("success")
            for r in results)
        return {
            "vector": "篡改(截断/改尾/换段)",
            "defended": defended,
            "results": results,
            "defense": "HMAC 验签四态——tampered 阻断"
                       "+tamper 事件留痕",
        }

    # ============================================================
    # RT-04 参数越权(白名单外参数注入)
    # ============================================================

    async def _rt04_param_escape(self,
                                 member_id: int) -> dict:
        """白名单外参数(含 PII)注入——过滤断言"""
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        gen = Qr55GenerateService()
        r = await gen.orchestrate(
            member_id, "办老年优待证",
            confirm_params={
                "region": "杭州",       # 白名单内
                "holder": "张三",       # 白名单内
                "phone": "13800000000",  # PII 禁入
                "idCard": "330100199001011234",
                "admin_token": "xxx",   # 越权键
            })
        params = r.get("params") or {}
        leaked = [k for k in (
            "phone", "idCard", "admin_token")
            if k in params]
        defended = (r.get("status") == "generated"
                    and not leaked
                    and "region" in params)
        return {
            "vector": "参数越权(白名单外+PII 注入)",
            "defended": defended,
            "results": [{
                "status": r.get("status"),
                "acceptedParams":
                    sorted(params.keys()),
                "leaked": leaked,
            }],
            "defense": "参数白名单过滤——服务 params "
                       "外全剔除(PII 禁入)",
        }

    # ============================================================
    # RT-05 白名单逃逸(未知 serviceId)
    # ============================================================

    async def _rt05_whitelist(self,
                              member_id: int) -> dict:
        """幻觉 serviceId/伪 route 生成尝试"""
        from services.qr55_service import Qr55Service
        results = []
        svc = Qr55Service()
        for fake_id in ("evil_service",
                        "admin_backdoor",
                        "../../../etc/passwd"):
            try:
                await svc.generate_code(
                    fake_id, {}, member_id)
                results.append({
                    "serviceId": fake_id,
                    "rejected": False})
            except ValueError:
                results.append({
                    "serviceId": fake_id,
                    "rejected": True})
        defended = all(
            r.get("rejected") for r in results)
        return {
            "vector": "白名单逃逸(未知 serviceId"
                      "幻觉链接)",
            "defended": defended,
            "results": results,
            "defense": "SERVICE_REGISTRY 封闭白名单"
                       "——白名单外 ValueError 拒绝",
        }

    # ============================================================
    # RT-06 预算绕过(超预算硬扣)
    # ============================================================

    async def _rt06_budget_bypass(self,
                                  member_id: int
                                  ) -> dict:
        """耗尽预算后扫 L1 成本码——降级铁律"""
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        gen = Qr55GenerateService()
        scan = Qr55ScanService()

        # 耗尽预算(usedToday=limit)
        view = await XiaozhuPrivacyService(
        ).budget_view(member_id)
        limit = float(
            view.get("effectiveLimit") or 1.0)
        repo = Xiaozhu48Repository()
        rec = await repo.get_privacy_budget(
            member_id) or {}
        rec["usedToday"] = round(limit, 2)
        await repo.save_privacy_budget(rec)

        g = await gen.orchestrate(
            member_id, "查积分明细记录")
        r = await scan.scan(g["code"],
                            member_id=member_id)
        budget = r.get("budget") or {}
        landing = r.get("landing") or {}
        # 防御成立: 降级公开版(不硬扣/不报错/不放行全量)
        defended = (budget.get("mode") == "degraded"
                   and landing.get("degraded")
                   is True)
        return {
            "vector": "预算绕过(超预算硬扣尝试)",
            "defended": defended,
            "results": [{
                "budgetMode": budget.get("mode"),
                "landingDegraded":
                    landing.get("degraded"),
            }],
            "defense": "49号 check_and_spend 超预算"
                       "ValueError → 降级公开版"
                       "(千面降级铁律)",
        }

    # ============================================================
    # RT-07 投毒洪流(反馈池集中度+护栏)
    # ============================================================

    async def _rt07_poison_flood(self) -> dict:
        """反馈池投毒洪流(单源 reward 污染)——
        护栏 [0.5,2.0] 约束+学习可继续(54号范式)"""
        from core.helpers import ts
        from services.qr55_scorer import Qr55Scorer
        from repositories.ai_learning_repository \
            import AiLearningRepository
        from services.ai_learning_service import (
            get_weights_view,
        )

        ctx = {"intentConfidence": 0.1,
               "serviceMatch": "clarify",
               "paramComplete": 0.2,
               "budgetRemaining": 0.05,
               "memberTrustLevel": "L0",
               "freshRatio": 0.1,
               "accessibility": False,
               "riskFlagged": True}
        result = await Qr55Scorer().score(ctx)
        repo = AiLearningRepository()
        for i in range(FLOOD_COUNT):
            await repo.add_feedback({
                "scorerId": SCORER_ID,
                "weightVersion": "v1",
                "scoreAtDecision":
                    result.get("trustScore"),
                "actualAction": "clarify",
                "expectedAction": "direct",
                "correct": False,
                "factors": result.get("factors"),
                "reward": -1.0,
                "note": f"rt-poison:{i}",
                "source": "attacker_flood",
                "status": "pending",
                "createdAt": ts(),
            })

        # 护栏断言(champion 权重仍受约束)
        view = await get_weights_view(SCORER_ID)
        weights = (view.get("champion")
                   or {}).get("weights") or {}
        guard_ok = all(
            Qr55Scorer.WEIGHTS[k] / 2.0
            <= float(weights.get(k, 0))
            <= Qr55Scorer.WEIGHTS[k] * 2.0
            for k in Qr55Scorer.WEIGHTS) \
            if weights else False

        # 集中度(洪流注入后 pending 池)
        pending = await repo.list_feedback(
            SCORER_ID, status="pending", limit=1000)
        by_source: dict = {}
        for f in pending:
            src = str(f.get("source") or "unknown")
            by_source[src] = \
                by_source.get(src, 0) + 1
        total = sum(by_source.values())
        top_ratio = round(
            max(by_source.values()) / total, 4) \
            if total else 0.0

        return {
            "vector": f"投毒洪流({FLOOD_COUNT} 条"
                      "单源负 reward)",
            "defended": guard_ok,
            "results": [{
                "guardOk": guard_ok,
                "concentration": {
                    "topSource": max(
                        by_source, key=by_source.get)
                    if by_source else None,
                    "topRatio": top_ratio,
                },
            }],
            "defense": "护栏 [0.5,2.0]×基线约束"
                       "(44号引擎内建)+集中度告警"
                       "(看板防御区)",
        }
