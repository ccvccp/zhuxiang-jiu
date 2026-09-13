"""72号·AI智能自动引流大模型 P3 预算预判服务
(attract72_p3_service)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§四 4.5/§七 P3):
    ① 72h 预分配(历史 GMV 分布×雷达信号
       提升×定律助推 → 渠道 core 配额+
       探索基金候选——确定性公式)
    ② 偏差重博弈(实际消耗 vs 期望偏离
       >15% → 再生成方案+diff 留痕
       ——域内自动快环)
    ③ 三层时间尺度(分钟级热点追投窗/
       日级 72h 调配/月级战略储备)
    ④ 探索基金(5%-15% 浮动——新渠道
       样本不足驱动上浮)
    ⑤ 建议系数 46号链(propose 留痕 →
       apply 显式执行——奖励系数变更
       永不自动铁律)

铁律(规划 §九/§4.5):
    - 奖励系数变更永远 46号建议书;
      重博弈只生成方案与 diff
    - 预算池总量月度锁定(72h 窗口=
      月池切片, 此消彼长不新增总成本
      ——v1.0 铁律继承)
    - LLM 禁入判定链(配额/偏差/系数
      =确定性公式全留痕)
    - 生成/建议/执行为决策面(off=409
      由路由层门控); 偏差重博弈为快环
      域内自动(不受 MODE 影响, KILL
      制动)

异常约定(71号口径):
    KeyError → 404(方案不存在)
    ValueError → 409(状态机/参数/阈值)
"""

import logging
from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.attract72_repository import (
    Attract72Repository,
)
from services.attract72_registry import (
    BUDGET_SCORER_ID,
    DEVIATION_THRESHOLD,
    EXPLORATION_LOW_SAMPLES,
    EXPLORATION_MAX, EXPLORATION_MIN,
    EXPLORATION_STEP,
    FORECAST_WINDOW_HOURS,
    LAW_BOOST_CAP,
    MODEL_VERSION,
    POOL_WINDOW_SHARE,
    RATE_CONF_SAMPLES,
    SIGNAL_LIFT_CAP,
    SUGGESTED_RATE_STEP,
    current_mode, is_kill,
)

logger = logging.getLogger("attract72_p3_service")


def _safe_div(n: float, d: float) -> float:
    """确定性安全除法(d<=0 → 0)"""
    return round(n / d, 4) if d and d > 0 else 0.0


