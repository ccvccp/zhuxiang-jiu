"""36号·AI智能推广模块 进化引擎层(P3 自适应进化升级)

「从规则驱动到反馈驱动」——四大进化引擎(设计文档 §升级方案):
    引擎1 热点价值评估进化: 品类维度(文化/场景/AI提问/综合)全链路
        ROI 回归 → 品类权重安全阀内自调 + 高价值热点优先级清单
    引擎2 内容风格自适应进化: 5 风格 A/B 轮转(确定性) → 冠军组合
        识别 → 生成时 tone 反哺(风格特征注入 Step3 prompt)
    引擎3 承接页智能路由进化: UCB1 多臂老虎机(确定性, 无随机)
        → 曝光/点击 reward 更新 → 流量倾斜最优承接页
    引擎4 合规与平台适配进化: 平台审核反馈(拒绝/限流) → n-gram
        频次差提取候选风险词 → 人工批准后生效(永不自动阻断)

商品拓展: match_products 热点×全站商品库匹配(复用 02号 search/
    get_hot_products), 生成 focus 与承接页路由均支持多商品。

铁律(LLM 禁入判定链):
    - 全部进化决策为确定性代码: 品类权重回归/UCB1 选臂/n-gram
      提取均为数学计算, 无 LLM 参与
    - 安全阀: 品类权重 clamp [0.5×, 1.5×] 基线, 单步 ≤±20%
    - 人工否决权: 风险词仅人工批准后生效; 进化日志全量留痕供审计

数据表(复用 PromoRepository 双模式存储范式):
    promo_evo_cat_weights / promo_evo_cat_stats / promo_evo_style_stats
    / promo_evo_bandit / promo_evo_risk_candidates / promo_evo_risk_words
    / promo_evo_log / promo_evo_product_match
"""

import logging
import math
import re
from datetime import datetime, UTC

from repositories.promo_repository import (
    PromoRepository, BRAND_RELEVANCE_WORDS,
)
from repositories.backend import is_redis_mode, get_redis_client, _k

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# 引擎1: 热点品类(确定性关键词映射, 命中优先级从上到下)
# ============================================================

HOTSPOT_CATEGORIES = ("ai_question", "scene", "culture", "general")

CATEGORY_NAME = {
    "ai_question": "AI提问类",
    "scene": "场景类",
    "culture": "文化类",
    "general": "综合类",
}

CATEGORY_KEYWORDS = {
    "ai_question": ("提问", "是什么", "怎么选", "攻略", "测评",
                    "推荐", "指南", "如何", "排行", "清单"),
    "scene": ("宴", "聚会", "送礼", "婚", "节", "露营", "微醺", "独处",
              "家宴", "夜宵", "中秋", "春节", "端午", "团圆", "礼盒"),
    "culture": ("非遗", "文化", "国风", "传统", "工艺", "历史", "酿造",
                "竹", "匠心"),
}

# 品类权重基线(设计文档: 文化30% + 场景40% + AI提问30%; 综合类兜底)
CATEGORY_WEIGHT_BASE = {
    "ai_question": 0.30,
    "scene": 0.40,
    "culture": 0.30,
    "general": 0.20,
}
# 安全阀: 权重相对基线的上下限 + 单轮回调幅度上限
WEIGHT_CLAMP_RATIO = (0.5, 1.5)
WEIGHT_STEP_RATIO = 0.20
# 品类参与回归的最小内容样本数(冷启动保护, 不足不调)
CATEGORY_MIN_SAMPLES = 3


def tag_hotspot_category(title: str) -> str:
    """热点标题 → 品类(确定性: 按优先级 ai_question > scene > culture)"""
    text = title or ""
    for category in ("ai_question", "scene", "culture"):
        if any(word in text for word in CATEGORY_KEYWORDS[category]):
            return category
    return "general"


# ============================================================
# 引擎2: 内容风格库(5 风格, 含 toneHint 反哺 Step3 prompt)
# ============================================================

