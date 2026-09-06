"""57号·AI智能知识库 缺口信号+采集源注册表
(kb57_registry)

计划(docs/57号_AI智能知识库模块实施计划.md §二/§三):
    缺口信号封闭白名单 GAP_SIGNAL_REGISTRY——全站既有
    观测面纯读取(46/52/55号+44号池+既有 knowledge_*缺口
    /统计+48号失败挖掘), 零侵入零改动。

设计(52号 us52_registry 范式):
    - 封闭注册表: 可断言/可测试/启动自检
    - 五侧覆盖: 业务/用户/系统/合规/模型
    - 权重和=1.0(知识必要性评分归一基础)
    - 采集口径 fail-soft(单源异常跳过留痕)

启动自检 _validate_registry()(RuntimeError 宪法级):
    - 10 项数量+五侧覆盖
    - 权重和=1.0
    - direction 合法(positive/negative)
    - status 合法(active/pending/retired)
    - 来源标识非空+权重越界
    - 采集源 6 项+类型合法+可信度 [0,1]
"""

import logging
import os

logger = logging.getLogger("kb57_registry")

MODEL_VERSION = "v1-kb57-registry"

DEFAULT_MODE = "off"

# 模式三态(off/shadow/assist——计划 §十一开关矩阵)
MODE_VALUES = ("off", "shadow", "assist")

# 缺口信号五侧(业务/用户/系统/合规/模型)
SIGNAL_SIDES = (
    "business", "user", "system", "compliance", "model")

# 信号方向(命中→推动/抑制采集)
DIRECTION_VALUES = ("positive", "negative")

# 信号状态
SIGNAL_STATUS_VALUES = ("active", "pending", "retired")


def current_mode() -> str:
    """模块开关(KB57_MODE, 默认 off——决策面关闭:
    off=缺口诊断面仅 registry 观测; shadow=观察学习期
    仅缺口诊断+采集留痕; assist=辅助生产期鉴别+种子
    +植入开放)"""
    mode = os.environ.get("KB57_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 缺口信号注册表(10 项——计划 §二五侧)
# ============================================================

GAP_SIGNAL_REGISTRY: dict = {
    # ---- 业务侧(0.35) ----
    "kb_gap_open": {
        "label": "既有缺口未决",
        "side": "business",
        "source": "knowledge_gaps",
        "direction": "positive", "weight": 0.20,
        "threshold": 1, "status": "active",
    },
    "kb_search_miss": {
        "label": "搜索无结果高频",
        "side": "business",
        "source": "knowledge_stats",
        "direction": "positive", "weight": 0.15,
        "threshold": 10, "status": "active",
    },
    # ---- 用户侧(0.25) ----
    "us52_inclusion_drop": {
        "label": "包容性下降",
        "side": "user",
        "source": "us52_metrics",
        "direction": "positive", "weight": 0.15,
        "threshold": 0.1, "status": "active",
    },
    "qr55_satisfaction_drop": {
        "label": "满意度下降",
        "side": "user",
        "source": "qr55_metrics",
        "direction": "positive", "weight": 0.10,
        "threshold": 0.1, "status": "active",
    },
    # ---- 系统侧(0.20) ----
    "gov46_stagnation": {
        "label": "学习停滞",
        "side": "system",
        "source": "ai_governance_health",
        "direction": "positive", "weight": 0.10,
        "threshold": 1, "status": "active",
    },
    "xz48_failure_high": {
        "label": "小竹失败挖掘高频",
        "side": "system",
        "source": "xiaozhu_failures",
        "direction": "positive", "weight": 0.10,
        "threshold": 5, "status": "active",
    },
    # ---- 合规侧(0.15) ----
    "gov46_alert_open": {
        "label": "合规告警未决",
        "side": "compliance",
        "source": "ai_governance_alerts",
        "direction": "negative", "weight": 0.075,
        "threshold": 1, "status": "active",
    },
    "gov46_drift_high": {
        "label": "因子漂移高",
        "side": "compliance",
        "source": "ai_governance_health",
        "direction": "positive", "weight": 0.075,
        "threshold": 1, "status": "active",
    },
    # ---- 模型侧(0.05) ----
    "pool44_alignment": {
        "label": "回流对齐下降",
        "side": "model",
        "source": "ai_learning_pool",
        "direction": "positive", "weight": 0.025,
        "threshold": 0.05, "status": "active",
    },
    "kb_freshness_stale": {
        "label": "知识新鲜度过期",
        "side": "model",
        "source": "knowledge_stats",
        "direction": "positive", "weight": 0.025,
        "threshold": 0.3, "status": "active",
    },
}


def get_signal(signal_id: str) -> dict | None:
    """取信号定义"""
    return GAP_SIGNAL_REGISTRY.get(signal_id)


def active_signals() -> list[dict]:
    """全部 active 信号(扫描可用域)"""
    return [dict(v, signalId=k) for k, v
            in GAP_SIGNAL_REGISTRY.items()
            if v.get("status") == "active"]


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    sides: dict = {}
    for sig in GAP_SIGNAL_REGISTRY.values():
        sides[sig["side"]] = \
            sides.get(sig["side"], 0) + 1
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "total": len(GAP_SIGNAL_REGISTRY),
        "bySide": sides,
        "weightSum": round(sum(
            s["weight"] for s
            in GAP_SIGNAL_REGISTRY.values()
            if s.get("status") == "active"), 4),
        "signals": [
            {"signalId": k, **v}
            for k, v in GAP_SIGNAL_REGISTRY.items()],
        "modeValues": MODE_VALUES,
        "note": "缺口信号封闭白名单——五侧纯读取"
                "(46/52/55号+44号池+knowledge_*/"
                "48号失败挖掘零侵入)",
    }


