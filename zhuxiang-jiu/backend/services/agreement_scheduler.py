"""条款协议大模型 护栏自动巡检调度器

大模型二代(全站范式——PDM/智图/信用同款):
    AGREEMENT_GUARD_AUTO 默认 on(护栏是保护机制)
    AGREEMENT_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    条款停用率 agreementInactiveRate
        = inactive / 已决条款(published+inactive)
          (已生效条款被停用——合规失效恶化代理;
           流程态 draft/reviewing 不进分母)
    协议停用率 protocolInactiveRate
        = inactive / 已决协议(active+inactive)
          (角色协议配置失效恶化代理)
    版本失配率 versionMismatchRate
        = 旧版同意记录 / 总同意记录
          (条款更新后用户未重签——法律风险恶化代理)

聚合口径: agreement_repository list_agreements/
list_consents/list_protocols(上限 1000) 确定性聚合。

run_guard_patrol 可独立调用(控制面
POST /api/agreements/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None

# 巡检聚合上限(防无限膨胀)
_PATROL_LIST_CAP = 1000


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(AGREEMENT_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "AGREEMENT_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "AGREEMENT_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        条款停用率 = inactive / (published+inactive)
        协议停用率 = inactive / (active+inactive)
        版本失配率 = 旧版同意 / 总同意
    分母为 0 时指标取 0(冷启动不误判)。
    """
    from repositories.agreement_repository import (
        AgreementRepository,
        AGREEMENT_STATUS_PUBLISHED,
        AGREEMENT_STATUS_INACTIVE,
        PROTOCOL_STATUS_ACTIVE,
        PROTOCOL_STATUS_INACTIVE,
    )
    from services.agreement_mode_service import (
        AgreementModeService,
    )
    repo = AgreementRepository()

    agreements = (await repo.list_agreements(
        limit=_PATROL_LIST_CAP))
    protocols = (await repo.list_protocols(
        limit=_PATROL_LIST_CAP))
    consents = (await repo.list_consents(
        limit=_PATROL_LIST_CAP))
    # fail-soft 过滤: 生产曾见列表混入非 dict 行(int)致
    # 巡检崩(guard_patrol_fail 'int' object has no attribute
    # 'get')——巡检跳过坏行不阻断(巡检本义即采样聚合)
    agreements = [a for a in agreements if isinstance(a, dict)]
    protocols = [p for p in protocols if isinstance(p, dict)]
    consents = [c for c in consents if isinstance(c, dict)]

    # 条款停用率: 已决 = published + inactive
    # (流程态 draft/reviewing 不进分母)
    agree_published = sum(
        1 for a in agreements
        if (a.get("status") or "")
        == AGREEMENT_STATUS_PUBLISHED)
    agree_inactive = sum(
        1 for a in agreements
        if (a.get("status") or "")
        == AGREEMENT_STATUS_INACTIVE)
    agree_settled = agree_published + agree_inactive

    # 协议停用率: 已决 = active + inactive
    proto_active = sum(
        1 for p in protocols
        if (p.get("status") or "")
        == PROTOCOL_STATUS_ACTIVE)
    proto_inactive = sum(
        1 for p in protocols
        if (p.get("status") or "")
        == PROTOCOL_STATUS_INACTIVE)
    proto_settled = proto_active + proto_inactive

    # 版本失配率: 同意记录版本 != 条款当前版本
    version_by_id = {
        a.get("id"): a.get("currentVersion")
        for a in agreements
    }
    consent_total = len(consents)
    consent_mismatch = sum(
        1 for c in consents
        if version_by_id.get(c.get("agreementId"))
        and c.get("version")
        != version_by_id[c.get("agreementId")])

    agreement_inactive_rate = (
        agree_inactive / agree_settled
        if agree_settled > 0 else 0.0)
    protocol_inactive_rate = (
        proto_inactive / proto_settled
        if proto_settled > 0 else 0.0)
    version_mismatch_rate = (
        consent_mismatch / consent_total
        if consent_total > 0 else 0.0)

    guard = await AgreementModeService() \
        .guard_check(
            agreement_inactive_rate,
            protocol_inactive_rate,
            version_mismatch_rate)
    return {
        "metrics": {
            "agreementInactiveRate": round(
                agreement_inactive_rate, 4),
            "protocolInactiveRate": round(
                protocol_inactive_rate, 4),
            "versionMismatchRate": round(
                version_mismatch_rate, 4),
        },
        "samples": {
            "agreements": {
                "total": len(agreements),
                "published": agree_published,
                "inactive": agree_inactive},
            "protocols": {
                "total": len(protocols),
                "active": proto_active,
                "inactive": proto_inactive},
            "consents": {
                "total": consent_total,
                "versionMismatch": consent_mismatch},
        },
        "breached": guard.get("breached"),
        "pausedNow": guard.get("pausedNow"),
        "breaches": guard.get("breaches")
        or [],
    }


async def _guard_loop() -> None:
    """护栏巡检循环(整轮异常不退出)"""
    while True:
        try:
            r = await run_guard_patrol()
            if r.get("pausedNow"):
                logger.warning(
                    "agreement_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "agreement_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "agreement_guard_patrol_fail: %s",
                exc)
        await asyncio.sleep(
            guard_interval_seconds())


def start_guard_loop() -> bool:
    """启动护栏巡检循环(幂等;
    未启用返回 False)"""
    if not guard_patrol_enabled():
        return False
    global _GUARD_TASK
    if _GUARD_TASK and not _GUARD_TASK.done():
        return True
    _GUARD_TASK = asyncio.get_event_loop() \
        .create_task(_guard_loop())
    logger.info("agreement_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True
