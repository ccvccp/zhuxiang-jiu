"""58号·AI智能优化意图识别 置信度引擎
(ii58_service, P0; P2 业务耦合+动态信任校准)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md §四/§九):
    P0:
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

    P2(业务耦合+动态信任校准):
    ⑥ 识别即合规前置校验(仅 resolved 交付域):
       minRole 角色校验+沙箱五级裁决+越界拦截
       boundaryIntercepted(归因保留原始意图)
    ⑦ 槽位上下文预填(48号会话上轮 card.subject
       指代消解+页面状态——纯读取零写入)
    ⑧ 合规模板关联(ii58_compliance——57号
       valueTags 只读联动语义)
    ⑨ 阈值配置域: calibrate→46号审批总线留痕
       (config 人工通道)+58号镜像终审轨
       (pending→active 唯一出口——人工铁律);
       基线生效源三级回退(镜像 active→代码常量)

铁律(计划 §一):
    - 澄清优于错误执行: 低置信度必 clarify,
      禁止盲执行
    - 归因 ID 强制: 每次识别携带归因链
    - 三态纯度: 权限裁决不污染置信度三态
    - 阈值校准不落库运行态口径不变
      (tier delta 每轮重算; 基线变更唯一
      生效通道=人工终审)
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

# P2 阈值校准合法域(0.50≤lower<upper≤0.99)
CALIBRATE_MIN = 0.50
CALIBRATE_MAX = 0.99

# 阈值镜像键(ii58_thresholds 表 tier 键——
# 单记录状态机: pending→active/rejected)
THRESHOLD_MIRROR_KEY = "baseline"

# 指代词(槽位预填——48号 REFERENCE_WORDS 范式)
REFERENCE_WORDS = ("这个", "它", "这件", "这款", "那个")

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
            "note": "P2 底座: 意图注册表三位一体+"
                    "置信度引擎+归因链+识别即合规"
                    "(权限前置校验/越界拦截)+阈值"
                    "配置域(46号审批留痕+人工终审)",
        })
        return view

    # ============================================================
    # evaluate 主链(识别+三态+归因)
    # ============================================================

    async def evaluate(self, text: str,
                       member_id: int = None,
                       member_role: str = "member",
                       session_id: int = None,
                       current_page: str = None
                       ) -> dict:
        """意图识别主链: L1 语料匹配→动态阈值校准
        →三态响应→识别即合规前置校验→归因链落库

        Args:
            text: 用户输入(已过 48号唤醒/消解的
                  指令文本)
            member_id: 会员(47号 tier 联动——
                       缺省系统态走 standard 基线)
            member_role: 会员角色(权限校验用)
            session_id: 48号会话(槽位上下文预填
                       ——上轮 card.subject 纯读取)
            current_page: 页面状态(page 槽位预填)

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

        # ② 动态阈值校准(运行态计算不落库——
        #    基线经人工终审生效域)
        upper, lower = await \
            self._calibrated_thresholds(tier)

        # ③ L1 语料匹配(置信度+候选)
        match = await self._match_corpus(text)

        # ④ 三态响应(澄清优于错误执行铁律)
        state, confidence, candidates = \
            self._classify(match, upper, lower)

        # ⑤ 识别即合规前置校验(P2——仅 resolved
        #    交付域; 三态纯度: 权限不污染置信度)
        compliance = None
        if state == "resolved":
            from services.ii58_compliance import (
                judge,
            )
            compliance = judge(
                str(match.get("intentId")
                    or "unknown.unrecognized"),
                member_role)
        denied = bool(compliance
                      and compliance.get("decision")
                      == "denied")

        # ⑥ 槽位抽取+上下文预填(交付域且未拦截)
        slots = {}
        slot_sources = {}
        if state == "resolved" and not denied:
            slots = self._extract_slots(
                str(match.get("intentId") or ""),
                text)
            slots, slot_sources = \
                await self._prefill_slots(
                    str(match.get("intentId")
                        or ""), text, slots,
                    session_id, current_page)

        # ⑦ 归因链落库(无归因不计入有效服务)
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
        if compliance is not None:
            attribution["compliance"] = compliance
        if slot_sources:
            attribution["slotSources"] = \
                slot_sources
        record = {
            "evalId": eval_id,
            "text": text[:200],
            "intentId": match.get("intentId")
            or "unknown.unrecognized",
            "state": state,
            "confidence": round(confidence, 4),
            "candidates": candidates,
            "attribution": attribution,
            "slots": slots,
            "memberId": int(member_id or 0),
            "memberRole": member_role,
            "corpusHits": int(
                match.get("corpusHits") or 0),
            "boundaryIntercepted": denied,
            "pooledFeedbackId": 0,
            "evalCount": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_evaluation(record)

        # ⑧ evaluate 事件留痕
        await self._track(eval_id, "evaluate", {
            "intentId": record["intentId"],
            "state": state,
            "confidence":
                record["confidence"],
            "tier": tier,
            "corpusHits":
                record["corpusHits"],
            "boundaryIntercepted": denied,
        })

        # ⑨ 响应组装(按三态+合规裁决语义)
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
        if compliance is not None:
            result["compliance"] = compliance
        if state == "resolved" and denied:
            # 越界拦截: 输出改判越界元意图
            # (归因保留原始意图——审计完整)
            result["intentId"] = \
                "boundary.unauthorized"
            result.update({
                "boundaryIntercepted": True,
                "note": compliance.get(
                    "refusalNote") or
                    "越界拦截(识别即合规)",
            })
        elif state == "resolved" \
                and compliance \
                and compliance.get("decision") \
                == "confirm_required":
            result.update({
                "requireConfirm": True,
                "slots": slots,
                "note": "敏感意图(sensitive 沙箱)——"
                        "需二次确认: 屏幕码核销"
                        "(48号 confirmToken 流)",
            })
        elif state == "resolved":
            result.update({
                "note": "识别完成(resolved)——"
                        "直接意图交付",
                "slots": slots,
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

    # ============================================================
    # 阈值基线生效域(P2——镜像→代码常量回退)
    # ============================================================

    async def _effective_baseline(self) -> tuple:
        """生效基线(三级回退: 镜像 active 值→
        代码常量; pending 态取镜像 effective 值)

        Returns:
            (upper, lower, source)——source:
            mirror|code_default
        """
        mirror = None
        try:
            mirror = await self.repo.get_threshold(
                THRESHOLD_MIRROR_KEY)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_mirror_read_failed: %s", exc)
        if mirror and mirror.get("baseUpper"):
            return (round(float(mirror["baseUpper"]), 4),
                    round(float(mirror["baseLower"]), 4),
                    "mirror")
        return (BASE_UPPER, BASE_LOWER,
                "code_default")

    async def _calibrated_thresholds(
            self, tier: str) -> tuple:
        """动态阈值(运行态计算不落库——
        计划 §4.2; 基线经 _effective_baseline)"""
        base_upper, base_lower, _ = await \
            self._effective_baseline()
        delta_upper, delta_lower = \
            TIER_THRESHOLD_DELTA.get(
                tier, (0.0, 0.0))
        upper = round(
            min(CALIBRATE_MAX,
                base_upper + delta_upper), 4)
        lower = round(
            min(upper - 0.05,
                base_lower + delta_lower), 4)
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
    # 槽位上下文预填(P2——48号纯读取)
    # ============================================================

    async def _prefill_slots(self, intent_id: str,
                             text: str, slots: dict,
                             session_id,
                             current_page
                             ) -> tuple:
        """槽位上下文预填(会话上轮指代消解+
        页面状态; 纯读取零写入)

        预填域(不覆盖显式抽取值):
            - keyword: 无引号显式抽取+文本含
              指代词+48号上轮 card.subject 有值
              → 指代消解预填("这个多少钱"→
              上轮商品名)
            - page: 文本无页面词+currentPage
              给定 → 页面状态预填

        Returns:
            (slots, slot_sources)——来源标记
            可审计(source/origin)
        """
        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        meta = INTENT_REGISTRY.get(intent_id) or {}
        schema = meta.get("slotSchema") or []
        sources = {}

        if "keyword" in schema \
                and session_id is not None:
            has_explicit = bool(re.search(
                r"[「『\"']([^「」『』\"']+)"
                r"[」』\"']", text))
            if not has_explicit and any(
                    w in text
                    for w in REFERENCE_WORDS):
                subject = await \
                    self._last_turn_subject(
                        session_id)
                if subject:
                    slots["keyword"] = subject[:32]
                    sources["keyword"] = {
                        "source": "context_prefill",
                        "origin": "last_turn",
                    }

        if "page" in schema \
                and "page" not in slots \
                and current_page:
            slots["page"] = str(current_page)[:16]
            sources["page"] = {
                "source": "context_prefill",
                "origin": "currentPage",
            }
        return slots, sources

    @staticmethod
    async def _last_turn_subject(session_id) -> str:
        """48号会话最近一轮 card.subject 纯读取
        (fail-soft——无会话/无轮次/异常空串)"""
        if session_id is None:
            return ""
        try:
            from repositories.xiaozhu_repository \
                import Xiaozhu48Repository
            turns = await Xiaozhu48Repository(
            ).list_turns(int(session_id), limit=50)
            if not turns:
                return ""
            last = turns[-1]
            card = last.get("card") or {}
            return str(card.get("subject")
                       or "")[:64]
        except Exception:  # noqa: BLE001
            return ""

    # ============================================================
    # 阈值配置域(P2——46号审批留痕+人工终审轨)
    # ============================================================

    async def calibrate(self, upper: float,
                        lower: float,
                        reason: str,
                        requested_by: str = "admin"
                        ) -> dict:
        """基线阈值校准申请(→46号审批总线留痕
        +58号镜像 pending; 不直接生效)

        46号 config 类为人工通道语义(总线不自动
        执行业务侧变更)——生效唯一出口为
        review_calibration 人工终审。

        Raises:
            ValueError: off 态/阈值域非法/
                理由非法/已有待终审校准
        """
        require_active_mode()
        try:
            upper = round(float(upper), 4)
            lower = round(float(lower), 4)
        except (TypeError, ValueError):
            raise ValueError(
                "阈值须为数值")
        if not (CALIBRATE_MIN <= lower < upper
                <= CALIBRATE_MAX):
            raise ValueError(
                f"阈值域非法(须 {CALIBRATE_MIN}≤lower"
                f"<upper≤{CALIBRATE_MAX})")
        reason = str(reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError(
                "变更理由必填(1-500 字符)")

        mirror = await self.repo.get_threshold(
            THRESHOLD_MIRROR_KEY)
        if mirror and mirror.get("status") \
                == "pending":
            raise ValueError(
                "已有待终审校准(先处置再提交)")

        cur_upper, cur_lower, _ = await \
            self._effective_baseline()

        # ① 46号审批总线留痕(config——人工通道)
        change = await self._gov_submit(
            {"scope": "threshold_baseline",
             "before": {"upper": cur_upper,
                        "lower": cur_lower},
             "after": {"upper": upper,
                       "lower": lower}},
            reason, requested_by)
        change_id = int(change.get("changeId") or 0)

        # ② 58号镜像 pending 快照
        record = {
            "tier": THRESHOLD_MIRROR_KEY,
            "status": "pending",
            "baseUpper": cur_upper,
            "baseLower": cur_lower,
            "thresholds": {
                "pendingUpper": upper,
                "pendingLower": lower,
                "scope": "threshold_baseline",
            },
            "changeId": change_id,
            "extra": {
                "lastAction": "submit",
                "requestedBy": str(
                    requested_by or "admin"),
                "reason": reason[:200],
            },
            "updatedAt": ts(),
        }
        await self.repo.save_threshold(
            record, create=mirror is None)

        await self._track(0, "threshold_change", {
            "action": "submit",
            "changeId": change_id,
            "before": {"upper": cur_upper,
                       "lower": cur_lower},
            "after": {"upper": upper,
                      "lower": lower},
        })
        return {
            "success": True,
            "changeId": change_id,
            "status": "pending",
            "effective": {"upper": cur_upper,
                          "lower": cur_lower},
            "proposed": {"upper": upper,
                         "lower": lower},
            "note": "校准申请已受理(46号留痕+"
                    "镜像 pending)——人工终审 "
                    "review 后生效",
            "calibratedAt": ts(),
        }

    async def review_calibration(self, change_id: int,
                                 approve: bool,
                                 reviewer: str = "admin",
                                 note: str = ""
                                 ) -> dict:
        """阈值校准人工终审(pending→active 唯一
        出口——不受开关影响的人工铁律)

        46号 change 同步收口(config 人工通道:
        批准侧总线记录 rejected+error"请人工
        执行"为设计语义; 驳回侧一致 rejected)。

        Raises:
            KeyError: 无待终审校准
            ValueError: changeId 不匹配
        """
        mirror = await self.repo.get_threshold(
            THRESHOLD_MIRROR_KEY)
        if mirror is None \
                or mirror.get("status") != "pending":
            raise KeyError(
                "无待终审的阈值校准(pending)")
        if int(mirror.get("changeId") or 0) \
                != int(change_id):
            raise ValueError(
                f"changeId 不匹配(镜像 "
                f"{mirror.get('changeId')}——"
                f"当前申请 {change_id})")

        pending = mirror.get("thresholds") or {}
        p_upper = float(pending.get("pendingUpper")
                        or 0)
        p_lower = float(pending.get("pendingLower")
                        or 0)
        old_upper = round(float(
            mirror.get("baseUpper") or 0), 4)
        old_lower = round(float(
            mirror.get("baseLower") or 0), 4)

        # ① 46号 change 收口(fail-soft)
        await self._gov_settle(
            int(change_id), approve, reviewer, note)

        # ② 镜像翻转(58号生效域唯一出口)
        extra = dict(mirror.get("extra") or {})
        if approve:
            mirror["baseUpper"] = p_upper
            mirror["baseLower"] = p_lower
            mirror["status"] = "active"
            extra.update({
                "lastAction": "approve",
                "approvedAt": ts(),
                "approvedBy": str(reviewer or "admin"),
                "note": str(note or "")[:200],
                "history": (extra.get("history")
                            or [])[-4:] + [{
                    "action": "approve",
                    "upper": p_upper,
                    "lower": p_lower,
                    "at": ts()}],
            })
        else:
            mirror["status"] = "rejected"
            extra.update({
                "lastAction": "reject",
                "rejectedAt": ts(),
                "rejectedBy": str(reviewer or "admin"),
                "note": str(note or "")[:200],
                "history": (extra.get("history")
                            or [])[-4:] + [{
                    "action": "reject",
                    "at": ts()}],
            })
        mirror["extra"] = extra
        mirror["updatedAt"] = ts()
        await self.repo.save_threshold(
            mirror, create=False)

        await self._track(0, "threshold_change", {
            "action": "approve" if approve
            else "reject",
            "changeId": int(change_id),
            "effective": {
                "upper": p_upper if approve
                else old_upper,
                "lower": p_lower if approve
                else old_lower,
            },
            "reviewer": reviewer,
        })
        return {
            "success": True,
            "changeId": int(change_id),
            "status": mirror["status"],
            "effective": {
                "upper": round(float(
                    mirror["baseUpper"]), 4),
                "lower": round(float(
                    mirror["baseLower"]), 4),
            },
            "note": "校准已批准生效(镜像 active)"
                    if approve
                    else "校准已驳回(基线不变)",
            "reviewedAt": ts(),
        }

    async def thresholds_view(self) -> dict:
        """阈值全景(观测面——各 tier 运行态计算
        值+当前生效基线+pending 申请)"""
        mirror = await self.repo.get_threshold(
            THRESHOLD_MIRROR_KEY)
        upper, lower, source = await \
            self._effective_baseline()

        by_tier = {}
        for tier in ("trusted", "standard",
                     "watched", "restricted"):
            d_upper, d_lower = \
                TIER_THRESHOLD_DELTA.get(
                    tier, (0.0, 0.0))
            by_tier[tier] = {
                "upper": round(min(CALIBRATE_MAX,
                                  upper + d_upper), 4),
                "lower": round(min(
                    upper + d_upper - 0.05,
                    lower + d_lower), 4),
            }

        pending = None
        if mirror and mirror.get("status") \
                == "pending":
            p = mirror.get("thresholds") or {}
            pending = {
                "changeId": int(
                    mirror.get("changeId") or 0),
                "proposed": {
                    "upper": p.get("pendingUpper"),
                    "lower": p.get("pendingLower"),
                },
                "reason": (mirror.get("extra")
                           or {}).get("reason"),
            }
        return {
            "success": True,
            "baseline": {
                "upper": upper, "lower": lower,
                "source": source,
                "mirrorStatus": (mirror or {}).get(
                    "status") or "none",
            },
            "pending": pending,
            "byTier": by_tier,
            "tierDelta": {
                k: {"upper": v[0], "lower": v[1]}
                for k, v in
                TIER_THRESHOLD_DELTA.items()},
            "note": "阈值全景——tier delta 运行态"
                    "计算不落库; 基线变更唯一生效"
                    "通道=人工终审(46号留痕)",
        }

    # --------------------------------------------------------
    # 46号审批总线联动(fail-soft)
    # --------------------------------------------------------

    async def _gov_submit(self, payload: dict,
                          reason: str,
                          requested_by: str
                          ) -> dict:
        """46号 submit_change 留痕(config——
        冷态自愈 sync 幂等)"""
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov = AiGovernanceService()
        if await gov.repo.get_gov(
                SCORER_ID) is None:
            await gov.sync_registry()
        return await gov.submit_change(
            SCORER_ID, "config", payload,
            reason, requested_by)

    @staticmethod
    async def _gov_settle(change_id: int,
                           approve: bool,
                           reviewer: str,
                           note: str) -> None:
        """46号 change 收口(fail-soft——
        config 人工通道语义)"""
        try:
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            gov = AiGovernanceService()
            if approve:
                try:
                    await gov.review_change(
                        int(change_id), True,
                        str(reviewer or "admin"),
                        str(note or "")
                        or "58号阈值终审: 批准生效"
                           "(config 人工通道收口)")
                except ValueError:
                    # 总线对 config 不执行业务变更
                    # (rejected+error"请人工执行"
                    #  为设计语义——留痕已收口)
                    pass
            else:
                await gov.review_change(
                    int(change_id), False,
                    str(reviewer or "admin"),
                    str(note or "")
                    or "58号阈值终审: 驳回")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_gov_settle_failed %s: %s",
                change_id, exc)

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
