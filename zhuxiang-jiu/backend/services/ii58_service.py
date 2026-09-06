"""58号·AI智能优化意图识别 置信度引擎
(ii58_service, P0)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md §四/§九 P0):
    ① L1 语料匹配引擎(55号 parse_intent 范式升级):
       关键词加权打分(FULL/PARTIAL/AMBIGUOUS)
       +命中数共识加成+对抗样本否决(易混淆域
       双降权)
    ② 动态信任校准(47号 tier 联动阈值——
       trusted 下调/watched restricted 上调)
    ③ 三态响应: resolved/partial/clarify
       (澄清优于错误执行铁律)
    ④ 识别记录(归因链: corpusId 匹配链+track
       +tier 快照——无归因不计入有效服务)
    ⑤ evaluate 主链(决策面 off 409)

铁律(计划 §一):
    - 澄清优于错误执行: 低置信度必 clarify,
      禁止盲执行
    - 归因 ID 强制: 每次识别携带归因链
"""

import logging
import os
import re

from core.helpers import ts

from repositories.ii58_repository import (
    Ii58Repository,
)

logger = logging.getLogger("ii58_service")

MODEL_VERSION = "v1-ii58-service"

SCORER_ID = "intent_orchestration"

# 相似度常量(55号范式继承)
FULL_MATCH = 1.0
PARTIAL_MATCH = 0.6
AMBIGUOUS_MATCH = 0.4

# 基线三态阈值(动态校准前)
BASE_UPPER = 0.9
BASE_LOWER = 0.7

# 对抗样本否决降权系数(易混淆域双方各乘)
ADVERSARIAL_PENALTY = 0.5

# 47号 tier 阈值 delta(计划 §4.2——
# trusted 流畅/watched restricted 管控)
TIER_THRESHOLD_DELTA = {
    "trusted": (-0.05, 0.0),
    "standard": (0.0, 0.0),
    "watched": (0.05, 0.0),
    "restricted": (0.05, 0.10),
}

MODE_KEY = "II58_MODE"


