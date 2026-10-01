"""45号 P6·信号自动入分总线 + 限制矩阵/档位权益 settings

D3 行为约束(保守灰度, 用户口径):
    - ingest_signal(): 信号源统一入口, **默认 shadow**——只落
      shadow 留痕不改分(观察 1 周误伤面), settings 切真入分
    - 信号源注册表: 首批 2 源已接(escrow_forfeit/growth_anomaly),
      其余登记待接(security_ban/refund_abuse/help_breach)
    - 低信值限制矩阵: settings 默认全关, 逐项人工开启

D4 高信值收益(档位权益):
    - grade: healthy(≥75)/watch(≥50)/strained(≥30)/critical
    - 修复上限系数按档位上浮(healthy 1.2×/watch 1.0×)
    - 权益菜单注册表: 本批落修复费率+兑换上限两挂点, 其余
      (臻选 α/语音加权/推广阶梯)登记待接

settings 载体: trust45_state 单例 record 的 "p6" 字段
(DEFAULT 合并惯例——存量 state 无该字段自动补默认, 同 80号)。

铁律:
    - 全 fail-soft: 信号/限制/权益异常不阻断业务主流程
    - shadow 留痕保 200 条环形(防膨胀)
    - LLM 禁入: 全确定性阈值
"""

import logging
from datetime import UTC, datetime

from repositories.trust_value_repository import (
    TrustValue45Repository,
)

logger = logging.getLogger(__name__)

SHADOW_KEEP = 200   # shadow 信号留痕上限(环形裁剪)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# P6 settings(限制矩阵 + 权益 + 信号模式; 全默认关/shadow)
# ============================================================

DEFAULT_P6_SETTINGS = {
    # D3 信号入分模式: True=shadow 留痕不改分(默认); False=真入分
    "signalShadow": True,
    # D3 低信值限制矩阵(逐项人工开启)
    "restrictRedeem": False,    # strained 限兑换上限×0.5/critical 拒
    "restrictXinzhi": False,    # 臻选硬闸(登记待挂)
    "restrictHelp": False,      # 互助押金(登记待挂)
    "restrictPromo": False,     # 推广计分冻结(登记待挂)
    # D4 高信值权益(逐项人工开启)
    "benefitRepairAlpha": False,   # healthy 修复上限 ×1.2
    "benefitRedeemCap": False,     # healthy 兑换上限 ×1.5(登记待挂)
}

P6_SETTINGS_WHITELIST = set(DEFAULT_P6_SETTINGS)


# 信号源注册表(已接=挂点已落; 待接=下批逐个翻牌)
SIGNAL_SOURCES = {
    "escrow_forfeit": {
        "label": "80号引进积分观察期作废(僵尸注册信号)",
        "factor": "platform_conduct", "delta": -5,
        "severity": "general", "wired": True},
    "growth_anomaly": {
        "label": "81号绑定速率异常(WARN/FAIL 推荐人)",
        "factor": "platform_conduct", "delta": -3,
        "severity": "general", "wired": True},
    "security_ban": {
        "label": "43号安全封禁/撞库高档", "factor": "platform_conduct",
        "delta": -10, "severity": "severe", "wired": False},
    "refund_abuse": {
        "label": "恶意退款/订单违约终态", "factor": "platform_conduct",
        "delta": -5, "severity": "general", "wired": False},
    "help_breach": {
        "label": "67号互助违约/爽约终态", "factor": "platform_conduct",
        "delta": -5, "severity": "general", "wired": False},
}

# 正向信号(高信值积累, 同一通道)
POSITIVE_SOURCES = {
    "tier_promotion": {
        "label": "80号阶梯高档(精英/王者)", "factor": "longtail_good",
        "delta": 3, "severity": "general", "wired": False},
}


async def get_p6_settings() -> dict:
    """P6 settings(DEFAULT 合并——存量自动补默认)"""
    repo = TrustValue45Repository()
    st = await repo.load_trust45_state() or {}
    merged = dict(DEFAULT_P6_SETTINGS)
    merged.update(st.get("p6") or {})
    return merged


async def update_p6_settings(changes: dict,
                              admin: str = "admin") -> dict:
    """P6 settings 更新(白名单; 非法键拒绝)"""
    bad = {k for k in changes if k not in P6_SETTINGS_WHITELIST}
    if bad:
        raise ValueError(
            f"非法 P6 配置键: {sorted(bad)}"
            f"(合法: {sorted(P6_SETTINGS_WHITELIST)})")
    repo = TrustValue45Repository()
    st = await repo.load_trust45_state() or {}
    st.setdefault("p6", {}).update(changes)
    st["p6UpdatedBy"] = admin
    st["p6UpdatedAt"] = _now_iso()
    await repo.save_trust45_state(st)
    logger.info("trust45_p6_settings_updated by=%s %s", admin,
                {k: v for k, v in changes.items()})
    return await get_p6_settings()


