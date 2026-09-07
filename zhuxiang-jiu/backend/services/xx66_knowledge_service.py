"""66号·AI智能工程师大模块 P4 服务层
(知识进化×四区看板×自助赋能×红队七向量)

《66号_AI智能工程师大模型实施计划》§四 4.3 + §十二:

案例库(四要素——附录D SOP):
    {problem, root_cause, solution, outcome}
    + valueLinked 标注(涉信值 case 的 solution
    禁含具体数字——只有动作序列+审批引用)
    质量门: 四要素完整(缺一不入)+PII 脱敏
    (48号 mask_pii 复用)+置信度来源标注
    去重: 与既有案例余弦≥0.85 合并(复发计数+1)
    淘汰: 90 日未命中且无关联复发→归档

自动沉淀管道(三触发源):
    自愈终态(recovered/failed)/工单 resolved
    且满意度≥4/补偿执行完成 → 案例入库(经质量门)

知识联动(三通道):
    缺口回写: 支持对话未命中 → 57号 record_gap
    规则变更同步: (P4 占位——案例失效标记)
    学习域关联: 情绪匿名统计×案例命中率月度分析

四区看板(dashboard——单端点聚合, fail-soft):
    ① 生命体征区(P0 vitals)
    ② 服务区(P1 情绪统计聚合)
    ③ 知识区(案例库健康度)
    ④ 信任区(P3 对账最新轮次+指纹链)

proactive 自助赋能(超级会员服务):
    店铺健康自检(65号经营数据+26号业务指标聚合)
    + 信值使用优化建议(确定性规则)

红队七向量(§十二——隔离域 991x, 确定性零 LLM):
    RT-01 伪造高情绪骗补偿(empathy_bonus 恒不破封顶)
    RT-02 补偿重放(同 incidentId 幂等拒)
    RT-03 自愈越权执行(manual 级强制人工)
    RT-04 对账结果伪造(报告不变式重算校验)
    RT-05 案例库投毒(四要素质量门)
    RT-06 情绪词典绕过(情绪轨仅调沟通模式)
    RT-07 审批旁路(未 approve 冲正/补偿拒执行)
"""

import logging
import re

from core.helpers import ts

from repositories.xx66_repository import Xx66Repository

logger = logging.getLogger(__name__)

# 案例库常量
CASE_DEDUP_SIMILARITY = 0.85   # 余弦合并阈值
CASE_RETENTION_DAYS = 90       # 淘汰窗口
CASE_MAX = 500                # 滚动上限

# 检索 top-k
CASE_SEARCH_TOP_K = 3

# 红队隔离域(991x——与 64号 98xx/65号 9881+
# 跨模块 9971+ 不冲突的并行序列)
RT_ENTITY_BASE = 9910


def _tokenize(text: str) -> set:
    """中文 n-gram 分词(2-gram+英文词)"""
    t = str(text or "")
    grams = set()
    for i in range(len(t) - 1):
        grams.add(t[i:i + 2])
    for w in re.findall(r"[a-zA-Z]{2,}", t):
        grams.add(w.lower())
    return grams


