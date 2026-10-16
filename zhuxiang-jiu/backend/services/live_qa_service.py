"""数字人直播弹幕 QA 服务(蓝图对照分析近期项④)

定位:
    - 复用 knowledge RAG 规则轨(rule, 确定性), 为直播中控/RPA 程序
      提供弹幕问答 HTTP 端点的业务内核——只读问答, 供轮询调用;
    - 不做自动外发(答案由中控决定是否口播/上屏)、无交易行为;
    - 不做 WS 直播间(本任务仅 QA 内核, 直播间形态留待后续)。

铁律:
    - fail-soft: RAG 异常返回兜底话术, 绝不向调用方抛出;
    - 幂等去重: 进程级 60s 同问(同直播间+归一化问题)直接复用
      上次结果并标 cached=True;
    - LLM 默认不进关键路径(provider 固定 rule; llm 轨留给
      KnowledgeService.rag_answer 自身能力, 本服务不主动启用)。

留痕形态(选择理由):
    - 对齐 agent75_orchestrator_service 的追加式观测日志——
      每条一 dict 即写 JSONL(append-only), 无全量读写/重写,
      直播高频追加下最轻且崩溃安全(对比 store.save 全量 JSON
      重写有丢尾部风险);
    - 进程内 list 仅供 stats 实时汇总, JSONL 供事后审计。

模式闸:
    - LIVE_QA_MODE=off|on(默认 on——纯只读问答无副作用);
      off 时返回统一 disabled 话术并留痕 ragMode=off。

unsolved 闭环(L1/L2, 依据 docs/复购预测与直播QA模块优化建议.md §三):
    - L1 趋势分桶+阈值告警: _record 按小时桶(键 "YYYY-MM-DD HH")
      累计 total/unsolved/filtered/cached, 进程内保留最近 48 桶;
      stats 输出 hourlyBuckets 与 unsolvedRateTrend; 连续 3 个
      已结束桶 unsolved 率 > 40% → 管理员站内信告警一次(armed
      幂等去重——仍超阈值不重复, 跌回 ≤40% 解除后可再告警;
      告警仅发管理员不属于用户触达, 发送 fail-soft 不影响问答主链);
    - L2 缺口闭环: list_gaps 拉取 live_qa 来源缺口(sessionId 前缀
      匹配), gap_to_draft 转知识草稿(status=pending 进候选池——
      先审后显铁律, 绝不直接 publish)。

L3 直播专属知识源(category 优先检索):
    - answer 经 rag_answer(category=直播类) 优先检索直播语料,
      该类零命中自动回退全库(fail-soft), 结果 categoryFallback
      标记透传至返回体;
    - 直播类名 env LIVE_QA_KB_CATEGORY 可配(默认 live);
    - 语料录入: 不新增录入代码——运营经 knowledge 既有
      create_entry 以 category="live" 录入直播语料(价格口径/
      当日活动规则/配送时效等), 先审后显(review→publish)铁律照旧。
"""

import contextlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from services.knowledge_service import (
    KnowledgeService, GAP_STATUS_OPEN,
)

logger = logging.getLogger(__name__)

# 留痕目录(env 可重定向, 测试指向 tmp)
LIVE_QA_LOG_DIR = Path(os.environ.get("LIVE_QA_LOG_DIR", "live_qa_logs"))

# 弹幕问题长度边界(strip 后 1-200 字)
LIVE_QA_MIN_QUESTION_LEN = 1
LIVE_QA_MAX_QUESTION_LEN = 200

# 同问去重窗口(秒)与缓存上限(防内存膨胀, 超限淘汰最旧一半)
LIVE_QA_DEDUP_TTL = 60
LIVE_QA_DEDUP_MAX = 1000

# 小时分桶(L1 unsolved 趋势观测): 键 "YYYY-MM-DD HH", 进程内保留
# 最近 48 桶, 超限淘汰最旧(对齐去重缓存的淘汰策略风格)
LIVE_QA_HOURLY_BUCKET_KEEP = 48
# unsolved 率阈值告警: 连续 3 个已结束桶率 > 0.40 → 管理员站内信一次
LIVE_QA_UNSOLVED_ALERT_THRESHOLD = 0.40
LIVE_QA_UNSOLVED_ALERT_BUCKETS = 3
# 直播缺口会话前缀(record_gap 的 session_id=f"live_qa:{room}" 形态)
LIVE_QA_GAP_SESSION_PREFIX = "live_qa:"
# 缺口转草稿的占位答案(不含违禁词, 正式答案由运营补充后送审)
LIVE_QA_GAP_DRAFT_ANSWER = (
    "【待补充】直播弹幕高频未解问题转知识草稿，正式答案待运营补充。"
)

