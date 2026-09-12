"""69号·AI智能支付大模型 智能路由服务
(pay69_router_service, P1)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.2/§七 P1):
    ① 确定性多因子评分选道(费率×健康度
       ×意图亲和×会员习惯——查表计算,
       LLM 禁入判定链)
    ② 硬过滤(金额超单笔限额/人工冻结
       frozen 通道——路由永不选中)
    ③ 静默重试备选(首选失败自动尝试次
       优——沙盘执行域, 留痕 routeScore)
    ④ 快环通道成功率滚动窗口(最近 N 次
       执行——确定性统计基线)

铁律(规划 §1.2/§1.3):
    - 路由决策=查表评分(每因子数值
      明细留痕——LLM 禁入实证)
    - frozen 永不被成功率导出(P0
      铁律继承)——路由侧硬过滤
    - 健康度分层: 滚动窗口 > P0 手工
      上报 > 默认 1.0(未观测)
    - 69号永不写 60号表; 本服务为沙盘
      执行域(mock 语义, 真实渠道集成
      归 60号 CHANNEL_MODE)
"""

import logging

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    CHANNEL_IDS, CHANNEL_REGISTRY,
    INTENT_AFFINITY, INTENT_TAGS,
    ROUTE_WEIGHTS, ROUTE_WINDOW_SIZE,
    MAX_FEE_RATE, health_of,
    current_mode, MODEL_VERSION,
)

logger = logging.getLogger("pay69_router")

# 最大尝试次数(含首选——静默备选上限)
MAX_ATTEMPTS = 3


