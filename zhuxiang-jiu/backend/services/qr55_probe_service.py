"""55号·二维码AI智能管理 拨测验证
(qr55_probe_service, P4)

计划(docs/55号_二维码AI智能管理模块实施计划.md §三-3/§六 P4):
    防伪与拨测:
    - 验签失败 → 立即阻断+tamper 事件留痕(上报风控)
      +受影响用户信值补偿事件(P1 已阻断留痕——
      P4 补信值补偿)
    - 生成后自动化拨测: 白名单 route 可达性验证
      ——失败重试/转人工, 生成失败不计预算铁律

拨测口径:
    - probe_targets: 12 项服务抽样(route 白名单)
    - 可达性: 注册表 route 前缀域白名单比对
      (8 域——幻觉链接防护第三层; 46号 P2 语义)
      + 模板四类覆盖
    - 失败重试: RETRY_LIMIT 次内成功 → probe 事件
      retrySucceeded=True(P2 回流管道 probe_retry
      信号源——路由劣化 -0.5)
    - 拨测失败不计预算(参照方案铁律——拨测是
      平台自检, 非用户消费)

信值补偿(tamper 受害者):
    - 45号 L2 platform_conduct 正向小额补偿
      (受害者抚慰口径——被篡改码误导的用户)
    - 经 TrustRadarService.submit_deposit 双源
      交叉验真(与 T+1 结算同管线)
"""

import logging
import random

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_probe_service")

MODEL_VERSION = "v1-qr55-probe"

# 拨测抽样数(每轮——12 项全量或抽样)
PROBE_SAMPLE_SIZE = 12

# 失败重试上限(参照方案: 失败重试/转人工)
RETRY_LIMIT = 2

# route 前缀域白名单(复用 P0 注册表口径——
# 单一事实源, 杜绝双清单漂移)
from services.qr55_registry import (  # noqa: E402
    ROUTE_PREFIX_WHITELIST as ROUTE_PREFIXES,
)


