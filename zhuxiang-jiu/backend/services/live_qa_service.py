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
"""

import contextlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

# 留痕目录(env 可重定向, 测试指向 tmp)
LIVE_QA_LOG_DIR = Path(os.environ.get("LIVE_QA_LOG_DIR", "live_qa_logs"))

# 弹幕问题长度边界(strip 后 1-200 字)
LIVE_QA_MIN_QUESTION_LEN = 1
LIVE_QA_MAX_QUESTION_LEN = 200

# 同问去重窗口(秒)与缓存上限(防内存膨胀, 超限淘汰最旧一半)
LIVE_QA_DEDUP_TTL = 60
LIVE_QA_DEDUP_MAX = 1000

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


def _norm_question(question: str) -> str:
    """问题归一化(去首尾/压缩空白/小写——去重键口径)"""
    return " ".join((question or "").split()).lower()


def _hit_sensitive(question: str) -> bool:
    """敏感词命中判定(简表包含匹配, 小写口径)"""
    lowered = question.lower()
    return any(word in lowered for word in LIVE_QA_SENSITIVE_WORDS)


def _append_log(record: dict) -> None:
    """JSONL 追加留痕(观测面; 失败静默——留痕绝不阻断问答)"""
    with contextlib.suppress(Exception):
        LIVE_QA_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LIVE_QA_LOG_DIR / "live_qa.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
            self._record(question, result)
            return result

        q = (question or "").strip()

        # ② 基础过滤: 长度边界 + 敏感词简表
        if not (LIVE_QA_MIN_QUESTION_LEN
                <= len(q) <= LIVE_QA_MAX_QUESTION_LEN):
            result = {**base, "answer": LIVE_QA_FILTERED_ANSWER,
                      "ragMode": "filtered", "filtered": True, "ts": ts}
            self._record(question, result)
            return result
        if _hit_sensitive(q):
            result = {**base, "answer": LIVE_QA_FILTERED_ANSWER,
                      "ragMode": "filtered", "filtered": True, "ts": ts}
            self._record(question, result)
            return result

        # ③ 进程级 60s 同问去重(幂等铁律: 命中直接复用上次结果)
        key = (room, _norm_question(q))
        hit = self._recent.get(key)
        if hit is not None and hit[0] > time.time():
            cached_result = dict(hit[1])
            cached_result["cached"] = True
            self._record(question, cached_result)
            return cached_result

        # ④ RAG 问答(确定性 rule 轨; LLM 轨不进关键路径)
        try:
            rag = await self._knowledge.rag_answer(q, "rule")
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

        # 去重缓存登记(超限淘汰最旧一半, 对齐 knowledge 结果缓存策略)
        self._recent[key] = (time.time() + LIVE_QA_DEDUP_TTL, result)
        if len(self._recent) > LIVE_QA_DEDUP_MAX:
            for k in list(self._recent)[:len(self._recent) // 2]:
                self._recent.pop(k, None)

        # ⑥⑦ 留痕 + 返回
        self._record(question, result)
        return result

    # ------------------------------------------------------------
    # 统计汇总
    # ------------------------------------------------------------

    def stats(self, room_id: str = None) -> dict:
        """弹幕问答统计(total/unsolved/filtered/cached/未解率/高频问top5)"""
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
        return {
            "total": total,
            "unsolved": unsolved,
            "filtered": filtered,
            "cached": cached,
            "unsolvedRate": round(unsolved / total, 4) if total else 0.0,
            "topQuestions": top,
        }

    # ------------------------------------------------------------
    # 内部: 留痕(进程内 list + 即写 JSONL)
    # ------------------------------------------------------------

    def _record(self, question: str, result: dict) -> None:
        """每条问答留痕一 dict(含全部返回字段+原始问题), 即写 JSONL"""
        record = {**result, "question": question}
        self._records.append(record)
        _append_log(record)
