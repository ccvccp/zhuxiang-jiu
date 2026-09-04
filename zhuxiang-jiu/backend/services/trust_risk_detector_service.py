"""47号·L2/L3 信值验真风控模块 P1 检测器
(语义近似指纹 + 价值分布检测)

计划(docs/47号_L2L3信值验真风控模块实施计划.md §四):
    ① 语义近似指纹(3-gram + Jaccard, 纯标准库):
        - SHA-256 精确指纹只防同文重放——"编号 ZY2026-088"
          改成 "ZY2026-089" 即绕过; 3-gram 捕捉改字重放
        - 相似度 > 0.8 → semantic_reuse 命中
          → 存证 delta ×0.3 + 画像沉淀
        - 同角色复用是主目标(跨角色留给 P2 协同)
    ② 价值分布检测器(角色级, 与 P7 单事件互补):
        - 小额高频: 近 30 日正向存证 ≥N 次(默认 6)且
          单次净贡献全部 < 窗口外基线中位数×0.5
          → value_anomaly(基线=30日前正向存证中位数,
          无基线不判——自有中位数口径数学恒假, 见
          detect_small_high_frequency 修正说明)
        - 价值-证据错配: 申报值 > 同群体 P90 且验真组件
          分 < 0.7 → 同命中(高申报低证据)
        - 命中 → 该次 delta ×0.5 + 画像沉淀
    ③ 扫描入口(POST /risk/scan/{trustId}):
        触发一轮角色级检测(语义复用+价值分布→命中沉淀,
        幂等——扫描只读存证留痕, 不重复计数)

设计红线:
    - 修正只作用于正向 delta(负向不折损铁律)
    - 语义指纹桶随画像滚动(100 条), 纯函数无模型依赖
    - mock 确定性(3-gram 是确定性算法)
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from core.helpers import ts

from repositories.trust_risk_repository import (
    TrustRisk47Repository,
)
from services.trust_risk_profile_service import (
    TrustRiskProfileService,
)

logger = logging.getLogger(__name__)

# 语义近似阈值(Jaccard > 0.8 → 复用嫌疑; 保守防误判)
SEMANTIC_SIMILARITY_THRESHOLD = 0.8

# 复用命中折损(存证 delta ×0.3——计划 §四)
SEMANTIC_REUSE_PENALTY = 0.3

# 指纹桶滚动截断(近 100 条)
FINGERPRINT_BUCKET_MAX = 100

# 小额高频阈值(近 30 日正向存证 ≥6 次且单次全 < 中位数×0.5)
SMALL_COUNT_MIN = 6
SMALL_VALUE_RATIO = 0.5

# 价值错配: 申报值 > 群体 P90 且验真组件 < 0.7
VALUE_MISMATCH_COMPONENT_THRESHOLD = 0.7

# 价值异常折损(该次 delta ×0.5——计划 §四)
VALUE_ANOMALY_PENALTY = 0.5


# ============================================================
# ① 语义近似指纹(3-gram + Jaccard)
# ============================================================

def char_grams(text: str, n: int = 3) -> set:
    """字符 n-gram 集合(中文友好——逐字符切分)"""
    text = (text or "").strip()
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n]
            for i in range(len(text) - n + 1)}


def jaccard(a: set, b: set) -> float:
    """Jaccard 相似度(空集定义 0)"""
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


def semantic_similarity(evidence_a: str,
                        evidence_b: str) -> float:
    """两条证据的语义近似度(3-gram Jaccard)"""
    return jaccard(char_grams(evidence_a),
                   char_grams(evidence_b))


def ev_sha(evidence: str) -> str:
    """精确指纹(SHA-256 前 16——与 P7 口径一致)"""
    return hashlib.sha256(
        (evidence or "").encode("utf-8")).hexdigest()[:16]


def check_semantic_reuse(evidence: str,
                         bucket: list) -> dict:
    """与指纹桶比对(精确命中直判; 近似超阈值判嫌疑)

    Args:
        bucket: 画像携带的指纹桶 [{grams, ts, evSha}]
    Returns:
        {hit, similarity, matchedSha, reason}
    """
    sha = ev_sha(evidence)
    grams = char_grams(evidence)
    best = {"hit": False, "similarity": 0.0,
            "matchedSha": "", "reason": ""}
    for item in (bucket or []):
        # 精确重放(SHA 命中)
        if item.get("evSha") == sha:
            return {"hit": True, "similarity": 1.0,
                    "matchedSha": sha,
                    "reason": "证据精确重放(同文指纹命中)"}
        stored = set(item.get("grams") or [])
        if not stored or not grams:
            continue
        sim = jaccard(grams, stored)
        if sim > best["similarity"]:
            best["similarity"] = sim
            best["matchedSha"] = item.get("evSha", "")
    if best["similarity"] > SEMANTIC_SIMILARITY_THRESHOLD:
        best["hit"] = True
        best["reason"] = (
            f"语义近似复用(相似度 "
            f"{best['similarity']} > "
            f"{SEMANTIC_SIMILARITY_THRESHOLD}——疑似改字重放)")
    return best


def fingerprint_entry(evidence: str) -> dict:
    """构造指纹桶条目(存 grams 前 60 个防膨胀 + SHA)"""
    grams = sorted(char_grams(evidence))[:60]
    return {"grams": grams, "evSha": ev_sha(evidence),
            "ts": ts()}


# ============================================================
# ② 价值分布检测器(角色级)
# ============================================================

def _parse_dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def detect_small_high_frequency(
        deposits: list,
        now: datetime = None,
        window_days: int = 30) -> dict:
    """小额高频检测

    口径修正(计划 §四 4.2 的数学性落地):
        "单次净贡献全部 < 单次中位数×0.5" 若中位数取自
        同一窗口则恒假(至少半数样本 ≥ 中位数)——故小额
        参照系取**窗口外基线**: 近 window_days 日正向存证
        ≥N 次 且 全部 < 基线中位数×0.5(基线=窗口前全部
        正向存证的中位数; 无基线不判——与 P7 burst_ratio
        "观察窗不足不判"同范式, 防新角色误伤)。

    Args:
        deposits: 存证列表 [{net, ts}] (net=净贡献,
            含窗口外历史——scan 侧传全量事件)
    Returns:
        {hit, count, median(基线中位数), threshold, reason}
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    parsed = [(float(d.get("net") or 0),
               _parse_dt(d.get("ts")) or now)
              for d in (deposits or [])]
    nets = [n for n, t in parsed
            if t >= cutoff and n > 0]
    baseline = [n for n, t in parsed
                if t < cutoff and n > 0]
    if len(nets) < SMALL_COUNT_MIN:
        return {"hit": False, "count": len(nets),
                "median": None, "threshold": None,
                "reason": f"近{window_days}日正向存证 "
                          f"{len(nets)} 次(<{SMALL_COUNT_MIN}"
                          f" 不判)"}
    if not baseline:
        return {"hit": False, "count": len(nets),
                "median": None, "threshold": None,
                "reason": f"近{window_days}日 {len(nets)} 次"
                          f"正向存证, 无窗口外基线(不判)"}
    med = sorted(baseline)[len(baseline) // 2]
    threshold = med * SMALL_VALUE_RATIO
    all_small = all(n < threshold for n in nets)
    hit = all_small
    return {
        "hit": hit, "count": len(nets),
        "median": round(med, 2),
        "threshold": round(threshold, 2),
        "reason": (f"小额高频: 近{window_days}日 "
                   f"{len(nets)} 次正向存证, 单次全部 < "
                   f"基线中位数{round(med, 1)}×"
                   f"{SMALL_VALUE_RATIO}(刷分嫌疑)"
                   if hit else
                   f"分布正常(基线中位数 "
                   f"{round(med, 1)})")}


def detect_value_mismatch(observed: float,
                           peer_p90: float,
                           component_score:
                           float | None) -> dict:
    """价值-证据错配检测(高申报低证据)

    Args:
        observed: 本次申报值
        peer_p90: 同群体申报值 P90 基线
        component_score: 验真组件最低分(None=未走 v2)
    """
    if component_score is None:
        component_score = 1.0   # 未走 v2 视为无证据信号
    hit = (observed > peer_p90
           and component_score
           < VALUE_MISMATCH_COMPONENT_THRESHOLD)
    reason = ("价值错配: 申报 {} > 群体P90 {} 且验真组件 "
              "{} < {}".format(
                  round(float(observed), 1),
                  round(float(peer_p90), 1),
                  round(float(component_score), 2),
                  VALUE_MISMATCH_COMPONENT_THRESHOLD)
              if hit else "价值证据匹配正常")
    return {"hit": hit, "observed": observed,
            "peerP90": peer_p90,
            "componentScore": component_score,
            "reason": reason}


# ============================================================
# ③ 扫描服务(角色级一轮检测)
# ============================================================

class TrustRiskDetectorService:
    """P1 检测器编排(语义指纹沉淀 + 价值分布扫描)"""

    def __init__(self,
                 repo: TrustRisk47Repository = None):
        self.repo = repo or TrustRisk47Repository()

    async def scan(self, trust_id: int) -> dict:
        """触发一轮角色级检测(幂等)

        扫描内容: 读取画像指纹桶与近期存证 → 价值分布
        检测 → 命中沉淀画像(semantic_reuse 由提交流程实时
        判定, 扫描只补价值分布)。

        幂等口径: 与最近一次 scan 留痕(信号+判定说明)完全
        一致时不重复沉淀(不重复计数 value_anomaly——画像
        只反映检测状态变化, 不随扫描次数累积)。

        Raises:
            KeyError: trustId 无 trust45 档案
        """
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        t45 = await TrustValue45Repository().get_profile(
            trust_id)
        if t45 is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        events = await TrustValue45Repository(
        ).list_events_by_trust(trust_id)
        deposits = [
            {"net": e.get("delta"),
             "ts": e.get("ts")}
            for e in (events or [])
            if e.get("source") in ("deposit",
                                   "deposit_merge")
            and (e.get("delta") or 0) > 0]
        small = detect_small_high_frequency(deposits)

        results = {"valueAnomaly": small,
                   "semanticBucketSize": 0}
        hits = []
        if small.get("hit"):
            hits.append("value_anomaly")
        detail = small.get("reason", "")[:120]
        # 幂等去重: 与最近一次 scan 留痕一致则跳过沉淀
        existing = await self.repo.get_profile(trust_id)
        last_scan = next(
            (h for h in (existing or {}).get(
                "riskHistory") or []
             if h.get("source") == "scan"), None)
        unchanged = (
            last_scan is not None
            and list(last_scan.get("signals") or [])
            == hits
            and (last_scan.get("detail") or "") == detail)
        profile = existing
        if not unchanged:
            profile = await TrustRiskProfileService(
                repo=self.repo).record_risk_event(
                trust_id, "scan",
                signals=hits,
                detail=detail)
        if profile:
            results["semanticBucketSize"] = len(
                profile.get("evidenceFingerprints") or [])
        results["success"] = True
        results["trustId"] = trust_id
        results["scanAt"] = ts()
        return results