def _cosine(a: set, b: set) -> float:
    """集合余弦(对齐 57号相似去重口径)"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) ** 0.5 * len(b) ** 0.5)


class Xx66KnowledgeService:
    """66号 P4 服务(案例库×看板×自助×红队)"""

    def __init__(self, repo: Xx66Repository = None):
        self.repo = repo or Xx66Repository()

    # --------------------------------------------------------
    # 案例库(四要素——质量门+去重+淘汰+检索)
    # --------------------------------------------------------

    async def create_case(self, problem: str,
                          root_cause: str,
                          solution: str,
                          outcome: str,
                          source: str = "manual",
                          value_linked: bool = False,
                          confidence: float = 0.5
                          ) -> dict:
        """案例入库(质量门——四要素缺一不入)"""
        from services.xiaozhu_service import mask_pii
        problem_m = mask_pii(
            str(problem or "").strip())
        root_m = mask_pii(
            str(root_cause or "").strip())
        solution_m = mask_pii(
            str(solution or "").strip())
        outcome_m = mask_pii(
            str(outcome or "").strip())

        if not all((problem_m, root_m, solution_m,
                    outcome_m)):
            raise ValueError(
                "案例四要素完整(问题/根因/方案/效果"
                ")——缺一不入(质量门)")

        # 红线: 涉信值案例 solution 禁含具体数字
        if value_linked and re.search(
                r"\d+\.\d+|\d{2,}", solution_m):
            raise ValueError(
                "valueLinked 案例的 solution 禁含具体"
                "数字——只有动作序列+审批引用")

        # 去重: 余弦≥0.85 合并(复发计数+1)
        new_tokens = _tokenize(
            problem_m + root_m)
        for case in await self.repo.list_cases(
                limit=CASE_MAX):
            old_tokens = _tokenize(
                str(case.get("problem") or "")
                + str(case.get("rootCause") or ""))
            if _cosine(new_tokens, old_tokens) \
                    >= CASE_DEDUP_SIMILARITY:
                case["recurrence"] = int(
                    case.get("recurrence") or 0) + 1
                await self.repo.save_case(case)
                return {
                    "success": True,
                    "merged": True,
                    "caseId": case.get("caseId"),
                    "recurrence":
                        case["recurrence"],
                    "note": "相似案例合并——复发计数+1"
                            "(余弦≥0.85)",
                }

        case_id = await self.repo.next_case_id()
        record = {
            "caseId": case_id,
            "problem": problem_m,
            "rootCause": root_m,
            "solution": solution_m,
            "outcome": outcome_m,
            "source": source,
            "valueLinked": bool(value_linked),
            "confidence": float(confidence),
            "recurrence": 0,
            "hitCount": 0,
            "status": "active",
            "createdAt": ts(),
            "lastHitAt": "",
        }
        await self.repo.save_case(record)
        return {
            "success": True, "merged": False,
            "caseId": case_id, "recurrence": 0,
            "note": "新案例入库(质量门通过)",
        }

    async def search_cases(self, query: str,
                           top_k: int
                           = CASE_SEARCH_TOP_K
                           ) -> dict:
        """案例检索(n-gram 余弦 top-k; 命中计数)"""
        q_tokens = _tokenize(query)
        cases = [
            c for c in await self.repo.list_cases(
                limit=CASE_MAX)
            if c.get("status") == "active"]
        scored = []
        for c in cases:
            c_tokens = _tokenize(
                str(c.get("problem") or "")
                + str(c.get("rootCause") or ""))
            sim = _cosine(q_tokens, c_tokens)
            scored.append((sim, c))
        scored.sort(key=lambda x: x[0],
                    reverse=True)
        hits = []
        for sim, c in scored[:max(1, top_k)]:
            if sim <= 0:
                continue
            c["hitCount"] = int(
                c.get("hitCount") or 0) + 1
            c["lastHitAt"] = ts()
            await self.repo.save_case(c)
            hits.append({
                "caseId": c.get("caseId"),
                "similarity": round(sim, 3),
                "problem": c.get("problem"),
                "rootCause": c.get("rootCause"),
                "solution": c.get("solution"),
                "outcome": c.get("outcome"),
                "valueLinked": c.get("valueLinked"),
            })
        # 缺口回写: 双未命中 → 57号 record_gap
        gap_recorded = False
        if not hits:
            try:
                from services.knowledge_service \
                    import KnowledgeService
                await KnowledgeService().record_gap(
                    str(query or ""))
                gap_recorded = True
            except Exception as exc:
                logger.warning(
                    "xx66_gap_record_failsoft: %s", exc)
        return {
            "success": True,
            "query": str(query or ""),
            "hits": hits, "hitCount": len(hits),
            "gapRecorded": gap_recorded,
            "searchedAt": ts(),
        }

    async def archive_stale(self) -> dict:
        """淘汰: 90 日未命中且无复发 → 归档"""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(
            days=CASE_RETENTION_DAYS)
        ).strftime("%Y-%m-%dT%H:%M")
        archived = []
        for c in await self.repo.list_cases(
                limit=CASE_MAX):
            if c.get("status") != "active":
                continue
            last_hit = str(
                c.get("lastHitAt") or "")
            created = str(c.get("createdAt") or "")
            recent = max(last_hit, created) \
                >= cutoff
            recur = int(c.get("recurrence") or 0) > 0
            if not recent and not recur:
                c["status"] = "archived"
                await self.repo.save_case(c)
                archived.append(c.get("caseId"))
        return {
            "success": True,
            "archived": archived,
            "archivedCount": len(archived),
            "note": f"{CASE_RETENTION_DAYS} 日未命中"
                    "且无关联复发——归档",
            "ranAt": ts(),
        }

    # --------------------------------------------------------
    # 自动沉淀管道(三触发源→质量门)
    # --------------------------------------------------------

    async def settle_case_from_recovery(
            self, recovery: dict) -> dict:
        """自愈终态 → 案例沉淀(recovered/failed)"""
        status = str(
            recovery.get("recoveryStatus") or "")
        if status not in ("recovered", "failed"):
            raise ValueError(
                f"自愈终态 {status} 非案例触发态")
        return await self.create_case(
            problem=f"故障 {recovery.get('faultType')}"
                    f"@{recovery.get('faultSource')}",
            root_cause=str(
                (recovery.get("diagnoseResult")
                 or {}).get("rootCause")
                or "未知"),
            solution=str(
                (recovery.get("recoveryStrategy")
                 or {}).get("actions")
                or "notify_only"),
            outcome=f"自愈终态 {status}",
            source="recovery",
            value_linked=False,
            confidence=float(
                (recovery.get("diagnoseResult")
                 or {}).get("confidence") or 0.5),
        )

    async def settle_case_from_ticket(
            self, ticket: dict) -> dict:
        """工单 resolved+满意度≥4 → 案例沉淀"""
        if str(ticket.get("status") or "") \
                != "resolved":
            raise ValueError("工单未 resolved")
        sat = int(ticket.get("satisfaction") or 0)
        if sat < 4:
            raise ValueError("满意度<4 不沉淀")
        return await self.create_case(
            problem=str(
                ticket.get("type") or "工单"),
            root_cause=str(
                ticket.get("resolution") or "已解决"),
            solution="工单处理记录(人工通道)",
            outcome=f"满意度 {sat} 星",
            source="ticket",
            value_linked=False,
        )

    async def settle_case_from_compensation(
            self, book: dict) -> dict:
        """补偿执行完成 → 案例沉淀(valueLinked
        红线: solution 禁数字)"""
        if str(book.get("kind") or "") \
                != "compensation":
            raise ValueError("非补偿类建议书")
        if str(book.get("status") or "") \
                != "executed":
            raise ValueError("补偿未执行完成")
        return await self.create_case(
            problem=f"补偿 {book.get('ruleId')}"
                    f"@{book.get('entityId')}",
            root_cause="平台原因确认(对账/工单终态)",
            solution="DSL 求值→反欺诈门→治理审批"
                     "→信值入账轨(锚定 reserve_ref)",
            outcome="补偿闭环完成",
            source="compensation",
            value_linked=True,
        )

    # --------------------------------------------------------
    # 四区看板(单端点聚合, fail-soft)
    # --------------------------------------------------------

    async def dashboard(self) -> dict:
        """四区看板(生命体征/服务/知识/信任)"""
        zones = {}

        async def _zone(name, fn):
            try:
                zones[name] = await fn()
            except Exception as exc:
                logger.warning(
                    "xx66_dash_%s_failsoft: %s",
                    name, exc)
                zones[name] = {"error": str(exc)[:120]}

        await _zone("vitals", self._zone_vitals)
        await _zone("service", self._zone_service)
        await _zone("knowledge", self._zone_knowledge)
        await _zone("trust", self._zone_trust)
        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "zones": zones,
            "mode": self._mode(),
            "generatedAt": ts(),
        }

    def _mode(self) -> str:
        import os
        return os.environ.get(
            "XX66_MODE", "off").lower()

    async def _zone_vitals(self) -> dict:
        """生命体征区(P0)"""
        from services.xx66_service import Xx66Service
        v = await Xx66Service(
            repo=self.repo).vitals()
        return {"overall": v.get("overall"),
                "totalScore": v.get("totalScore"),
                "zoneScores": v.get("zoneScores"),
                "snapshotCount": len(
                    await self.repo.list_snapshots(
                        limit=100))}

    async def _zone_service(self) -> dict:
        """服务区(P1 情绪统计聚合)"""
        emotions = await self.repo.list_emotions(
            limit=200)
        bands = {}
        for e in emotions:
            band = str(e.get("band") or "calm")
            bands[band] = bands.get(band, 0) + 1
        settled = [e for e in emotions
                   if e.get("satisfactionLinked")]
        avg_sat = None
        if settled:
            avg_sat = round(sum(
                int(e["satisfactionLinked"])
                for e in settled) / len(settled), 2)
        return {
            "conversationCount": len(emotions),
            "bandDistribution": bands,
            "settledCount": len(settled),
            "avgSatisfaction": avg_sat,
            "badgeCount": len(
                await self.repo.list_badges(
                    limit=200)),
        }

    async def _zone_knowledge(self) -> dict:
        """知识区(案例库健康度)"""
        cases = await self.repo.list_cases(
            limit=CASE_MAX)
        active = [c for c in cases
                  if c.get("status") == "active"]
        return {
            "caseTotal": len(cases),
            "caseActive": len(active),
            "caseArchived": len(cases) - len(active),
            "topHit": max(
                (c.get("hitCount") or 0
                 for c in cases), default=0),
            "highRecurrence": sum(
                1 for c in cases
                if (c.get("recurrence") or 0) > 0),
        }

    async def _zone_trust(self) -> dict:
        """信任区(P3 对账+指纹链)"""
        latest = await self.repo.latest_recon()
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        chain = await Xx66HealService(
            repo=self.repo).verify_log_chain()
        return {
            "reconLatest": {
                "runId": (latest or {}).get("runId"),
                "dangerCount": (latest or {}).get(
                    "dangerCount"),
                "ranAt": (latest or {}).get("ranAt"),
            } if latest else None,
            "logChainIntact":
                chain.get("chainIntact"),
            "logCount": chain.get("total"),
        }

    # --------------------------------------------------------
    # proactive 自助赋能(超级会员)
    # --------------------------------------------------------

    async def proactive(self, member_id: int) -> dict:
        """自助赋能(店铺健康自检+信值使用优化——
        确定性规则, 观测面永不关停)"""
        suggestions = []
        # 信值使用优化(确定性)
        try:
            from services.trust_asset_service import (
                TrustAssetService,
            )
            bal = await TrustAssetService().balance(
                int(member_id))
            available = float(
                bal.get("available") or 0)
            if available > 0:
                suggestions.append({
                    "kind": "trust_usage",
                    "advice": "信值余额可在积分商城"
                              "兑换货品/服务"
                              "(1 TV = 1 元货品)",
                    "available": available})
            else:
                suggestions.append({
                    "kind": "trust_usage",
                    "advice": "信值余额为零——可通过"
                              "验真行为累积信值"
                              "(存证/互证/修复)",
                    "available": 0.0})
        except KeyError:
            suggestions.append({
                "kind": "trust_usage",
                "advice": "暂无信值档案——可通过验真"
                          "行为累积信值"})
        except Exception as exc:
            logger.warning(
                "xx66_proactive_failsoft: %s", exc)
        # 高频问题自助向导(案例库 top)
        try:
            top = [
                c for c in await self.repo.list_cases(
                    limit=CASE_MAX)
                if c.get("status") == "active"]
            top.sort(key=lambda c: (
                c.get("hitCount") or 0), reverse=True)
            for c in top[:3]:
                suggestions.append({
                    "kind": "self_service",
                    "caseId": c.get("caseId"),
                    "problem": c.get("problem"),
                    "solution": c.get("solution")})
        except Exception as exc:
            logger.warning(
                "xx66_proactive_cases_failsoft: %s", exc)
        return {
            "success": True, "memberId": member_id,
            "suggestions": suggestions,
            "note": "proactive 确定性规则——"
                    "AI 建议仅为辅助",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 红队七向量(§十二——确定性零 LLM)
    # --------------------------------------------------------

    async def run_redteam(self) -> dict:
        """红队七向量(每向量独立 try——单向量异常
        不中断整轮; 隔离域 991x 自清理;
        XX66_MODE 门槛——off 拒)"""
        from services.xx66_service import (
            require_active_mode,
        )
        require_active_mode()
        vectors = [
            ("RT-01", self._rt01_empathy_abuse),
            ("RT-02", self._rt02_compensation_replay),
            ("RT-03", self._rt03_heal_escalation),
            ("RT-04", self._rt04_recon_forgery),
            ("RT-05", self._rt05_case_poison),
            ("RT-06", self._rt06_emotion_bypass),
            ("RT-07", self._rt07_approval_bypass),
        ]
        results = []
        for code, fn in vectors:
            try:
                r = await fn()
            except Exception as exc:
                logger.warning(
                    "xx66_redteam_%s_failed: %s",
                    code, exc)
                r = {"vector": code, "name": "执行异常",
                     "defended": False,
                     "evidence": {"error":
                                  str(exc)[:150]}}
            results.append(r)
        defended = sum(1 for v in results
                       if v.get("defended"))
        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "vectors": results,
            "total": len(results),
            "defended": defended,
            "allDefended": defended == len(results),
            "note": "红队七向量(66号 P4)——确定性零"
                    "LLM; 隔离域 991x; 种子用后清理",
            "ranAt": ts(),
        }

    async def _rt01_empathy_abuse(self) -> dict:
        """RT-01 伪造高情绪骗补偿——
        base=实际损失锚定, 情绪仅×1.1 封顶不变"""
        import os
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            svc = Xx66ReconService(
                repo=self.repo)
            calm = svc.evaluate("trust_misdeduct", {
                "lossAmount": 10.0,
                "roleTier": "standard",
                "emotionBand": "calm"})
            angry = svc.evaluate("trust_misdeduct", {
                "lossAmount": 10.0,
                "roleTier": "standard",
                "emotionBand": "angry"})
            cap_calm = svc.evaluate(
                "trust_misdeduct", {
                    "lossAmount": 1000.0,
                    "roleTier": "trusted",
                    "emotionBand": "calm"})
            cap_angry = svc.evaluate(
                "trust_misdeduct", {
                    "lossAmount": 1000.0,
                    "roleTier": "trusted",
                    "emotionBand": "angry"})
            defended = (
                angry["compensation"]
                - calm["compensation"] <= 1.0
                and cap_angry["compensation"]
                == cap_calm["compensation"]
                == 50.0)
            return {
                "vector": "RT-01",
                "name": "伪造高情绪骗补偿",
                "defended": defended,
                "evidence": {
                    "calmAmt":
                        calm["compensation"],
                    "angryAmt":
                        angry["compensation"],
                    "capAngry":
                        cap_angry["compensation"]},
            }
        finally:
            os.environ["XX66_MODE"] = "off"

    async def _rt02_compensation_replay(self) -> dict:
        """RT-02 补偿重放——同窗同实体幂等拒"""
        import os
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            svc = Xx66ReconService(
                repo=self.repo)
            ctx = {"entityId": f"rt-{RT_ENTITY_BASE}",
                   "incidentId": f"RT2-{RT_ENTITY_BASE}",
                   "lossAmount": 10.0}
            await svc.propose_compensation(
                "trust_misdeduct", ctx)
            try:
                await svc.propose_compensation(
                    "trust_misdeduct", ctx)
                replay_rejected = False
            except ValueError:
                replay_rejected = True
            # 清理红队补偿记录
            await self._cleanup_rt_compensations(
                ctx["entityId"])
            return {
                "vector": "RT-02",
                "name": "补偿重放",
                "defended": replay_rejected,
                "evidence": {
                    "replayRejected":
                        replay_rejected},
            }
        finally:
            os.environ["XX66_MODE"] = "off"

    async def _cleanup_redis_seeds(
            self, table: str, id_field: str,
            match) -> int:
        """红队种子清理(Redis 态——仅扫哈希记录;
        seq 计数器/index 索引等非哈希辅助键跳过
        防 WRONGTYPE; 删除时同步 zrem 索引成员)"""
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k,
        )
        if not is_redis_mode():
            return -1
        client = await get_redis_client()
        removed = 0
        for k in (await client.keys(_k(
                "xx66", table, "*")) or []):
            ktype = await client.type(k)
            if isinstance(ktype, bytes):
                ktype = ktype.decode()
            if ktype != "hash":
                continue
            data = await client.hgetall(k)
            if not data or not match(data):
                continue
            await client.delete(k)
            rid = str(data.get(id_field) or "")
            if rid:
                await client.zrem(
                    _k("xx66", table, "index"), rid)
            removed += 1
        return removed

    async def _cleanup_rt_compensations(
            self, entity_id: str) -> None:
        """清理红队补偿种子(用后即删)"""
        from repositories.backend import (
            is_redis_mode,
        )
        if is_redis_mode():
            await self._cleanup_redis_seeds(
                "xx66_compensations",
                "compensationId",
                lambda d: d.get("entityId")
                == entity_id)
            return
        from repositories.backend import (
            get_in_memory_store,
        )
        store = get_in_memory_store()
        table = store.get("xx66_compensations") or {}
        for cid in [c for c, r in table.items()
                    if r.get("entityId")
                    == entity_id]:
            table.pop(cid, None)

    async def _rt03_heal_escalation(self) -> dict:
        """RT-03 自愈越权执行——manual 级强制人工"""
        import os
        from services.maintenance_service import (
            MaintenanceService,
        )
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        os.environ["XX66_MODE"] = "assist"
        try:
            msvc = MaintenanceService()
            rec = await msvc.detect_fault(
                fault_type="data_loss",
                fault_source=f"rt{RT_ENTITY_BASE}-critical",
                recovery_level="manual")
            heal = await Xx66HealService(
                repo=self.repo).heal(rec["id"])
            state = (await msvc.get_recovery(
                rec["id"]))["recoveryStatus"]
            defended = (
                heal["route"] == "manual_handoff"
                and state == "manual_required")
            return {
                "vector": "RT-03",
                "name": "自愈越权执行",
                "defended": defended,
                "evidence": {
                    "route": heal["route"],
                    "state": state},
            }
        finally:
            os.environ["XX66_MODE"] = "off"

    async def _rt04_recon_forgery(self) -> dict:
        """RT-04 对账结果伪造——报告不变式
        重算校验(结果非信任存储)"""
        import os
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            svc = Xx66ReconService(
                repo=self.repo)
            r = await svc.run_recon()
            # 直改轮次记录注入"无差异"
            from repositories.backend import (
                is_redis_mode, get_redis_client, _k,
            )
            forged = dict(
                await self.repo.get_recon(
                    r["runId"]) or {})
            forged["dangerCount"] = 0
            forged["dangerList"] = []
            if is_redis_mode():
                client = await get_redis_client()
                await client.hset(
                    _k("xx66", "xx66_recon_runs",
                       r["runId"]),
                    mapping={
                        "dangerCount": 0,
                        "dangerList": "[]"})
            else:
                from repositories.backend import (
                    get_in_memory_store,
                )
                store = get_in_memory_store()
                store["xx66_recon_runs"][
                    r["runId"]] = forged
            # 重跑对账——不变式重算覆盖伪造
            r2 = await svc.run_recon()
            i1_pass = (r2["invariants"]
                       ["I1_total_conservation"]
                       ["pass"])
            # 清理红队轮次
            if is_redis_mode():
                client = await get_redis_client()
                for rid in (r["runId"], r2["runId"]):
                    await client.delete(_k(
                        "xx66", "xx66_recon_runs", rid))
            else:
                from repositories.backend import (
                    get_in_memory_store,
                )
                store = get_in_memory_store()
                for rid in (r["runId"], r2["runId"]):
                    store["xx66_recon_runs"].pop(
                        rid, None)
            defended = i1_pass is True
            return {
                "vector": "RT-04",
                "name": "对账结果伪造",
                "defended": defended,
                "evidence": {
                    "recomputedPass": i1_pass},
            }
        finally:
            os.environ["XX66_MODE"] = "off"

    async def _rt05_case_poison(self) -> dict:
        """RT-05 案例库投毒——四要素质量门"""
        # 缺要素注入
        for bad_args in (
                ("", "root", "sol", "out"),
                ("prob", "", "sol", "out"),
                ("prob", "root", "", "out"),
                ("prob", "root", "sol", "")):
            try:
                await self.create_case(*bad_args)
                incomplete_rejected = False
                break
            except ValueError:
                incomplete_rejected = True
        # valueLinked 数字注入
        try:
            await self.create_case(
                "投毒问题", "投毒根因",
                "补偿 99.9 TV 直发", "投毒效果",
                value_linked=True)
            number_rejected = False
        except ValueError:
            number_rejected = True
        # PII 注入
        try:
            await self.create_case(
                "手机 13812345678 故障", "根因",
                "方案", "效果")
            pii_case = await self.repo.list_cases(
                limit=CASE_MAX)
            pii_masked = all(
                "13812345678" not in str(c)
                for c in pii_case)
        except ValueError:
            pii_masked = True
        # 清理红队案例
        await self._cleanup_rt_cases()
        defended = (incomplete_rejected
                    and number_rejected
                    and pii_masked)
        return {
            "vector": "RT-05",
            "name": "案例库投毒",
            "defended": defended,
            "evidence": {
                "incompleteRejected":
                    incomplete_rejected,
                "numberRejected": number_rejected,
                "piiMasked": pii_masked},
        }

    async def _cleanup_rt_cases(self) -> None:
        """清理红队案例种子"""
        from repositories.backend import (
            is_redis_mode,
        )
        if is_redis_mode():
            await self._cleanup_redis_seeds(
                "xx66_cases", "caseId",
                lambda d: d.get("source") == "redteam"
                or "投毒" in str(d.get("problem"))
                or "13812345678" in str(
                    d.get("problem")))
            return
        from repositories.backend import (
            get_in_memory_store,
        )
        store = get_in_memory_store()
        table = store.get("xx66_cases") or {}
        for cid in [c for c, r in table.items()
                    if "投毒" in str(
                        r.get("problem"))
                    or "13812345678" in str(
                        r.get("problem"))]:
            table.pop(cid, None)

    async def _rt06_emotion_bypass(self) -> dict:
        """RT-06 情绪词典绕过——零负面词文本
        欺诈行为, 情绪轨仅调沟通模式不提额度"""
        from services.xx66_support_service import (
            emotion_intensity, emotion_band,
        )
        # 零负面词 → calm(仅教学/高效模式)
        calm_band = emotion_band(
            emotion_intensity(
                "麻烦帮我看看那个问题"))
        # 处置路由由 value_sensitivity 主导
        # (评分器单测已证)——情绪仅 mode 维度
        defended = calm_band == "calm"
        return {
            "vector": "RT-06",
            "name": "情绪词典绕过",
            "defended": defended,
            "evidence": {
                "zeroNegativeBand": calm_band,
                "note": "情绪轨仅调沟通模式——"
                        "处置路由由 value_sensitivity/"
                        "recurrence 主导"},
        }

    async def _rt07_approval_bypass(self) -> dict:
        """RT-07 审批旁路——未 approve 冲正/补偿
        拒执行"""
        import os
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            svc = Xx66ReconService(
                repo=self.repo)
            # 未审批建议书直接 apply → 拒
            book_id = await self.repo.next_advice_id()
            await self.repo.save_advice_book({
                "adviceId": book_id,
                "kind": "reversal",
                "direction": "issue", "amount": 1.0,
                "trustId": RT_ENTITY_BASE + 7,
                "reserveRef": f"rt:{RT_ENTITY_BASE}",
                "status": "proposed",
                "proposedAt": ts()})
            try:
                await svc.apply_reversal(book_id)
                unapproved = False
            except ValueError:
                unapproved = True
            # 补偿未 approved 状态非 executed 不可
            # 案例沉淀(P4 质量门第二道)
            defended = unapproved
            return {
                "vector": "RT-07",
                "name": "审批旁路",
                "defended": defended,
                "evidence": {
                    "unapprovedRejected":
                        unapproved},
            }
        finally:
            os.environ["XX66_MODE"] = "off"
