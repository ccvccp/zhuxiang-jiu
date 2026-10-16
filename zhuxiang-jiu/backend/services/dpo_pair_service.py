# -*- coding: utf-8 -*-
"""DPO 偏好对服务(小竹蓝图 P4-10 第一批·数据面 + 第三批·串联)

五源归集(S1-S5, 全部现成留痕零新建采集)+质量闸+人工评审环+stats:
    - S1 双轨对照(rag dual 源): knowledge_dual_samples 双师对抗样本,
      同 question 的 golden(双生成双评审胜出高分)×negative(判别否决)
      天然成对——winnerLabel/totalScore 进 meta(仅为建议, 人工可翻转)。
      ※现场核实: voice48_turns 落库无 track 字段(track 仅在 HTTP 响应体
      透传), 对话双轨轮在存储层不可区分, 故 S1 采用 rag dual 样本源。
    - S2 显式反馈配对: voice48_turns 的 feedback 字段(up/down,
      save_turn_feedback 留痕)——down 轮为 rejected, 同 intent ±7 天
      窗口内 up 轮为 chosen(取时间最近一条)。
    - S3 转人工隐式配对(第三批): chat 转人工留痕——rejected=触发
      转人工前的最后自动答, chosen 三级确定性择优: 人工坐席回复
      (chat_messages senderType=customer_service, cs_reply 留痕——
      现场核实人工回复有留痕) > knowledge direct 命中(2-gram 向量
      余弦 top-1, 相似度≥噪声线的降级链) > 放弃(宁缺毋滥)。
    - S4 失败挖掘配对: voice48_failures 失败留痕表——
      kind∈{negative, fallback} 失败案例(rawText 回查 turn 取 reply)
      为 rejected × 同 intent 高分轮(kind=turn_score 且 reward≥0.6
      评分流, sessionId+intent+ts 最近匹配定位 turn)为 chosen。
    - S5 对抗评审对(第三批, selfplay 桥): selfplay A6 领域盲类缺口
      经 L2 闭环(to-draft→review→publish)发布后——chosen=该知识条目
      answer(published 态), rejected=LiveQaService 的
      LIVE_QA_UNSOLVED_ANSWER 兜底话术(unsolved 的失败应答);
      现场核实字段链通: knowledge gaps 表 status=resolved 且
      entryId>0 → get_entry(entryId).answer。

质量闸(进对前确定性过滤, 纯函数):
    ①文本长度 5-2000 字(question/chosen/rejected 逐字段)
    ②两答非空且互不包含 ③chosen/rejected 2-gram 相似度兜底
    (≥0.85 视为语义同稿拒入) ④PII 复检(三字段过 mask_pii 脱敏
    管线——即使原料已脱敏, 防二次泄漏) ⑤幂等(同 source+question
    归一化哈希已有 pair 不重复产)。

铁律: 评审环常闭——归集只产 status=pending, approved 必经人工
(operator 必填), 判别器 winnerLabel 仅建议; 全部确定性规则,
LLM 禁入判定链; fail-soft(单源异常跳过该源不断整轮)。
导出即快照: export_pairs 只读不打 status 标记, 可重复导出。

表: dpo_pairs(已登记进 ZK_TABLES, _ZkStore 惯例存取)。
"""

import hashlib
import re
from datetime import datetime, timedelta, UTC

from services.zk_fabric_service import _ZkStore, _now_iso

__all__ = [
    "DpoPairService", "DPO_PAIR_TABLE", "SOURCE_S1", "SOURCE_S2",
    "SOURCE_S3", "SOURCE_S4", "SOURCE_S5", "SOURCES",
    "TEXT_MIN_LEN", "TEXT_MAX_LEN",
    "S2_WINDOW_DAYS", "S4_HIGH_REWARD_LINE", "ANSWER_SIM_LINE",
    "S3_INTENT", "S3_MIN_SIMILARITY", "S5_INTENT", "S5_TRACK",
    "STATUS_RANK",
    "normalize_question", "question_key", "bigram_similarity",
    "quality_gate", "_parse_ts",
]

logger = __import__("logging").getLogger(__name__)

# ============================================================
# 常量(确定性)
# ============================================================

# 偏好对留痕表(已登记进 ZK_TABLES)
DPO_PAIR_TABLE = "dpo_pairs"

# 五源标识(S1 双轨对照/S2 显式反馈/S3 转人工隐式/S4 失败挖掘/
# S5 对抗评审——第三批扩 S3/S5 两源)
SOURCE_S1 = "S1"
SOURCE_S2 = "S2"
SOURCE_S3 = "S3"
SOURCE_S4 = "S4"
SOURCE_S5 = "S5"
SOURCES = (SOURCE_S1, SOURCE_S2, SOURCE_S3, SOURCE_S4, SOURCE_S5)