# ============================================================
# D3 信号总线(shadow 默认——留痕不改分)
# ============================================================

async def ingest_signal(trust_id: int, source: str,
                        summary: str = "",
                        delta: float | None = None,
                        factor: str = None,
                        severity: str = None) -> dict:
    """信号源统一入口(全 fail-soft)

    Args:
        source: SIGNAL_SOURCES/POSITIVE_SOURCES 注册键
        delta/factor/severity: 覆盖注册表默认(缺省用注册表)
    Returns:
        {applied: bool, shadow: bool} applied=是否真入分
    """
    try:
        spec = (SIGNAL_SOURCES.get(source)
                or POSITIVE_SOURCES.get(source))
        if spec is None:
            logger.warning("trust_p6_unknown_source %s", source)
            return {"applied": False, "shadow": False}
        settings = await get_p6_settings()
        shadow = bool(settings.get("signalShadow", True))
        payload = {
            "ts": _now_iso(), "trustId": trust_id, "source": source,
            "factor": factor or spec["factor"],
            "delta": (spec["delta"] if delta is None
                      else float(delta)),
            "severity": severity or spec["severity"],
            "summary": summary or spec["label"],
        }
        if shadow:
            # 留痕不改分(观察期——回看误伤面后人工切真入分)
            repo = TrustValue45Repository()
            st = await repo.load_trust45_state() or {}
            bucket = st.setdefault("p6ShadowSignals", [])
            bucket.append(payload)
            if len(bucket) > SHADOW_KEEP:
                del bucket[:-SHADOW_KEEP]
            await repo.save_trust45_state(st)
            logger.info("trust_p6_signal_shadow trustId=%s src=%s",
                        trust_id, source)
            return {"applied": False, "shadow": True}
        # 真入分(record_event: 因子增量+熔断计数+重算, 既有链)
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        await TrustProfileService().record_event(
            trust_id, "L2", payload["factor"], payload["delta"],
            severity=payload["severity"],
            source=f"p6_{source}", summary=payload["summary"])
        logger.info("trust_p6_signal_applied trustId=%s src=%s "
                    "delta=%s", trust_id, source, payload["delta"])
        return {"applied": True, "shadow": False}
    except Exception as exc:  # noqa: BLE001
        logger.warning("trust_p6_signal_skip trustId=%s src=%s: %s",
                       trust_id, source, exc)
        return {"applied": False, "shadow": False, "error": str(exc)[:120]}


async def shadow_signals(trust_id: int = None,
                         limit: int = 50) -> list[dict]:
    """shadow 信号留痕回看(观察期误伤面核查——最新在前)"""
    st = await TrustValue45Repository().load_trust45_state() or {}
    rows = st.get("p6ShadowSignals") or []
    if trust_id is not None:
        rows = [r for r in rows if r.get("trustId") == trust_id]
    return list(reversed(rows[-limit:]))


# ============================================================
# D3 限制矩阵 / D4 档位权益 helper(全 fail-soft)
# ============================================================

async def restrict_check(trust_id: int, biz: str) -> dict:
    """低信值限制检查(settings 关 → 恒放行)

    Args:
        biz: redeem|xinzhi|help|promo
    Returns:
        {restricted: bool, reason: str}
    """
    try:
        settings = await get_p6_settings()
        key = f"restrict{biz[0].upper()}{biz[1:]}"
        if not settings.get(key):
            return {"restricted": False, "reason": ""}
        profile = await TrustValue45Repository().get_profile(
            trust_id)
        grade = str((profile or {}).get("grade") or "watch")
        if biz == "redeem":
            if grade == "critical":
                return {"restricted": True,
                        "reason": "信值档位 critical, 兑换已冻结"
                                  "(信值修复通道可回分)"}
            if grade == "strained":
                return {"restricted": False, "reason": "",
                        "capFactor": 0.5,
                        "note": "信值档位 strained, 兑换上限×0.5"}
        # 其理业务档位口径接入时逐个补(注册表)
        return {"restricted": False, "reason": ""}
    except Exception as exc:  # noqa: BLE101
        logger.warning("trust_p6_restrict_skip %s: %s", trust_id, exc)
        return {"restricted": False, "reason": ""}


async def repair_alpha_factor(trust_id: int) -> float:
    """D4 修复费率: 档位系数(healthy ×1.2 提升, settings 关→1.0)"""
    try:
        settings = await get_p6_settings()
        if not settings.get("benefitRepairAlpha"):
            return 1.0
        profile = await TrustValue45Repository().get_profile(
            trust_id)
        grade = str((profile or {}).get("grade") or "watch")
        return 1.2 if grade == "healthy" else 1.0
    except Exception:  # noqa: BLE101
        return 1.0