def current_mode() -> str:
    """模块开关(动态读取——运行时可切换)"""
    return os.environ.get(MODE_KEY, "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"II58_MODE={mode}(默认 off——决策面"
            f"关闭, registry 观测面不受影响)")


class Ii58Service:
    """58号置信度引擎+识别底座(P0)"""

    def __init__(self):
        self.repo = Ii58Repository()

    # --------------------------------------------------------
    # 观测面(注册表自描述)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """意图注册表视图(观测面不受开关影响)"""
        from services.ii58_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe", "optimize",
                              "urgent"),
            },
            "confidence": {
                "baseUpper": BASE_UPPER,
                "baseLower": BASE_LOWER,
                "states": ("resolved", "partial",
                           "clarify"),
            },
            "note": "P0 底座: 意图注册表三位一体+"
                    "置信度引擎+归因链(语料采集 P1 "
                    "接管)",
        })
        return view

    # ============================================================
    # evaluate 主链(识别+三态+归因)
    # ============================================================

    async def evaluate(self, text: str,
                       member_id: int = None,
                       member_role: str = "member"
                       ) -> dict:
        """意图识别主链: L1 语料匹配→动态阈值校准
        →三态响应→归因链落库

        Args:
            text: 用户输入(已过 48号唤醒/消解的
                  指令文本)
            member_id: 会员(47号 tier 联动——
                       缺省系统态走 standard 基线)
            member_role: 会员角色(权限校验用)

        Raises:
            ValueError: off 态/文本为空
        """
        require_active_mode()
        text = str(text or "").strip()
        if not text:
            raise ValueError("识别文本不能为空")

        # ① 47号 tier 联动(fail-soft——
        #    未建档走 standard 基线)
        tier = await self._member_tier(member_id)

        # ② 动态阈值校准(运行态计算不落库)
        upper, lower = self._calibrated_thresholds(
            tier)

        # ③ L1 语料匹配(置信度+候选)
        match = await self._match_corpus(text)

        # ④ 三态响应(澄清优于错误执行铁律)
        state, confidence, candidates = \
            self._classify(match, upper, lower)

        # ⑤ 归因链落库(无归因不计入有效服务)
        eval_id = await self.repo.next_eval_id()
        attribution = {
            "corpusIds": match.get(
                "matchedCorpusIds") or [],
            "track": match.get("track") or "corpus",
            "tier": tier,
            "thresholds": {
                "upper": upper, "lower": lower,
            },
        }
        record = {
            "evalId": eval_id,
            "text": text[:200],
            "intentId": match.get("intentId")
            or "unknown.unrecognized",
            "state": state,
            "confidence": round(confidence, 4),
            "candidates": candidates,
            "attribution": attribution,
            "memberId": int(member_id or 0),
            "memberRole": member_role,
            "corpusHits": int(
                match.get("corpusHits") or 0),
            "boundaryIntercepted": False,
            "pooledFeedbackId": 0,
            "evalCount": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_evaluation(record)

        # ⑥ evaluate 事件留痕
        await self._track(eval_id, "evaluate", {
            "intentId": record["intentId"],
            "state": state,
            "confidence":
                record["confidence"],
            "tier": tier,
            "corpusHits":
                record["corpusHits"],
        })

        # ⑦ 响应组装(按三态语义)
        result = {
            "success": True,
            "evalId": eval_id,
            "text": text[:80],
            "intentId": record["intentId"],
            "state": state,
            "confidence":
                record["confidence"],
            "attribution": attribution,
            "tier": tier,
        }
        if state == "resolved":
            result.update({
                "note": "识别完成(resolved)——"
                        "直接意图交付",
                "slots": self._extract_slots(
                    record["intentId"], text),
            })
        elif state == "partial":
            result.update({
                "candidates": candidates,
                "note": "候选澄清(partial)——请从候选"
                        "中选择(澄清优于错误执行铁律)",
            })
        else:
            result.update({
                "note": "澄清追问(clarify)——置信度"
                        "不足, 拒绝盲执行"
                        "(48号追问而非猜测红线)",
            })
        result["evaluatedAt"] = ts()
        return result

    # ============================================================
    # L1 语料匹配(55号打分范式升级)
    # ============================================================

    async def _match_corpus(self,
                            text: str) -> dict:
        """语料库匹配(active 态 positive 样本)

        置信度 = Σ(相似度×权重)/Σ(权重)
                 × 命中数共识加成
                 × 对抗样本否决(易混淆域降权)
        """
        corpus = await self.repo.list_corpus(
            status="active", limit=1000)
        positive = [c for c in corpus
                    if c.get("sampleType")
                    == "positive"]
        adversarial = [c for c in corpus
                       if c.get("sampleType")
                       == "adversarial"]

        # 逐意图累计得分
        scores: dict = {}
        matched: dict = {}
        for c in positive:
            intent_id = str(c.get("intentId") or "")
            if not intent_id:
                continue
            sim = self._similarity(
                str(c.get("text") or ""), text)
            if sim <= 0:
                continue
            weight = float(c.get("weight") or 1.0)
            entry = scores.setdefault(
                intent_id,
                {"weighted": 0.0,
                 "weightSum": 0.0,
                 "hits": 0,
                 "corpusIds": []})
            entry["weighted"] += sim * weight
            entry["weightSum"] += weight
            entry["hits"] += 1
            entry["corpusIds"].append(
                int(c.get("corpusId") or 0))

        if not scores:
            return {
                "intentId": None,
                "confidence": 0.0,
                "corpusHits": 0,
                "matchedCorpusIds": [],
                "track": "corpus",
                "candidates": [],
            }

        # 基础置信度+共识加成
        ranked = []
        for intent_id, entry in scores.items():
            base = (entry["weighted"]
                    / entry["weightSum"])
            # 共识加成: 多语料命中→上浮
            # (log2 衰减——3 条命中 ×1.58 上限内)
            consensus = min(
                1.15,
                1.0 + 0.15 * max(
                    0, entry["hits"] - 1))
            ranked.append((
                base * consensus, intent_id,
                entry))

        ranked.sort(key=lambda kv: -kv[0])
        top_conf, top_intent, top_entry = \
            ranked[0]

        # 对抗样本否决(易混淆域降权)——
        # 对抗样本标注在某意图域(intentId)的
        # 混淆边界文本: 输入全含对抗文本(FULL)
        # 且 top 意图即该域 → 置信度受抑(该域
        # 存在易混文本, 应澄清而非直接执行);
        # PARTIAL 相似不触发(防正常文本误伤)
        penalty_applied = False
        for adv in adversarial:
            adv_text = str(adv.get("text") or "")
            adv_intent = str(
                adv.get("intentId") or "")
            if not adv_text \
                    or adv_intent != top_intent:
                continue
            adv_sim = self._similarity(
                adv_text, text)
            if adv_sim >= FULL_MATCH:
                top_conf *= ADVERSARIAL_PENALTY
                penalty_applied = True
                break

        # 候选列表(次高分接近→多候选)
        candidates = []
        if len(ranked) > 1:
            second_conf, second_intent, _ = \
                ranked[1]
            if second_conf >= top_conf - 0.15:
                candidates = [
                    {"intentId": top_intent,
                     "confidence": round(
                         top_conf, 4)},
                    {"intentId": second_intent,
                     "confidence": round(
                         second_conf, 4)},
                ][:4]

        return {
            "intentId": top_intent,
            "confidence": round(
                min(top_conf, 1.0), 4),
            "corpusHits": top_entry["hits"],
            "matchedCorpusIds":
                top_entry["corpusIds"][:10],
            "track": "corpus",
            "adversarialPenalty": penalty_applied,
            "candidates": candidates,
        }

    @staticmethod
    def _similarity(corpus_text: str,
                    input_text: str) -> float:
        """语料↔输入相似度(55号范式)

        FULL_MATCH: 语料文本为输入子串(全含)
        PARTIAL_MATCH: 语料关键词条命中(≥2 字)
        AMBIGUOUS_MATCH: 单字模糊命中
        0: 无命中
        """
        corpus_text = corpus_text.strip()
        input_text = input_text.strip()
        if not corpus_text or not input_text:
            return 0.0
        if corpus_text in input_text:
            return FULL_MATCH
        if len(corpus_text) >= 2 \
                and corpus_text in input_text:
            return FULL_MATCH
        # 条命中(语料分词近似——按 2 字滑窗)
        windows = [
            corpus_text[i:i + 2]
            for i in range(
                len(corpus_text) - 1)]
        hits = sum(
            1 for w in windows if w in input_text)
        if hits >= max(
                1, len(windows) // 2):
            return PARTIAL_MATCH
        if hits >= 1:
            return AMBIGUOUS_MATCH
        return 0.0

    # ============================================================
    # 动态信任校准(47号 tier 联动)
    # ============================================================

    @staticmethod
    async def _member_tier(member_id) -> str:
        """47号 tier 纯读取(fail-soft——
        未建档/异常走 standard 基线)"""
        if member_id is None:
            return "standard"
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(int(member_id)))
            return str(profile.get("tier")
                       or "standard")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_tier_read_failed: %s", exc)
            return "standard"

    @staticmethod
    def _calibrated_thresholds(tier: str
                                ) -> tuple:
        """动态阈值(运行态计算不落库——
        计划 §4.2)"""
        delta_upper, delta_lower = \
            TIER_THRESHOLD_DELTA.get(
                tier, (0.0, 0.0))
        upper = round(
            min(0.99,
                BASE_UPPER + delta_upper), 4)
        lower = round(
            min(upper - 0.05,
                BASE_LOWER + delta_lower), 4)
        return upper, lower

    # ============================================================
    # 三态判定
    # ============================================================

    @staticmethod
    def _classify(match: dict,
                  upper: float,
                  lower: float) -> tuple:
        """三态响应(计划 §4.1)

        resolved ≥upper / partial [lower, upper)
        / clarify <lower——澄清优于错误执行铁律
        """
        confidence = float(
            match.get("confidence") or 0.0)
        intent_id = match.get("intentId")
        candidates = match.get("candidates") or []

        if not intent_id:
            return "clarify", 0.0, []
        # 多候选且接近——优先 partial(候选澄清)
        if candidates and confidence < upper:
            return "partial", confidence, candidates
        if confidence >= upper:
            return "resolved", confidence, candidates
        if confidence >= lower:
            return "partial", confidence, candidates
        return "clarify", confidence, candidates

    # ============================================================
    # 槽位抽取(确定性正则——55号范式)
    # ============================================================

    @staticmethod
    def _extract_slots(intent_id: str,
                       text: str) -> dict:
        """槽位抽取(意图 slotSchema 驱动——
        引号/书名号确定性正则)"""
        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        meta = INTENT_REGISTRY.get(intent_id) or {}
        schema = meta.get("slotSchema") or []
        slots = {}
        for slot in schema:
            if slot == "keyword":
                # 引号/书名号内取词(55号范式)
                m = re.search(
                    r"[「『\"']([^「」『』\"']+)"
                    r"[」』\"']",
                    text)
                if m:
                    slots["keyword"] = m.group(1)
                else:
                    # 去指令词后的剩余文本
                    slots["keyword"] = text[:32]
            elif slot == "page":
                for page in (
                        "首页", "产品", "购物车",
                        "结算", "信值", "帮助"):
                    if page in text:
                        slots["page"] = page
                        break
            elif slot == "amount":
                m = re.search(
                    r"(\d+(?:\.\d+)?)", text)
                if m:
                    slots["amount"] = float(
                        m.group(1))
        return slots

    # ============================================================
    # 观测面(识别记录/模型状态)
    # ============================================================

    async def list_evaluations(self,
                               intent_id: str = None,
                               state: str = None
                               ) -> dict:
        """识别记录列表(观测面)"""
        records = await self.repo.list_evaluations(
            intent_id=intent_id, state=state,
            limit=200)
        return {
            "success": True,
            "total": len(records),
            "evaluations": records,
            "note": "意图识别记录——置信度三态"
                    "+归因链",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view 复用)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        view.update({
            "module": "ii58",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "corpus_quality": "语料质量",
                "intent_confidence": "意图置信度",
                "member_trust": "会员信值",
                "boundary_clarity": "边界清晰度",
                "history_success": "历史成功率",
                "compliance_posture": "合规态势",
                "latency_budget": "延迟预算",
                "coverage_breadth": "覆盖广度",
            },
            "decisions": ["observe", "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——第33档案",
        })
        return {"success": True, "status": view}

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, eval_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "evalId": int(eval_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_track_failed %s: %s",
                event_type, exc)