# 质量闸文本长度区间(字)
TEXT_MIN_LEN = 5
TEXT_MAX_LEN = 2000

# S2 配对窗口: down 轮与 up 轮 ts 差 ≤7 天(同 intent 同周口径)
S2_WINDOW_DAYS = 7

# S4 高分轮判定线(评分流 reward≥0.6 为成功轮候选)
S4_HIGH_REWARD_LINE = 0.6

# 答案 2-gram 相似度兜底线(≥0.85 视为语义同稿, 无偏好价值拒入)
ANSWER_SIM_LINE = 0.85

# 评审操作三态(approve/reject/flip)
REVIEW_ACTIONS = ("approve", "reject", "flip")

# 失败挖掘中视为 rejected 锚点的失败分类(三分类取二, repeat 为
# 重复输入非应答质量问题, 不入对)
S4_FAILURE_KINDS = ("negative", "fallback")

# v92 评分流 kind 标识(与 XiaozhuEvolutionService.SCORE_KIND 一致)
SCORE_KIND = "turn_score"

# ---------- S3 转人工隐式配对(第三批) ----------
# chat 域无 intent 留痕(chat_messages 无 intent 字段), S3 统一挂
# chat_transfer 场景标识(质量闸 intent 非空要求)
S3_INTENT = "chat_transfer"
# S3 降级检索噪声线(与 knowledge_service.MIN_SIMILARITY 同值——
# 低于此相似度的 knowledge 命中视为噪声, 不做 chosen)
S3_MIN_SIMILARITY = 0.10
# 转人工系统通知前缀(chat_service._do_transfer 固定文案——
# 锚点识别: senderType=system 且 content 以此前缀开头)
S3_TRANSFER_NOTICE_PREFIX = "已为您转接人工客服"
# S3 会话/消息扫描上限(定时批量成本护栏)
S3_SESSION_SCAN_LIMIT = 1000
S3_MESSAGE_SCAN_LIMIT = 200
# 消息发送方(对齐 chat_repository SENDER_* 常量值——字面量消费
# 防跨模块 import 链风险)
_SENDER_USER = "user"
_SENDER_AI = "ai"
_SENDER_CS = "customer_service"
_SENDER_SYSTEM = "system"

# ---------- S5 对抗评审对(第三批, selfplay 桥) ----------
# S5 intent/track 场景标识(selfplay A6 缺口闭环域)
S5_INTENT = "knowledge_qa"
S5_TRACK = "selfplay_gap"
# 缺口已补知识态(与 knowledge_repository.GAP_STATUS_RESOLVED 同值)
S5_GAP_RESOLVED = "resolved"
# 知识条目已发布态(与 knowledge_repository.ENTRY_STATUS_PUBLISHED
# 同值——草稿未发布不算达标答案)
S5_ENTRY_PUBLISHED = "published"

# 导出面状态推进序(pending 未评审 < rejected 已评审(拒) <
# approved 已评审(过)——min_status 过滤口径: 只导推进度达标者,
# 默认 approved 即「approved 才是数据集」铁律)
STATUS_RANK = {"pending": 0, "rejected": 1, "approved": 2}


# ============================================================
# 纯函数(确定性, 可独立测试)
# ============================================================

def _mask_pii(text: str) -> str:
    """PII 脱敏复检(复用 xiaozhu_service 既有正则管线, lazy import
    防循环依赖; 导入失败 fail-soft 原样返回——脱敏只增不减)"""
    try:
        from services.xiaozhu_service import mask_pii
        return mask_pii(text)
    except Exception:  # noqa: BLE001
        return str(text or "")


def normalize_question(question: str) -> str:
    """question 归一化(strip + 连续空白合一 + 小写——去重键口径)"""
    return re.sub(r"\s+", " ", str(question or "").strip()).lower()


def question_key(question: str, source: str) -> str:
    """配对幂等键: md5(source + 归一化 question)"""
    return hashlib.md5(
        f"{source}:{normalize_question(question)}".encode("utf-8")
    ).hexdigest()


