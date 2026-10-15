# -*- coding: utf-8 -*-
"""IMTM 动态定价因子·最小闭环 v1(observe 留痕 + M_game 三档留存乘数)

蓝图: R_final = R_base × f(Q_user) × f(S_demand) × f(P_risk) × M_game
对照分析(docs/会员流量智能模块蓝图与现有实现对照分析.md §四):
    R_base      = escrow.points                          (现成)
    M_game      = T+7 留存三档乘数 1.2/1.0/0.8            (本模块实算)
    f(S_demand) = 推广者近7天 escrow 作废率线性惩罚         (轻量供给·留痕 only)
    f(Q_user)   = 1.0 占位(P3 个体化后启用)
    f(P_risk)   = 1.0 占位(P3 设备闸作弊分接入)

留存三档(§4 M_game 行, Escrow 同构升级):
    gold  1.2  首笔订单(付费级留存信号)
    base  1.0  注册后回访登录 / login_count ≥ 3(与 v2-E3 达标口径等价)
    weak  0.8  login_count 1-2 且无订单无回访(低留存降权档——
               observe 下仍 forfeited 仅留痕, enforce 下解冻 80%)
    none  0    观察期零信号(维持 v2-E3 没收)

模式闸 GROWTH_ESCROW_MULTIPLIER=off|observe|enforce(默认 observe):
    off      完全跳过, 行为与 v2-E3 bit 级一致(性能兜底)
    observe  因子计算+快照写入 escrow.factors+结构化日志, 结算金额不变
    enforce  M_game 乘数生效: unlock_points = round(points × multiplier);
             weak 档转为解冻 80%(f(Q_user)/f(S_demand)/f(P_risk) 恒不生效)
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

SCHEMA = "imtm_pricing_v1"
TIERS = {"gold": 1.2, "base": 1.0, "weak": 0.8, "none": 0.0}
DEMAND_MIN_SAMPLE = 5     # f(S_demand) 最小样本量(小样本跳过=1.0)
DEMAND_FLOOR = 0.5        # f(S_demand) 下限(作废率 50%+ 时减半封顶)


def multiplier_mode() -> str:
    """模式闸(默认 observe——留痕无副作用; enforce 需显式开启)"""
    mode = os.environ.get("GROWTH_ESCROW_MULTIPLIER", "observe")
    mode = mode.strip().lower()
    return mode if mode in ("off", "observe", "enforce") else "observe"


async def classify_retention(escrow: dict, settings: dict) -> dict:
    """被邀人 T+7 留存分档(独立实现, 不触碰 v2-E3 的 _invitee_active)

    Returns: {tier, multiplier, eligible_now, eligible_enforced, signal}
        eligible_now      = tier in (gold, base)  —— observe/off 判定口径
                            (与 v2-E3 三信号达标完全等价)
        eligible_enforced = tier != none          —— enforce 判定口径
                            (weak 档由没收转 80% 解冻)
    """
    ref_id = str(escrow.get("refId") or "")
    try:
        invitee_id = int(ref_id.rsplit(":", 1)[-1])
    except ValueError:
        return {"tier": "none", "multiplier": 0.0,
                "eligible_now": False, "eligible_enforced": False,
                "signal": "refId 解析失败"}

    has_order = False
    if settings.get("escrowUnlockOrder", True):
        try:
            from repositories.order_repository import OrderRepository
            has_order = bool(await OrderRepository().get_by_member(invitee_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("pricing_order_check_skip: %s", exc)

    revisited = False
    login_count = 0
    try:
        from repositories.member_repository import MemberRepository
        member = await MemberRepository().get_by_id(invitee_id)
        last_login = str((member or {}).get("last_login_at") or "")
        created = str(escrow.get("createdAt") or "")
        revisited = bool(last_login and created and last_login > created)
        login_count = int((member or {}).get("login_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pricing_revisit_check_skip: %s", exc)

    if has_order:
        tier, signal = "gold", "首笔订单"
    elif revisited or login_count >= 3:
        tier, signal = "base", (
            f"注册后回访(login={login_count})" if revisited
            else f"登录{login_count}次")
    elif login_count >= 1:
        tier, signal = "weak", f"弱留存(login={login_count}, 无回访无订单)"
    else:
        tier, signal = "none", "观察期零信号"
    m = TIERS[tier]
    return {"tier": tier, "multiplier": m,
            "eligible_now": tier in ("gold", "base"),
            "eligible_enforced": tier != "none", "signal": signal}


async def demand_factor(repo, escrow: dict) -> float:
    """f(S_demand): 推广者近 7 天 escrow 作废率线性惩罚(留痕 only)

    作废率是流量质量的直接代理信号(高作废=该渠道/推广者流量掺水);
    样本 < DEMAND_MIN_SAMPLE 时跳过(返回 1.0, 防小样本抖动)。
    """
    try:
        week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        rows = await repo.list_escrows(
            user_id=int(escrow.get("userId", 0)), limit=200)
        settled = [e for e in rows
                   if e.get("state") in ("unlocked", "forfeited")
                   and str(e.get("settledAt") or "") >= week_ago]
        if len(settled) < DEMAND_MIN_SAMPLE:
            return 1.0
        forfeited = len([e for e in settled if e.get("state") == "forfeited"])
        return max(DEMAND_FLOOR, 1.0 - forfeited / len(settled))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pricing_demand_skip: %s", exc)
        return 1.0


async def compute_factors(repo, escrow: dict, cls: dict) -> dict:
    """五因子快照(写入 escrow.factors 的完整载荷)

    enforce 生效面仅 M_game(final_multiplier = m_game.multiplier);
    其余因子留痕备 P3 启用, 不参与分值计算。
    """
    mode = multiplier_mode()
    base_points = int(escrow.get("points", 0) or 0)
    m_game = float(cls["multiplier"])
    f_sd = await demand_factor(repo, escrow)
    final_m = m_game if mode == "enforce" else 1.0
    unlock_pts = round(base_points * final_m) if cls["eligible_enforced"] \
        or cls["eligible_now"] else 0
    # observe: 实发原值(达标时); would_be = enforce 假想值(供 D+N 复盘分布)
    actual_pts = base_points if cls["eligible_now"] else 0
    would_be = round(base_points * m_game) if cls["tier"] != "none" else 0
    return {
        "schema": SCHEMA,
        "mode": mode,
        "rBase": base_points,
        "fQUser": 1.0,          # P3 占位(个体 pLTV 分箱后启用)
        "fSDemand": round(f_sd, 3),
        "fPRisk": 1.0,          # P3 占位(设备闸作弊分接入)
        "mGame": {"tier": cls["tier"], "multiplier": m_game,
                  "signal": cls["signal"]},
        "finalMultiplier": final_m,
        "unlockPoints": unlock_pts if mode == "enforce" else actual_pts,
        "wouldBePoints": would_be,
        "ts": datetime.now(UTC).isoformat(),
    }


def log_snapshot(escrow_id, factors: dict) -> None:
    """结构化留痕日志(D+N 复盘的采集面之一, 与 escrow.factors 双写)"""
    mg = factors.get("mGame") or {}
    logger.info(
        "imtm_pricing_snapshot escrow=%s mode=%s tier=%s m_game=%s "
        "f_s_demand=%s r_base=%s unlock=%s would_be=%s",
        escrow_id, factors.get("mode"), mg.get("tier"), mg.get("multiplier"),
        factors.get("fSDemand"), factors.get("rBase"),
        factors.get("unlockPoints"), factors.get("wouldBePoints"))