# ============================================================
# 采集源注册表(SOURCE_REGISTRY——可信源封闭白名单)
# ============================================================

# 源类型
SOURCE_TYPES = ("authority", "partner", "internal", "media")

# 强制人工复审可信度线(<0.75——内容安全关高危路径)
CREDIBILITY_REVIEW_LINE = 0.75

SOURCE_REGISTRY: dict = {
    "gov_policy_official": {
        "label": "政府政策官方源",
        "sourceType": "authority",
        "credibility": 0.95,
        "license": "公开政务(署名标注)",
    },
    "authority_clauses_18": {
        "label": "站内条款(18号)",
        "sourceType": "authority",
        "credibility": 0.95,
        "license": "站内自有",
    },
    "academic_open": {
        "label": "开放学术库",
        "sourceType": "authority",
        "credibility": 0.90,
        "license": "CC-BY(署名要求)",
    },
    "partner_authorized": {
        "label": "授权合作方",
        "sourceType": "partner",
        "credibility": 0.85,
        "license": "授权协议",
    },
    "ops_manual": {
        "label": "运营手册",
        "sourceType": "internal",
        "credibility": 0.90,
        "license": "站内自有",
    },
    "media_whitelist": {
        "label": "白名单媒体",
        "sourceType": "media",
        "credibility": 0.70,
        "license": "转载标注",
    },
}


def get_source(source_id: str) -> dict | None:
    """取采集源定义(内置白名单)"""
    return SOURCE_REGISTRY.get(source_id)


def sources_view() -> dict:
    """采集源自描述(观测面——内置白名单)"""
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "total": len(SOURCE_REGISTRY),
        "sourceTypes": SOURCE_TYPES,
        "credibilityReviewLine":
            CREDIBILITY_REVIEW_LINE,
        "sources": [
            {"sourceId": k, **v} for k, v
            in SOURCE_REGISTRY.items()],
        "note": "采集源封闭白名单——白名单外来源"
                "采集即拒(版权关第一道阻断); "
                "可信度<0.75 强制人工复审",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级——52号范式)"""
    errors = []
    if len(GAP_SIGNAL_REGISTRY) != 10:
        errors.append(
            f"缺口信号数量应为 10, 实际 "
            f"{len(GAP_SIGNAL_REGISTRY)}")
    # 五侧覆盖
    sides = {v.get("side")
             for v in GAP_SIGNAL_REGISTRY.values()}
    if sides != set(SIGNAL_SIDES):
        errors.append(f"五侧覆盖不齐: {sorted(sides)}")
    # 权重和(active 域)=1.0
    weight_sum = sum(
        v.get("weight") or 0
        for v in GAP_SIGNAL_REGISTRY.values()
        if v.get("status") == "active")
    if abs(weight_sum - 1.0) > 1e-9:
        errors.append(
            f"active 权重和应为 1.0, 实际 {weight_sum}")
    for sid, sig in GAP_SIGNAL_REGISTRY.items():
        if sig.get("direction") not in DIRECTION_VALUES:
            errors.append(
                f"{sid}: 非法 direction "
                f"{sig.get('direction')}")
        if sig.get("status") not in \
                SIGNAL_STATUS_VALUES:
            errors.append(
                f"{sid}: 非法 status {sig.get('status')}")
        if not str(sig.get("source") or "").strip():
            errors.append(f"{sid}: 来源标识为空")
        w = sig.get("weight")
        if not isinstance(w, (int, float)) \
                or not 0 < float(w) <= 1:
            errors.append(f"{sid}: 权重越界 {w}")
    # 采集源自检
    if len(SOURCE_REGISTRY) != 6:
        errors.append(
            f"采集源数量应为 6, 实际 "
            f"{len(SOURCE_REGISTRY)}")
    for skey, src in SOURCE_REGISTRY.items():
        if src.get("sourceType") not in SOURCE_TYPES:
            errors.append(
                f"{skey}: 非法源类型 "
                f"{src.get('sourceType')}")
        c = src.get("credibility")
        if not isinstance(c, (int, float)) \
                or not 0 <= float(c) <= 1:
            errors.append(f"{skey}: 可信度越界 {c}")
        if not str(src.get("license") or "").strip():
            errors.append(f"{skey}: 授权协议为空")
    if errors:
        raise RuntimeError(
            "kb57 注册表自检失败: "
            + "; ".join(errors))
    logger.info(
        "kb57_registry_validated signals=%s sides=%s "
        "sources=%s weightSum=1.0",
        len(GAP_SIGNAL_REGISTRY), len(sides),
        len(SOURCE_REGISTRY))


_validate_registry()