def bigram_similarity(a: str, b: str) -> float:
    """字符 2-gram Jaccard 相似度(语义缓存同款兜底口径)"""
    ta, tb = str(a or ""), str(b or "")
    if len(ta) < 2 or len(tb) < 2:
        return 1.0 if ta == tb else 0.0
    ga = {ta[i:i + 2] for i in range(len(ta) - 1)}
    gb = {tb[i:i + 2] for i in range(len(tb) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _parse_ts(iso: str) -> datetime | None:
    """ISO 时间戳解析(naive 视为 UTC; 空/非法 → None)"""
    raw = str(iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def quality_gate(question: str, chosen: str, rejected: str,
                 intent: str) -> tuple[bool, str]:
    """质量闸(确定性纯函数——进对前过滤)

    Returns: (通过, 未通过原因); PII 复检由调用方先行 mask。
    """
    q, c, r = str(question or ""), str(chosen or ""), str(rejected or "")
    # ① 文本长度 5-2000 字(三字段逐个)
    for name, text in (("question", q), ("chosen", c), ("rejected", r)):
        if not TEXT_MIN_LEN <= len(text) <= TEXT_MAX_LEN:
            return False, f"{name}长度越界({len(text)}字)"
    # ② 两答非空(长度闸已含非空)且互不包含
    cs, rs = c.strip(), r.strip()
    if cs in rs or rs in cs:
        return False, "两答互包含"
    # ③ 场景可比: intent 非空(同 intent 配对在归集侧保证)
    if not str(intent or "").strip():
        return False, "intent为空"
    # ④ 答案 2-gram 相似度兜底(语义同稿无偏好价值)
    if bigram_similarity(c, r) >= ANSWER_SIM_LINE:
        return False, "答案相似度过高"
    return True, ""


# ============================================================
# 服务
# ============================================================

class DpoPairService:
    """DPO 偏好对五源归集+评审环+导出+统计(pending 燃料池, approved
    才是数据集)"""

    def __init__(self, store: _ZkStore = None,
                 xiaozhu_repo=None, knowledge_repo=None,
                 chat_repo=None):
        self.store = store or _ZkStore()
        # 数据源仓储(只读消费留痕; 可注入打桩)
        if xiaozhu_repo is None:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository)
            xiaozhu_repo = Xiaozhu48Repository()
        if knowledge_repo is None:
            from repositories.knowledge_repository import (
                KnowledgeRepository)
            knowledge_repo = KnowledgeRepository()
        if chat_repo is None:
            from repositories.chat_repository import ChatRepository
            chat_repo = ChatRepository()
        self.xiaozhu_repo = xiaozhu_repo
        self.knowledge_repo = knowledge_repo
        self.chat_repo = chat_repo

    # --------------------------------------------------------
    # 内部: 落对(PII 复检 + 质量闸 + 幂等)
    # --------------------------------------------------------

    async def _save_pair(self, source: str, question: str,
                         chosen: str, rejected: str, intent: str,
                         track: str, meta: dict,
                         seen_keys: set) -> bool:
        """单对入表(全闸通过才落; 幂等键命中返回 False)"""
        # PII 复检: 三字段过脱敏管线(即使原料已脱敏, 防二次泄漏)
        q = _mask_pii(question)
        c = _mask_pii(chosen)
        r = _mask_pii(rejected)
        ok, reason = quality_gate(q, c, r, intent)
        if not ok:
            logger.debug("dpo_pair_gate_rejected source=%s reason=%s",
                         source, reason)
            return False
        key = question_key(q, source)
        if key in seen_keys:
            return False   # 幂等: 同 source+question 不重复产
        seen_keys.add(key)
        pair_id = await self.store.next_id("dpo_pair")
        record = {
            "pairId": pair_id,
            "source": source,
            "question": q,
            "chosen": c,
            "rejected": r,
            "intent": str(intent or ""),
            "track": str(track or ""),
            "meta": dict(meta or {}),
            "status": "pending",     # 评审环常闭: 归集只产 pending
            "reviewerId": "",
            "createdAt": _now_iso(),
        }
        await self.store.save(DPO_PAIR_TABLE, pair_id, record)
        return True

    async def _existing_keys(self) -> set:
        """已落 pair 的幂等键集(查表去重)"""
        rows = await self.store.list(DPO_PAIR_TABLE)
        return {question_key(r.get("question", ""),
                             r.get("source", "")) for r in rows}

    # --------------------------------------------------------
    # S1 双轨对照(rag dual 源: golden × negative 同问配对)
    # --------------------------------------------------------

    async def collect_s1(self, seen_keys: set) -> int:
        """双师对抗样本配对: 同 question 的 golden(高分胜出)为
        chosen × negative(判别否决)为 rejected; winnerLabel 仅建议"""
        samples = await self.knowledge_repo.list_dual_samples(
            limit=1000)
        goldens, negatives = {}, {}
        for s in samples:
            key = normalize_question(s.get("question", ""))
            if not key:
                continue
            score = float((s.get("scores") or {}).get("totalScore")
                          or 0)
            if s.get("kind") == "golden":
                # 同问多条 golden 取 totalScore 最高(平分取先到,
                # 确定性)
                if key not in goldens \
                        or score > goldens[key][1]:
                    goldens[key] = (s, score)
            elif s.get("kind") == "negative":
                if key not in negatives \
                        or score < negatives[key][1]:
                    negatives[key] = (s, score)
        produced = 0
        for key, (g, g_score) in sorted(goldens.items()):
            neg = negatives.get(key)
            if not neg:
                continue
            n, n_score = neg
            # 互不重复样本(同一条样本不会既 golden 又 negative)
            meta = {
                "kind": "rag_dual",
                "winnerLabel": (g.get("scores") or {}).get("label"),
                "chosenScore": g_score,
                "rejectedScore": n_score,
                "goldenSampleId": g.get("id"),
                "negativeSampleId": n.get("id"),
            }
            if await self._save_pair(
                    SOURCE_S1, g.get("question", ""),
                    g.get("answer", ""), n.get("answer", ""),
                    intent="knowledge_qa",
                    track=str((g.get("scores") or {}).get("label")
                              or "dual"),
                    meta=meta, seen_keys=seen_keys):
                produced += 1
        return produced

    # --------------------------------------------------------
    # S2 显式反馈配对(down 轮 × 同 intent ±7 天 up 轮)
    # --------------------------------------------------------

    async def collect_s2(self, seen_keys: set) -> int:
        """👍/👎 配对: down 轮 rejected; 同 intent 窗口内 up 轮
        chosen(时间最近优先, 平局取 seq 大——确定性)"""
        turns = await self.xiaozhu_repo.scan_turns(limit=10000)
        downs, ups = [], []
        for t in turns:
            fb = str(t.get("feedback") or "")
            reply = str(t.get("reply") or "")
            intent = str(t.get("intent") or "")
            if not fb or not reply.strip() or not intent:
                continue
            entry = (t, _parse_ts(t.get("ts") or t.get("feedbackAt")))
            if fb == "down":
                downs.append(entry)
            elif fb == "up":
                ups.append(entry)
        produced = 0
        for d_turn, d_ts in downs:
            # 同 intent ±7 天窗口内的 up 轮(时间最近优先)
            best = None
            for u_turn, u_ts in ups:
                if u_turn.get("intent") != d_turn.get("intent"):
                    continue
                if d_ts is None or u_ts is None:
                    continue
                delta = abs((d_ts - u_ts).total_seconds())
                if delta > S2_WINDOW_DAYS * 86400:
                    continue
                cand = (delta, -int(u_turn.get("seq") or 0))
                if best is None or cand < best[0]:
                    best = (cand, u_turn)
            if best is None:
                continue
            u_turn = best[1]
            meta = {
                "kind": "explicit_feedback",
                "downTurn": {"sessionId": d_turn.get("sessionId"),
                             "seq": d_turn.get("seq"),
                             "ts": d_turn.get("ts")},
                "upTurn": {"sessionId": u_turn.get("sessionId"),
                           "seq": u_turn.get("seq"),
                           "ts": u_turn.get("ts")},
            }
            if await self._save_pair(
                    SOURCE_S2, d_turn.get("rawText", ""),
                    u_turn.get("reply", ""),
                    d_turn.get("reply", ""),
                    intent=str(d_turn.get("intent") or ""),
                    track="turn_feedback",
                    meta=meta, seen_keys=seen_keys):
                produced += 1
        return produced

    # --------------------------------------------------------
    # S4 失败挖掘配对(失败轮 × 同 intent 高分轮)
    # --------------------------------------------------------

    async def _turns_by_session(self) -> dict:
        """轮次按 sessionId 分组(回查失败案例/评分流定位轮)"""
        turns = await self.xiaozhu_repo.scan_turns(limit=10000)
        by_session: dict = {}
        for t in turns:
            by_session.setdefault(
                t.get("sessionId"), []).append(t)
        return by_session

    @staticmethod
    def _locate_turn(turns: list, *, raw_text: str = None,
                     intent: str = None, near_ts: datetime = None,
                     ) -> dict | None:
        """留痕定位轮(确定性):
        - raw_text 精确匹配(失败案例 rawText 留痕为前 200 字, 以
          turn rawText 前 200 字对齐口径);
        - intent 匹配下取 |ts 差| 最小(平局取 seq 大)。
        """
        best, best_key = None, None
        for t in turns:
            if raw_text is not None:
                t_text = str(t.get("rawText") or "")[:200]
                if t_text != str(raw_text or "")[:200]:
                    continue
            if intent is not None \
                    and str(t.get("intent") or "") != str(intent):
                continue
            t_ts = _parse_ts(t.get("ts"))
            if near_ts is not None and t_ts is not None:
                cand = (abs((t_ts - near_ts).total_seconds()),
                        -int(t.get("seq") or 0))
            else:
                cand = (-int(t.get("seq") or 0),)
            if best_key is None or cand < best_key:
                best, best_key = t, cand
        return best

    async def collect_s4(self, seen_keys: set) -> int:
        """失败挖掘配对: negative/fallback 失败轮 rejected ×
        同 intent 高分轮(reward≥0.6 评分流)chosen"""
        cases = await self.xiaozhu_repo.list_records(
            self.xiaozhu_repo.TABLE_FAILURES, limit=5000)
        by_session = await self._turns_by_session()
        # rejected 池: 失败案例(排除评分流与 repeat)→ 回查 turn
        rejected_pool = []
        for c in cases:
            if c.get("kind") not in S4_FAILURE_KINDS:
                continue
            turns = by_session.get(c.get("sessionId")) or []
            turn = self._locate_turn(
                turns, raw_text=str(c.get("rawText") or ""))
            if not turn:
                continue
            if not str(turn.get("reply") or "").strip():
                continue
            rejected_pool.append((c, turn))
        # chosen 池: 高分评分流 → sessionId+intent+ts 最近定位轮
        chosen_pool = []
        for c in cases:
            if c.get("kind") != SCORE_KIND:
                continue
            try:
                reward = float(c.get("reward") or 0)
            except (TypeError, ValueError):
                continue
            if reward < S4_HIGH_REWARD_LINE:
                continue
            turns = by_session.get(c.get("sessionId")) or []
            turn = self._locate_turn(
                turns, intent=str(c.get("intent") or ""),
                near_ts=_parse_ts(c.get("ts")))
            if not turn:
                continue
            if not str(turn.get("reply") or "").strip():
                continue
            chosen_pool.append((c, turn))
        # 配对: 每失败轮取同 intent 高分轮(ts 最近优先, 排除自身)
        produced = 0
        for case, r_turn in rejected_pool:
            r_ts = _parse_ts(r_turn.get("ts"))
            best, best_key = None, None
            for score, c_turn in chosen_pool:
                if c_turn.get("sessionId") == r_turn.get("sessionId") \
                        and c_turn.get("seq") == r_turn.get("seq"):
                    continue   # 自身轮不入对
                if c_turn.get("intent") != r_turn.get("intent"):
                    continue
                c_ts = _parse_ts(c_turn.get("ts"))
                cand = ((abs((c_ts - r_ts).total_seconds())
                         if (c_ts and r_ts) else float("inf")),
                        -int(c_turn.get("seq") or 0))
                if best_key is None or cand < best_key:
                    best, best_key = (score, c_turn), cand
            if best is None:
                continue
            score, c_turn = best
            meta = {
                "kind": "failure_mining",
                "failureKind": case.get("kind"),
                "failureCaseId": case.get("caseId"),
                "rejectedTurn": {"sessionId": r_turn.get("sessionId"),
                                 "seq": r_turn.get("seq")},
                "chosenTurn": {"sessionId": c_turn.get("sessionId"),
                               "seq": c_turn.get("seq")},
                "chosenReward": float(score.get("reward") or 0),
            }
            if await self._save_pair(
                    SOURCE_S4, r_turn.get("rawText", ""),
                    c_turn.get("reply", ""),
                    r_turn.get("reply", ""),
                    intent=str(r_turn.get("intent") or ""),
                    track=str(score.get("track") or "turn_score"),
                    meta=meta, seen_keys=seen_keys):
                produced += 1
        return produced

    # --------------------------------------------------------
    # S3 转人工隐式配对(第三批: 转人工锚点 × 前后消息确定性定位)
    # --------------------------------------------------------

    @staticmethod
    def _last_content(msgs: list, sender_type: str) -> str:
        """消息序列中指定发送方的最后一条文本(传入序列已按
        createdAt 正序——末位即最新, 确定性)"""
        for msg in reversed(msgs):
            if str(msg.get("senderType") or "") == sender_type:
                content = str(msg.get("content") or "").strip()
                if content:
                    return content
        return ""

    async def _s3_pick_chosen(self, question: str,
                              after_msgs: list) -> tuple:
        """S3 chosen 三级确定性择优(现场核实: 人工坐席回复有留痕)

        ① 人工坐席回复: 转人工锚点后首条 customer_service 消息
           (cs_reply 留痕——工作台回复落 chat_messages);
        ② 降级: knowledge direct 命中(2-gram 向量余弦 top-1,
           相似度 ≥ S3_MIN_SIMILARITY 才算命中——对齐 knowledge
           search 噪声线口径);
        ③ 全无 → 放弃该锚点(宁缺毋滥, 「转人工提示话术规范版」
           与 rejected 兜底话术偏好信号弱, 不作 chosen)。

        Returns: (chosen, chosenFrom, 额外 meta 字段 dict)
        """
        # ① 人工坐席回复(锚点后首条)
        for msg in after_msgs:
            if str(msg.get("senderType") or "") == _SENDER_CS:
                content = str(msg.get("content") or "").strip()
                if content:
                    return content, "cs_reply", {}
        # ② 降级: knowledge direct 命中(检索异常 fail-soft 放弃)
        try:
            from repositories.knowledge_repository import tokenize
            hits = await self.knowledge_repo.search_published(
                tokenize(question), top_k=1)
            if hits:
                entry, sim = hits[0]
                if float(sim) >= S3_MIN_SIMILARITY:
                    answer = str(entry.get("answer") or "").strip()
                    if answer:
                        return answer, "knowledge_fallback", {
                            "knowledgeEntryId": entry.get("id"),
                            "knowledgeSimilarity": round(
                                float(sim), 4),
                        }
        except Exception as exc:  # noqa: BLE001
            logger.debug("s3_knowledge_fallback_failed: %s", exc)
        return "", "", {}

    async def collect_s3(self, seen_keys: set) -> int:
        """S3 转人工隐式配对: 每个转人工锚点(系统通知消息)——
        question=锚点前最后一条用户消息, rejected=锚点前最后一条
        AI 自动答(无 AI 答的锚点如 VIP 直通不配), chosen 三级择优
        (见 _s3_pick_chosen); 转人工会话判定=customerServiceId 非空
        (_do_transfer 必写留痕)"""
        sessions = await self.chat_repo.list_all_sessions(
            limit=S3_SESSION_SCAN_LIMIT)
        produced = 0
        for session in sessions:
            # 转人工留痕判定: customerServiceId 非空
            if not session.get("customerServiceId"):
                continue
            session_id = str(session.get("sessionId") or "")
            if not session_id:
                continue
            msgs = await self.chat_repo.list_messages(
                session_id, limit=S3_MESSAGE_SCAN_LIMIT)
            # 时间正序(锚点前后切分的确定性前提)
            msgs.sort(key=lambda m: str(m.get("createdAt") or ""))
            for anchor_i, msg in enumerate(msgs):
                if str(msg.get("senderType") or "") != _SENDER_SYSTEM:
                    continue
                if not str(msg.get("content") or "").startswith(
                        S3_TRANSFER_NOTICE_PREFIX):
                    continue
                before, after = msgs[:anchor_i], msgs[anchor_i + 1:]
                question = self._last_content(before, _SENDER_USER)
                rejected = self._last_content(before, _SENDER_AI)
                if not question or not rejected:
                    continue   # 无 AI 自动答的转人工(如 VIP 直通)
                chosen, chosen_from, extra = await self._s3_pick_chosen(
                    question, after)
                if not chosen:
                    continue
                meta = {
                    "kind": "human_transfer",
                    "sessionId": session_id,
                    "chosenFrom": chosen_from,
                    **extra,
                }
                if await self._save_pair(
                        SOURCE_S3, question, chosen, rejected,
                        intent=S3_INTENT, track=S3_INTENT,
                        meta=meta, seen_keys=seen_keys):
                    produced += 1
        return produced

    # --------------------------------------------------------
    # S5 对抗评审对(第三批: selfplay A6 缺口 × L2 发布闭环桥)
    # --------------------------------------------------------

    async def collect_s5(self, seen_keys: set) -> int:
        """S5 对抗评审对: 已 resolved 且关联 entryId 的知识缺口 →
        chosen=该知识条目 answer(published 态——经 L2 to-draft 转草稿
        并 review 发布后的达标答案) × rejected=LIVE_QA_UNSOLVED_
        ANSWER 兜底话术(unsolved 的失败应答); 现场核实字段链:
        gaps{status=resolved, entryId>0} → get_entry(entryId)"""
        try:
            from services.live_qa_service import LIVE_QA_UNSOLVED_ANSWER
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"unsolved 兜底常量不可用: {exc}") from exc
        unsolved = str(LIVE_QA_UNSOLVED_ANSWER or "").strip()
        if not unsolved:
            raise RuntimeError("LIVE_QA_UNSOLVED_ANSWER 为空")
        gaps = await self.knowledge_repo.list_gaps(
            status=S5_GAP_RESOLVED, limit=5000)
        produced = 0
        for gap in gaps:
            # 双保险: stub 忽略 status 过滤时兜底复核
            if str(gap.get("status") or "") != S5_GAP_RESOLVED:
                continue
            try:
                entry_id = int(gap.get("entryId") or 0)
            except (TypeError, ValueError):
                continue
            if entry_id <= 0:
                continue   # 未关联条目的缺口(字段链断)不配
            entry = await self.knowledge_repo.get_entry(entry_id)
            if not entry:
                continue
            # published 态才配(草稿未发布不算达标答案)
            if str(entry.get("status") or "") != S5_ENTRY_PUBLISHED:
                continue
            question = str(gap.get("question") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            if not question or not answer:
                continue
            meta = {
                "kind": "selfplay_review",
                "gapId": gap.get("id"),
                "entryId": entry_id,
                "resolvedAt": str(gap.get("resolvedAt") or ""),
            }
            if await self._save_pair(
                    SOURCE_S5, question, answer, unsolved,
                    intent=S5_INTENT, track=S5_TRACK,
                    meta=meta, seen_keys=seen_keys):
                produced += 1
        return produced

    # --------------------------------------------------------
    # 归集总入口(fail-soft: 单源异常跳过该源不断整轮)
    # --------------------------------------------------------

    async def collect_all(self) -> dict:
        """五源归集(逐源 fail-soft; 幂等键跨源共享查表去重)"""
        seen = await self._existing_keys()
        produced = {s: 0 for s in SOURCES}
        errors = []
        for source, collector in (
                (SOURCE_S1, self.collect_s1),
                (SOURCE_S2, self.collect_s2),
                (SOURCE_S3, self.collect_s3),
                (SOURCE_S4, self.collect_s4),
                (SOURCE_S5, self.collect_s5)):
            try:
                produced[source] = await collector(seen)
            except Exception as exc:  # noqa: BLE001
                # fail-soft: 单源异常只记跳过, 不中断后续源
                errors.append(f"{source}: {exc}")
                logger.warning("dpo_collect_source_failed source=%s: %s",
                               source, exc)
        result = {
            "success": True,
            "produced": produced,
            "total": sum(produced.values()),
            "errors": errors,
            "collectedAt": _now_iso(),
        }
        logger.info(
            "dpo_pairs_collected s1=%s s2=%s s3=%s s4=%s s5=%s "
            "total=%s errors=%s", produced[SOURCE_S1],
            produced[SOURCE_S2], produced[SOURCE_S3],
            produced[SOURCE_S4], produced[SOURCE_S5],
            result["total"], len(errors))
        return result

    # --------------------------------------------------------
    # 评审环(常闭: approved 必经人工 operator 必填)
    # --------------------------------------------------------

    async def list_pairs(self, status: str = None,
                         limit: int = 50, offset: int = 0) -> list:
        """偏好对列表(status 过滤 pending/approved/rejected;
        pairId 倒序=最近优先, 对齐 repurchase suggestions 口径)"""
        rows = await self.store.list(DPO_PAIR_TABLE)
        if status:
            rows = [r for r in rows
                    if r.get("status") == status]
        rows.sort(key=lambda r: int(r.get("pairId", 0)), reverse=True)
        offset = max(0, int(offset))
        return rows[offset:offset + max(1, int(limit))]

    async def list_approved(self,
                            limit: int = 1000) -> list:
        """approved 导出面(第三批 export 端点数据源——pending/
        rejected 永不出现在此)"""
        return await self.list_pairs(status="approved", limit=limit)

    async def export_pairs(self, source: str = None,
                           intent: str = None,
                           min_status: str = "approved") -> tuple:
        """数据集导出面(标准 DPO JSONL 的行源, 第三批)

        过滤口径(确定性):
            - source/intent 精确匹配(空=不过滤);
            - min_status 状态推进度下限(STATUS_RANK: pending <
              rejected < approved)——默认 approved 即「approved 才是
              数据集」铁律; min_status=pending 导全量三态快照。

        导出即快照: 只读不打 status 标记, 可重复导出(同一份数据
        多次导出仅 datasetVersion 时间戳不同)。

        Returns:
            (rows, datasetVersion): rows=pairId 升序稳定序的 pair
            全字段列表; datasetVersion="dpo-{yyyyMMdd-HHmmss}"。

        Raises:
            ValueError: min_status 非法
        """
        min_status = str(min_status or "approved").strip()
        if min_status not in STATUS_RANK:
            raise ValueError(
                f"min_status 须为 {list(STATUS_RANK)} 之一")
        rows = await self.store.list(DPO_PAIR_TABLE)
        floor = STATUS_RANK[min_status]
        out = []
        for r in rows:
            if source and str(r.get("source") or "") != str(source):
                continue
            if intent and str(r.get("intent") or "") != str(intent):
                continue
            if STATUS_RANK.get(str(r.get("status") or ""),
                               -1) < floor:
                continue
            out.append(r)
        # pairId 升序稳定导出序(可复现快照)
        out.sort(key=lambda r: int(r.get("pairId", 0)))
        version = "dpo-" + datetime.now(UTC).strftime(
            "%Y%m%d-%H%M%S")
        return out, version

    async def review(self, pair_id: int, action: str,
                     operator: str) -> dict:
        """人工评审三操作(approve/reject/flip)

        铁律: operator 必填(路由层 Pydantic 强制)——approved 必经
        人工; flip 互换 chosen/rejected(翻转权在人), status 不变。

        Raises:
            KeyError: pair 不存在
            ValueError: action 非法
        """
        pair_id = int(pair_id)
        action = str(action or "").strip()
        operator = str(operator or "").strip()
        if action not in REVIEW_ACTIONS:
            raise ValueError(
                f"action 须为 {list(REVIEW_ACTIONS)} 之一")
        if not operator:
            raise ValueError("operator 必填(人工评审环铁律)")
        record = await self.store.get(DPO_PAIR_TABLE, pair_id)
        if record is None:
            raise KeyError(pair_id)
        if action == "flip":
            record["chosen"], record["rejected"] = (
                record["rejected"], record["chosen"])
            flips = list((record.get("meta") or {}).get("flips") or [])
            flips.append({"operator": operator,
                          "at": _now_iso()})
            record.setdefault("meta", {})["flips"] = flips
        else:
            record["status"] = ("approved" if action == "approve"
                                else "rejected")
            record["reviewerId"] = operator
            record["reviewedAt"] = _now_iso()
        await self.store.save(DPO_PAIR_TABLE, pair_id, record)
        return record

    # --------------------------------------------------------
    # 统计面板(口径对齐 repurchase stats 形态)
    # --------------------------------------------------------

    async def stats(self) -> dict:
        """偏好对留痕聚合统计(纯读确定性, 无 LLM)

        口径:
            total        pairs 总量(=pending+approved+rejected)
            bySource     五源占比分桶(动态分桶——只含实际有留痕的
                         源, 全源枚举见路由 stats sources 字段)
            byStatus     状态分桶
            byIntent     intent 分布(count 降序, 平分按 intent 名
                         正序——确定性)
            reviewRate   评审转化率 = approved/(approved+rejected)
                         (已评审中的通过率; 分母 0 → 0.0)
            last7dDaily  近 7 天(含今日, 旧→新)按 createdAt 前 10
                         位日期分桶, 无留痕日补零
            mode         当前 DPO_COLLECT_AUTO 快照
        """
        rows = await self.store.list(DPO_PAIR_TABLE)
        today = datetime.now(UTC).date()
        days = [(today - timedelta(days=i)).isoformat()
                for i in range(6, -1, -1)]     # 旧 → 新, 恒 7 天
        daily = dict.fromkeys(days, 0)
        by_source: dict = {}
        by_status = {"pending": 0, "approved": 0, "rejected": 0}
        by_intent: dict = {}
        for r in rows:
            src = str(r.get("source") or "")
            if src:
                by_source[src] = by_source.get(src, 0) + 1
            st = r.get("status")
            if st in by_status:
                by_status[st] += 1
            intent = str(r.get("intent") or "") or "unknown"
            by_intent[intent] = by_intent.get(intent, 0) + 1
            day = str(r.get("createdAt", ""))[:10]
            if day in daily:
                daily[day] += 1
        reviewed = by_status["approved"] + by_status["rejected"]
        top_intents = sorted(by_intent.items(),
                             key=lambda kv: (-kv[1], kv[0]))[:10]
        import os
        mode = os.environ.get("DPO_COLLECT_AUTO", "off") \
            .strip().lower()
        return {
            "total": len(rows),
            "bySource": by_source,
            "byStatus": by_status,
            "byIntent": [{"intent": k, "count": v}
                         for k, v in top_intents],
            "reviewRate": round(
                by_status["approved"] / reviewed, 4) if reviewed
            else 0.0,
            "last7dDaily": [{"date": d, "count": daily[d]}
                            for d in days],
            "mode": mode,
        }
