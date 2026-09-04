"""47号·L2/L3 信值验真风控模块 P2 轻量协同分析
(互证对分析 + 跨角色指纹共享 + 团伙嫌疑标记 + 团伙视图)

计划(docs/47号_L2L3信值验真风控模块实施计划.md §五):
    ① 互证对分析(基于 trust45_events 存证留痕):
        - sources 中的角色互证引用约定: "trust:{trustId}"
          (B 出现在 A 的存证 sources → B 为 A 作证)
        - A 的存证引用 B 且 B 的存证引用 A → 互证对(A,B)
        - 互证对数 = min(双向计数)——完整互证回合数;
          ≥3 → 双方 collusive_suspect
    ② 跨角色指纹共享:
        - 精确指纹: 同一 evSha 出现在 ≥2 角色画像指纹桶
        - 语义近似: 跨角色桶内 3-gram Jaccard > 0.8
          (数学等价条件: 公共 gram 数 > 4/9×(|A|+|B|),
          倒排索引预筛免全对比较)
        - 单角色共享次数 ≥2 → collusive_suspect
    ③ 团伙视图(GET /risk/collusion):
        嫌疑对列表 + 证据链明细(互证时间线/共享指纹),
        供人工复核; 扫描(POST /risk/collusion/scan)负责标记。

设计红线(计划 §一 1.4/§五):
    - 宁可标记不可误罚: collusive_suspect 仅标记 + 通道收窄
      (P3), 任何处罚必须走人工复核——本模块零自动处罚
    - 标记幂等: hitCounts 已含 collusive_suspect 则跳过
      (不随扫描次数累积; 证据链幅度由视图实时重算呈现)
    - 自证剔除: 存证 sources 中引用自己("trust:{自身id}")
      不计独立源, 也不构成互证对
    - GNN 图谱轨明确不落地(外部待办); MinHash LSH 为
      证据量 >10 万条后的升级项(当前倒排索引扫描足够)
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from core.helpers import ts

from repositories.trust_risk_repository import (
    TrustRisk47Repository,
)
from services.trust_risk_profile_service import (
    TrustRiskProfileService,
)

logger = logging.getLogger(__name__)

# 互证引用前缀(sources 数组约定: "trust:{trustId}")
ATTESTATION_PREFIX = "trust:"

# 互证对观察窗(计划 §五: 近 90 日)
MUTUAL_WINDOW_DAYS = 90

# 互证对数阈值(完整回合 ≥3 → 双方嫌疑标记)
MUTUAL_PAIR_MIN = 3

# 单角色共享指纹次数阈值(≥2 → 嫌疑标记)
SHARED_FP_SUSPECT_MIN = 2

# 跨角色语义近似阈值(与同角色 P1 口径一致, Jaccard > 0.8)
CROSS_ROLE_SEMANTIC_THRESHOLD = 0.8

# 语义跨角色扫描的条目上限(防大库全对比较; 超限降级
# 精确指纹-only——MinHash LSH 为外部待办, 计划 §十)
SEMANTIC_SCAN_MAX_ENTRIES = 5000

# 画像扫描上限(与风险排行口径一致)
PROFILE_SCAN_LIMIT = 500


# ============================================================
# ① 互证对分析(纯函数)
# ============================================================

def parse_attestation_ref(source) -> int | None:
    """解析互证引用: "trust:42" → 42; 非引用格式 → None"""
    if not isinstance(source, str):
        return None
    if not source.startswith(ATTESTATION_PREFIX):
        return None
    tail = source[len(ATTESTATION_PREFIX):]
    try:
        return int(tail)
    except ValueError:
        return None


def _parse_dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def extract_mutual_pairs(events: list,
                         now: datetime = None,
                         window_days: int =
                         MUTUAL_WINDOW_DAYS) -> dict:
    """从存证事件提取互证对

    Args:
        events: 存证事件列表 [{eventId, trustId,
            sources(list), ts}](source=deposit 口径)
    Returns:
        {"pairs": [{a, b, aRefsB, bRefsA, mutual,
                    suspect, timeline}]}(双向均≥1 的对,
        mutual 降序), "directedCount": {a: {b: n}},
        "scanned": 窗口内参与统计的事件数
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    directed: dict = defaultdict(lambda: defaultdict(int))
    timeline: dict = defaultdict(list)
    scanned = 0
    for ev in events or []:
        t = _parse_dt(ev.get("ts"))
        if t is None or t < cutoff:
            continue
        scanned += 1
        depositor = ev.get("trustId")
        for s in ev.get("sources") or []:
            referenced = parse_attestation_ref(s)
            if referenced is None or referenced == depositor:
                continue   # 非引用/自证不构成互证
            directed[depositor][referenced] += 1
            key = (min(depositor, referenced),
                   max(depositor, referenced))
            timeline[key].append({
                "eventId": ev.get("eventId"),
                "ts": ev.get("ts"),
                "depositor": depositor,
                "referenced": referenced})
    pairs = []
    seen = set()
    for a, refs in directed.items():
        for b in refs:
            key = (min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            x, y = key
            x_refs_y = int(directed.get(x, {}).get(y, 0))
            y_refs_x = int(directed.get(y, {}).get(x, 0))
            if x_refs_y <= 0 or y_refs_x <= 0:
                continue   # 单向作证不构成互证对
            mutual = min(x_refs_y, y_refs_x)
            pairs.append({
                "a": x, "b": y,
                "aRefsB": x_refs_y,
                "bRefsA": y_refs_x,
                "mutual": mutual,
                "suspect": mutual >= MUTUAL_PAIR_MIN,
                "timeline": sorted(
                    timeline[key],
                    key=lambda e: e.get("ts") or ""),
            })
    pairs.sort(key=lambda p: -p["mutual"])
    return {"pairs": pairs,
            "directedCount": {a: dict(r)
                              for a, r in directed.items()},
            "scanned": scanned}


# ============================================================
# ② 跨角色指纹共享(纯函数)
# ============================================================

def find_shared_fingerprints(
        profiles: list,
        semantic: bool = True) -> dict:
    """跨角色指纹共享检测

    Args:
        profiles: 画像列表 [{trustId,
            evidenceFingerprints: [{grams, evSha, ts}]}]
        semantic: 是否跑跨角色语义近似(条目超限自动降级)
    Returns:
        {"shared": [{type: exact|semantic, evSha?,
            roles, similarity}], "shareCounts": {trustId: n},
        "semanticSkipped": bool}
    """
    # --- 精确指纹: evSha → 角色集 ---
    sha_roles: dict = defaultdict(set)
    for p in profiles or []:
        tid = p.get("trustId")
        for e in p.get("evidenceFingerprints") or []:
            sha = e.get("evSha")
            if sha:
                sha_roles[sha].add(tid)
    shared = []
    for sha, roles in sha_roles.items():
        if len(roles) >= 2:
            shared.append({
                "type": "exact", "evSha": sha,
                "roles": sorted(roles), "similarity": 1.0})

    # --- 语义近似: 跨角色桶内 3-gram Jaccard > 0.8 ---
    semantic_skipped = False
    if semantic:
        entries = []   # (trustId, grams_set, evSha)
        for p in profiles or []:
            tid = p.get("trustId")
            for e in p.get("evidenceFingerprints") or []:
                grams = set(e.get("grams") or [])
                if grams and e.get("evSha"):
                    entries.append((tid, grams, e["evSha"]))
        if len(entries) > SEMANTIC_SCAN_MAX_ENTRIES:
            semantic_skipped = True
            logger.info("trust47_semantic_skip_entries=%s",
                        len(entries))
        else:
            # 倒排索引: gram → 条目下标
            gram_index: dict = defaultdict(list)
            for i, (_, grams, _) in enumerate(entries):
                for g in grams:
                    gram_index[g].append(i)
            # 公共 gram 计数(跨角色 + 非精确重复对)
            common: dict = defaultdict(int)
            for idxs in gram_index.values():
                for x in range(len(idxs)):
                    for y in range(x + 1, len(idxs)):
                        i, j = idxs[x], idxs[y]
                        if i > j:
                            i, j = j, i
                        (ta, _, sa) = entries[i]
                        (tb, _, sb) = entries[j]
                        if ta == tb or sa == sb:
                            continue
                        common[(i, j)] += 1
            for (i, j), c in common.items():
                (ta, ga, _) = entries[i]
                (tb, gb, _) = entries[j]
                # J > 0.8 ⟺ c > (4/9)×(|A|+|B|)
                if c > (4.0 / 9.0) * (len(ga) + len(gb)):
                    union = len(ga) + len(gb) - c
                    shared.append({
                        "type": "semantic",
                        "roles": sorted({ta, tb}),
                        "similarity": round(c / union, 4)})
    shared.sort(key=lambda s: (-len(s["roles"]),
                              s.get("evSha") or ""))
    share_counts: dict = defaultdict(int)
    for s in shared:
        for r in s["roles"]:
            share_counts[r] += 1
    return {"shared": shared,
            "shareCounts": dict(share_counts),
            "semanticSkipped": semantic_skipped}


# ============================================================
# ③ 协同扫描服务(检测 + 标记 + 视图)
# ============================================================

class TrustRiskCollusionService:
    """P2 轻量协同分析编排(互证对 + 指纹共享 → 嫌疑标记)"""

    def __init__(self,
                 repo: TrustRisk47Repository = None):
        self.repo = repo or TrustRisk47Repository()

    async def _collect(self) -> dict:
        """汇集检测素材(近窗存证事件 + 全量画像)"""
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        events = await TrustValue45Repository(
        ).list_deposit_events(days=MUTUAL_WINDOW_DAYS)
        profiles = await self.repo.list_profiles(
            limit=PROFILE_SCAN_LIMIT)
        return {"events": events, "profiles": profiles}

    async def _detect(self) -> dict:
        """执行一轮协同检测(互证对 + 指纹共享 → 嫌疑汇总)"""
        material = await self._collect()
        mutual = extract_mutual_pairs(material["events"])
        fps = find_shared_fingerprints(
            material["profiles"])
        suspects: dict = {}
        for p in mutual["pairs"]:
            if p["suspect"]:
                for role in (p["a"], p["b"]):
                    suspects.setdefault(
                        role, {"mutualPairs": [],
                               "sharedFingerprints": []})
                    suspects[role]["mutualPairs"].append(p)
        for s in fps["shared"]:
            for role in s["roles"]:
                if fps["shareCounts"].get(role, 0) \
                        >= SHARED_FP_SUSPECT_MIN:
                    suspects.setdefault(
                        role, {"mutualPairs": [],
                               "sharedFingerprints": []})
                    suspects[role][
                        "sharedFingerprints"].append(s)
        return {"events": material["events"],
                "profiles": material["profiles"],
                "mutual": mutual, "fps": fps,
                "suspects": suspects}

    async def scan(self) -> dict:
        """协同扫描 + 嫌疑标记(幂等——已标记角色跳过)

        标记只沉淀 collusive_suspect 信号(hitCounts+1 +
        riskEMA 更新), 零自动处罚(红线: 处罚走人工复核);
        证据链幅度由 GET /collusion 视图实时重算呈现。
        """
        det = await self._detect()
        marked, skipped = [], []
        profile_svc = TrustRiskProfileService(repo=self.repo)
        for tid in sorted(det["suspects"]):
            info = det["suspects"][tid]
            existing = await self.repo.get_profile(tid)
            already = int((existing or {}).get(
                "hitCounts", {}).get("collusive_suspect")
                or 0) >= 1
            if already:
                skipped.append(tid)
                continue
            pairs = ", ".join(
                f"{p['a']}↔{p['b']}×{p['mutual']}"
                for p in info["mutualPairs"])
            shares = len(info["sharedFingerprints"])
            detail = (f"互证对[{pairs}] "
                     f"共享指纹{shares}次" if pairs else
                     f"共享指纹{shares}次")[:120]
            await profile_svc.record_risk_event(
                tid, "collusion",
                signals=["collusive_suspect"],
                detail=detail)
            marked.append(tid)
        # 标记后刷新画像(marked 标志反映扫描后状态)
        if marked:
            det["profiles"] = await self.repo.list_profiles(
                limit=PROFILE_SCAN_LIMIT)
        result = await self._view(det)
        result["marked"] = marked
        result["skipped"] = skipped
        result["success"] = True
        result["scanAt"] = ts()
        return result

    async def view(self) -> dict:
        """团伙视图(纯读——实时重算, 零标记零写入)"""
        det = await self._detect()
        return await self._view(det)

    async def _view(self, det: dict) -> dict:
        """检测产物 → 团伙视图结构"""
        mutual = det["mutual"]
        fps = det["fps"]
        marked_map = {
            p.get("trustId") for p in det["profiles"]
            if int((p.get("hitCounts") or {})
                   .get("collusive_suspect") or 0) >= 1}
        suspect_view = []
        for tid, info in sorted(det["suspects"].items()):
            suspect_view.append({
                "trustId": tid,
                "mutualPairs": [
                    {"partner": (p["b"] if p["a"] == tid
                                 else p["a"]),
                     "mutual": p["mutual"],
                     "aRefsB": p["aRefsB"],
                     "bRefsA": p["bRefsA"]}
                    for p in info["mutualPairs"]],
                "sharedFingerprints": [
                    {"type": s["type"],
                     "evSha": s.get("evSha", ""),
                     "roles": s["roles"],
                     "similarity": s["similarity"]}
                    for s in info["sharedFingerprints"]],
                "shareCount": fps["shareCounts"].get(tid, 0),
                "marked": tid in marked_map})
        return {
            "success": True,
            "generatedAt": ts(),
            "windowDays": MUTUAL_WINDOW_DAYS,
            "thresholds": {
                "mutualPairMin": MUTUAL_PAIR_MIN,
                "sharedFingerprintMin":
                    SHARED_FP_SUSPECT_MIN,
                "semanticSimilarity":
                    CROSS_ROLE_SEMANTIC_THRESHOLD},
            "totals": {
                "depositEvents": mutual["scanned"],
                "profilesScanned": len(det["profiles"]),
                "mutualPairs": len(mutual["pairs"]),
                "sharedFingerprints": len(fps["shared"]),
                "suspects": len(det["suspects"])},
            "suspects": suspect_view,
            "mutualPairs": [
                {"a": p["a"], "b": p["b"],
                 "aRefsB": p["aRefsB"],
                 "bRefsA": p["bRefsA"],
                 "mutual": p["mutual"],
                 "suspect": p["suspect"],
                 "timeline": p["timeline"]}
                for p in mutual["pairs"]],
            "sharedFingerprints": fps["shared"],
            "semanticSkipped": fps["semanticSkipped"],
            "note": "嫌疑仅标记不处罚(红线); 处罚须经人工"
                    "复核——画像校准通道为复核出口",
        }