STYLE_LIBRARY = {
    "culture": {
        "name": "文化科普型",
        "toneHint": "以竹香型白酒工艺与文化切入, 理性科普, 引用国标与工艺事实",
    },
    "emotion": {
        "name": "情绪共鸣型",
        "toneHint": "第一人称体验视角, 情绪场景共鸣, 真实感受表达",
    },
    "scene": {
        "name": "场景故事型",
        "toneHint": "具体场景故事化带入(宴席/独处/露营), 画面感叙述",
    },
    "review": {
        "name": "产品测评型",
        "toneHint": "横向测评口吻, 客观对比维度, 数据来自权威引用池",
    },
    "craft": {
        "name": "工艺解说型",
        "toneHint": "酿造工艺与标准解说, 安全合规形式, 避免沉浸式表达",
    },
}

STYLE_KEYS = tuple(STYLE_LIBRARY.keys())
# 风格参与冠军判定的最小样本数
STYLE_MIN_SAMPLES = 3


def assign_style(variant_index: int) -> str:
    """同内容组变体 → 风格(确定性轮转, A/B 均匀分配)"""
    return STYLE_KEYS[variant_index % len(STYLE_KEYS)]


# ============================================================
# 引擎3: 承接页臂(UCB1, 确定性)
# ============================================================

LANDING_ARMS = {
    "product_page": {
        "name": "商品详情页", "intent": "转化导向",
        "path": "/pages/product-detail/index",
    },
    "culture_topic": {
        "name": "文化专题页", "intent": "种草导向",
        "path": "/pages/promotion/index",
    },
    "promo_page": {
        "name": "活动承接页", "intent": "促销导向",
        "path": "/pages/activity/index",
    },
}

ARM_KEYS = tuple(LANDING_ARMS.keys())
UCB_EXPLORATION = math.sqrt(2.0)


def _ucb1_score(arm: dict, total_pulls: int) -> float:
    """UCB1 得分(确定性; 均值 + 探索项, tie-break 按 arm 名稳定)"""
    pulls = max(1, int(arm.get("pulls", 0)))
    reward = float(arm.get("rewardTotal", 0.0)) / pulls
    return reward + UCB_EXPLORATION * math.sqrt(
        math.log(total_pulls + 2) / pulls)


# ============================================================
# 引擎4: 合规进化(n-gram 提取 + 人工批准)
# ============================================================

# n-gram 长度窗口(中文 2-4 字)
NGRAM_RANGE = (2, 4)
# 候选词判定: 出现在 ≥2 条被拒内容, 且通过内容中出现率 < 拒绝出现率
CANDIDATE_MIN_REJECT_DOCS = 2
# 白名单(强制警示语等不可作为风险词, 防误杀)
NGRAM_STOP_WORDS = (
    "过量饮酒有害健康", "未成年人禁止饮酒", "18周岁以下", "请勿饮酒",
)


# 清洗正则: 仅去空白/标点/ASCII 字母数字(\w 在 Unicode 下会吞汉字, 不可用)
_PUNCT_STRIP = re.compile(
    r"[\s，。！？、；：「」『』（）#“”‘’0-9A-Za-z\-—…·,\.!?:;()]+"
)


def _extract_ngrams(text: str) -> set[str]:
    """文本 → n-gram 集合(2-4 字连续片段, 去空白/标点)"""
    cleaned = _PUNCT_STRIP.sub("", text or "")
    grams = set()
    for size in range(NGRAM_RANGE[0], NGRAM_RANGE[1] + 1):
        for i in range(len(cleaned) - size + 1):
            grams.add(cleaned[i:i + size])
    return grams


# ============================================================
# 数据访问(继承 promo 双模式存储范式)
# ============================================================

