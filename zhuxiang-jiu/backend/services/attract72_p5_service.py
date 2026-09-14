"""72号·AI智能自动引流大模型 P5 热点卡位服务
(attract72_p5_service)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§四 4.6 + §八 P5 4 端点):
    - 消费 40号 P7 雷达 L1/L2 事件(只读铁律)
    - 四维势能评估(时效/相关/安全/转化潜力
      ——确定性公式, LLM 禁入)
    - 卡位决策(chase/observe/reject 三裁决,
      MODE 门控: shadow=落档不执行)
    - 高风险热点自动拒追(安全性硬编码)
    - 结果回流(预期 vs 实际)

铁律(宪法十则 §九):
    - 势能四维全公式可复现
    - 卡位执行永不直接操作 40号账号(只产
      建议注入创作情境参考——assist 语义)
"""

import logging
import time

from core.helpers import ts
from repositories.attract72_repository import (
    Attract72Repository,
)
from services.attract72_registry import (
    current_mode, is_kill,
    RADAR_CONSUME_GRADES,
    RADAR_MIN_VALUE,
    RADAR_CATEGORY_CHANNELS,
    POTENTIAL_WEIGHT_TIMELINESS,
    POTENTIAL_WEIGHT_RELEVANCE,
    POTENTIAL_WEIGHT_SAFETY,
    POTENTIAL_WEIGHT_CONVERSION,
    HOTSPOT_SAFETY_FLOOR,
    HOTSPOT_CHASE_THRESHOLD,
)

logger = logging.getLogger("attract72_p5")

# 时效黄金窗(小时——雷达事件龄超过则时效归零)
GOLDEN_WINDOW_HOURS = 48.0

# 品牌契合词表(相关性判定——确定性)
BRAND_RELEVANCE_WORDS = (
    "酒", "白酒", "竹香", "竹奕", "宴请",
    "送礼", "礼盒", "中秋", "春节", "团圆",
    "国货", "老字号", "非遗", "传统文化",
    "瑞麒", "徂徕", "国潮",
)


