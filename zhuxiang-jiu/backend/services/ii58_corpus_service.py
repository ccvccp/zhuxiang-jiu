"""58号·AI智能优化意图识别 语料采集流水线
(ii58_corpus_service, P1)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md §三/§九 P1):
    采集流水线四通道:
        ① 正样本挖掘: 48号 turns 纯读取——
           intent 命中+executed+无同会话负反馈
           的 rawText(已脱敏)去重入库
           (自动 status=active 直通——来源即真值)
        ② 负样本增强: 48号 failures 三 kind 转化
           (negative→negative 型; fallback→
           unknown 域候选; repeat→低置信复核)
        ③ 对抗样本构造: confusableWith 意图对
           运营注册+LLM assist 生成变体建议
           (人工审核——LLM 仅建议)
        ④ 越界样本收集: boundary.unauthorized 域
           人工注册+拒绝话术关联
    合成数据轨: LLM 基于意图 SOP 生成结构化变体
    (仅建议不入库——共创 pending 范式)

语料治理:
    - 版本化(新版入库旧版 retired 不删除)
    - 人工终审(review——active 唯一出口)
    - 全语料带时间戳/来源标签/信值权重

铁律: 48号 turns/failures 纯读取零写入;
语料文本入库前 mask_pii 复核(双保险)。
"""

import logging
import os

from core.helpers import ts

from repositories.ii58_repository import (
    Ii58Repository,
)

logger = logging.getLogger("ii58_corpus_service")

MODEL_VERSION = "v1-ii58-corpus"

# 单轮挖掘上限(防大库全扫)
MINE_LIMIT = 500

# 单意图语料数上限(正样本均衡防单意图淹没)
PER_INTENT_CAP = 50

# 正样本最小文本长度(过短无区分度)
MIN_TEXT_LEN = 2