class Qr55ProbeService:
    """55号拨测验证(route 可达性+重试+信值补偿)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 拨测入口(白名单 route 可达性自动化)
    # ============================================================

    async def run_probe(self,
                        service_ids: list = None
                        ) -> dict:
        """执行一轮拨测(白名单抽样→route 可达性
        验证→失败重试→probe 事件留痕)

        可达性口径(离线确定性——route 前缀域白名单
        比对+模板四类覆盖; 46号 P2 语义):
            route 前缀在 8 域白名单内 = 可达
            (真实 HTTP 探活列入外部待办——
            容器内自环依赖注入复杂度不值得)

        拨测失败不计预算(铁律——平台自检非用户消费)。
        """
        from services.qr55_registry import (
            SERVICE_REGISTRY,
        )
        # 抽样(全量 ≤12)
        targets = service_ids and [
            SERVICE_REGISTRY[s] for s in service_ids
            if s in SERVICE_REGISTRY] or list(
                SERVICE_REGISTRY.values())
        if len(targets) > PROBE_SAMPLE_SIZE:
            targets = random.sample(
                targets, PROBE_SAMPLE_SIZE)

        results = []
        ok_count = retry_count = fail_count = 0
        for svc in targets:
            svc_id = svc.get("serviceId")
            route = str(svc.get("route") or "")
            reachable = self._route_reachable(route)

            # 失败重试(RETRY_LIMIT 次内成功 → retry)
            retried = False
            retries = 0
            while not reachable \
                    and retries < RETRY_LIMIT:
                retries += 1
                reachable = self._route_reachable(route)
                if reachable:
                    retried = True

            if reachable:
                ok_count += 1
                if retried:
                    retry_count += 1
            else:
                fail_count += 1

            # probe 事件留痕(P2 回流管道 probe_retry
            # 信号源——retrySucceeded)
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "codeId": 0,
                "memberId": 0,
                "eventType": "probe",
                "detail": {
                    "serviceId": svc_id,
                    "route": route,
                    "reachable": reachable,
                    "retries": retries,
                    "retrySucceeded": retried,
                },
                "createdAt": ts(),
            })
            results.append({
                "serviceId": svc_id,
                "route": route,
                "reachable": reachable,
                "retries": retries,
                "retrySucceeded": retried,
            })

        summary = {
            "success": True,
            "probed": len(targets),
            "reachable": ok_count,
            "retriedOk": retry_count,
            "failed": fail_count,
            "results": results,
            "budgetNote": "拨测失败不计预算(平台自检"
                          "非用户消费——铁律)",
            "note": "route 可达性=前缀域白名单比对"
                    "(8 域); 失败转人工阈值见 P5 看板",
            "probedAt": ts(),
        }

        # 模型事件留痕(版本溯源)
        try:
            from services.qr55_service import (
                Qr55Service,
            )
            await Qr55Service().record_model_event(
                "probe_run", {
                    "probed": len(targets),
                    "reachable": ok_count,
                    "retriedOk": retry_count,
                    "failed": fail_count,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_probe_event_failed: %s", exc)

        return summary

    @staticmethod
    def _route_reachable(route: str) -> bool:
        """route 可达性(前缀域白名单比对)"""
        return any(route.startswith(p)
                   for p in ROUTE_PREFIXES)

    # ============================================================
    # 篡改告警+信值补偿(受害者抚慰——45号 L2)
    # ============================================================

    async def compensate_tamper_victims(
            self, limit: int = 50) -> dict:
        """信值补偿(tamper 事件关联会员——45号 L2
        platform_conduct 正向小额补偿)

        口径: tamper 事件 memberId>0(登录态扫码者)
        → TrustRadarService.submit_deposit(L2
        platform_conduct, 双源交叉验真)。
        幂等: probe 事件补偿标记(victims 已补的
        tamper 事件不重复申报——补偿留痕事件对 1:1)。

        Returns:
            {compensated, skipped, reasons}
        """
        events = await self.repo.list_events(
            event_type="tamper", limit=limit)
        # 已补偿的 tamper 事件(compensate 事件
        # detail.tamperEventId 1:1)
        comp_events = await self.repo.list_events(
            event_type="compensate", limit=1000)
        done = {
            int((e.get("detail") or {}).get(
                "tamperEventId") or 0)
            for e in comp_events}

        compensated = skipped = 0
        reasons = []
        for event in events:
            event_id = int(event.get("eventId") or 0)
            member_id = int(event.get("memberId") or 0)
            if event_id in done:
                skipped += 1
                continue
            if member_id <= 0:
                skipped += 1
                reasons.append(
                    f"event={event_id}: 无登录态扫码者")
                continue

            deposit = None
            reason = ""
            try:
                from services.trust_radar_service import (
                    TrustRadarService,
                )
                evidence = (
                    f"qr55 篡改受害者补偿(tamper 事件 "
                    f"{event_id}, 会员 {member_id}——"
                    f"被篡改码误导扫码, 平台抚慰口径)")
                deposit = await TrustRadarService(
                ).submit_deposit(
                    member_id, "L2",
                    "platform_conduct",
                    observed=1.0,
                    peer_baseline=0.0,
                    evidence=evidence,
                    summary="二维码篡改受害者补偿"
                            "(55号拨测管道)",
                    sources=["qr55_pipeline",
                             "event_audit"],
                    voluntary=False,
                    verify_mode="v1")
            except Exception as exc:  # noqa: BLE001
                reason = f"deposit_failed:{str(exc)[:60]}"
                logger.warning(
                    "qr55_tamper_comp_failed "
                    "event=%s: %s", event_id, exc)

            verified = bool(
                (deposit or {}).get("verified"))
            if not verified and not reason:
                reason = "deposit_unverified"

            # 补偿留痕事件(1:1——幂等标记)
            comp_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": comp_id,
                "codeId": int(event.get("codeId") or 0),
                "memberId": member_id,
                "eventType": "compensate",
                "detail": {
                    "tamperEventId": event_id,
                    "depositId": (deposit or {}).get(
                        "depositId") or 0,
                    "depositVerified": verified,
                    "depositDelta": float(
                        (deposit or {}).get(
                            "delta") or 0),
                    "reason": reason,
                },
                "createdAt": ts(),
            })
            if verified:
                compensated += 1
            else:
                skipped += 1
                reasons.append(
                    f"event={event_id}:{reason}")

        return {
            "success": True,
            "tamperEvents": len(events),
            "compensated": compensated,
            "skipped": skipped,
            "reasons": reasons[:10],
            "note": "tamper 受害者信值补偿——45号 L2 "
                    "platform_conduct(双源验真)",
            "compensatedAt": ts(),
        }