class Pay69RouterService:
    """69号智能路由(P1)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # 因子计算(确定性——查表)
    # ============================================================

    def _fee_score(self, channel_id: str) -> float:
        """费率因子: 1 - feeRate/上限
        (越低越优——credit_tv 0 费率=1.0)"""
        rate = CHANNEL_REGISTRY[channel_id]["feeRate"]
        return round(
            max(0.0, 1 - rate / MAX_FEE_RATE), 4)

    async def _health_layer(
            self, channel_id: str) -> dict:
        """健康度分层(窗口 > 手工上报
        > 默认 1.0)——返回 {score, state,
        source}"""
        window = await self.repo.window_of(
            channel_id)
        if window["attemptCount"]:
            rate = window["successRate"]
            return {"score": rate,
                    "state": health_of(rate),
                    "source": "window"}
        rec = await self.repo.get_channel(
            channel_id)
        if rec and rec.get("attemptCount"):
            rate = float(
                rec.get("successRate", 1.0))
            state = ("frozen" if rec.get("frozen")
                     else rec.get("state",
                                  "healthy"))
            return {"score": rate, "state": state,
                    "source": "manual"}
        return {"score": 1.0, "state": "healthy",
                "source": "default"}

    def _affinity_score(
            self, channel_id: str,
            candidates: list) -> float:
        """亲和因子: 意图候选=1.0,
        其余=0.5(中性)"""
        return (1.0 if channel_id in candidates
                else 0.5)

    async def _habit_layer(
            self, member_id: int,
            channel_id: str) -> dict:
        """习惯因子: 会员通道使用占比
        (无历史=0.5 中性)"""
        habits = await self.repo.get_habits(
            member_id)
        total = sum(habits.values())
        if not total:
            return {"score": 0.5, "uses": 0}
        uses = habits.get(channel_id, 0)
        return {"score": round(uses / total, 4),
                "uses": uses}

    # ============================================================
    # 评分选道(决策面)
    # ============================================================

    async def compute_route(
            self, member_id: int, amount: float,
            tags: list = None,
            intent_text: str = "",
            tv_eligible: bool = False) -> dict:
        """多因子确定性评分选道

        硬过滤: 金额超单笔限额 / frozen /
        credit_tv 需信值资格(tv_eligible
        ——45号 TV 资金源, 非全员默认)
        排序: routeScore 降序, 同分按
        channelId 升序(全确定性)

        Raises:
            ValueError: 金额非法/标签域外
        """
        # 标签(显式优先, 否则规则轨解析)
        tag_list = [str(t) for t in (tags or [])]
        unknown = set(tag_list) - set(INTENT_TAGS)
        if unknown:
            raise ValueError(
                f"意图标签域外: {sorted(unknown)}")
        if not tag_list and intent_text:
            from services.pay69_p0_service \
                import Pay69P0Service
            parsed = Pay69P0Service()\
                .parse_intent(intent_text, member_id)
            tag_list = parsed["tags"]
        if not tag_list:
            tag_list = ["default"]
        # 亲和候选(封闭映射)
        candidates = []
        for tag in tag_list:
            for ch in INTENT_AFFINITY.get(
                    tag, ()):
                if ch not in candidates:
                    candidates.append(ch)

        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")

        scored = []
        for cid in CHANNEL_IDS:
            meta = CHANNEL_REGISTRY[cid]
            # 硬过滤①: 单笔限额
            if amount > meta["singleLimit"]:
                continue
            # 硬过滤②: frozen(人工专属
            # ——路由永不选中)
            rec = await self.repo.get_channel(cid)
            if rec and rec.get("frozen"):
                continue
            # 硬过滤③: credit_tv 需信值资格
            # (45号 TV 资金源——非全员默认)
            if cid == "credit_tv" \
                    and not tv_eligible:
                continue
            fee = self._fee_score(cid)
            health = await self._health_layer(cid)
            affinity = self._affinity_score(
                cid, candidates)
            habit = await self._habit_layer(
                member_id, cid)
            score = round(
                ROUTE_WEIGHTS["fee"] * fee
                + ROUTE_WEIGHTS["health"]
                * health["score"]
                + ROUTE_WEIGHTS["affinity"]
                * affinity
                + ROUTE_WEIGHTS["habit"]
                * habit["score"], 4)
            scored.append({
                "channelId": cid,
                "label": meta["label"],
                "routeScore": score,
                "factors": {
                    "fee": fee,
                    "health": health["score"],
                    "affinity": affinity,
                    "habit": habit["score"],
                },
                "healthState": health["state"],
                "healthSource": health["source"],
                "habitUses": habit["uses"],
            })
        scored.sort(key=lambda x: (
            -x["routeScore"], x["channelId"]))
        return {
            "modelVersion": MODEL_VERSION,
            "memberId": int(member_id or 0),
            "amount": amount,
            "tags": tag_list,
            "candidateCount": len(scored),
            "ranking": scored,
            "computedAt": ts(),
        }

    # ============================================================
    # 沙盘执行+静默备选(assist 态)
    # ============================================================

    async def execute_route(
            self, member_id: int, amount: float,
            tags: list = None,
            intent_text: str = "",
            simulate_fail: list = None,
            tv_eligible: bool = False) -> dict:
        """路由沙盘执行(首选失败静默重试
        次优——用户支付动作内的执行域;
        每次尝试留痕 flows, 快环窗口消费)

        simulate_fail: 沙盘失败注入(测试
        钩子——admin 域, mock 语义)

        Raises:
            ValueError: 无可用通道
        """
        route = await self.compute_route(
            member_id, amount, tags, intent_text,
            tv_eligible=tv_eligible)
        ranking = route["ranking"]
        if not ranking:
            raise ValueError(
                f"无可用通道(金额超限或全部"
                f"冻结): amount={amount}")
        sim = set(simulate_fail or ())
        attempts = []
        for rank, cand in enumerate(
                ranking[:MAX_ATTEMPTS]):
            cid = cand["channelId"]
            # 确定性模拟耗时(通道名派生)
            latency = 120 + (
                sum(map(ord, cid)) % 180)
            success = cid not in sim
            flow = {
                "memberId": int(member_id or 0),
                "channelId": cid,
                "amount": route["amount"],
                "success": success,
                "latencyMs": latency,
                "attemptNo": rank + 1,
                "routeScore": cand["routeScore"],
                "at": ts(),
            }
            saved = await self.repo.save_flow(
                dict(flow))
            attempts.append(saved)
            if success:
                break
        final = attempts[-1]
        return {
            "routeId": f"R69-"
                       f"{attempts[0]['flowSeq']}",
            "modelVersion": MODEL_VERSION,
            "memberId": int(member_id or 0),
            "amount": route["amount"],
            "tags": route["tags"],
            "candidateCount": route[
                "candidateCount"],
            "ranking": ranking[:MAX_ATTEMPTS],
            "attempts": attempts,
            "finalChannelId": final["channelId"],
            "success": bool(final["success"]),
            "silentFallback": len(attempts) > 1,
            "executedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    def route_dict(self) -> dict:
        """路由字典公示(权重+因子口径
        +窗口——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "weights": dict(ROUTE_WEIGHTS),
            "factors": {
                "fee": ("费率因子: 1 - feeRate/"
                        f"{MAX_FEE_RATE}(越低越优)"),
                "health": ("健康度因子: 滚动窗口"
                           "成功率(分层: 窗口>"
                           "手工上报>默认1.0)"),
                "affinity": ("亲和因子: 意图候选"
                             "=1.0, 其余=0.5"),
                "habit": ("习惯因子: 会员通道使用"
                          "占比(无历史=0.5)"),
            },
            "windowSize": ROUTE_WINDOW_SIZE,
            "maxAttempts": MAX_ATTEMPTS,
            "hardFilters": (
                "singleLimit(单笔限额)",
                "frozen(人工冻结——路由永不选中)",
                "credit_tv(信值资格 tvEligible"
                "——45号 TV 资金源)"),
        }

    async def window_view(self) -> dict:
        """七通道滚动窗口统计(快环基线
        ——观测面)"""
        channels = []
        for cid in CHANNEL_IDS:
            w = await self.repo.window_of(cid)
            w["state"] = (
                health_of(w["successRate"])
                if w["successRate"] is not None
                else "unobserved")
            channels.append(w)
        return {
            "modelVersion": MODEL_VERSION,
            "windowSize": ROUTE_WINDOW_SIZE,
            "channels": channels,
        }

    async def flows_view(
            self, channel_id: str = None,
            limit: int = 50) -> dict:
        """路由执行留痕视图(观测面)"""
        flows = await self.repo.list_flows(
            channel_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(flows),
            "flows": flows,
        }

    async def habit_report(
            self, member_id: int,
            channel_id: str,
            count: int = 1) -> dict:
        """会员通道习惯上报(快环观测
        ——不受 PAY69_MODE 影响)

        Raises:
            KeyError: 通道域外
            ValueError: 次数非法
        """
        if channel_id not in CHANNEL_REGISTRY:
            raise KeyError(
                f"通道不存在(channelId="
                f"{channel_id})")
        if int(count) <= 0:
            raise ValueError(
                f"习惯次数非法: {count}(须>0)")
        habits = await self.repo.bump_habit(
            member_id, channel_id, int(count))
        await self.repo.save_event({
            "type": "habit_report",
            "memberId": int(member_id or 0),
            "detail": {
                "channelId": channel_id,
                "count": int(count),
            },
            "at": ts(),
        })
        return {
            "memberId": int(member_id),
            "habits": habits,
        }

    async def habits_view(
            self, member_id: int) -> dict:
        """会员习惯视图(观测面)"""
        habits = await self.repo.get_habits(
            member_id)
        dominant = (max(habits,
                        key=habits.get)
                    if habits else "")
        return {
            "memberId": int(member_id),
            "habits": habits,
            "dominantChannel": dominant,
        }