class Attract72P5Service:
    """P5 热点卡位服务(雷达联动四维势能+
    卡位决策三段留痕+结果回流)"""

    def __init__(self):
        self.repo = Attract72Repository()

    # ============================================================
    # ① 机会清单(观测面)
    # ============================================================

    async def list_opportunities(
            self, limit: int = 20) -> list[dict]:
        """雷达事件×势能清单(观测面——off 常开)

        只读消费 40号雷达 L1/L2 事件, 逐条
        计算四维势能(确定性, 不落库)。
        """
        from repositories.radar_repository import (
            RadarRepository,
        )
        events = []
        for grade in RADAR_CONSUME_GRADES:
            for ev in await RadarRepository() \
                    .list_events(grade=grade, limit=50):
                if float(ev.get("valueScore")
                         or 0) >= RADAR_MIN_VALUE:
                    events.append(ev)

        opportunities = []
        for ev in events[:limit]:
            pot = self._potential(ev)
            opportunities.append({
                "radarEventId": int(
                    ev.get("eventId", 0)),
                "title": ev.get("title", ""),
                "grade": ev.get("grade", ""),
                "category": ev.get("category", ""),
                "valueScore": float(
                    ev.get("valueScore", 0)),
                "potential": pot,
                "verdict": self._verdict(pot),
            })
        return sorted(
            opportunities,
            key=lambda o: -o["potential"]["total"]
        )[:limit]

    # ============================================================
    # ② 四维势能(确定性公式——LLM 禁入)
    # ============================================================

    @staticmethod
    def _potential(event: dict) -> dict:
        """四维势能计算(全公式可复现)

        - 时效性: 雷达剩余黄金窗占比
          (事件龄/GOLDEN_WINDOW_HOURS 线性衰减)
        - 相关性: 品牌契合词表命中密度
          (标题+摘要, 命中≥3 词满分)
        - 安全性: 雷达 grade 折算(L1=1.0,
          L2=0.8; L4 风险屏蔽永不消费)
        - 转化潜力: valueScore/100
          (历史同类热点引流基准)
        - 综合: 四维加权和(权重和=1)
        """
        # 时效(事件龄→黄金窗线性衰减)
        age_hours = 0.0
        try:
            born = event.get("detectedAt") \
                or event.get("at") or ""
            if born:
                # ISO 8601 → 秒(容错 Z/偏移)
                import datetime
                with_output = born.replace(
                    "Z", "+00:00")
                dt = datetime.datetime.fromisoformat(
                    with_output)
                age_hours = max(
                    0.0,
                    (datetime.datetime.now(
                        dt.tzinfo)
                     - dt).total_seconds() / 3600)
        except (ValueError, TypeError):
            age_hours = 0.0
        timeliness = max(
            0.0, 1.0 - age_hours / GOLDEN_WINDOW_HOURS)

        # 相关性(词表命中密度)
        text = f"{event.get('title', '')}" \
               f"{event.get('summary', '')}" \
               f"{event.get('content', '')}"
        hits = sum(1 for w
                   in BRAND_RELEVANCE_WORDS
                   if w in text)
        relevance = min(1.0, hits / 3.0)

        # 安全性(grade 折算——确定性)
        safety = 1.0 if event.get("grade") == "L1" \
            else 0.8

        # 转化潜力(valueScore 基准)
        conversion = min(
            1.0, float(event.get("valueScore")
                       or 0) / 100.0)

        total = (
            POTENTIAL_WEIGHT_TIMELINESS * timeliness
            + POTENTIAL_WEIGHT_RELEVANCE * relevance
            + POTENTIAL_WEIGHT_SAFETY * safety
            + POTENTIAL_WEIGHT_CONVERSION
            * conversion
        )
        return {
            "timeliness": round(timeliness, 4),
            "relevance": round(relevance, 4),
            "safety": round(safety, 4),
            "conversion": round(conversion, 4),
            "total": round(total, 4),
        }

    @staticmethod
    def _verdict(potential: dict) -> str:
        """三裁决(确定性——安全硬编码优先)"""
        # 高风险自动拒追("热点安全硬编码"铁律)
        if potential["safety"] < HOTSPOT_SAFETY_FLOOR:
            return "reject"
        if potential["total"] > HOTSPOT_CHASE_THRESHOLD:
            return "chase"
        return "observe"

    # ============================================================
    # ③ 卡位决策(决策面——MODE 门控)
    # ============================================================

    async def decide(self, radar_event_id: int,
                     now: str = "") -> dict:
        """卡位决策(决策面——off 409/shadow 落档/
        assist 方案+人工确认)

        产出一体化方案: 渠道选择×发布时机窗×
        预算注入建议×短链承接变体。
        卡位执行永不直接操作 40号账号(只产
        建议注入其创作情境参考)。

        Raises:
            KeyError: 雷达事件不存在或不达标
            ValueError: MODE=off / KILL 制动 /
                事件已决策(幂等拒绝)
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——决策面冻结")
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "ATTRACT72_MODE=off(决策面关闭——"
                "影子期评估签核后开放)")

        from repositories.radar_repository import (
            RadarRepository,
        )
        event = await RadarRepository() \
            .get_event(radar_event_id)
        if event is None:
            raise KeyError(
                f"雷达事件不存在(id={radar_event_id})")
        if float(event.get("valueScore") or 0) \
                < RADAR_MIN_VALUE:
            raise ValueError(
                "雷达事件价值分不足消费下限")

        # 幂等: 同事件重复决策拒绝
        existing = await self.repo \
            .find_decision_by_radar(radar_event_id)
        if existing is not None:
            raise ValueError(
                f"该事件已决策(decisionId="
                f"{existing['decisionId']})")

        potential = self._potential(event)
        verdict = self._verdict(potential)

        # 一体化方案(确定性查表)
        channels = list(RADAR_CATEGORY_CHANNELS.get(
            event.get("category", "current"),
            ("douyin",)))
        plan = {
            "channels": channels,
            # 发布时机窗(黄金窗内前 1/3——小时段)
            "timeWindow": [
                int(time.time() / 3600) * 3600,
                int(time.time() / 3600) * 3600
                + int(GOLDEN_WINDOW_HOURS
                      * potential["timeliness"]
                      / 3) * 3600,
            ],
            # 预算注入建议(只产建议——46号审批后生效)
            "budgetSuggestion": round(
                100.0 * potential["total"], 0),
            # 短链承接变体
            "landingVariant":
                "benefit_first"
                if potential["relevance"] >= 0.5
                else "trust_first",
        }

        status = "pending"
        if mode == "shadow":
            status = "pending"   # 落档不执行
        elif mode == "assist":
            status = "pending"   # 方案产出待人工确认
        # full 档: 卡位仍属"操作40号情境"域——
        # 永远 assist 语义(规划铁律: 执行永不
        # 直接操作 40号账号)

        decision_id = await self.repo.next_id(
            "decision")
        record = {
            "decisionId": decision_id,
            "radarEventId": radar_event_id,
            "radarEventRef":
                f"radar:{radar_event_id}",
            "potential": potential,
            "verdict": verdict,
            "plan": plan if verdict == "chase"
            else None,
            "mode": mode,
            "status": status,
            "executed": False,
            "executedAt": "",
            "outcome": None,
            "at": ts(),
        }
        await self.repo.save_decision(record)
        logger.info(
            "attract72_p5_decide id=%s radar=%s "
            "verdict=%s potential=%s mode=%s",
            decision_id, radar_event_id, verdict,
            potential["total"], mode)
        return record

    async def confirm_decision(
            self, decision_id: int) -> dict:
        """人工确认(assist 档——决策留痕)

        Raises:
            KeyError: 决策不存在
            ValueError: 状态非法/非 assist 档产出
        """
        record = await self.repo.get_decision(
            decision_id)
        if record is None:
            raise KeyError(
                f"卡位决策不存在(id={decision_id})")
        if record.get("status") != "pending":
            raise ValueError(
                f"决策状态非 pending("
                f"{record.get('status')})")
        if record.get("mode") == "shadow":
            raise ValueError(
                "shadow 期决策不可确认("
                "影子期评估签核后转段)")
        record["status"] = "confirmed"
        await self.repo.save_decision(record)
        return record

    async def execute_decision(
            self, decision_id: int) -> dict:
        """执行推送(将方案注入 40号创作情境参考
        ——不直接操作账号)

        Raises:
            KeyError: 决策不存在
            ValueError: 状态非法(须 confirmed)
        """
        record = await self.repo.get_decision(
            decision_id)
        if record is None:
            raise KeyError(
                f"卡位决策不存在(id={decision_id})")
        if record.get("status") != "confirmed":
            raise ValueError(
                f"决策须先人工确认("
                f"当前{record.get('status')})")
        record["status"] = "executed"
        record["executed"] = True
        record["executedAt"] = ts()
        await self.repo.save_decision(record)
        logger.info(
            "attract72_p5_execute id=%s "
            "radar=%s(情境参考注入)",
            decision_id, record.get("radarEventId"))
        return record

    async def record_outcome(
            self, decision_id: int,
            impressions: int = 0,
            conversions: int = 0,
            actual_roi: float = 0.0) -> dict:
        """结果回流(观测面——预期 vs 实际闭环)

        Raises:
            KeyError: 决策不存在
            ValueError: 决策未执行
        """
        record = await self.repo.get_decision(
            decision_id)
        if record is None:
            raise KeyError(
                f"卡位决策不存在(id={decision_id})")
        if not record.get("executed"):
            raise ValueError("决策未执行——无法回流")
        record["outcome"] = {
            "impressions": int(impressions),
            "conversions": int(conversions),
            "actualRoi": round(
                float(actual_roi or 0), 4),
            "expectedPotential":
                (record.get("potential") or {})
                .get("total", 0.0),
            "reflowedAt": ts(),
        }
        record["status"] = "closed"
        await self.repo.save_decision(record)
        return record

    async def list_decisions(
            self, verdict: str = None,
            status: str = None,
            limit: int = 100) -> list[dict]:
        """卡位决策台账(观测面)"""
        return await self.repo.list_decisions(
            verdict, status, limit)
