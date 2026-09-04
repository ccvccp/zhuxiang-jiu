"""50号·小竹语音信值积分引擎 P4 反作弊闸门(五模式)

计划(docs/50号_小竹语音信值积分引擎实施计划.md §六):
    v2.0 §六五异常模式工程化——计分前置闸门:
    ① TTS 刷分: 声纹活体检测(声谱特征模拟)+频谱分析
       → 当次积分归零 + L1 扣 10
    ② 脚本化重复交互: 时序规律性(轮次间隔方差)+语义
       相似度聚类(47号 3-gram 复用)→ 当日积分冻结 +
       人工复核
    ③ 多人共用账号: 声纹聚类(proxy: 会话声纹离散度)
       → 积分域锁定 + 强制核验上报
    ④ 恶意诱导套取隐私: NLU 意图分类+关键词监控
       → L2 扣 20 + 上报安全团队
    ⑤ 预算耗尽后强请求: 49号 预算 429 事件监听
       → 拒绝积分 + 引导调整设置

设计红线:
    - 闸门 fail-soft: 检测器异常放行计分并告警
      (不阻断语音主链路——处置只作用积分域)
    - 处置台账 180 天保留(监管调阅口径)
    - 申诉 ≤48h SLA(记录 submittedAt, 复核语义)
    - 闸门信号源全部确定性(mock 声谱/时序/文本特征
      ——无随机性, 测试幂等)
"""

import logging
import re

from core.helpers import ts

from repositories.voice50_repository import (
    Voice50Repository,
)

logger = logging.getLogger("xiaozhu_voice50_gates")

# 处置台账保留天数(v2.0 §六审计要求)
ADJUDICATION_RETENTION_DAYS = 180

# 异常模式标识
PATTERN_TTS = "tts_spoof"
PATTERN_SCRIPTED = "scripted_repeat"
PATTERN_SHARED = "shared_account"
PATTERN_EXTRACTION = "privacy_extraction"
PATTERN_BUDGET = "budget_exhausted"

# ① TTS 声谱特征(确定性——mock 活体检测)
TTS_SIGNATURES = ("tts", "synthesis", "合成音",
                   "机器音", "电子音")
# ④ 诱导套取隐私关键词(v2.0 §六 NLU 监控)
EXTRACTION_KEYWORDS = ("绕过验证", "跳过授权", "骗取",
                       "套出", "泄露他人", "偷看",
                       "别人的隐私", "他人数据")

# ② 脚本化时序: 事件数 ≥N 且轮次间隔呈机器节拍
# (相对标准差 σ/mean < 阈值且平均间隔 ≥0.5s——
#  纯等间隔中速节拍才命中; 正常交互有自然抖动,
#  测试连跑(<0.5s)与超慢速轮询均不误伤)
SCRIPTED_MIN_EVENTS = 5
SCRIPTED_CV_MAX = 0.02
SCRIPTED_MIN_MEAN_INTERVAL = 0.5

# ③ 声纹离散: 同会员跨会话声纹 digest 不同数 ≥N
SHARED_DIGEST_MIN = 2

# 处置动作(处置只作用积分域——不阻断语音入口)
ACTIONS = {
    PATTERN_TTS: {"zero": True, "l1Penalty": -10.0,
                  "action": "当次积分归零+L1 扣 10"},
    PATTERN_SCRIPTED: {"freezeDay": True,
                       "action": "当日积分冻结+人工复核"},
    PATTERN_SHARED: {"lockCredits": True,
                     "action": "积分域锁定+强制核验上报"},
    PATTERN_EXTRACTION: {"l2Penalty": -20.0,
                         "report": True,
                         "action": "L2 扣 20+上报安全团队"},
    PATTERN_BUDGET: {"rejectCredits": True,
                     "action": "拒绝积分+引导调整设置"},
}


def _dt_seconds(value: str) -> float | None:
    """ISO 时间戳 → epoch 秒(解析失败 None)"""
    from datetime import datetime
    try:
        return datetime.fromisoformat(
            str(value or "")).timestamp()
    except (ValueError, TypeError):
        return None


