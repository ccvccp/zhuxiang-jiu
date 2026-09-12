"""智客·AI智能会员大模型 P3 进化闭环(zk_evolution_service)

「参数学习 + 异常检测 + 决策备忘」:
    - 反馈闭环: adopted/corrected/rejected 三裁决驱动参数权重学习
      (ltvRetainFactor 起始 0.6, ×1.05/×0.95/×0.9, clamp [0.4,0.8]
      安全阀——参数永不越界)
    - 三检测器: 消费 spike / 积分 drop / 注册 surge
      (μ±3σ 与 ×3 范式, 对齐 blogger_trust 三检测器铁律)
    - 决策备忘录: 会员日活动/等级门槛调整两主题模板
      (数据插值 + 假设标注 + disposition)

铁律: 全部确定性; 学习只动智客自有参数(zk_params), 零回写既有模块。
"""

import logging
import math

from services.zk_fabric_service import (
    _ZkStore, ZkFabricService, order_amount, _round2, _now_iso)
from services.zk_insight_service import order_paid_of

logger = logging.getLogger(__name__)

# ============================================================
# P3 常量(确定性)
# ============================================================

# 反馈裁决与学习倍率
FEEDBACK_VERDICTS = {
    "adopted": 1.05,     # 采纳: 参数上修 5%
    "corrected": 0.95,   # 修正: 参数下修 5%
    "rejected": 0.90,    # 拒绝: 负样本, 参数下修 10%
}

# 可反馈对象
FEEDBACK_TARGETS = ("ltv", "churn_scan", "wakeup", "benefit_match",
                    "sandbox", "portrait")

# 检测器参数(μ±3σ 与 ×3 范式, 对齐 blogger_trust_service)
DETECTOR_SIGMA = 3.0
DETECTOR_SPIKE_MU_MIN = 5.0     # spike 防冷启动(μ≥5)
DETECTOR_DROP_ABS = 20.0        # drop 绝对值下限(防零基误报)
DETECTOR_SURGE_SAMPLE = 20      # surge 样本下限
DETECTOR_SURGE_RATIO = 3.0      # surge ×3 激增

# 备忘录主题
MEMO_TOPICS = ("member_day", "level_threshold")
MEMO_TOPIC_NAMES = {"member_day": "会员日活动",
                    "level_threshold": "等级门槛调整"}

# 决策备忘统一口径
MEMO_DISPOSITION = "备忘为建议书; 决策须管理层确认后方可执行"


def detect_spike(history: list[float], current: float) -> bool:
    """尖峰检测: current > μ+3σ 且 μ≥5(防冷启动误报)"""
    if len(history) < 3:
        return False
    mu = sum(history) / len(history)
    sigma = math.sqrt(sum((h - mu) ** 2 for h in history) / len(history))
    return (mu >= DETECTOR_SPIKE_MU_MIN
            and current > mu + DETECTOR_SIGMA * sigma)


def detect_drop(history: list[float], current: float) -> bool:
    """骤降检测: current < μ−3σ 且绝对值 ≥20(防零基误报)"""
    if len(history) < 3:
        return False
    mu = sum(history) / len(history)
    sigma = math.sqrt(sum((h - mu) ** 2 for h in history) / len(history))
    return (current < mu - DETECTOR_SIGMA * sigma
            and abs(current) >= DETECTOR_DROP_ABS)


def detect_surge(past: float, current: float) -> bool:
    """激增检测: current ≥20 且 ≥ past×3(样本门槛+倍率)"""
    return (current >= DETECTOR_SURGE_SAMPLE
            and current >= past * DETECTOR_SURGE_RATIO)


