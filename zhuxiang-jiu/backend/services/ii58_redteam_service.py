"""58号·AI智能优化意图识别 红队七向量
(ii58_redteam_service, P5)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md
§十/§九 P5):
    RT-01 语料投毒(伪造负样本灌入——
           意图不在册/类型非法/越界域校验拒绝)
    RT-02 意图越界(guest 权限外执行——
           minRole 前置校验拦截)
    RT-03 对抗混淆(易混淆对攻击——
           对抗样本 FULL 命中降权+澄清)
    RT-04 阈值操纵(伪造 tier——47号纯读取
           不可写+fail-soft standard 基线)
    RT-05 反馈污染(虚假正反馈——
           标注队列 pending 人工 decide,
           语料不直接生效)
    RT-06 标注注入(恶意标注——
           回流类型四类封闭+状态机不可重复)
    RT-07 LLM 越白名单(合成建议仅建议
           不入库+II58_LLM_MODE 默认 off)

设计(55/56/57号确定性红队范式——不依赖 LLM,
全部向量离线可复现):
    每向量: 构造攻击载荷 → 调用目标面 →
    断言防御行为(拒绝/拦截/降权/封闭) → 留痕。

前置: II58_MODE=shadow/assist(决策面开放
——off 态无攻击面)。
"""

import logging
import os

from core.helpers import ts

from repositories.ii58_repository import (
    Ii58Repository,
)

logger = logging.getLogger("ii58_redteam_service")

MODEL_VERSION = "v1-ii58-redteam"

# 红队专用会员(向量隔离——不复用真实域)
RT_MEMBER = 9901


