"""71号·AI智能支付端口大模型 端口自愈编排服务
(pay71_p1_service, P1)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P1 / §4.1):
    ① 前兆滚动窗口评分(多信号去重叠加
       ——确定性权重查表, LLM 禁入)
    ② 保护方向自愈编排(degraded 降权
       →broken 熔断摘除→恢复; 触发窗口/
       动作/恢复证据全留痕)
    ③ 半开探测(broken 态连续 N 次探针
       成功→恢复 healthy——恢复证据链)
    ④ 影子冷启动(纳管→7 天影子期只观测
       →转正建议书→admin 终审→active;
       摘牌对称走建议书)
    ⑤ 自愈轨迹视图(可审计)

铁律(规划 §4.1/§1.2):
    - 熔断/恢复为保护方向可自动(全留痕
      ——触发窗口/动作/恢复证据三要素)
    - 端口正式启停(转正/摘牌)永远走
      建议书+admin 终审, 永不自动
    - frozen 人工专属铁律继承(69号 P0
      惯例——71号编排不消费 frozen 触发)
    - 保护面(编排/探测)与人工面(纳管/
      建议书)不受 PAY71_MODE 影响——
      决策面(调配建议)自 P3 引入
"""

import logging
from datetime import UTC, datetime

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    PORT_STATE_FACTORS,
    PRECURSOR_ALERT_THRESHOLD,
    PRECURSOR_FUSE_THRESHOLD,
    PRECURSOR_WEIGHTS,
    PROBE_REQUIRED_SUCCESSES,
    PROPOSAL_KINDS, SELFHEAL_ACTIONS,
    SELFHEAL_WINDOW_SIZE, SHADOW_DAYS,
    _port_registry, MODEL_VERSION,
    current_mode, is_kill,
)

logger = logging.getLogger("pay71_p1_service")


def _days_elapsed(start_iso: str) -> float:
    """影子期已过天数(UTC ISO 口径——
    确定性计算)"""
    start = datetime.fromisoformat(start_iso)
    now = datetime.now(UTC)
    return round(
        (now - start).total_seconds() / 86400,
        4)