# 统一话术(off / 过滤 / 低置信兜底)
LIVE_QA_DISABLED_ANSWER = "直播问答小助手暂时下线啦，感谢关注竹香酒直播间～"
LIVE_QA_FILTERED_ANSWER = (
    "这个问题暂时不方便在直播间回答，想了解竹香酒可以直接看商品页哦～"
)
LIVE_QA_UNSOLVED_ANSWER = "这个问题我记下来啦，直播结束后补充到百科。"

# 敏感词简表: 品牌竞品(避免直播对比引战) + 违规/风险词基础清单
LIVE_QA_SENSITIVE_WORDS = (
    # 品牌竞品
    "茅台", "五粮液", "剑南春", "泸州老窖", "汾酒", "洋河",
    "江小白", "习酒", "郎酒",
    # 违规/风险词(酒类直播口径, 对齐 attract 违禁词风格)
    "假酒", "赌博", "刷单", "疗效", "包治", "壮阳", "治百病",
    "延年益寿",
)


def live_qa_mode() -> str:
    """模式闸(默认 on——纯只读问答无副作用; off 返回统一话术)"""
    mode = os.environ.get("LIVE_QA_MODE", "on")
    mode = mode.strip().lower()
    return mode if mode in ("off", "on") else "on"


def _live_kb_category() -> str:
    """直播专属知识库 category(L3)

    env LIVE_QA_KB_CATEGORY 可配(默认 live); 运营按该 category
    录入直播语料(价格口径/活动规则/配送时效等)。
    """
    return ((os.environ.get("LIVE_QA_KB_CATEGORY", "live") or "live")
            .strip() or "live")


def _norm_question(question: str) -> str:
    """问题归一化(去首尾/压缩空白/小写——去重键口径)"""
    return " ".join((question or "").split()).lower()


def _hit_sensitive(question: str) -> bool:
    """敏感词命中判定(简表包含匹配, 小写口径)"""
    lowered = question.lower()
    return any(word in lowered for word in LIVE_QA_SENSITIVE_WORDS)


def _now() -> datetime:
    """进程时钟(小时分桶键与告警判定用; 测试可 monkeypatch 注入假时钟)"""
    return datetime.utcnow()


def _append_log(record: dict) -> None:
    """JSONL 追加留痕(观测面; 失败静默——留痕绝不阻断问答)"""
    with contextlib.suppress(Exception):
        LIVE_QA_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LIVE_QA_LOG_DIR / "live_qa.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _notify_admins_unsolved(alert_buckets: list[dict],
                                  top_question: dict) -> bool:
    """unsolved 趋势告警 → 管理员站内信(仅发管理员, 不属于用户触达)

    先例对齐 security_alert_service(_list_admin_ids/_broadcast):
        - 管理员来源: MemberRepository().list_all() 中 role=admin;
        - 发送: MessageService.send_message(channel=inmail,
          category=security, priority=P1), 逐一发送单人失败不阻断。
    发送整体 fail-soft: 无收件人/异常仅 warning, 绝不向问答主链抛出。
    Returns: 是否至少送达一人。
    """
    try:
        from repositories.member_repository import MemberRepository
        admins = sorted(int(m["id"]) for m in
                        await MemberRepository().list_all()
                        if m.get("role") == "admin" and m.get("id"))
        if not admins:
            logger.warning("live_qa_unsolved_alert_no_admin_recipient")
            return False
        lines = [f"- {b['hour']}: unsolved {b['unsolved']}/{b['total']}"
                 f"({b['unsolvedRate']:.1%})" for b in alert_buckets]
        top_q = str(top_question.get("question") or "")
        top_n = int(top_question.get("count") or 0)
        title = "[直播运维] 直播问答未解决率连续走高告警"
        content = (
            "直播弹幕问答未解决率(unsolved)连续 "
            f"{len(alert_buckets)} 个小时桶超过 40%:\n"
            + "\n".join(lines)
            + (f"\n最高频未解问题: 「{top_q[:30]}」(被问 {top_n} 次)"
               if top_q else "")
            + "\n处置建议: 优先补充知识条目——管理端拉取直播缺口"
            "(GET /api/live-qa/gaps)并将高频缺口转知识草稿送审"
            "(POST /api/live-qa/gaps/to-draft)。"
            "\n(本告警仅发管理员; 同轮持续超阈值不重复发送)"
        )
        from services.message_service import (
            MessageService, CHANNEL_INMAIL, CATEGORY_SECURITY, PRIORITY_P1,
        )
        svc = MessageService()
        sent = 0
        for admin_id in admins:
            try:
                await svc.send_message(
                    admin_id, CHANNEL_INMAIL, title, content,
                    category=CATEGORY_SECURITY, priority=PRIORITY_P1)
                sent += 1
            except Exception as exc:
                # 单人发送失败不影响其余管理员触达
                logger.warning("live_qa_unsolved_alert_send_failed "
                               "admin=%s: %s", admin_id, exc)
        return sent > 0
    except Exception as exc:
        logger.warning("live_qa_unsolved_alert_skip: %s", exc)
        return False