class EvolutionRepository(PromoRepository):
    """进化层数据表(复用 PromoRepository 通用 _save/_get/_list + 撤销用 _delete)"""

    def _ensure_store(self) -> None:
        super()._ensure_store()
        for table in ("promo_evo_cat_weights", "promo_evo_cat_stats",
                      "promo_evo_style_stats", "promo_evo_bandit",
                      "promo_evo_risk_candidates", "promo_evo_risk_words",
                      "promo_evo_log", "promo_evo_product_match"):
            self.store.setdefault(table, {})
        for key in ("_promo_evo_log_seq", "_promo_evo_candidate_seq"):
            self.store.setdefault(key, 0)

    async def _delete(self, table: str, record_id) -> None:
        """撤销生效词用: 删除记录(redis DEL / 内存 pop)"""
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            await client.delete(_k("promo", table, key_id))
        else:
            self._ensure_store()
            self.store[table].pop(record_id, None)


# ============================================================
# 进化服务
# ============================================================

class PromoEvolutionService:
    """36号·四大进化引擎编排(反馈驱动, 确定性)"""

    def __init__(self, repo: EvolutionRepository = None):
        self.repo = repo or EvolutionRepository()

    # ========================================================
    # 进化日志(全量留痕, 月度审计用)
    # ========================================================

    async def _log(self, engine: str, action: str, detail: dict) -> None:
        log_id = await self.repo.next_id("evo_log")
        await self.repo._save("promo_evo_log", log_id, {
            "logId": log_id, "engine": engine, "action": action,
            "detail": detail, "createdAt": _now_iso(),
        })

    async def list_log(self, engine: str = None, limit: int = 100) -> list[dict]:
        logs = await self.repo._list("promo_evo_log", limit * 5)
        if engine:
            logs = [l for l in logs if l.get("engine") == engine]
        return sorted(logs, key=lambda l: l.get("createdAt", ""),
                      reverse=True)[:limit]

    # ========================================================
    # 引擎1: 热点价值评估进化
    # ========================================================

    async def feed_metrics(self, content: dict, hotspot: dict,
                           metrics: dict) -> None:
        """效果回流 → 品类/风格/老虎机三路统计(幂等: 由调用方保证一次)"""
        clicks = int(metrics.get("clicks", 0) or 0)
        registered = int(metrics.get("registered", 0) or 0)
        ordered = int(metrics.get("ordered", 0) or 0)
        gmv = float(metrics.get("gmv", 0.0) or 0.0)
        exposure = float((content.get("receipt") or {}).get(
            "exposureEstimate", 0) or 0)
        # --- 品类统计 ---
        category = tag_hotspot_category(hotspot.get("title", ""))
        stats = (await self.repo._get("promo_evo_cat_stats", category)
                 or {"category": category, "contents": 0,
                     "exposures": 0.0, "clicks": 0, "registered": 0,
                     "ordered": 0, "gmv": 0.0})
        stats["contents"] += 1
        stats["exposures"] += exposure
        stats["clicks"] += clicks
        stats["registered"] += registered
        stats["ordered"] += ordered
        stats["gmv"] += gmv
        await self.repo._save("promo_evo_cat_stats", category, stats)
        # --- 风格统计 ---
        style_key = content.get("styleKey") or ""
        if style_key in STYLE_LIBRARY:
            style = (await self.repo._get("promo_evo_style_stats", style_key)
                     or {"styleKey": style_key, "variants": 0,
                         "clicks": 0, "orders": 0, "exposures": 0.0})
            style["variants"] += 1
            style["clicks"] += clicks
            style["orders"] += ordered
            style["exposures"] += exposure
            await self.repo._save("promo_evo_style_stats", style_key, style)
        # --- 老虎机 reward(点击率归一) ---
        arm = content.get("landingArm") or ""
        if arm in LANDING_ARMS:
            bandit = (await self.repo._get("promo_evo_bandit", arm)
                      or {"arm": arm, "pulls": 0, "rewardTotal": 0.0})
            bandit["pulls"] = int(bandit.get("pulls", 0)) + 1
            reward = (clicks / exposure) if exposure > 0 else 0.0
            bandit["rewardTotal"] = (float(bandit.get("rewardTotal", 0.0))
                                    + reward)
            await self.repo._save("promo_evo_bandit", arm, bandit)

    async def evolve_hotspot_weights(self) -> dict:
        """品类权重回归(安全阀内自动调整 + 全量留痕)

        算法: 每品类 ROI = (订单×100 + 注册×10 + 点击) 加权产出
              / 曝光; 相对全站均值的比值驱动权重步进(±20% 内),
              并 clamp 到基线的 [0.5×, 1.5×]。样本不足品类跳过。
        """
        stats_all = await self.repo._list("promo_evo_cat_stats", 50)
        by_cat = {s.get("category"): s for s in stats_all}
        # 全站加权产出均值(ROI 单位: 产出自定义分/千次曝光)
        totals = {"output": 0.0, "exposure": 0.0}
        for stats in stats_all:
            output = (int(stats.get("ordered", 0)) * 100
                      + int(stats.get("registered", 0)) * 10
                      + int(stats.get("clicks", 0)))
            exposure = max(1.0, float(stats.get("exposures", 0.0)))
            totals["output"] += output
            totals["exposure"] += exposure
        avg_roi = (totals["output"] / totals["exposure"]) \
            if totals["exposure"] > 0 else 0.0
        adjustments = []
        for category, base in CATEGORY_WEIGHT_BASE.items():
            stats = by_cat.get(category)
            if not stats or int(stats.get("contents", 0)) < \
                    CATEGORY_MIN_SAMPLES:
                continue   # 冷启动保护
            output = (int(stats.get("ordered", 0)) * 100
                      + int(stats.get("registered", 0)) * 10
                      + int(stats.get("clicks", 0)))
            exposure = max(1.0, float(stats.get("exposures", 0.0)))
            roi = output / exposure
            ratio = (roi / avg_roi) if avg_roi > 0 else 1.0
            current = (await self.repo._get(
                "promo_evo_cat_weights", category)
                or {"category": category, "weight": base})
            old_weight = float(current.get("weight", base))
            # 步进: 比值>1 升, <1 降; 单步 ≤±20%
            step = max(-WEIGHT_STEP_RATIO, min(WEIGHT_STEP_RATIO,
                                               (ratio - 1.0) * 0.5))
            new_weight = old_weight * (1.0 + step)
            lo, hi = base * WEIGHT_CLAMP_RATIO[0], base * \
                WEIGHT_CLAMP_RATIO[1]
            new_weight = max(lo, min(hi, new_weight))
            new_weight = round(new_weight, 4)
            await self.repo._save("promo_evo_cat_weights", category, {
                "category": category, "weight": new_weight,
                "base": base, "roi": round(roi, 6),
                "avgRoi": round(avg_roi, 6), "samples": int(
                    stats.get("contents", 0)),
                "updatedAt": _now_iso(),
            })
            adjustments.append({
                "category": category, "categoryName": CATEGORY_NAME[
                    category],
                "oldWeight": round(old_weight, 4), "newWeight": new_weight,
                "roi": round(roi, 6), "avgRoi": round(avg_roi, 6),
            })
        await self._log("hotspot_weights", "regression", {
            "avgRoi": round(avg_roi, 6), "adjustments": adjustments})
        return {"avgRoi": round(avg_roi, 6), "adjustments": adjustments}

    async def category_weights(self) -> list[dict]:
        """品类权重现状(未进化品类回落基线)"""
        rows = []
        stored = {w.get("category"): w for w in await self.repo._list(
            "promo_evo_cat_weights", 20)}
        stats = {s.get("category"): s for s in await self.repo._list(
            "promo_evo_cat_stats", 20)}
        for category, base in CATEGORY_WEIGHT_BASE.items():
            weight = stored.get(category, {})
            stat = stats.get(category, {})
            rows.append({
                "category": category,
                "categoryName": CATEGORY_NAME[category],
                "weight": float(weight.get("weight", base)),
                "base": base,
                "samples": int(stat.get("contents", 0)),
                "clicks": int(stat.get("clicks", 0)),
                "ordered": int(stat.get("ordered", 0)),
                "gmv": float(stat.get("gmv", 0.0)),
            })
        return rows

    async def hotspot_priority(self, limit: int = 20) -> list[dict]:
        """高价值热点优先级清单(品类权重 × 热点评分)"""
        weights = {row["category"]: row["weight"]
                   for row in await self.category_weights()}
        hotspots = await self.repo.list_hotspots(
            status="engaged", limit=limit * 3)
        ranked = []
        for h in hotspots:
            category = tag_hotspot_category(h.get("title", ""))
            score = float(h.get("score", 0)) * weights.get(
                category, CATEGORY_WEIGHT_BASE["general"])
            ranked.append({
                "hotspotId": h.get("hotspotId"),
                "title": h.get("title", ""),
                "platform": h.get("platform", ""),
                "score": float(h.get("score", 0)),
                "category": category,
                "categoryName": CATEGORY_NAME[category],
                "priorityScore": round(score, 2),
            })
        ranked.sort(key=lambda r: (-r["priorityScore"],
                                  r.get("hotspotId") or 0))
        return ranked[:limit]

    # ========================================================
    # 引擎2: 内容风格自适应进化
    # ========================================================

    async def style_stats(self) -> list[dict]:
        """风格 A/B 统计(变体/点击/订单/CTR/订单率 + 冠军标记)"""
        stored = {s.get("styleKey"): s for s in await self.repo._list(
            "promo_evo_style_stats", 20)}
        rows = []
        for key, meta in STYLE_LIBRARY.items():
            stat = stored.get(key, {})
            variants = int(stat.get("variants", 0))
            clicks = int(stat.get("clicks", 0))
            orders = int(stat.get("orders", 0))
            exposures = float(stat.get("exposures", 0.0))
            rows.append({
                "styleKey": key, "name": meta["name"],
                "toneHint": meta["toneHint"],
                "variants": variants, "clicks": clicks, "orders": orders,
                "ctr": round(clicks / exposures, 6) if exposures > 0 else 0.0,
                "orderRate": (round(orders / clicks, 6)
                              if clicks > 0 else 0.0),
                "champion": False,
            })
        eligible = [r for r in rows if r["variants"] >= STYLE_MIN_SAMPLES
                    and r["ctr"] > 0]
        if eligible:
            champion = max(eligible, key=lambda r: (r["ctr"], r["styleKey"]))
            champion["champion"] = True
        return rows

    async def champion_style(self) -> dict | None:
        """当前冠军风格(样本充足且 CTR 最高; 不足返回 None 不反哺)"""
        for row in await self.style_stats():
            if row["champion"]:
                return row
        return None

    async def sop_report(self) -> dict:
        """高转化内容生成 SOP(冠军组合特征 → 可执行范式)"""
        champion = await self.champion_style()
        return {
            "championStyle": champion,
            "sop": (f"风格: {champion['name']} · "
                    f"toneHint: {champion['toneHint']} · "
                    f"CTR {champion['ctr']}"
                    ) if champion else "样本积累中(冠军未产生, 保持 A/B 轮转)",
            "policy": "冠军风格注入 Step3 tone 反哺; 其余风格保持探索分配",
            "generatedAt": _now_iso(),
        }

    # ========================================================
    # 引擎3: 承接页智能路由(UCB1)
    # ========================================================

    async def route_landing(self) -> dict:
        """UCB1 选臂(确定性; 冷启动全 0 时按臂名稳定次序取首个)"""
        arms = {arm: (await self.repo._get("promo_evo_bandit", arm)
                      or {"arm": arm, "pulls": 0, "rewardTotal": 0.0})
                for arm in ARM_KEYS}
        total = sum(int(a.get("pulls", 0)) for a in arms.values())
        best_arm, best_score = None, -1.0
        for arm in sorted(ARM_KEYS):   # 稳定 tie-break
            score = _ucb1_score(arms[arm], total)
            if score > best_score:
                best_arm, best_score = arm, score
        return {
            "arm": best_arm,
            "meta": LANDING_ARMS[best_arm],
            "ucbScore": round(best_score, 6),
            "totalPulls": total,
        }

    async def bandit_stats(self) -> list[dict]:
        """老虎机三臂状态(均值/拉取/置信上界)"""
        arms = {arm: (await self.repo._get("promo_evo_bandit", arm)
                      or {"arm": arm, "pulls": 0, "rewardTotal": 0.0})
                for arm in ARM_KEYS}
        total = sum(int(a.get("pulls", 0)) for a in arms.values())
        rows = []
        for arm in ARM_KEYS:
            record = arms[arm]
            pulls = int(record.get("pulls", 0))
            mean = (float(record.get("rewardTotal", 0.0)) / pulls
                    if pulls > 0 else 0.0)
            rows.append({
                "arm": arm, "name": LANDING_ARMS[arm]["name"],
                "intent": LANDING_ARMS[arm]["intent"],
                "path": LANDING_ARMS[arm]["path"],
                "pulls": pulls,
                "meanReward": round(mean, 6),
                "ucb": round(_ucb1_score(record, total), 6),
            })
        return rows

    # ========================================================
    # 引擎4: 合规与平台适配进化
    # ========================================================

    async def record_audit_feedback(self, content: dict,
                                    outcome: str,
                                    note: str = "") -> dict:
        """平台审核反馈录入(rejected/limited/removed)

        录入后即时提取该内容的 n-gram 候选增量(确定性), 与
        通过内容语料做频次差, 达阈值者入候选队列(仅观察, 永不自动阻断)。
        """
        if outcome not in ("rejected", "limited", "removed"):
            raise ValueError(f"审核反馈类型无效({outcome})")
        rejected_grams = _extract_ngrams(
            f"{content.get('title', '')}{content.get('body', '')}")
        # 通过内容语料(已发布且非拒绝)
        approved_corpus = set()
        approved_docs = 0
        for row in await self.repo.list_contents(limit=500):
            if row.get("status") == "published":
                approved_docs += 1
                approved_corpus |= _extract_ngrams(
                    f"{row.get('title', '')}{row.get('body', '')}")
        # 历史拒绝语料(含本次)
        rejected_docs = 1
        rejected_counts: dict[str, int] = {g: 1 for g in rejected_grams}
        for row in await self.repo.list_contents(limit=500):
            if row.get("auditOutcome") in ("rejected", "limited", "removed"):
                rejected_docs += 1
                for gram in _extract_ngrams(
                        f"{row.get('title', '')}{row.get('body', '')}"):
                    rejected_counts[gram] = rejected_counts.get(gram, 0) + 1
        # 候选判定: ≥2 条拒绝内容包含 且 通过内容出现率 < 拒绝出现率
        new_candidates = []
        for gram, count in rejected_counts.items():
            if count < CANDIDATE_MIN_REJECT_DOCS:
                continue
            if any(stop in gram or gram in stop
                   for stop in NGRAM_STOP_WORDS):
                continue
            reject_rate = count / max(1, rejected_docs)
            approve_rate = ((1 if gram in approved_corpus else 0)
                            / max(1, approved_docs))
            if reject_rate > approve_rate:
                existing = await self.repo._get(
                    "promo_evo_risk_candidates", gram)
                if existing:
                    continue
                word_id = await self.repo.next_id("evo_candidate")
                await self.repo._save(
                    "promo_evo_risk_candidates", gram, {
                        "word": gram, "rejectDocs": count,
                        "rejectRate": round(reject_rate, 4),
                        "approveRate": round(approve_rate, 4),
                        "status": "pending", "note": note,
                        "createdAt": _now_iso(), "seq": word_id,
                    })
                new_candidates.append(gram)
        await self._log("compliance", "audit_feedback", {
            "contentId": content.get("contentId"), "outcome": outcome,
            "newCandidates": new_candidates})
        return {"contentId": content.get("contentId"),
                "outcome": outcome, "newCandidates": new_candidates}

    async def list_risk_candidates(self,
                                   status: str = None) -> list[dict]:
        """候选风险词队列(待人工裁决)"""
        rows = await self.repo._list("promo_evo_risk_candidates", 500)
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return sorted(rows, key=lambda r: (-int(r.get("rejectDocs", 0)),
                                            str(r.get("word", ""))))[:100]

    async def approve_risk_word(self, word: str,
                                approver: str = "admin") -> dict:
        """人工批准候选词 → 生效入附加风险词表(永不自动, 全留痕)

        Raises:
            KeyError: 候选不存在
            ValueError: 已批准/已拒绝
        """
        candidate = await self.repo._get("promo_evo_risk_candidates", word)
        if candidate is None:
            raise KeyError(f"候选风险词不存在({word})")
        if candidate.get("status") != "pending":
            raise ValueError(f"候选词已裁决(当前{candidate.get('status')})")
        await self.repo._save("promo_evo_risk_candidates", word, {
            **candidate, "status": "approved",
            "approvedBy": approver, "approvedAt": _now_iso()})
        await self.repo._save("promo_evo_risk_words", word, {
            "word": word, "approvedBy": approver,
            "approvedAt": _now_iso()})
        await self._log("compliance", "risk_word_approved", {
            "word": word, "approver": approver,
            "rejectDocs": candidate.get("rejectDocs")})
        return {"word": word, "status": "approved", "approver": approver}

    async def reject_risk_word(self, word: str,
                              operator: str = "admin") -> dict:
        """人工拒绝候选词(误报处理, 全留痕)"""
        candidate = await self.repo._get("promo_evo_risk_candidates", word)
        if candidate is None:
            raise KeyError(f"候选风险词不存在({word})")
        if candidate.get("status") != "pending":
            raise ValueError(f"候选词已裁决(当前{candidate.get('status')})")
        await self.repo._save("promo_evo_risk_candidates", word, {
            **candidate, "status": "rejected",
            "rejectedBy": operator, "rejectedAt": _now_iso()})
        await self._log("compliance", "risk_word_rejected", {
            "word": word, "operator": operator})
        return {"word": word, "status": "rejected"}

    async def extra_risk_words(self) -> list[str]:
        """已生效附加风险词(引擎4 输出; 供 check_risk/compliance_gate 注入)

        失败回退空列表(不影响静态词库主链)。
        """
        try:
            rows = await self.repo._list("promo_evo_risk_words", 200)
            return [r.get("word", "") for r in rows if r.get("word")]
        except Exception as exc:
            logger.warning("evo_extra_risk_words_failed(回退空): %s", exc)
            return []

    async def active_risk_words(self) -> list[dict]:
        """已生效附加风险词明细(词/批准人/批准时间; 供撤销操作)"""
        rows = await self.repo._list("promo_evo_risk_words", 200)
        return sorted((r for r in rows if r.get("word")),
                      key=lambda r: str(r.get("approvedAt", "")),
                      reverse=True)

    async def revoke_risk_word(self, word: str,
                                operator: str = "admin") -> dict:
        """撤销已批准的风险词(误批回滚; 全留痕)

        撤销 = 从生效表移除(下一轮生成/扫描即不再拦截), 候选记录
        标记 revoked 终态(轨迹可审计)。与 approve 对称的人工操作。

        Raises:
            KeyError: 生效词不存在(未批准过)
            ValueError: 候选记录状态异常
        """
        active = await self.repo._get("promo_evo_risk_words", word)
        if active is None:
            raise KeyError(f"生效风险词不存在({word})")
        await self.repo._delete("promo_evo_risk_words", word)
        candidate = await self.repo._get("promo_evo_risk_candidates", word)
        if candidate is not None:
            await self.repo._save("promo_evo_risk_candidates", word, {
                **candidate, "status": "revoked",
                "revokedBy": operator, "revokedAt": _now_iso()})
        await self._log("compliance", "risk_word_revoked", {
            "word": word, "operator": operator,
            "approvedBy": active.get("approvedBy", "")})
        return {"word": word, "status": "revoked", "operator": operator}

    # ========================================================
    # 商品拓展: 热点 × 全站商品库匹配
    # ========================================================

    async def match_products(self, hotspot: dict) -> list[dict]:
        """热点 → 全站商品匹配(复用 02号 search; 兜底热销榜)

        匹配度 = 品牌命中词 + 标题关键词 在商品 name/subtitle/
        series/tags 的命中数(确定性降序, 同分按 productId 稳定)。
        """
        title = hotspot.get("title", "") or ""
        keywords = list(hotspot.get("brandHits") or []) + [
            w for w in BRAND_RELEVANCE_WORDS if w in title]
        seen: dict[int, dict] = {}
        try:
            from repositories.product_repository import ProductRepository
            products = await ProductRepository().list_all()
        except Exception as exc:
            logger.warning("evo_product_lib_unavailable: %s", exc)
            return []
        for product in products:
            pid = (product.get("product_id")
                   or product.get("id") or "")
            if not pid:
                continue
            haystack = " ".join([
                str(product.get("name", "")),
                str(product.get("subtitle", "")),
                str(product.get("series", "")),
                " ".join(str(t) for t in product.get("tags", [])),
                " ".join(str(s) for s in product.get("scenes", [])),
            ]).lower()
            hits = sum(1 for kw in keywords if kw and kw.lower()
                       in haystack)
            seen[str(pid)] = {
                "productId": pid, "name": product.get("name", ""),
                "series": product.get("series", ""),
                "matchHits": hits,
                "salesMonthly": int(product.get("sales_monthly", 0) or 0),
            }
        matched = sorted(seen.values(),
                        key=lambda m: (-m["matchHits"], -m["salesMonthly"],
                                       str(m["productId"])))
        top = matched[:3]
        if not top or all(m["matchHits"] == 0 for m in top):
            # 兜底: 热销榜补位(仍输出, 保持三臂路由可用)
            hot = sorted(seen.values(),
                         key=lambda m: (-m["salesMonthly"],
                                        str(m["productId"])))
            top = hot[:3]
        # 缓存关联(hotspotId 粒度, 供生成与报表复用)
        await self.repo._save("promo_evo_product_match",
                              int(hotspot.get("hotspotId", 0) or 0), {
                                  "hotspotId": hotspot.get("hotspotId"),
                                  "products": top,
                                  "matchedAt": _now_iso(),
                              })
        return top

    # ========================================================
    # 总览(工作台/进化中枢)
    # ========================================================

    async def status(self) -> dict:
        """四引擎 + 商品拓展总览"""
        weights = await self.category_weights()
        styles = await self.style_stats()
        bandit = await self.bandit_stats()
        candidates = await self.list_risk_candidates(status="pending")
        champion = await self.champion_style()
        return {
            "engines": {
                "hotspotWeights": {
                    "name": "热点价值评估进化",
                    "categories": weights,
                    "evolved": any(w["weight"] != w["base"]
                                   for w in weights),
                },
                "styleChampion": {
                    "name": "内容风格自适应进化",
                    "champion": champion["name"] if champion else "积累中",
                    "styles": styles,
                },
                "landingBandit": {
                    "name": "承接页智能路由进化",
                    "arms": bandit,
                },
                "compliance": {
                    "name": "合规与平台适配进化",
                    "pendingCandidates": len(candidates),
                    "activeExtraWords": len(await self.extra_risk_words()),
                },
            },
            "productExpansion": {
                "name": "全站商品拓展",
                "library": "02号商品库(实时匹配, 热销兜底)",
            },
            "generatedAt": _now_iso(),
        }