class Attract72P3Service:
    """72号 P3 预算预判(预分配/重博弈/基金)"""

    def __init__(self):
        self.repo = Attract72Repository()

    # ============================================================
    # ① 72h 预分配生成(决策面——路由 off 门控)
    # ============================================================

    async def generate_forecast(
            self, anchor: str = "") -> dict:
        """生成 72h 预分配(确定性公式)

        输入(只读消费——叠加铁律):
            - attract v1.0: 渠道账本
              (monthlyPool/currentRate)+点击流
              +归因表(历史 GMV/佣金)
            - 72号 P1: 雷达信号(渠道提升)+
              节日信号(需求面元数据)
            - 72号 P2: active 定律(渠道助推)

        公式:
            windowBudget = 月池 × 10%(72h/720h)
            core 配额 = GMV 权重 ×(1+lift
            +lawBoost) 归一; 探索基金 =
            ratio(5%-15%) × windowBudget
            分配零 GMV 候选渠道

        Raises:
            ValueError: KILL 态/锚点非法
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——预分配"
                "拒绝(安全方向)")
        anchor_dt = self._parse_ts(anchor, "锚点")
        anchor_iso = anchor_dt.isoformat()

        from repositories.attract_repository import (
            AttractRepository,
            CHANNEL_SEEDS, RATE_CEIL, RATE_FLOOR,
        )
        attract_repo = AttractRepository()
        await attract_repo.ensure_budgets()
        budgets = {b["channel"]: b
                   for b in await
                   attract_repo.list_budgets(
                       limit=100)}
        clicks = await attract_repo.list_clicks(
            limit=10000)
        attrs = await attract_repo \
            .list_attributions(limit=10000)
        attr_by_click = {
            a.get("clickId"): a for a in attrs
            if a.get("clickId")}

        # 历史窗口(锚点之前——确定性字符串比较)
        gmv = {ch: 0.0 for ch in CHANNEL_SEEDS}
        comm = {ch: 0.0 for ch in CHANNEL_SEEDS}
        click_n = {ch: 0 for ch in CHANNEL_SEEDS}
        for c in clicks:
            if (c.get("at") or "") >= anchor_iso:
                continue
            ch = c.get("channel") or "direct"
            if ch not in click_n:
                continue
            click_n[ch] += 1
            a = attr_by_click.get(c.get("clickId"))
            if a and a.get("orderId"):
                gmv[ch] += float(
                    a.get("orderAmount") or 0)
                comm[ch] += float(
                    a.get("commission") or 0)

        # 雷达信号提升(渠道维度, 封顶)
        lift = {ch: 0.0 for ch in CHANNEL_SEEDS}
        radar_sigs = await self.repo.list_signals(
            signal_type="radar_event", limit=1000)
        for s in radar_sigs:
            for ch in (s.get("impactChannels")
                       or []):
                if ch in lift:
                    lift[ch] += float(
                        s.get("weight") or 0)
        for ch in lift:
            lift[ch] = min(SIGNAL_LIFT_CAP,
                           round(lift[ch], 4))

        # 定律助推(law=+confidence/
        # anti=-confidence, ±封顶)
        boost = {ch: 0.0 for ch in CHANNEL_SEEDS}
        for law in await self.repo.list_laws(
                status="active", limit=1000):
            factor = law.get("factor") or ""
            if law.get("dimension") \
                    != "channel_feature" \
                    or not factor.startswith(
                        "channel:"):
                continue
            ch = factor.split(":", 1)[1]
            if ch not in boost:
                continue
            sign = (1.0
                    if law.get("kind") == "law"
                    else -1.0)
            boost[ch] += sign * float(
                law.get("confidence") or 0)
        for ch in boost:
            boost[ch] = max(
                -LAW_BOOST_CAP,
                min(LAW_BOOST_CAP,
                    round(boost[ch], 4)))

        # 月池与窗口预算(池总量锁定铁律)
        pool = 10000.0
        if budgets:
            first = next(iter(
                budgets.values()))
            pool = float(
                first.get("monthlyPool")
                or 10000.0)
        window_budget = round(
            pool * POOL_WINDOW_SHARE, 2)

        # 探索基金(5%-15% 浮动)
        zero_gmv = [ch for ch in CHANNEL_SEEDS
                    if gmv[ch] <= 0]
        low_sample = [ch for ch in CHANNEL_SEEDS
                     if click_n[ch]
                     < EXPLORATION_LOW_SAMPLES]
        ratio = EXPLORATION_MIN
        if zero_gmv:
            ratio += EXPLORATION_STEP
        if low_sample:
            ratio += EXPLORATION_STEP
        ratio = min(EXPLORATION_MAX, ratio)
        candidates = zero_gmv or low_sample
        exploration_amount = (
            round(window_budget * ratio, 2)
            if candidates else 0.0)
        core = round(
            window_budget - exploration_amount,
            2)

        # core 配额(GMV 权重×调整归一)
        weights = {ch: gmv[ch]
                   for ch in CHANNEL_SEEDS
                   if gmv[ch] > 0}
        amounts = {}
        if weights:
            adj = {ch: max(
                0.01, w * (1 + lift[ch]
                           + boost[ch]))
                for ch, w in weights.items()}
            total_adj = sum(adj.values())
            amounts = {
                ch: round(core * w / total_adj, 2)
                for ch, w in adj.items()}
        else:
            # 全零历史 → core 均分全渠道
            per = round(core / len(CHANNEL_SEEDS),
                        2)
            amounts = {ch: per
                       for ch in CHANNEL_SEEDS}

        # allocations 组装(确定性字段全留痕)
        allocations = []
        for ch in sorted(amounts):
            conf = min(
                1.0, _safe_div(
                    click_n[ch],
                    RATE_CONF_SAMPLES))
            current = float(
                (budgets.get(ch) or {}).get(
                    "currentRate", 1.0))
            if boost[ch] < 0:
                srate = current \
                    - SUGGESTED_RATE_STEP
            elif lift[ch] > 0 or boost[ch] > 0:
                srate = current \
                    + SUGGESTED_RATE_STEP
            else:
                srate = current
            srate = max(RATE_FLOOR,
                        min(RATE_CEIL,
                            round(srate, 2)))
            amount = amounts[ch]
            allocations.append({
                "channel": ch,
                "amount": amount,
                "share": _safe_div(
                    amount, window_budget),
                "currentRate": current,
                "suggestedRate": srate,
                "confidence": conf,
                "lift": lift[ch],
                "lawBoost": boost[ch],
                "hotspot": lift[ch] > 0,
                "exploration": False,
            })
        for ch in candidates:
            conf = min(
                1.0, _safe_div(
                    click_n[ch],
                    RATE_CONF_SAMPLES))
            current = float(
                (budgets.get(ch) or {}).get(
                    "currentRate", 1.0))
            amount = round(
                exploration_amount
                / len(candidates), 2)
            allocations.append({
                "channel": ch,
                "amount": amount,
                "share": _safe_div(
                    amount, window_budget),
                "currentRate": current,
                "suggestedRate": current,
                "confidence": conf,
                "lift": 0.0,
                "lawBoost": 0.0,
                "hotspot": False,
                "exploration": True,
            })

        # 期望 ROI(历史总量确定性)
        total_gmv = sum(gmv.values())
        total_comm = sum(comm.values())
        expected_roi = (
            round(total_gmv / total_comm, 2)
            if total_comm > 0
            else (999.99 if total_gmv > 0
                  else 0.0))

        # 需求信号元数据(节日+雷达)
        festival_sigs = await self.repo \
            .list_signals(
                signal_type="festival",
                limit=100)
        demand_signals = {
            "radar": [
                {"ref": s.get("ref", ""),
                 "weight": s.get("weight", 0.0)}
                for s in radar_sigs],
            "festival": [
                {"ref": s.get("ref", ""),
                 "weight": s.get("weight", 0.0)}
                for s in festival_sigs],
        }

        # 旧 active → superseded(留痕)
        old = await self.repo \
            .find_active_forecast()
        if old is not None:
            old["status"] = "superseded"
            old["supersededAt"] = ts()
            await self.repo.save_forecast(old)

        forecast_id = await self.repo.next_id(
            "forecast")
        record = {
            "forecastId": forecast_id,
            "windowStart": anchor_iso,
            "windowEnd": (
                anchor_dt + timedelta(
                    hours=FORECAST_WINDOW_HOURS)
            ).isoformat(),
            "allocations": allocations,
            "poolTotal": round(pool, 2),
            "windowBudget": window_budget,
            "explorationRatio": ratio,
            "explorationAmount":
                exploration_amount,
            "expectedRoi": expected_roi,
            "actualDeviation": 0.0,
            "status": "active",
            "supersededAt": "",
            "rateChangeId": 0,
            "rateAppliedAt": "",
            "demandSignals": demand_signals,
            "timeScales": {
                "minute": "热点追投窗"
                           "(雷达 L1 渠道"
                           " hotspot 标记)",
                "day": f"{FORECAST_WINDOW_HOURS}h "
                       f"渠道调配窗",
                "month": "战略储备 "
                         f"¥{round(pool - window_budget, 2)}",
            },
            "createdAt": ts(),
        }
        await self.repo.save_forecast(record)
        logger.info(
            "attract72_forecast_generated id=%s "
            "window=%s allocations=%s",
            forecast_id, anchor_iso,
            len(allocations))
        return record

    @staticmethod
    def _parse_ts(text: str,
                  field: str) -> datetime:
        """时间解析(空→当前; 非法→409)"""
        raw = (text or "").strip()
        if not raw:
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                f"{field}时间格式非法"
                f"(ISO 8601): {raw}") from exc

    # ============================================================
    # ② 偏差监控与重博弈(快环——域内自动)
    # ============================================================

    async def _deviation(self, forecast: dict,
                         now: str) -> dict:
        """偏差度量(确定性公式)

        - actual: 窗口内点击的归因佣金
          (D-12 双轨消耗代理)
        - expected: windowBudget × 时间
          进度(0-1)
        - 无实际消耗 → 0(不可判);
          有消耗无期望 → 1.0(超速)
        """
        start = forecast.get("windowStart") or ""
        now_dt = self._parse_ts(now, "当前")
        start_dt = datetime.fromisoformat(start)
        hours = (now_dt - start_dt) \
            .total_seconds() / 3600
        ratio = max(0.0, min(1.0,
                             hours
                             / FORECAST_WINDOW_HOURS))
        expected = round(float(
            forecast.get("windowBudget") or 0)
            * ratio, 2)

        from repositories.attract_repository import (
            AttractRepository,
        )
        attract_repo = AttractRepository()
        clicks = await attract_repo.list_clicks(
            limit=10000)
        attrs = await attract_repo \
            .list_attributions(limit=10000)
        attr_by_click = {
            a.get("clickId"): a for a in attrs
            if a.get("clickId")}
        actual = 0.0
        for c in clicks:
            if (c.get("at") or "") < start:
                continue
            a = attr_by_click.get(
                c.get("clickId"))
            if a and a.get("orderId"):
                actual += float(
                    a.get("commission") or 0)
        if actual <= 0:
            deviation = 0.0
        elif expected <= 0:
            deviation = 1.0
        else:
            deviation = round(
                abs(actual - expected)
                / expected, 4)
        return {
            "deviation": deviation,
            "actual": round(actual, 2),
            "expected": expected,
            "elapsedRatio": round(ratio, 4),
        }

    async def forecast_status(
            self, now: str = "") -> dict:
        """当前方案+偏差+历史(观测面)"""
        active = await self.repo \
            .find_active_forecast()
        history = await self.repo.list_forecasts(
            limit=20)
        result = {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "active": None,
            "history": [
                {"forecastId": f["forecastId"],
                 "status": f["status"],
                 "windowStart":
                     f.get("windowStart", ""),
                 "createdAt":
                     f.get("createdAt", "")}
                for f in history],
        }
        if active is not None:
            m = await self._deviation(active, now)
            active_view = dict(active)
            active_view.update({
                "deviation": m["deviation"],
                "actualConsumed": m["actual"],
                "expectedSofar": m["expected"],
                "elapsedRatio": m["elapsedRatio"],
            })
            result["active"] = active_view
        return result

    async def rebalance_auto(
            self, now: str = "") -> dict:
        """偏差重博弈(>15% → 再生成+diff
        留痕——域内自动快环, 不受 MODE
        影响; 系数变更永不自动铁律)

        Raises:
            ValueError: KILL 态/无活动方案/
                偏差未达阈值
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——重博弈"
                "拒绝(安全方向)")
        active = await self.repo \
            .find_active_forecast()
        if active is None:
            raise ValueError(
                "无活动方案——先生成 72h 预分配")
        m = await self._deviation(active, now)
        if m["deviation"] <= DEVIATION_THRESHOLD:
            raise ValueError(
                f"偏差未达阈值"
                f"({m['deviation']:.4f}≤"
                f"{DEVIATION_THRESHOLD})"
                f"——不触发重博弈")

        old_alloc = {
            a["channel"]: a["amount"]
            for a in active.get("allocations")
            or []}
        old_id = active["forecastId"]
        active["status"] = "rebalanced"
        active["supersededAt"] = ts()
        active["actualDeviation"] = \
            m["deviation"]
        await self.repo.save_forecast(active)

        new = await self.generate_forecast(
            anchor=(now or "").strip())
        diff = []
        for a in new.get("allocations") or []:
            ch = a["channel"]
            if ch in old_alloc:
                diff.append({
                    "channel": ch,
                    "fromAmount": old_alloc[ch],
                    "toAmount": a["amount"],
                })
        logger.info(
            "attract72_rebalanced old=%s "
            "new=%s deviation=%s",
            old_id, new["forecastId"],
            m["deviation"])
        return {
            "deviation": m["deviation"],
            "actual": m["actual"],
            "expected": m["expected"],
            "rebalancedId": old_id,
            "newForecastId":
                new["forecastId"],
            "diff": diff,
            "forecast": new,
        }

    # ============================================================
    # ④ 探索基金(观测面)
    # ============================================================

    async def exploration_status(self) -> dict:
        """探索基金状态(观测面)

        Raises:
            ValueError: 无活动方案
        """
        active = await self.repo \
            .find_active_forecast()
        if active is None:
            raise ValueError(
                "无活动方案——先生成 72h 预分配")
        candidates = [
            {"channel": a["channel"],
             "amount": a["amount"]}
            for a in active.get("allocations")
            or [] if a.get("exploration")]
        pool = float(
            active.get("poolTotal") or 0)
        wb = float(
            active.get("windowBudget") or 0)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "forecastId":
                active["forecastId"],
            "ratio": active.get(
                "explorationRatio", 0.0),
            "amount": active.get(
                "explorationAmount", 0.0),
            "windowBudget": wb,
            "poolTotal": pool,
            "monthlyReserve": round(
                pool - wb, 2),
            "candidates": candidates,
            "timeScales":
                active.get("timeScales") or {},
        }

    # ============================================================
    # ⑤ 建议系数 46号链(决策面——路由 off 门控)
    # ============================================================

    @staticmethod
    def _rate_changes(forecast: dict) -> list:
        """方案内系数变更(确定性重算——纯计算)"""
        changes = []
        for a in forecast.get("allocations") \
                or []:
            cur = float(a.get("currentRate")
                        or 1.0)
            sug = float(a.get("suggestedRate")
                        or cur)
            if abs(sug - cur) > 1e-9:
                changes.append({
                    "channel": a["channel"],
                    "from": cur,
                    "to": sug})
        return changes

    async def propose_rates(self) -> dict:
        """系数建议 → 46号建议书(纯调用——
        46号零改动; 奖励系数变更永不自动)

        Raises:
            ValueError: KILL 态/无活动方案/
                无变更建议/46号 pending 冲突
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——建议"
                "拒绝(安全方向)")
        active = await self.repo \
            .find_active_forecast()
        if active is None:
            raise ValueError(
                "无活动方案——先生成 72h 预分配")
        changes = self._rate_changes(active)
        if not changes:
            raise ValueError(
                "无系数变更建议"
                "(建议与现行一致)")

        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov = AiGovernanceService()
        await gov.sync_registry()
        summary = "; ".join(
            f"{c['channel']} "
            f"{c['from']}→{c['to']}"
            for c in changes)
        result = await gov.submit_change(
            scorer_id=BUDGET_SCORER_ID,
            kind="config",
            payload={
                "forecastId":
                    active["forecastId"],
                "rates": changes,
            },
            reason=f"[72号预算] 奖励系数"
                   f"建议: {summary}",
            requested_by="attract72-budget")
        active["rateChangeId"] = result.get(
            "changeId", 0)
        await self.repo.save_forecast(active)
        logger.info(
            "attract72_rates_proposed "
            "changeId=%s n=%s",
            active["rateChangeId"],
            len(changes))
        return {
            "changeId": active["rateChangeId"],
            "forecastId":
                active["forecastId"],
            "changes": changes,
        }

    async def apply_rates(self) -> dict:
        """系数执行(46号留痕后的 admin 显式
        动作——审批总线 P0 不自动执行业务侧
        变更, 本端点即人工执行位; v1.0
        currentRate 双轨变更)

        Raises:
            ValueError: KILL 态/未 propose/
                已执行/无变更
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——执行"
                "拒绝(安全方向)")
        active = await self.repo \
            .find_active_forecast()
        if active is None:
            raise ValueError(
                "无活动方案——先生成 72h 预分配")
        if not active.get("rateChangeId"):
            raise ValueError(
                "先提交 46号建议书"
                "(rates/propose)——系数变更"
                "永不自动铁律")
        if active.get("rateAppliedAt"):
            raise ValueError(
                "系数已执行——勿重复")
        changes = self._rate_changes(active)
        if not changes:
            raise ValueError(
                "无系数变更建议"
                "(建议与现行一致)")

        # 46号 pending 清理(留痕——
        # 人工执行口径, 71号范式)
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        change = await AiGovernance46Repository() \
            .get_change(active["rateChangeId"])
        if change is None:
            raise ValueError(
                f"46号变更不存在(changeId="
                f"{active['rateChangeId']})")
        if change.get("status") == "pending":
            from services.ai_governance_service \
                import AiGovernanceService
            await AiGovernanceService() \
                .review_change(
                    active["rateChangeId"],
                    approve=False,
                    reviewed_by="admin",
                    review_note="payload 已由 "
                                "72号 rates/apply "
                                "人工执行"
                                "(审批总线 P0 "
                                "不自动执行"
                                "业务侧变更)")

        # v1.0 currentRate 显式变更
        # (双轨继承, 上下限钳制)
        from repositories.attract_repository import (
            AttractRepository,
            RATE_CEIL, RATE_FLOOR,
        )
        attract_repo = AttractRepository()
        applied = []
        applied_rates = {}
        for c in changes:
            budget = await attract_repo \
                .get_budget(c["channel"])
            if budget is None:
                continue
            budget["currentRate"] = max(
                RATE_FLOOR,
                min(RATE_CEIL, c["to"]))
            budget["lastAdjustedAt"] = ts()
            await attract_repo.save_budget(
                budget)
            applied_rates[c["channel"]] = \
                budget["currentRate"]
            applied.append({
                "channel": c["channel"],
                "currentRate":
                    budget["currentRate"],
            })
        # allocation 快照同步(再建议时
        # 现行=suggested → 无变更)
        for a in active.get("allocations") \
                or []:
            if a["channel"] in applied_rates:
                a["currentRate"] = \
                    applied_rates[
                        a["channel"]]
        active["rateAppliedAt"] = ts()
        await self.repo.save_forecast(active)
        logger.info(
            "attract72_rates_applied n=%s",
            len(applied))
        return {
            "changeId": active["rateChangeId"],
            "applied": applied,
        }