class LiveQaService:
    """数字人直播弹幕 QA 业务内核(只读, 无自动外发)"""

    def __init__(self, knowledge: KnowledgeService | None = None):
        # 知识内核(knowledge_routes 同款默认构造; 可注入测试桩)
        self._knowledge = (knowledge if knowledge is not None
                           else KnowledgeService())
        # 进程级同问去重缓存: (roomId, 归一化问题) -> (过期时间, 结果)
        self._recent: dict[tuple[str, str], tuple[float, dict]] = {}
        # 进程内留痕(stats 汇总数据源; 全量审计走 JSONL)
        self._records: list[dict] = []
        # 小时分桶(L1): "YYYY-MM-DD HH" -> 桶计数(全局口径不分直播间)
        self._hourly: dict[str, dict] = {}
        # 阈值告警状态: armed=本轮已告警(仍超阈值不重复, 跌回解除后可再告警)
        self._alert_armed = False

    # ------------------------------------------------------------
    # 弹幕问答主入口
    # ------------------------------------------------------------

    async def answer(self, room_id: str, question: str) -> dict:
        """弹幕问答: 模式闸→基础过滤→同问去重→RAG(rule)→置信处理→留痕

        Returns:
            {roomId, answer, confidence, ragMode, cached, filtered, ts};
            ragMode ∈ off / filtered / direct / synthesized / unsolved
            (RAG 异常归一为 unsolved 兜底——fail-soft 不抛出)。
        """
        room = (room_id or "").strip()
        ts = datetime.utcnow().isoformat()
        base = {"roomId": room, "confidence": 0.0,
                "cached": False, "filtered": False}

        # ① 模式闸
        if live_qa_mode() == "off":
            result = {**base, "answer": LIVE_QA_DISABLED_ANSWER,
                      "ragMode": "off", "ts": ts}
            await self._record(question, result)
            return result

        q = (question or "").strip()

        # ② 基础过滤: 长度边界 + 敏感词简表
        if not (LIVE_QA_MIN_QUESTION_LEN
                <= len(q) <= LIVE_QA_MAX_QUESTION_LEN):
            result = {**base, "answer": LIVE_QA_FILTERED_ANSWER,
                      "ragMode": "filtered", "filtered": True, "ts": ts}
            await self._record(question, result)
            return result
        if _hit_sensitive(q):
            result = {**base, "answer": LIVE_QA_FILTERED_ANSWER,
                      "ragMode": "filtered", "filtered": True, "ts": ts}
            await self._record(question, result)
            return result

        # ③ 进程级 60s 同问去重(幂等铁律: 命中直接复用上次结果)
        key = (room, _norm_question(q))
        hit = self._recent.get(key)
        if hit is not None and hit[0] > time.time():
            cached_result = dict(hit[1])
            cached_result["cached"] = True
            await self._record(question, cached_result)
            return cached_result

        # ④ RAG 问答(确定性 rule 轨; LLM 轨不进关键路径)
        # L3: category=直播类优先检索直播语料, 该类零命中由
        # knowledge 侧自动回退全库(fail-soft)
        rag: dict = {}
        try:
            rag = await self._knowledge.rag_answer(
                q, "rule", category=_live_kb_category())
            rag_mode = str(rag.get("mode", "unsolved"))
            confidence = float(rag.get("confidence") or 0.0)
            answer_text = str(rag.get("answer") or "").strip()
        except Exception:
            # fail-soft: RAG 异常归一 unsolved 兜底, 绝不抛出
            logger.warning("live_qa_rag_failed room=%s", room,
                           exc_info=True)
            rag_mode, confidence, answer_text = "unsolved", 0.0, ""

        # ⑤ 置信处理: direct/synthesized 原样; unsolved 兜底+缺口登记
        if rag_mode in ("direct", "synthesized") and answer_text:
            final_answer, final_conf = answer_text, confidence
        else:
            final_answer, final_conf = LIVE_QA_UNSOLVED_ANSWER, 0.0
            rag_mode = "unsolved"
            # best-effort 知识缺口登记(KnowledgeService.record_gap;
            # 失败静默——登记不阻断问答主链路)
            with contextlib.suppress(Exception):
                await self._knowledge.record_gap(
                    q, session_id=f"live_qa:{room}")

        result = {**base, "answer": final_answer,
                  "confidence": final_conf, "ragMode": rag_mode, "ts": ts}
        # L3: category 检索空回退标记透传(knowledge 结果带键才透传,
        # ragMode 口径不变)
        if "categoryFallback" in rag:
            result["categoryFallback"] = bool(rag["categoryFallback"])

        # 去重缓存登记(超限淘汰最旧一半, 对齐 knowledge 结果缓存策略)
        self._recent[key] = (time.time() + LIVE_QA_DEDUP_TTL, result)
        if len(self._recent) > LIVE_QA_DEDUP_MAX:
            for k in list(self._recent)[:len(self._recent) // 2]:
                self._recent.pop(k, None)

        # ⑥⑦ 留痕 + 返回
        await self._record(question, result)
        return result

    # ------------------------------------------------------------
    # 统计汇总
    # ------------------------------------------------------------

    def stats(self, room_id: str = None) -> dict:
        """弹幕问答统计(total/unsolved/filtered/cached/未解率/高频问top5
        + 小时分桶 hourlyBuckets 与 unsolvedRateTrend——L1 观测面,
        分桶为全局口径(不分直播间, 与告警判定一致))"""
        rows = [r for r in self._records
                if not room_id or r.get("roomId") == room_id]
        total = len(rows)
        unsolved = sum(1 for r in rows if r.get("ragMode") == "unsolved")
        filtered = sum(1 for r in rows if r.get("filtered"))
        cached = sum(1 for r in rows if r.get("cached"))
        counter: dict[str, dict] = {}
        for r in rows:
            norm = _norm_question(str(r.get("question", "")))
            if not norm:
                continue
            item = counter.setdefault(
                norm, {"question": str(r.get("question", "")), "count": 0})
            item["count"] += 1
        top = sorted(counter.values(),
                     key=lambda x: (-x["count"], x["question"]))[:5]
        # 小时分桶(时间升序, 近 N 桶; N=保留窗口上限)
        buckets = [self._bucket_public(self._hourly[k])
                   for k in sorted(self._hourly)]
        return {
            "total": total,
            "unsolved": unsolved,
            "filtered": filtered,
            "cached": cached,
            "unsolvedRate": round(unsolved / total, 4) if total else 0.0,
            "topQuestions": top,
            "hourlyBuckets": buckets,
            "unsolvedRateTrend": [b["unsolvedRate"] for b in buckets],
        }

    # ------------------------------------------------------------
    # 直播缺口闭环(L2): 拉取 live_qa 来源缺口 + 转知识草稿
    # ------------------------------------------------------------

    async def list_gaps(self, limit: int = 100) -> list[dict]:
        """直播来源知识缺口(open; sessionId 前缀 live_qa:, 按频次倒序)

        仅未处置(open)缺口有补知识运营意义; 输出含频次(askCount)
        与最近提问时间(lastAskedAt)。缺口字段结构(以
        KnowledgeService.record_gap 落库为准): id/question/
        normQuestion/sessionId/askCount/status/entryId/createdAt/
        lastAskedAt/resolvedAt。
        """
        gaps = await self._knowledge.list_gaps(
            status=GAP_STATUS_OPEN, limit=500)
        live = [g for g in gaps if str(g.get("sessionId") or "")
                .startswith(LIVE_QA_GAP_SESSION_PREFIX)]
        live.sort(key=lambda g: (-int(g.get("askCount", 0)),
                                 int(g.get("id") or 0)))
        return [{
            "id": g.get("id"),
            "question": g.get("question"),
            "sessionId": g.get("sessionId"),
            "askCount": int(g.get("askCount", 0)),
            "status": g.get("status"),
            "createdAt": g.get("createdAt"),
            "lastAskedAt": g.get("lastAskedAt"),
        } for g in live[:limit]]

    async def gap_to_draft(self, gap_id: int = 0,
                            question: str = "") -> dict:
        """直播缺口转知识草稿(先审后显铁律: 落 pending, 绝不 publish)

        定位缺口: gapId 精确匹配 / question 文本匹配(二选一), 仅接受
        sessionId 前缀 live_qa: 的直播来源缺口;
        落草稿: KnowledgeService.create_entry(question=缺口问题,
        answer=占位)——治理流水线原生 status=pending 进候选池,
        keywords 留空待运营补充;
        收尾: best-effort resolve_gap(action=resolve, 关联草稿条目)
        标记缺口已补——对齐 teach_submit 的既有缺口闭环范式。

        Raises:
            KeyError: 直播来源缺口不存在
            ValueError: 参数缺失/知识治理拦截(违禁词/重复知识)
        """
        gap_id = int(gap_id or 0)
        target = (question or "").strip()
        if not gap_id and not target:
            raise ValueError("须提供 gapId 或 question 以定位缺口")
        gaps = await self._knowledge.list_gaps(
            status=GAP_STATUS_OPEN, limit=500)
        live = [g for g in gaps if str(g.get("sessionId") or "")
                .startswith(LIVE_QA_GAP_SESSION_PREFIX)]
        matched = ([g for g in live
                    if int(g.get("id") or 0) == gap_id] if gap_id
                   else [g for g in live
                         if str(g.get("question") or "").strip() == target])
        if not matched:
            raise KeyError(f"直播知识缺口不存在(gapId={gap_id}, "
                           f"question={target[:20]})")
        gap = matched[0]
        # 转草稿: 占位答案经 create_entry 合规筛查, pending 进候选池
        entry = await self._knowledge.create_entry(
            question=str(gap.get("question") or target),
            answer=LIVE_QA_GAP_DRAFT_ANSWER)
        # 缺口标记 best-effort(失败不影响草稿已落库, 留痕人工复核)
        gap_resolved = False
        with contextlib.suppress(Exception):
            await self._knowledge.resolve_gap(
                int(gap["id"]), action="resolve", entry_id=entry["id"])
            gap_resolved = True
        if not gap_resolved:
            logger.warning("live_qa_gap_mark_resolved_failed gap=%s",
                           gap.get("id"))
        return {
            "gapId": gap.get("id"),
            "question": entry["question"],
            "entryId": entry["id"],
            "entryStatus": entry["status"],   # pending(待人工审核后发布)
            "gapResolved": gap_resolved,
        }

    # ------------------------------------------------------------
    # 内部: 留痕(进程内 list + 即写 JSONL + 小时分桶 + 阈值告警)
    # ------------------------------------------------------------

    async def _record(self, question: str, result: dict) -> None:
        """每条问答留痕一 dict(含全部返回字段+原始问题), 即写 JSONL;
        同一回答链路内完成小时分桶累计与 unsolved 阈值告警判定"""
        record = {**result, "question": question}
        self._records.append(record)
        _append_log(record)
        self._bucket_accumulate(question, result)
        await self._check_unsolved_alert()

    # ------------------------------------------------------------
    # 内部: 小时分桶(L1)与阈值告警判定
    # ------------------------------------------------------------

    def _bucket_accumulate(self, question: str, result: dict) -> None:
        """小时分桶累计(键 "YYYY-MM-DD HH"; 保留最近 48 桶淘汰最旧)

        桶内 _unsolvedQ 为该桶 unsolved 问题计数器(告警 topQuestion
        数据源, 对外投影时剥离)。
        """
        key = _now().strftime("%Y-%m-%d %H")
        bucket = self._hourly.get(key)
        if bucket is None:
            bucket = {"hour": key, "total": 0, "unsolved": 0,
                      "filtered": 0, "cached": 0, "_unsolvedQ": {}}
            self._hourly[key] = bucket
        bucket["total"] += 1
        if result.get("ragMode") == "unsolved":
            bucket["unsolved"] += 1
            norm = _norm_question(str(question or ""))
            if norm:
                item = bucket["_unsolvedQ"].setdefault(
                    norm, {"question": str(question), "count": 0})
                item["count"] += 1
        if result.get("filtered"):
            bucket["filtered"] += 1
        if result.get("cached"):
            bucket["cached"] += 1
        # 超限淘汰最旧桶(键 "YYYY-MM-DD HH" 字典序即时间序)
        if len(self._hourly) > LIVE_QA_HOURLY_BUCKET_KEEP:
            for k in sorted(self._hourly)[
                    :len(self._hourly) - LIVE_QA_HOURLY_BUCKET_KEEP]:
                self._hourly.pop(k, None)

    @staticmethod
    def _bucket_rate(bucket: dict) -> float:
        """桶 unsolved 率(纯计数确定性; 空桶防 0 除返回 0.0)"""
        total = int(bucket.get("total", 0))
        if not total:
            return 0.0
        return round(int(bucket.get("unsolved", 0)) / total, 4)

    @classmethod
    def _bucket_public(cls, bucket: dict) -> dict:
        """桶对外投影(剥内部 _unsolvedQ 计数器, 补 unsolvedRate)"""
        return {
            "hour": bucket["hour"],
            "total": int(bucket["total"]),
            "unsolved": int(bucket["unsolved"]),
            "filtered": int(bucket["filtered"]),
            "cached": int(bucket["cached"]),
            "unsolvedRate": cls._bucket_rate(bucket),
        }

    @staticmethod
    def _top_unsolved_question(buckets: list[dict]) -> dict:
        """跨桶最高频 unsolved 问题(并列取问题串较小者——确定性)"""
        merged: dict[str, dict] = {}
        for b in buckets:
            for norm, item in (b.get("_unsolvedQ") or {}).items():
                m = merged.setdefault(
                    norm, {"question": item["question"], "count": 0})
                m["count"] += int(item["count"])
        if not merged:
            return {"question": "", "count": 0}
        return sorted(merged.values(),
                      key=lambda x: (-x["count"], x["question"]))[0]

    async def _check_unsolved_alert(self) -> None:
        """unsolved 阈值告警(同一回答链路内判定, 纯计数确定性)

        最近 3 个已结束桶(小时已翻页且有留痕的桶, 无留痕的小时
        无桶不参与判定)率均 > 0.40 → 管理员站内信告警一次:
            - 幂等去重: 告警后进入 armed, 仍超阈值不重复告警;
            - 解除重置: 最近 3 桶任一跌回 ≤0.40 → 解除 armed
              (重置连续口径), 之后再次连续 3 桶超阈值可再告警。
        发送 fail-soft: 先置 armed 再发送(发送失败不重试轰炸,
        异常仅 warning, 不影响问答主链)。
        """
        current_key = _now().strftime("%Y-%m-%d %H")
        closed_keys = sorted(k for k in self._hourly if k < current_key)
        last3 = [self._hourly[k] for k in
                 closed_keys[-LIVE_QA_UNSOLVED_ALERT_BUCKETS:]]
        if len(last3) < LIVE_QA_UNSOLVED_ALERT_BUCKETS:
            return
        over = all(self._bucket_rate(b) > LIVE_QA_UNSOLVED_ALERT_THRESHOLD
                   for b in last3)
        if not over:
            self._alert_armed = False   # 跌回阈值 → 解除 armed(重置连续)
            return
        if self._alert_armed:
            return                     # 本轮已告警, 不重复
        self._alert_armed = True
        top = self._top_unsolved_question(last3)
        try:
            delivered = await _notify_admins_unsolved(
                [self._bucket_public(b) for b in last3], top)
            logger.warning(
                "live_qa_unsolved_alert_dispatched buckets=%s top=%s "
                "delivered=%s", [b["hour"] for b in last3],
                top.get("question"), delivered)
        except Exception as exc:
            logger.warning("live_qa_unsolved_alert_send_skip: %s", exc)