class Pay71P1Service:
    """71号端口自愈编排(P1)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ① 前兆滚动窗口评分(确定性)
    # ============================================================

    async def precursor_window(
            self, port_id: str) -> dict:
        """前兆滚动窗口(每端口最近 N 条信号
        ——多信号类型去重叠加, 确定性
        权重查表; 同窗口同分可复现)

        Raises:
            KeyError: 端口域外
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        signals = await self.repo.list_signals(
            limit=SELFHEAL_WINDOW_SIZE,
            port_id=port_id)
        types = sorted({
            s["signal"] for s in signals
            if s.get("signal")})
        score = min(sum(
            PRECURSOR_WEIGHTS[t]
            for t in types), 1.0)
        return {
            "portId": port_id,
            "windowSize": SELFHEAL_WINDOW_SIZE,
            "signalCount": len(signals),
            "signalTypes": types,
            "windowScore": round(score, 4),
            "alertThreshold":
                PRECURSOR_ALERT_THRESHOLD,
            "fuseThreshold":
                PRECURSOR_FUSE_THRESHOLD,
            "engine": "rule_based",
        }

    # ============================================================
    # ② 保护方向自愈编排(状态机——确定性)
    # ============================================================

    async def _pay69_state_map(self) -> dict:
        """69号健康度快照(只读消费——叠加
        铁律, 永不写 69号表)"""
        from services.pay69_p0_service import (
            Pay69P0Service,
        )
        base = await Pay69P0Service()\
            .health_view()
        return {
            c["channelId"]: c.get(
                "state", "healthy")
            for c in base.get("channels", [])
        }

    async def _orchestrate_port(
            self, port_id: str,
            pay69_state: str) -> dict:
        """单端口保护方向评估+执行

        状态机(确定性——严重度单调):
            窗口叠加≥熔断阈值 0.80 → broken
            窗口叠加≥0.50 或 69号 critical
                → degraded(自 healthy)
            窗口叠加<0.50 且 69号非 critical
                → healthy(自 degraded 恢复;
                broken 仅经半开探测恢复)
        """
        window = await self.precursor_window(
            port_id)
        score = window["windowScore"]
        current = (await self.repo.get_port(
            port_id) or {}).get(
            "portState", "healthy")
        # 目标态判定(确定性)
        if score >= PRECURSOR_FUSE_THRESHOLD:
            target = "broken"
        elif (score >= PRECURSOR_ALERT_THRESHOLD
              or pay69_state == "critical"):
            target = "degraded"
        else:
            target = "healthy"
        # broken 仅经半开探测恢复——编排
        # 不直接降级 broken→healthy
        if current == "broken" \
                and target != "broken":
            target = "broken"
        action = "no_change"
        if target != current:
            if target == "broken":
                action = "fuse"
            elif target == "degraded":
                action = "degrade"
            else:
                action = "recover"
            record = dict(await self.repo.get_port(
                port_id) or {})
            record.update({
                "portId": port_id,
                "portState": target,
                "stateFactor": PORT_STATE_FACTORS[
                    target],
                "stateAt": ts(),
            })
            await self.repo.save_port(
                port_id, record)
            # 全留痕(触发窗口/动作/证据)
            await self.repo.save_trace({
                "portId": port_id,
                "action": action,
                "fromState": current,
                "toState": target,
                "trigger": {
                    "windowScore": score,
                    "signalTypes":
                        window["signalTypes"],
                    "pay69State": pay69_state,
                },
                "evidence": {
                    "alertThreshold":
                        PRECURSOR_ALERT_THRESHOLD,
                    "fuseThreshold":
                        PRECURSOR_FUSE_THRESHOLD,
                },
                "at": ts(),
            })
            await self.repo.save_event({
                "type": "selfheal_transition",
                "portId": port_id,
                "detail": {
                    "action": action,
                    "from": current,
                    "to": target,
                    "score": score,
                },
                "at": ts(),
            })
        return {
            "portId": port_id,
            "action": action,
            "fromState": current,
            "toState": target,
            "windowScore": score,
            "signalTypes": window["signalTypes"],
            "pay69State": pay69_state,
        }

    async def orchestrate(
            self, port_id: str = None) -> dict:
        """保护方向自愈编排(纳管端口
        评估+执行——全留痕; 不受
        PAY71_MODE/PAY71_KILL 影响:
        保护方向永续铁律)

        未纳管(ungoverned/offboarded)端口
        跳过评估(纳管走 onboard 人工轨)

        Raises:
            KeyError: 指定端口域外
        """
        targets: list[str]
        if port_id is not None:
            if port_id not in _port_registry():
                raise KeyError(
                    f"端口不存在(portId="
                    f"{port_id})")
            targets = [port_id]
        else:
            targets = list(_port_registry())
        pay69_map = await self._pay69_state_map()
        results = []
        skipped = []
        for pid in targets:
            shadow = await self.repo.get_shadow(
                pid)
            governance = (shadow or {}).get(
                "governance", "ungoverned")
            if governance not in (
                    "shadow", "active"):
                skipped.append({
                    "portId": pid,
                    "governance": governance,
                    "reason": "未纳管(编排仅"
                              "作用于 shadow/"
                              "active 态)",
                })
                continue
            results.append(
                await self._orchestrate_port(
                    pid, pay69_map.get(
                        pid, "healthy")))
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "evaluated": len(results),
            "skipped": len(skipped),
            "results": results,
            "skippedPorts": skipped,
            "actions": list(SELFHEAL_ACTIONS),
            "protectivePlane": "unconditional"
                              "(保护方向永续)",
            "orchestratedAt": ts(),
        }

    # ============================================================
    # ③ 半开探测(broken 态恢复证据链)
    # ============================================================

    async def probe(self, port_id: str,
                    success: bool) -> dict:
        """半开探针(broken 态端口专用——
        连续 N 次成功→恢复 healthy;
        失败清零重计; 恢复证据链全留痕)

        保护方向动作(不受开关影响)

        Raises:
            KeyError: 端口域外
            ValueError: 非 broken 态端口
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        existing = await self.repo.get_port(
            port_id) or {}
        current = existing.get(
            "portState", "healthy")
        if current != "broken":
            raise ValueError(
                f"半开探测仅适用于 broken 态"
                f"端口(当前 {current})")
        streak = int(existing.get(
            "probeStreak", 0) or 0)
        streak = streak + 1 if success else 0
        recovered = (success
                     and streak
                     >= PROBE_REQUIRED_SUCCESSES)
        record = dict(existing)
        record.update({
            "portId": port_id,
            "probeStreak": streak,
            "probeRequired":
                PROBE_REQUIRED_SUCCESSES,
        })
        probe_record = {
            "portId": port_id,
            "probeSuccess": bool(success),
            "probeStreak": streak,
            "probeRequired":
                PROBE_REQUIRED_SUCCESSES,
            "recovered": recovered,
            "probedAt": ts(),
        }
        await self.repo.save_probe(probe_record)
        action = "probe_pass"
        if recovered:
            action = "recover"
            record.update({
                "portState": "healthy",
                "stateFactor": PORT_STATE_FACTORS[
                    "healthy"],
                "stateAt": ts(),
                "probeStreak": 0,
            })
            # 恢复证据: 最近 N 次探针全成功
            probes = await self.repo.list_probes(
                port_id=port_id,
                limit=PROBE_REQUIRED_SUCCESSES)
            await self.repo.save_trace({
                "portId": port_id,
                "action": action,
                "fromState": "broken",
                "toState": "healthy",
                "trigger": {
                    "probeStreak": streak,
                    "required":
                        PROBE_REQUIRED_SUCCESSES,
                },
                "evidence": {
                    "probeSeqs": [
                        p.get("probeSeq")
                        for p in probes],
                    "allSuccess": all(
                        p.get("probeSuccess")
                        for p in probes),
                },
                "at": ts(),
            })
        elif not success:
            action = "probe_fail"
            await self.repo.save_trace({
                "portId": port_id,
                "action": action,
                "fromState": "broken",
                "toState": "broken",
                "trigger": {
                    "probeStreak": 0,
                    "required":
                        PROBE_REQUIRED_SUCCESSES,
                },
                "evidence": {
                    "consecutiveBroken":
                        "探针失败清零重计"
                        "(半开保护)",
                },
                "at": ts(),
            })
        await self.repo.save_port(
            port_id, record)
        await self.repo.save_event({
            "type": "selfheal_probe",
            "portId": port_id,
            "detail": {
                "success": bool(success),
                "streak": streak,
                "recovered": recovered,
            },
            "at": ts(),
        })
        return {
            "portId": port_id,
            "action": action,
            "probeSuccess": bool(success),
            "probeStreak": streak,
            "probeRequired":
                PROBE_REQUIRED_SUCCESSES,
            "recovered": recovered,
            "portState": record["portState"],
        }

    # ============================================================
    # ④ 影子冷启动(纳管生命周期——人工轨)
    # ============================================================

    async def onboard(self, port_id: str) -> dict:
        """端口纳管(影子冷启动入口——人工
        动作; 7 天影子期只观测不参与调配,
        达标后出转正建议书)

        允许重纳管(offboarded→shadow)

        Raises:
            KeyError: 端口域外
            ValueError: 已在管(shadow/active)
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        existing = await self.repo.get_shadow(
            port_id)
        if existing and existing.get(
                "governance") in (
                "shadow", "active"):
            raise ValueError(
                f"端口已在管(governance="
                f"{existing.get('governance')}"
                f"——先经建议书摘牌方可重纳管)")
        record = dict(existing or {})
        record.update({
            "portId": port_id,
            "governance": "shadow",
            "onboardedAt": ts(),
            "shadowStart": ts(),
            "promotedAt": "",
            "offboardedAt": "",
            "onboardCount": int(
                (existing or {}).get(
                    "onboardCount", 0) or 0) + 1,
        })
        await self.repo.save_shadow(
            port_id, record)
        await self.repo.save_event({
            "type": "port_onboard",
            "portId": port_id,
            "detail": {
                "governance": "shadow",
                "shadowDays": SHADOW_DAYS,
            },
            "at": ts(),
        })
        return record

    async def shadow_view(self) -> dict:
        """影子冷启动视图(纳管端口全景
        ——天数/达标判定/观测面)"""
        shadows = await self.repo.list_shadows()
        views = []
        for rec in shadows:
            days = _days_elapsed(
                rec.get("shadowStart") or ts())
            port = await self.repo.get_port(
                rec["portId"]) or {}
            port_state = port.get(
                "portState", "healthy")
            window = await self.precursor_window(
                rec["portId"])
            views.append({
                "portId": rec["portId"],
                "governance": rec.get(
                    "governance", "shadow"),
                "daysElapsed": days,
                "shadowDays": SHADOW_DAYS,
                "daysMet": days >= SHADOW_DAYS,
                "portState": port_state,
                "windowScore":
                    window["windowScore"],
                # 达标口径(确定性): 7 天期满
                # 且非 broken(熔断中不可转正)
                "eligible": (
                    days >= SHADOW_DAYS
                    and port_state != "broken"),
                "onboardCount": rec.get(
                    "onboardCount", 1),
            })
        return {
            "modelVersion": MODEL_VERSION,
            "shadowDays": SHADOW_DAYS,
            "count": len(views),
            "shadows": views,
        }

    async def propose(self, port_id: str,
                      kind: str) -> dict:
        """转正/摘牌建议书发起(端口正式
        启停永不自动铁律——proposed→
        admin 终审)

        Args:
            kind: promote(转正 shadow→
                active)/offboard(摘牌
                active→offboarded)

        Raises:
            KeyError: 端口域外
            ValueError: 种类域外/前置状态
                不满足/存在待审建议书
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        if kind not in PROPOSAL_KINDS:
            raise ValueError(
                f"建议书种类域外(kind={kind}, "
                f"域={PROPOSAL_KINDS})")
        shadow = await self.repo.get_shadow(
            port_id)
        governance = (shadow or {}).get(
            "governance", "ungoverned")
        pending = [
            p for p in await
            self.repo.list_proposals(
                port_id=port_id, limit=100)
            if p.get("state") == "proposed"]
        if pending:
            raise ValueError(
                f"存在待审建议书("
                f"proposalSeq="
                f"{pending[0]['proposalSeq']}"
                f"——先终审再发起新建议)")
        if kind == "promote":
            if governance != "shadow":
                raise ValueError(
                    f"转正前置失败(governance="
                    f"{governance}, 需 shadow)")
            days = _days_elapsed(
                shadow.get("shadowStart") or ts())
            port = await self.repo.get_port(
                port_id) or {}
            port_state = port.get(
                "portState", "healthy")
            if days < SHADOW_DAYS:
                raise ValueError(
                    f"影子期未满({days}天"
                    f"/{SHADOW_DAYS}天)")
            if port_state == "broken":
                raise ValueError(
                    "熔断中端口不可转正"
                    "(broken 态——先恢复)")
            evidence = {
                "daysElapsed": days,
                "shadowDays": SHADOW_DAYS,
                "portState": port_state,
            }
            disposition = {
                "proposedAction":
                    "shadow→active(转正参与"
                    "调配——P3 消费)",
                "expectedGain":
                    "端口纳入调配建议候选池",
                "riskAssessment": "low"
                "(影子期满+非熔断)",
                "rollbackPlan":
                    "摘牌建议书可回退"
                    "offboarded 态",
            }
        else:
            if governance != "active":
                raise ValueError(
                    f"摘牌前置失败(governance="
                    f"{governance}, 需 active)")
            evidence = {
                "governance": governance,
            }
            disposition = {
                "proposedAction":
                    "active→offboarded(摘牌"
                    "退出调配池)",
                "expectedGain":
                    "端口退出调配候选池",
                "riskAssessment": "low"
                "(人工终审轨)",
                "rollbackPlan":
                    "重纳管(onboard)可回退"
                "shadow 态",
            }
        record = {
            "portId": port_id,
            "kind": kind,
            "state": "proposed",
            "evidence": evidence,
            "disposition": disposition,
            "proposedAt": ts(),
            "decidedAt": "",
            "decidedBy": "",
            "engine": "rule_based",
        }
        await self.repo.save_proposal(record)
        await self.repo.save_event({
            "type": "proposal_created",
            "portId": port_id,
            "detail": {"kind": kind},
            "at": ts(),
        })
        return record

    async def decide(self, proposal_seq: int,
                     approve: bool) -> dict:
        """建议书终审(admin 人工——不受
        开关影响; 端口启停生效唯一入口)

        Raises:
            KeyError: 建议书不存在
            ValueError: 建议书非待审态
        """
        proposal = await self.repo.get_proposal(
            int(proposal_seq))
        if proposal is None:
            raise KeyError(
                f"建议书不存在(proposalSeq="
                f"{proposal_seq})")
        if proposal.get("state") != "proposed":
            raise ValueError(
                f"建议书已终审(state="
                f"{proposal.get('state')})")
        port_id = proposal["portId"]
        kind = proposal["kind"]
        if approve:
            shadow = await self.repo.get_shadow(
                port_id) or {}
            if kind == "promote":
                shadow.update({
                    "governance": "active",
                    "promotedAt": ts(),
                })
                gov_after = "active"
            else:
                shadow.update({
                    "governance": "offboarded",
                    "offboardedAt": ts(),
                })
                gov_after = "offboarded"
            await self.repo.save_shadow(
                port_id, shadow)
            proposal["state"] = "approved"
        else:
            gov_after = (await self.repo.get_shadow(
                port_id) or {}).get(
                "governance", "ungoverned")
            proposal["state"] = "rejected"
        proposal.update({
            "decidedAt": ts(),
            "decidedBy": "admin(human)",
            "governanceAfter": gov_after,
        })
        await self.repo.update_proposal(
            int(proposal_seq), proposal)
        await self.repo.save_event({
            "type": "proposal_decided",
            "portId": port_id,
            "detail": {
                "kind": kind,
                "approve": bool(approve),
                "state": proposal["state"],
            },
            "at": ts(),
        })
        return proposal

    # ============================================================
    # ⑤ 自愈轨迹视图(可审计)
    # ============================================================

    async def trace_view(
            self, port_id: str = None,
            limit: int = 50) -> dict:
        """自愈轨迹视图(触发窗口/动作/
        恢复证据三要素——可审计口径)"""
        traces = await self.repo.list_traces(
            port_id=port_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "count": len(traces),
            "traces": traces,
            "actionDomain":
                list(SELFHEAL_ACTIONS),
        }