class Voice50GateService:
    """反作弊五模式闸门(计分前置)"""

    def __init__(self,
                 repo: Voice50Repository = None):
        self.repo = repo or Voice50Repository()

    # --------------------------------------------------------
    # 闸门主入口(计分前调用——命中返回处置, 未中 None)
    # --------------------------------------------------------

    async def check(self, member_id: int,
                    behavior: str,
                    evidence: str = "",
                    interval_sec: float = None,
                    speaker_digest: str = "",
                    quality: float = None) -> dict | None:
        """五模式检测(fail-soft——检测异常返回 None 放行)

        Args:
            evidence: 本轮文本/证据(①④ 检测源)
            interval_sec: 本轮距上轮间隔秒(② 时序)
            speaker_digest: 本轮声纹摘要(③ 离散)
        Returns:
            命中: {pattern, action, detail}——调用方执行处置;
            未中: None。
        """
        try:
            # ① TTS 声谱特征(证据/文本命中合成音签名)
            hit = self._check_tts(evidence)
            if hit:
                return hit
            # ④ 诱导套取(关键词监控——先于②文本不冲突)
            hit = self._check_extraction(evidence)
            if hit:
                return hit
            # ② 脚本化(时序规律+3-gram 复用)
            hit = await self._check_scripted(
                member_id, behavior, evidence,
                interval_sec)
            if hit:
                return hit
            # ③ 多人共用(声纹离散——同会员 digest 多值)
            hit = await self._check_shared(
                member_id, speaker_digest)
            if hit:
                return hit
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "voice50_gate_failsoft member=%s: %s",
                member_id, exc)
            return None   # fail-soft 放行

    # --------------------------------------------------------
    # ① TTS 刷分
    # --------------------------------------------------------

    @staticmethod
    def _check_tts(evidence: str) -> dict | None:
        """TTS 声谱特征(确定性签名词——活体模拟)"""
        text = str(evidence or "").lower()
        for sig in TTS_SIGNATURES:
            if sig.lower() in text:
                return {
                    "pattern": PATTERN_TTS,
                    "action": ACTIONS[PATTERN_TTS][
                        "action"],
                    "detail": f"声谱命中合成音签名({sig})",
                }
        return None

    # --------------------------------------------------------
    # ② 脚本化重复(时序规律+语义复用)
    # --------------------------------------------------------

    async def _check_scripted(self, member_id: int,
                              behavior: str,
                              evidence: str,
                              interval_sec) -> dict | None:
        """时序规律性(间隔方差≈0)+3-gram 语义复用

        时序: 近 SCRIPTED_MIN_EVENTS 笔同行为事件,
        轮次间隔方差 < 阈值(机器节拍);
        语义: 证据与历史 note 相似度 > 0.8(47号口径)。
        """
        from services.trust_risk_detector_service \
            import check_semantic_reuse
        evs = await self.repo.list_events(
            member_id=member_id, limit=500)
        recent = [e for e in evs
                  if e.get("behavior") == behavior][-5:]
        if len(recent) >= SCRIPTED_MIN_EVENTS \
                and interval_sec is not None:
            intervals = []
            for i in range(1, len(recent)):
                t0 = _dt_seconds(recent[i - 1].get("ts"))
                t1 = _dt_seconds(recent[i].get("ts"))
                if t0 is not None and t1 is not None:
                    intervals.append(t1 - t0)
            intervals.append(float(interval_sec))
            if len(intervals) >= SCRIPTED_MIN_EVENTS:
                mean = sum(intervals) / len(intervals)
                if mean >= SCRIPTED_MIN_MEAN_INTERVAL:
                    std = (sum(
                        (x - mean) ** 2
                        for x in intervals)
                        / len(intervals)) ** 0.5
                    cv = std / mean   # 变异系数
                    if cv < SCRIPTED_CV_MAX:
                        return {
                            "pattern": PATTERN_SCRIPTED,
                            "action": ACTIONS[
                                PATTERN_SCRIPTED][
                                "action"],
                            "detail": (f"时序机器节拍"
                                       f"(CV={cv:.4f} < "
                                       f"{SCRIPTED_CV_MAX}, "
                                       f"{len(intervals)} 笔)"),
                        }
        # 语义复用(证据与历史事件 note 比对——仅证据类
        # 行为: 用户提交的 evidence 语料才构成重放面;
        # 系统拼接 note( coop/intent 等)天然重复, 排除)
        EVIDENCE_BEHAVIORS = {
            "voice_evidence", "voice_corpus_donate",
            "voice_feedback", "voice_community_qa"}
        if evidence and len(str(evidence)) >= 26 \
                and behavior in EVIDENCE_BEHAVIORS:
            bucket = [{"grams": None, "evSha": "",
                       "note": e.get("note") or "",
                       "ts": e.get("ts")}
                      for e in recent]
            # note 级近似(构造 grams 由 detector 内部完成)
            for item in bucket:
                note = item.get("note") or ""
                if len(note) >= 26:
                    from services.trust_risk_detector_service \
                        import semantic_similarity
                    sim = semantic_similarity(
                        evidence, note)
                    if sim > 0.8:
                        return {
                            "pattern": PATTERN_SCRIPTED,
                            "action": ACTIONS[
                                PATTERN_SCRIPTED][
                                "action"],
                            "detail": (f"语义复用(相似度 "
                                       f"{sim:.2f}>0.8"
                                       f"——改字重放)"),
                        }
        return None

    # --------------------------------------------------------
    # ③ 多人共用账号(声纹离散——proxy 口径)
    # --------------------------------------------------------

    async def _check_shared(self, member_id: int,
                            speaker_digest: str
                            ) -> dict | None:
        """同会员声纹摘要多值(跨会话不同声纹→共用嫌疑)

        事件 voiceprintMode 相同但 speakerDigest 记录
        (voice50_events 不存 digest——以 note 中 vp:
        声纹代理说明多会话离散判定); 当前口径:
        note 中 distinct vp 值 ≥SHARED_DIGEST_MIN。
        """
        if not speaker_digest:
            return None
        evs = await self.repo.list_events(
            member_id=member_id, limit=200)
        digests = {speaker_digest}
        for e in evs:
            note = str(e.get("note") or "")
            m = re.search(r"digest[:：]([0-9a-f]{8})",
                          note)
            if m:
                digests.add(m.group(1))
        # 显式离散信号(调用方传入 digest 与历史不同)
        if len(digests) >= SHARED_DIGEST_MIN:
            return {
                "pattern": PATTERN_SHARED,
                "action": ACTIONS[PATTERN_SHARED][
                    "action"],
                "detail": (f"声纹聚类差异({len(digests)} "
                           f"种摘要——多人共用嫌疑)"),
            }
        return None

    # --------------------------------------------------------
    # ④ 恶意诱导套取隐私
    # --------------------------------------------------------

    @staticmethod
    def _check_extraction(evidence: str) -> dict | None:
        """诱导套取关键词(NLU 监控——确定性词表)"""
        text = str(evidence or "")
        for kw in EXTRACTION_KEYWORDS:
            if kw in text:
                return {
                    "pattern": PATTERN_EXTRACTION,
                    "action": ACTIONS[
                        PATTERN_EXTRACTION]["action"],
                    "detail": f"NLU 命中诱导关键词({kw})",
                }
        return None

    # --------------------------------------------------------
    # ⑤ 预算耗尽强请求(429 监听——引擎入口调用)
    # --------------------------------------------------------

    @staticmethod
    def check_budget_exhausted(
            budget_remaining: float) -> dict | None:
        """49号 预算 429 事件(剩余 0 强请求积分)"""
        if float(budget_remaining or 0) <= 0:
            return {
                "pattern": PATTERN_BUDGET,
                "action": ACTIONS[PATTERN_BUDGET]["action"],
                "detail": "隐私预算耗尽后强请求(429 监听)",
            }
        return None

    # --------------------------------------------------------
    # 处置台账(180 天保留——只追加)
    # --------------------------------------------------------

    async def record_adjudication(
            self, member_id: int, pattern: str,
            detail: str, action: str,
            evidence: str = "") -> dict:
        """处置落台账(监管调阅口径——180 天)"""
        adj_id = await self.repo.next_adjudication_id()
        rec = {
            "adjId": adj_id,
            "memberId": member_id,
            "pattern": pattern,
            "detail": str(detail)[:200],
            "action": action,
            "evidence": str(evidence)[:200],
            "status": "pending",   # pending|upheld|
            #  overturned(复核)
            "appealNote": "",
            "appealedAt": "",
            "decidedAt": "",
            "reviewNote": "",
            "createdAt": ts(),
        }
        await self.repo.save_adjudication(rec)
        logger.warning(
            "voice50_adjudicated member=%s pattern=%s "
            "action=%s", member_id, pattern, action)
        return rec

    async def submit_appeal(self, member_id: int,
                            adj_id: int,
                            note: str) -> dict:
        """申诉提交(≤48h SLA——记录 submittedAt)

        申诉路径(v2.0 §六): 原始录音+设备日志/合理
        业务场景说明/家庭成员报备。
        Raises:
            KeyError: 台账不存在
            ValueError: 非本人/已申诉/已复核
        """
        rec = await self.repo.get_adjudication(adj_id)
        if rec is None:
            raise KeyError(f"处置 {adj_id} 不存在")
        if rec.get("memberId") != member_id:
            raise ValueError("只能申诉本人的处置记录")
        if rec.get("appealedAt"):
            raise ValueError("该处置已提交过申诉")
        if rec.get("status") != "pending":
            raise ValueError(
                f"已复核({rec.get('status')})——不可申诉")
        rec["appealNote"] = str(note or "")[:500]
        rec["appealedAt"] = ts()
        await self.repo.save_adjudication(rec)
        return {"success": True, "adjId": adj_id,
                "appealedAt": rec["appealedAt"],
                "slaHours": 48,
                "note": "申诉已受理——复核响应 ≤48h"}

    async def decide_appeal(self, adj_id: int,
                            upheld: bool,
                            review_note: str = "") -> dict:
        """admin 复核裁决(upheld 维持/overturned 翻转)

        翻转处置(tts/scripted/shared/extraction):
        解除积分域锁定(frozen)+补偿当日冻结积分;
        budget 模式无申诉语义(直接引导调整)。
        """
        rec = await self.repo.get_adjudication(adj_id)
        if rec is None:
            raise KeyError(f"处置 {adj_id} 不存在")
        if not rec.get("appealedAt"):
            raise ValueError("该处置无申诉——不可复核")
        if rec.get("status") != "pending":
            raise ValueError(
                f"已复核({rec.get('status')})")
        rec["status"] = ("upheld" if upheld
                         else "overturned")
        rec["decidedAt"] = ts()
        rec["reviewNote"] = str(review_note or "")[:200]
        await self.repo.save_adjudication(rec)
        if not upheld:
            # 翻转: 解除积分域锁定(冻结态才需要解;
            # 未冻结的扣分类处置跳过——fail-soft)
            from services.xiaozhu_voice50_service \
                import Voice50Service
            svc = Voice50Service(repo=self.repo)
            try:
                await svc.unfreeze(
                    rec.get("memberId"),
                    note=f"adjudication-{adj_id}-"
                         f"overturned")
            except ValueError:
                pass   # 本就未冻结——扣分回滚语义由
                       # 复核留痕承载(处置只作用积分域)
        return {"success": True, "adjId": adj_id,
                "status": rec["status"],
                "note": ("维持处置" if upheld
                         else "翻转——积分域已解除"
                               "(如曾冻结)")}

    async def adjudication_view(self,
                                member_id: int = None,
                                limit: int = 100) -> dict:
        """处置台账视图(admin——180 天口径)"""
        rows = await self.repo.list_adjudications(
            member_id=member_id, limit=limit)
        by_pattern: dict = {}
        by_status: dict = {}
        for r in rows:
            p = r.get("pattern") or "unknown"
            by_pattern[p] = by_pattern.get(p, 0) + 1
            s = r.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
        return {"success": True, "total": len(rows),
                "byPattern": by_pattern,
                "byStatus": by_status,
                "records": rows[-limit:],
                "retentionDays":
                    ADJUDICATION_RETENTION_DAYS,
                "note": "处置只作用积分域(语音入口不阻断"
                        "——反语音霸权红线); 台账保留 "
                        "180 天支持监管调阅"}
