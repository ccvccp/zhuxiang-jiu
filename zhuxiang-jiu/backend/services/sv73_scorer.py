"""73号(sv)·短视频智能模型 P2——分镜风格评分器(44号第 53 档案)

职责: 剧本风格质量评分 + 数据闭环的决策时因子快照源
      (发布回流 → 44号 Hedge 乘性更新 → 风格权重冠军演化)
铁律: LLM 禁入——六因子全确定性打分(注册表基线), 权重学习
      只经 44号 run_learning_cycle(护栏 [1/2,2] 倍+归一)

变更链:
    - 2026-09-30 P2 立项(规划 §三: 数据闭环)
"""

from __future__ import annotations

MODEL_VERSION = "v1-sv73-storyboard-scorer"

# ---- 风格因子基线注册表(确定性; Hedge 学习调权不调基线分) ----
TEMPLATE_BASE = {"vertical": 72, "landscape": 65, "fast": 78}
TRACK_BASE = {"glm-5.3": 85, "glm-4-flash": 75, "rule": 55}
PERSONA_BASE = 70                     # 单档人设基准(竹小妹)
MATCH_HIT, MATCH_FALLBACK = 80, 40   # 品类匹配命中/兜底
DURATION_SWEET = 18.0                # 引流甜点时长(s)


def duration_score(total: float) -> int:
    """时长因子: 甜点 18s 满分, 偏离线性衰减(确定性)"""
    raw = 100 - abs(float(total or 0) - DURATION_SWEET) * 8
    return max(0, min(100, round(raw)))


def factors_from_content(sv73: dict) -> list:
    """从 content.sv73 快照构造六因子(回流口径——与
    score() 同源, 供 sv73_p2_service 构造 44号 factors)"""
    tpl = str(sv73.get("template") or "vertical")
    track = str(sv73.get("track") or "rule")
    return [
        {"name": "template_fit", "score":
            TEMPLATE_BASE.get(tpl, 60)},
        {"name": "track_quality", "score":
            TRACK_BASE.get(track, 55)},
        {"name": "compliance_health", "score":
            max(0, min(100, int(sv73.get("complianceScore")
                                or 0)))},
        {"name": "category_match", "score":
            (MATCH_FALLBACK if sv73.get("matchFallback")
             else MATCH_HIT)},
        {"name": "duration_fit", "score":
            duration_score(sv73.get("totalDuration") or 0)},
        {"name": "persona_fit", "score": PERSONA_BASE},
    ]


class Sv73StoryboardScorer:
    """73号 分镜风格评分器(第 53 档案——44号 SCORER_REGISTRY)"""

    WEIGHTS = {
        "template_fit": 0.25,
        "track_quality": 0.20,
        "compliance_health": 0.20,
        "category_match": 0.15,
        "duration_fit": 0.10,
        "persona_fit": 0.10,
    }

    LEVEL_ACTIONS = {
        "high": "优先排期发布(风格质量优)",
        "medium": "常规发布(观察引流)",
        "low": "降权观察(风格待优化)",
    }

    async def score(self, ctx: dict) -> dict:
        """风格质量评分(决策时——pipeline 可选挂门)

        ctx: {template, track, complianceScore, matchFallback,
              totalDuration}——空上下文拒绝(66号惯例)
        """
        if not ctx or not isinstance(ctx, dict):
            raise ValueError("评分上下文不可为空")
        factors = factors_from_content(ctx)
        weights = dict(self.WEIGHTS)
        total = 0.0
        for f in factors:
            weight = weights.get(f["name"], 0.0)
            f["weight"] = round(weight, 4)
            f["contribution"] = round(
                weight * float(f["score"]) / 100.0, 4)
            total += f["contribution"]
        score = round(min(100.0, total * 100.0), 1)
        level = ("high" if score >= 60
                 else "medium" if score >= 30 else "low")
        return {
            "success": True,
            "scorer": "sv73_storyboard",
            "module": "73号sv短视频智能模型",
            "score": score,
            "level": level,
            "action": self.LEVEL_ACTIONS[level],
            "factors": factors,
            "confidence": 1.0,
            "modelVersion": MODEL_VERSION,
            "scoredAt": __import__("core.helpers",
                                  fromlist=["ts"]).ts(),
        }