class ZkEvolutionService:
    """P3: 反馈参数学习 + 三检测器 + 决策备忘录"""

    def __init__(self, fabric: ZkFabricService = None,
                 store: _ZkStore = None):
        self.fabric = fabric or ZkFabricService()
        self.store = store or self.fabric.store

    # ============================================================
    # 反馈闭环(参数权重学习, 安全阀 clamp)
    # ============================================================

    async def feedback(self, target_type: str, verdict: str,
                       note: str = "") -> dict:
        """反馈闭环: 三裁决驱动 ltvRetainFactor 学习(clamp [0.4,0.8])

        Raises:
            ValueError: 对象/裁决非法
        """
        if target_type not in FEEDBACK_TARGETS:
            raise ValueError(f"反馈对象无效({target_type}: "
                             f"{'/'.join(FEEDBACK_TARGETS)})")
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(f"裁决无效({verdict}: "
                             f"adopted/corrected/rejected)")

        params = await self.store.get("zk_params", "params") or {}
        before = float(params.get(
            "ltvRetainFactor",
            0.6))
        lo, hi = 0.4, 0.8
        after = round(min(hi, max(lo, before * FEEDBACK_VERDICTS[verdict])),
                      4)
        params.update({
            "ltvRetainFactor": after,
            "updatedAt": _now_iso(),
        })
        await self.store.save("zk_params", "params", params)

        learning_note = (f"ltvRetainFactor {before} ×"
                         f"{FEEDBACK_VERDICTS[verdict]} → {after}"
                         f"(clamp [{lo}, {hi}] 安全阀内)")
        record = {
            "feedbackId": await self.store.next_id("zk_feedbacks"),
            "targetType": target_type, "verdict": verdict,
            "note": note or "",
            "paramBefore": before, "paramAfter": after,
            "learningNote": learning_note,
            "negativeSample": ("负样本回流: 拒绝裁决留痕, 供参数与阈值"
                               "校准观测(决策权在人工)")
            if verdict == "rejected" else None,
            "feedbackAt": _now_iso(),
        }
        await self.store.save("zk_feedbacks", record["feedbackId"], record)
        return record

    async def feedbacks(self, limit: int = 50) -> list[dict]:
        """反馈留痕列表(最近优先)"""
        rows = await self.store.list("zk_feedbacks")
        rows.sort(key=lambda r: int(r.get("feedbackId", 0)), reverse=True)
        return rows[:max(1, int(limit))]

    async def params(self) -> dict:
        """当前可学习参数视图"""
        params = await self.store.get("zk_params", "params") or {}
        return {
            "ltvRetainFactor": float(params.get("ltvRetainFactor", 0.6)),
            "clampRange": [0.4, 0.8],
            "defaultLtvRetainFactor": 0.6,
            "updatedAt": params.get("updatedAt", ""),
            "note": "参数仅由反馈闭环学习(clamp 安全阀), 不经人工直改",
        }

    # ============================================================
    # 三检测器(消费 spike / 积分 drop / 注册 surge)
    # ============================================================

    async def detect(self) -> dict:
        """全站三检测器扫描(日粒度序列, μ±3σ/×3 范式)

        序列口径:
            - 消费 spike: 全站日有效消费额(有效订单实付额按日聚合)
            - 积分 drop: 全站日积分抵扣量(订单 usedPoints 按日聚合)
            - 注册 surge: 日注册量(会员 created_at 日聚合)
        各序列取「最近一天」为当前观测, 其余为历史(≥3 天才检测)。
        """
        consume_by_day: dict[str, float] = {}
        points_by_day: dict[str, float] = {}
        for o in await self.fabric.all_orders():
            day = order_paid_of(o)[:10]
            if not day:
                continue
            if o.get("status") in ("PAID", "SHIPPED", "RECEIVED",
                                   "COMPLETED"):
                consume_by_day[day] = (consume_by_day.get(day, 0.0)
                                       + order_amount(o))
            points_by_day[day] = (points_by_day.get(day, 0.0)
                                  + int(o.get("usedPoints", 0) or 0))
        reg_by_day: dict[str, int] = {}
        for m in await self.fabric.members():
            day = str(m.get("created_at") or "")[:10]
            if day:
                reg_by_day[day] = reg_by_day.get(day, 0) + 1

        alerts = []
        for name, series, detector, unit, domain in (
            ("消费尖峰", consume_by_day, detect_spike, "元", "消费"),
            ("积分骤降", points_by_day, detect_drop, "竹叶", "积分"),
            ("注册激增", reg_by_day, detect_surge, "名", "会员增长"),
        ):
            days = sorted(series)
            if len(days) < 4:
                continue      # 历史<3 天不检测(冷启动保护)
            current_day = days[-1]
            history = [series[d] for d in days[:-1]]
            current = series[current_day]
            hit = (detector(sum(history) / len(history), current)
                   if detector is detect_surge else
                   detector(history, current))
            if hit:
                alerts.append({
                    "detector": name, "domain": domain,
                    "date": current_day,
                    "current": _round2(current),
                    "historyAvg": _round2(sum(history) / len(history)),
                    "unit": unit,
                    "rule": ("当前 > μ+3σ(μ≥5)" if detector is detect_spike
                             else "当前 < μ−3σ(绝对值≥20)"
                             if detector is detect_drop else
                             "当前 ≥20 且 ≥均值×3"),
                })

        return {
            "alerts": alerts,
            "seriesMeta": {
                "consumeDays": len(consume_by_day),
                "pointsDays": len(points_by_day),
                "registerDays": len(reg_by_day),
            },
            "formula": ("spike: 当前>μ+3σ 且 μ≥5; drop: 当前<μ−3σ 且 "
                        "|当前|≥20; surge: 当前≥20 且 ≥均值×3"
                        "(μ/σ 为历史日序列均值/标准差, 确定性)"),
            "disposition": "检测为观测告警; 处置动作须管理员确认",
            "detectedAt": _now_iso(),
        }

    # ============================================================
    # 决策备忘录(模板 + 数据插值 + 假设标注)
    # ============================================================

    async def memo(self, topic: str, notes: str = "") -> dict:
        """决策备忘录(会员日活动/等级门槛调整)

        Raises:
            ValueError: 主题非法
        """
        if topic not in MEMO_TOPICS:
            raise ValueError(f"备忘主题无效({topic}: "
                             f"{'/'.join(MEMO_TOPICS)})")
        overview = await self.fabric.overview()
        if topic == "member_day":
            memo = await self._memo_member_day(overview)
        else:
            memo = await self._memo_level_threshold(overview)
        memo.update({
            "memoId": await self.store.next_id("zk_memos"),
            "topic": topic,
            "topicName": MEMO_TOPIC_NAMES[topic],
            "notes": notes or "",
            "disposition": MEMO_DISPOSITION,
            "createdAt": _now_iso(),
        })
        await self.store.save("zk_memos", memo["memoId"], memo)
        return memo

    @staticmethod
    async def _memo_member_day(overview: dict) -> dict:
        """会员日活动备忘(数据插值 + 确定性力度建议)"""
        avg = overview["avgConsume"]
        total = overview["memberTotal"]
        active = overview["statusDistribution"]["active"]
        if avg < 200:
            plan = "满 100-20 / 满 300-50 双梯度满减"
        elif avg < 500:
            plan = "满 300-50 / 满 600-120 双梯度满减"
        else:
            plan = "满 500-100 / 满 1000-250 双梯度满减+赠品鉴小样"
        return {
            "title": "每月 8 日会员日: 消费力分层满减方案",
            "body": (f"当前会员 {total} 名(正常 {active}), 有效单平均消费 "
                     f"¥{avg}; 建议采用「{plan}」; 目标提升复购频次与"
                     f"客单价(数字全部来自织物总览)。"),
            "assumptions": [
                "满减梯度按当前平均消费分档(确定性规则)",
                "目标基线为活动后 30 天复购率, 需活动后复盘校验",
            ],
            "formula": f"平均消费 ¥{avg} → "
                       f"{'<200 低档' if avg < 200 else '<500 中档' if avg < 500 else '≥500 高档'}满减模板",
        }

    @staticmethod
    async def _memo_level_threshold(overview: dict) -> dict:
        """等级门槛调整备忘(分布数据插值)"""
        dist = overview["levelDistribution"]
        total = overview["memberTotal"] or 1
        l3_plus = dist[3] + dist[4] + dist[5]
        ratio = _round2(l3_plus / total * 100)
        if ratio < 20:
            advice = ("L3+ 占比偏低, 建议下调 L3 门槛(3000→2500)或"
                      "加发 L2→L3 升级券, 观察一季度转化")
        elif ratio > 60:
            advice = ("L3+ 占比偏高, 建议上调 L4 门槛(6999→8000),"
                      "维持高等级稀缺性")
        else:
            advice = "L3+ 占比处于健康带(20%-60%), 门槛维持不动"
        return {
            "title": "等级门槛审视: L3+ 渗透率观测",
            "body": (f"等级分布 L1-L5: {dist[1]}/{dist[2]}/{dist[3]}/"
                     f"{dist[4]}/{dist[5]}; L3+ 渗透率 {ratio}%"
                     f"({l3_plus}/{total}); {advice}。"),
            "assumptions": [
                "渗透率健康带取 20%-60%(运营经验值, 确定性阈值)",
                "门槛调整影响成长值达标人群, 须同步评估保级额",
            ],
            "formula": f"L3+ 渗透率 = ({dist[3]}+{dist[4]}+{dist[5]}) "
                       f"/ {total} × 100 = {ratio}%",
        }

    async def memos(self, limit: int = 50) -> list[dict]:
        """备忘录列表(最近优先)"""
        rows = await self.store.list("zk_memos")
        rows.sort(key=lambda r: int(r.get("memoId", 0)), reverse=True)
        return rows[:max(1, int(limit))]