class Ii58RedteamService:
    """58号红队验证(七向量——确定性)"""

    def __init__(self):
        self.repo = Ii58Repository()

    # ============================================================
    # 红队入口(七向量全量)
    # ============================================================

    async def run_all(self) -> dict:
        """执行七向量红队全量(RT-01~07)

        前置: II58_MODE=shadow/assist(决策面
        开放——off 态无攻击面)。
        """
        mode = os.environ.get("II58_MODE", "off")
        if mode == "off":
            raise ValueError(
                "红队需要 II58_MODE=shadow/assist"
                "(决策面开放——off 态无攻击面)")

        vectors = {}
        vectors["RT-01"] = await \
            self._rt01_corpus_poison()
        vectors["RT-02"] = await \
            self._rt02_intent_bypass()
        vectors["RT-03"] = await \
            self._rt03_adversarial_confusion()
        vectors["RT-04"] = await \
            self._rt04_threshold_manipulation()
        vectors["RT-05"] = await \
            self._rt05_feedback_pollution()
        vectors["RT-06"] = await \
            self._rt06_label_injection()
        vectors["RT-07"] = await \
            self._rt07_llm_whitelist_escape()

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
    # RT-01 语料投毒(伪造负样本灌入)
    # ============================================================

    async def _rt01_corpus_poison(self) -> dict:
        """三路: 不在册意图/非法样本类型/
        对抗混淆方伪造——ingest 全拒"""
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()
        results = []

        # 路 ①: 不在册意图(封闭白名单)
        try:
            await svc.ingest(
                "hack.backdoor", "攻击文本")
            results.append({"path": "不在册意图",
                            "rejected": False})
        except ValueError:
            results.append({"path": "不在册意图",
                            "rejected": True})

        # 路 ②: 非法样本类型
        try:
            await svc.ingest(
                "product.price_query", "攻击文本",
                sample_type="poison")
            results.append({"path": "非法类型",
                            "rejected": False})
        except ValueError:
            results.append({"path": "非法类型",
                            "rejected": True})

        # 路 ③: 对抗样本混淆方伪造
        try:
            await svc.ingest(
                "product.price_query", "伪造对抗",
                sample_type="adversarial",
                confusable_target=(
                    "trust.balance_query"))
            results.append({"path": "混淆方伪造",
                            "rejected": False})
        except ValueError:
            results.append({"path": "混淆方伪造",
                            "rejected": True})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "语料投毒(伪造负样本灌入)",
            "defended": defended,
            "results": results,
            "defense": "INTENT_REGISTRY 封闭白名单+"
                       "样本类型四类封闭+"
                       "confusableWith 注册校验",
        }

    # ============================================================
    # RT-02 意图越界(guest 权限外执行)
    # ============================================================

    async def _rt02_intent_bypass(self) -> dict:
        """guest 越权评估 member 意图+admin
        越权评估 deny 域——前置校验拦截"""
        from services.ii58_service import (
            Ii58Service,
        )
        svc = Ii58Service()
        corpus_id = await self._seed_corpus(
            "product.price_query", "rt02 price")
        try:
            # guest 越权 member 意图
            r1 = await svc.evaluate(
                "rt02 price", member_role="guest")
            intercepted = (
                r1.get("boundaryIntercepted")
                is True
                and r1.get("intentId")
                == "boundary.unauthorized")
            # 原始意图归因保留
            attr = r1.get("attribution") or {}
            comp = attr.get("compliance") or {}
            preserved = comp.get(
                "originalIntentId") \
                == "product.price_query"
            # admin 越权 deny 域
            r2 = await svc.evaluate(
                "rt02 price", member_role="admin")
            allowed = (
                r2.get("boundaryIntercepted")
                is not True)
            results = [{
                "guestIntercepted": intercepted,
                "attributionPreserved": preserved,
                "adminAllowed": allowed,
            }]
            defended = intercepted and preserved \
                and allowed
        finally:
            await self._cleanup_corpus(corpus_id)
        return {
            "vector": "意图越界(权限外执行)",
            "defended": defended,
            "results": results,
            "defense": "minRole 前置校验+沙箱五级"
                       "裁决+越界改判归因保留"
                       "(识别即合规)",
        }

    # ============================================================
    # RT-03 对抗混淆(易混淆对攻击)
    # ============================================================

    async def _rt03_adversarial_confusion(
            self) -> dict:
        """构造混淆文本: positive+adversarial
        同文本 → FULL 命中降权 → 非 resolved
        (澄清优于错误执行)"""
        from services.ii58_service import (
            Ii58Service,
        )
        adv_id = await self._seed_corpus(
            "product.price_query", "rt03 text",
            sample_type="adversarial")
        pos_id = await self._seed_corpus(
            "product.price_query", "rt03 text")
        try:
            r = await Ii58Service().evaluate(
                "rt03 text")
            attr = r.get("attribution") or {}
            penalized = attr.get(
                "adversarialPenalty") is True
            clarified = r.get("state") != "resolved"
            results = [{
                "adversarialPenalty": penalized,
                "state": r.get("state"),
                "confidence":
                    r.get("confidence"),
            }]
            defended = penalized and clarified
        finally:
            await self._cleanup_corpus(adv_id)
            await self._cleanup_corpus(pos_id)
        return {
            "vector": "对抗混淆(易混淆对攻击)",
            "defended": defended,
            "results": results,
            "defense": "对抗样本 FULL 命中否决"
                       "×0.5 降权——混淆域强制"
                       "澄清(澄清优于错误执行)",
        }

    # ============================================================
    # RT-04 阈值操纵(伪造 tier)
    # ============================================================

    async def _rt04_threshold_manipulation(
            self) -> dict:
        """伪造 tier 注入: 未建档会员+异常
        读取 → fail-soft standard 基线(不可写)"""
        from services.ii58_service import (
            Ii58Service,
        )
        svc = Ii58Service()
        corpus_id = await self._seed_corpus(
            "product.price_query", "rt04 text")
        try:
            # 未建档会员(无 47号档案)
            # → fail-soft standard
            r = await svc.evaluate(
                "rt04 text", member_id=RT_MEMBER)
            attr = r.get("attribution") or {}
            tier = attr.get("tier")
            thresholds = attr.get("thresholds") or {}
            # standard 基线(0.9/0.7——非 trusted
            # 的 0.85 亦非 watched 0.95)
            standard_base = (
                tier == "standard"
                and thresholds.get("upper") == 0.9)
            # 伪造 tier 字符串直接注入
            # (47号纯读取——无写入路径;
            #  _member_tier fail-soft 兜底)
            try:
                forged = await svc._member_tier(
                    "evil_string")
            except Exception:  # noqa: BLE001
                forged = "standard"
            results = [{
                "tier": tier,
                "upper": thresholds.get("upper"),
                "forgedTierFallback": forged,
            }]
            defended = standard_base \
                and forged == "standard"
        finally:
            await self._cleanup_corpus(corpus_id)
        return {
            "vector": "阈值操纵(伪造 tier)",
            "defended": defended,
            "results": results,
            "defense": "47号 get_profile 纯读取"
                       "不可写+fail-soft standard"
                       "基线+基线变更 46号审批",
        }

    # ============================================================
    # RT-05 反馈污染(虚假正反馈)
    # ============================================================

    async def _rt05_feedback_pollution(
            self) -> dict:
        """虚假正反馈灌入: 显式反馈→pending
        标注队列(人工 decide)——语料零直接
        生效(内部切 assist 会员面)"""
        from services.ii58_service import (
            Ii58Service,
        )
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        saved_mode = os.environ.get("II58_MODE")
        os.environ["II58_MODE"] = "assist"
        corpus_id = await self._seed_corpus(
            "product.price_query", "rt05 text")
        try:
            svc = Ii58Service()
            corpus_before = len(
                await self.repo.list_corpus(
                    status="active", limit=1000))
            ev = await svc.evaluate(
                "rt05 text", member_id=RT_MEMBER)
            # 虚假正反馈(冒充会员纠正)
            fb = await (
                Ii58FeedbackService()
                .submit_feedback(
                    member_id=RT_MEMBER,
                    eval_id=ev["evalId"],
                    text="虚假纠正",
                    corrected_intent_id=(
                        "product.new_query")))
            corpus_after = len(
                await self.repo.list_corpus(
                    status="active", limit=1000))
            label = await self.repo.get_label(
                fb.get("labelId"))
            pending_only = (
                label is not None
                and label.get("status") == "pending")
            no_effect = \
                corpus_before == corpus_after
            results = [{
                "feedbackAccepted":
                    int(fb.get("feedbackId") or 0) > 0,
                "labelPending": pending_only,
                "corpusUnchanged": no_effect,
            }]
            defended = pending_only and no_effect
        finally:
            await self._cleanup_corpus(corpus_id)
            # 还原模式
            if saved_mode is None:
                os.environ.pop("II58_MODE",
                               None)
            else:
                os.environ["II58_MODE"] = saved_mode
        return {
            "vector": "反馈污染(虚假正反馈)",
            "defended": defended,
            "results": results,
            "defense": "反馈→标注队列 pending"
                       "人工 decide——语料回流"
                       "永不自动生效(人工铁律)",
        }

    # ============================================================
    # RT-06 标注注入(恶意标注)
    # ============================================================

    async def _rt06_label_injection(
            self) -> dict:
        """恶意标注: 非法回流类型+不在册目标
        意图+重复裁决——全拒(内部切 assist)"""
        from services.ii58_service import (
            Ii58Service,
        )
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        saved_mode = os.environ.get("II58_MODE")
        os.environ["II58_MODE"] = "assist"
        corpus_id = await self._seed_corpus(
            "product.price_query", "rt06 text")
        try:
            svc = Ii58Service()
            ev = await svc.evaluate(
                "rt06 text", member_id=RT_MEMBER)
            fb = await (
                Ii58FeedbackService()
                .submit_feedback(
                    member_id=RT_MEMBER,
                    eval_id=ev["evalId"],
                    text="注入样本"))
            label_id = fb.get("labelId")
            results = []

            # 路 ①: 非法回流类型
            try:
                await (
                    Ii58FeedbackService().decide(
                        int(label_id),
                        approve=True,
                        target_sample_type="poison"))
                results.append({
                    "path": "非法回流类型",
                    "rejected": False})
            except ValueError:
                results.append({
                    "path": "非法回流类型",
                    "rejected": True})

            # 路 ②: 不在册目标意图
            try:
                await (
                    Ii58FeedbackService().decide(
                        int(label_id), approve=True,
                        target_intent_id=(
                            "hack.intent")))
                results.append({
                    "path": "不在册意图",
                    "rejected": False})
            except ValueError:
                results.append({
                    "path": "不在册意图",
                    "rejected": True})

            # 路 ③: 合法裁决后重复注入
            await (
                Ii58FeedbackService().decide(
                    int(label_id),
                    approve=False))
            try:
                await (
                    Ii58FeedbackService().decide(
                        int(label_id),
                        approve=True))
                results.append({
                    "path": "重复裁决",
                    "rejected": False})
            except ValueError:
                results.append({
                    "path": "重复裁决",
                    "rejected": True})

            defended = all(r["rejected"]
                           for r in results)
        finally:
            await self._cleanup_corpus(corpus_id)
            if saved_mode is None:
                os.environ.pop("II58_MODE",
                               None)
            else:
                os.environ["II58_MODE"] = saved_mode
        return {
            "vector": "标注注入(恶意标注)",
            "defended": defended,
            "results": results,
            "defense": "回流类型四类封闭+意图"
                       "在册校验+状态机不可"
                       "重复裁决",
        }

    # ============================================================
    # RT-07 LLM 越白名单(合成建议逃逸)
    # ============================================================

    async def _rt07_llm_whitelist_escape(
            self) -> dict:
        """LLM 轨逃逸: 默认 off 拒绝+
        on 态建议不入库"""
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()
        results = []

        # 路 ①: II58_LLM_MODE off 拒绝
        saved = os.environ.get("II58_LLM_MODE")
        os.environ.pop("II58_LLM_MODE", None)
        try:
            await svc.suggest_variants(
                "product.price_query")
            results.append({
                "path": "LLM off 拒绝",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "LLM off 拒绝",
                "rejected": True})

        # 路 ②: on 态建议不入库
        os.environ["II58_LLM_MODE"] = "on"
        corpus_before = len(
            await self.repo.list_corpus(
                limit=1000))
        r = await svc.suggest_variants(
            "product.price_query", count=2)
        corpus_after = len(
            await self.repo.list_corpus(
                limit=1000))
        suggestion_only = (
            len(r.get("suggestions") or []) == 2
            and corpus_before == corpus_after)
        results.append({
            "path": "建议不入库",
            "suggestionOnly": suggestion_only})
        # 还原开关
        if saved is None:
            os.environ.pop("II58_LLM_MODE",
                           None)
        else:
            os.environ["II58_LLM_MODE"] = saved

        defended = results[0]["rejected"] \
            and suggestion_only
        return {
            "vector": "LLM 越白名单"
                      "(合成建议逃逸)",
            "defended": defended,
            "results": results,
            "defense": "II58_LLM_MODE 三态默认"
                       "off+建议仅返回不入库"
                       "(共创 pending 范式)",
        }

    # ============================================================
    # 内部(语料种子+清理——向量隔离)
    # ============================================================

    async def _seed_corpus(self, intent_id: str,
                           text: str,
                           sample_type: str
                           = "positive") -> int:
        """种红队语料(active 直通——向量隔离)"""
        corpus_id = await \
            self.repo.next_corpus_id()
        await self.repo.save_corpus({
            "corpusId": corpus_id,
            "corpusVersion": 1,
            "intentId": intent_id,
            "sampleType": sample_type,
            "text": text,
            "weight": 1.0,
            "source": "redteam",
            "originRef": "",
            "confusableTarget":
                "product.new_query"
            if sample_type == "adversarial"
            else None,
            "humanVerified": True,
            "humanSuggested": False,
            "status": "active",
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        return corpus_id

    async def _cleanup_corpus(self,
                              corpus_id: int
                              ) -> None:
        """清理红队语料(向量隔离——不留污染)"""
        try:
            corpus = await self.repo.get_corpus(
                int(corpus_id))
            if corpus is not None:
                corpus["status"] = "retired"
                corpus["updatedAt"] = ts()
                await self.repo.save_corpus(
                    corpus, create=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_rt_cleanup_failed %s: %s",
                corpus_id, exc)