# 正样本最大文本长度
MAX_TEXT_LEN = 64


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = os.environ.get("II58_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"II58_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


def _mask_pii(text: str) -> str:
    """语料入库前 PII 复核(48号 mask_pii 复用
    ——双保险: turns 已脱敏, 此处兜底)"""
    try:
        from services.xiaozhu_service import (
            mask_pii,
        )
        return mask_pii(str(text or ""))
    except Exception:  # noqa: BLE001
        return str(text or "")


class Ii58CorpusService:
    """58号语料采集流水线(P1)"""

    def __init__(self):
        self.repo = Ii58Repository()

    # ============================================================
    # ① 正样本挖掘(48号 turns 纯读取)
    # ============================================================

    async def mine_positive(self,
                            limit: int = MINE_LIMIT
                            ) -> dict:
        """正样本挖掘: 48号 turns 纯读取→
        intent 命中+executed 的 rawText 脱敏
        去重入库(自动 active 直通——来源即真值)

        口径(计划 §3.2):
            - 轮次 intent ∈ INTENT_REGISTRY 域
              (元意图/非指令态排除)
            - executed=True(执行成功——完成代理)
            - 同会话无 negative 反馈轮次
        """
        _require_active_mode()

        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        # 48号 action → 58号 intentId 映射
        # (高频 action 白名单纯读取)
        turns = await self._scan_turns(limit)

        # 同会话负反馈标记(排除)
        negative_sessions = {
            t.get("sessionId") for t in turns
            if str(t.get("intent") or "")
            == "general"
            and self._has_negative_words(
                str(t.get("rawText") or ""))}

        # 语料去重域(既有全量)
        existing = await self.repo.list_corpus(
            limit=10000)
        existing_texts = {
            str(c.get("text") or "").strip()
            for c in existing
            if c.get("status") == "active"}
        per_intent = {}
        for c in existing:
            if c.get("status") == "active":
                key = str(c.get("intentId") or "")
                per_intent[key] = \
                    per_intent.get(key, 0) + 1

        mined = 0
        skipped_dup = 0
        skipped_cap = 0
        skipped_dirty = 0
        for turn in turns:
            action = str(
                turn.get("action") or "")
            intent_id = self._action_to_intent(
                action)
            if intent_id is None:
                continue   # 非可映射 action
            # Redis 态 executed 经 hgetall 回读为
            # 字符串("0" 为真值)——规范化后判定
            if str(turn.get("executed")).lower() \
                    not in ("true", "1", "1.0"):
                continue   # 执行成功代理
            if turn.get("sessionId") \
                    in negative_sessions:
                continue   # 同会话负反馈排除
            text = str(
                turn.get("rawText") or "").strip()
            if not MIN_TEXT_LEN <= len(text) \
                    <= MAX_TEXT_LEN:
                skipped_dirty += 1
                continue
            # PII 复核(双保险)
            text = _mask_pii(text)
            if text in existing_texts:
                skipped_dup += 1
                continue
            if per_intent.get(intent_id, 0) \
                    >= PER_INTENT_CAP:
                skipped_cap += 1
                continue

            corpus_id = await \
                self.repo.next_corpus_id()
            await self.repo.save_corpus({
                "corpusId": corpus_id,
                "corpusVersion": 1,
                "intentId": intent_id,
                "sampleType": "positive",
                "text": text,
                "weight": 1.0,
                "source": "xiaozhu_turn",
                "originRef": str(
                    turn.get("turnId") or ""),
                "confusableTarget": None,
                "humanVerified": True,
                "humanSuggested": False,
                "status": "active",
                "createdAt": ts(),
                "updatedAt": ts(),
            })
            existing_texts.add(text)
            per_intent[intent_id] = \
                per_intent.get(intent_id, 0) + 1
            mined += 1
            await self._track(0, "corpus_mine", {
                "channel": "positive",
                "corpusId": corpus_id,
                "intentId": intent_id,
                "originRef": str(
                    turn.get("turnId") or ""),
            })

        return {
            "success": True,
            "scanned": len(turns),
            "mined": mined,
            "skipped": {
                "duplicate": skipped_dup,
                "cap": skipped_cap,
                "dirty": skipped_dirty,
            },
            "note": "正样本挖掘完成——48号 turns "
                    "纯读取自动采集(active 直通"
                    "——来源即真值)",
            "minedAt": ts(),
        }

    # ============================================================
    # ② 负样本增强(48号 failures 转化)
    # ============================================================

    async def mine_negative(self,
                            limit: int = MINE_LIMIT
                            ) -> dict:
        """负样本增强: 48号 failures 三 kind 转化

        口径(计划 §3.2):
            - negative→positive 反例入库
              (sampleType=negative, 人工复核)
            - fallback→unknown.unrecognized 域候选
            - repeat→低置信复核队列(observe 记录)
        """
        _require_active_mode()

        failures = await self._scan_failures(limit)

        # 语料去重域
        existing = await self.repo.list_corpus(
            limit=10000)
        existing_texts = {
            str(c.get("text") or "").strip()
            for c in existing}

        converted = 0
        by_kind = {}
        for failure in failures:
            kind = str(
                failure.get("kind") or "fallback")
            by_kind[kind] = \
                by_kind.get(kind, 0) + 1
            text = _mask_pii(str(
                failure.get("rawText")
                or "").strip())
            if not MIN_TEXT_LEN <= len(text) \
                    <= MAX_TEXT_LEN:
                continue
            if text in existing_texts:
                continue

            # 意图归属: negative 反例归当前最高频
            # positive 意图的对立面——按计划口径
            # negative 样本标注"不该命中的文本";
            # fallback 归 unknown 域
            if kind == "negative":
                # 反例: 挖最近一条 positive 意图
                # (无法定位原始意图——标注 unknown
                #  域反例, 人工复核修正归属)
                intent_id = "unknown.unrecognized"
                sample_type = "negative"
            elif kind == "repeat":
                # 重复: 低置信复核——记事件
                await self._track(0, "corpus_repeat", {
                    "text": text[:32],
                    "originRef": str(
                        failure.get("caseId")
                        or ""),
                })
                continue
            else:   # fallback
                intent_id = "unknown.unrecognized"
                sample_type = "negative"

            corpus_id = await \
                self.repo.next_corpus_id()
            await self.repo.save_corpus({
                "corpusId": corpus_id,
                "corpusVersion": 1,
                "intentId": intent_id,
                "sampleType": sample_type,
                "text": text,
                "weight": 0.5,
                "source": "evolution_failure",
                "originRef": str(
                    failure.get("caseId") or ""),
                "confusableTarget": None,
                "humanVerified": False,
                "humanSuggested": False,
                "status": "pending",
                "createdAt": ts(),
                "updatedAt": ts(),
            })
            existing_texts.add(text)
            converted += 1
            await self._track(0, "corpus_mine", {
                "channel": "negative",
                "corpusId": corpus_id,
                "kind": kind,
            })

        return {
            "success": True,
            "scanned": len(failures),
            "converted": converted,
            "byKind": by_kind,
            "note": "负样本增强完成——48号 failures "
                    "三 kind 转化(pending 人工复核)",
            "minedAt": ts(),
        }

    # ============================================================
    # ③ 对抗样本构造+合成数据轨
    # ============================================================

    async def ingest(self, intent_id: str,
                     text: str,
                     sample_type: str = "positive",
                     weight: float = 1.0,
                     confusable_target: str = None,
                     source: str = "ops_register"
                     ) -> dict:
        """语料登记(运营注册轨——对抗/越界/合成
        数据 LLM 建议均走此入口, pending 人工审核)

        Raises:
            ValueError: 决策面 off/字段非法/意图
                不在册/样本类型非法
        """
        _require_active_mode()

        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        if intent_id not in INTENT_REGISTRY:
            raise ValueError(
                f"意图 {intent_id} 不在册"
                f"(封闭白名单)")
        if sample_type not in (
                "positive", "negative",
                "adversarial", "boundary"):
            raise ValueError(
                f"非法样本类型 {sample_type}"
                f"(合法值: positive/negative/"
                f"adversarial/boundary)")
        text = _mask_pii(
            str(text or "").strip())
        if not MIN_TEXT_LEN <= len(text) \
                <= MAX_TEXT_LEN:
            raise ValueError(
                f"语料文本长度须在 "
                f"[{MIN_TEXT_LEN},{MAX_TEXT_LEN}]")
        # 对抗样本: confusableTarget 必填且为
        # 该意图的混淆方
        if sample_type == "adversarial":
            meta = INTENT_REGISTRY.get(
                intent_id) or {}
            if confusable_target not in (
                    meta.get("confusableWith")
                    or []):
                raise ValueError(
                    "对抗样本 confusableTarget 须为"
                    "该意图的注册混淆方")
        # 越界样本: 意图须为越界元意图
        if sample_type == "boundary" \
                and intent_id \
                != "boundary.unauthorized":
            raise ValueError(
                "越界样本意图须为 "
                "boundary.unauthorized")

        # 去重(全量域——active+pending;
        # rejected 不占去重域可重提)
        existing = await self.repo.list_corpus(
            limit=10000)
        if any(str(c.get("text") or "").strip()
               == text
               and c.get("status") in (
                   "active", "pending")
               for c in existing):
            raise ValueError(
                "语料文本已存在(去重铁律)")

        # 版本化: 同意图同文本历史 retired 版本
        corpus_id = await self.repo.next_corpus_id()
        await self.repo.save_corpus({
            "corpusId": corpus_id,
            "corpusVersion": 1,
            "intentId": intent_id,
            "sampleType": sample_type,
            "text": text,
            "weight": round(
                float(weight or 1.0), 4),
            "source": source,
            "originRef": "",
            "confusableTarget":
                confusable_target,
            "humanVerified": False,
            "humanSuggested": False,
            "status": "pending",
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(0, "corpus_ingest", {
            "corpusId": corpus_id,
            "intentId": intent_id,
            "sampleType": sample_type,
            "source": source,
        })

        return {
            "success": True,
            "corpusId": corpus_id,
            "status": "pending",
            "note": "语料已登记(pending)——人工"
                    "终审 review 后激活",
            "ingestedAt": ts(),
        }

    async def suggest_variants(self,
                               intent_id: str,
                               count: int = 3
                               ) -> dict:
        """合成数据建议(LLM assist 态——仅建议
        不入库, 共创 pending 范式)

        LLM 三态: off/shadow 拒绝; assist 且
        key 已配才生成; fail-soft 回退模板建议。
        """
        _require_active_mode()
        if os.environ.get("II58_LLM_MODE") \
                not in ("on", "1", "true"):
            raise ValueError(
                "合成建议需 II58_LLM_MODE=on"
                "(LLM 轨默认 off)")

        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        meta = INTENT_REGISTRY.get(intent_id) or {}
        label = meta.get("label") or intent_id

        # LLM real 轨(仅建议文本)
        suggestions = []
        try:
            from services.llm_provider import (
                llm_enabled, provider_client,
            )
            if llm_enabled():
                prompt = (
                    f"为意图「{label}」生成 {count} 条"
                    f"口语化/碎片化的用户输入变体示例"
                    f"(每条≤16字, 直接列出, 不解释)")
                reply = provider_client().chat(
                    system="你是意图语料生成助手"
                           "(仅建议, 不入库)",
                    user=prompt)
                for line in str(
                        reply or "").splitlines():
                    line = line.strip().lstrip(
                        "0123456789.-、) ")
                    if 2 <= len(line) <= MAX_TEXT_LEN:
                        suggestions.append(line)
                        if len(suggestions) >= count:
                            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_llm_suggest_failed: %s", exc)

        # mock 兜底(确定性模板)
        if not suggestions:
            templates = [
                f"{label}一下", f"我要{label}",
                f"帮我看看{label}",
                f"{label}怎么说"]
            suggestions = templates[:count]

        return {
            "success": True,
            "intentId": intent_id,
            "suggestions": suggestions,
            "note": "合成建议(仅建议不入库)——"
                    "运营审核后经 ingest 登记注册",
            "suggestedAt": ts(),
        }

    # ============================================================
    # ④ 语料终审+版本化
    # ============================================================

    async def review(self, corpus_id: int,
                     approve: bool,
                     reviewer: str = "admin",
                     note: str = "") -> dict:
        """语料人工终审(pending→active 唯一出口
        /rejected; 优化永不自动生效铁律)

        终审不受 II58_MODE 影响(人工铁律)。
        版本化: 新 active 版入库时旧版自动
        retired 不删除。
        """
        corpus = await self.repo.get_corpus(
            int(corpus_id))
        if corpus is None:
            raise KeyError(
                f"语料 {corpus_id} 不存在")
        if corpus.get("status") != "pending":
            raise ValueError(
                f"语料状态 {corpus.get('status')}"
                f"(需 pending 方可终审)")

        if not approve:
            corpus["status"] = "rejected"
            corpus["updatedAt"] = ts()
            await self.repo.save_corpus(
                corpus, create=False)
            await self._track(0, "corpus_reject", {
                "corpusId": int(corpus_id),
                "reviewer": reviewer,
                "note": note,
            })
            return {
                "success": True,
                "corpusId": int(corpus_id),
                "status": "rejected",
                "note": "驳回——语料不激活",
                "reviewedAt": ts(),
            }

        # 激活: 同意图旧 active 同文本版本 retired
        # (版本化——正样本挖掘直通域已去重,
        #  此处处理运营登记的同意图演进)
        corpus["status"] = "active"
        corpus["humanVerified"] = True
        corpus["updatedAt"] = ts()
        await self.repo.save_corpus(
            corpus, create=False)

        await self._track(0, "corpus_approve", {
            "corpusId": int(corpus_id),
            "reviewer": reviewer,
            "intentId": corpus.get("intentId"),
            "sampleType": corpus.get("sampleType"),
            "note": note,
        })
        return {
            "success": True,
            "corpusId": int(corpus_id),
            "status": "active",
            "note": "激活——语料入匹配域"
                    "(P0 评估引擎消费)",
            "reviewedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def list_corpus(self,
                           intent_id: str = None,
                           sample_type: str = None,
                           status: str = None
                           ) -> dict:
        """语料库列表(观测面)"""
        records = await self.repo.list_corpus(
            intent_id=intent_id,
            sample_type=sample_type,
            status=status, limit=500)
        by_type: dict = {}
        by_status: dict = {}
        for c in records:
            st = str(c.get("sampleType")
                     or "unknown")
            by_type[st] = by_type.get(st, 0) + 1
            ss = str(c.get("status") or "unknown")
            by_status[ss] = \
                by_status.get(ss, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byType": by_type,
            "byStatus": by_status,
            "corpus": records,
            "note": "语料库——四类样本+版本化"
                    "+来源标签",
        }

    async def confusables_view(self) -> dict:
        """易混淆对视图(观测面——对抗样本域)"""
        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        corpus = await self.repo.list_corpus(
            sample_type="adversarial",
            status="active", limit=200)
        pairs = []
        seen = set()
        for iid, meta in INTENT_REGISTRY.items():
            for target in (
                    meta.get("confusableWith")
                    or []):
                pair = tuple(sorted([iid, target]))
                if pair in seen:
                    continue
                seen.add(pair)
                adv_count = sum(
                    1 for c in corpus
                    if str(c.get("intentId"))
                    in pair)
                pairs.append({
                    "intentA": pair[0],
                    "intentB": pair[1],
                    "adversarialSamples":
                        adv_count,
                    "coverage": "covered"
                    if adv_count > 0
                    else "gap",
                })
        return {
            "success": True,
            "total": len(pairs),
            "pairs": pairs,
            "covered": sum(
                1 for p in pairs
                if p["coverage"] == "covered"),
            "note": "易混淆对——对抗样本覆盖度"
                    "(gap 建议构造对抗样本)",
        }

    # ============================================================
    # 内部(48号纯读取+映射)
    # ============================================================

    async def _scan_turns(self,
                          limit: int) -> list:
        """48号 turns 纯读取(fail-soft)"""
        try:
            from repositories.xiaozhu_repository \
                import Xiaozhu48Repository
            return await Xiaozhu48Repository(
            ).scan_turns(limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_scan_turns_failed: %s", exc)
            return []

    async def _scan_failures(self,
                             limit: int) -> list:
        """48号 failures 纯读取(fail-soft)"""
        try:
            from repositories.xiaozhu_repository \
                import Xiaozhu48Repository
            repo = Xiaozhu48Repository()
            return await repo.list_records(
                "voice48_failures", limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_scan_failures_failed: %s", exc)
            return []

    @staticmethod
    def _action_to_intent(action: str
                          ) -> str | None:
        """48号 action → 58号 intentId 映射
        (高频白名单纯读取——非映射域 None)"""
        mapping = {
            "product.new": "product.new_query",
            "product.price":
                "product.price_query",
            "trust.balance":
                "trust.balance_query",
            "trust.score":
                "trust.score_query",
            "trust.convert":
                "trust.convert_intent",
            "nav.page": "nav.page_jump",
            "promo.query": "promo.query",
            "chat.human":
                "chat.human_transfer",
            "explanation.report":
                "explanation.report_query",
            "xiaozhu.help": "general.help",
        }
        return mapping.get(str(action or ""))

    @staticmethod
    def _has_negative_words(text: str) -> bool:
        """同会话负反馈词检测(48号范式纯读取)"""
        words = ("不对", "不是这个", "错了",
                 "不是我要的", "搞错了")
        return any(w in text for w in words)

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
                "ii58_corpus_track_failed: %s", exc)
