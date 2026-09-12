"""69号·AI智能支付大模型 认知中枢底座服务
(pay69_p0_service, P0)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§七 P0):
    ① 七通道字典公示(费率/限额/时效
       /特征——封闭注册表)
    ② 健康度观测面(滚动窗口统计口径
       /未观测默认健康/frozen 人工专属)
    ③ 意图标签解析(规则轨——LLM 禁入
       判定链; 归属确定性+留痕)
    ④ 全链事件埋点底座

铁律(规划 §1.3/§八):
    - 69号消费 60号归因链, 永不写
      60号表(叠加式)
    - LLM 禁入判定链(意图归属=关键词
      规则轨——55号 clarify 范式)
    - frozen 不可由成功率导出(仅人工
      /PAY69_KILL 制动)——路由(P1)
      永不选中 frozen 通道
"""

import logging
from typing import ClassVar

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    CHANNEL_IDS, CHANNEL_REGISTRY,
    HEALTH_STATES, HEALTH_THRESHOLDS,
    INTENT_TAGS, INTENT_AFFINITY,
    health_of, MODEL_VERSION, current_mode,
)

logger = logging.getLogger("pay69_p0_service")


class Pay69P0Service:
    """69号认知中枢底座(P0)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # ① 通道字典公示(观测面)
    # ============================================================

    def channel_dict(self) -> dict:
        """七通道字典(封闭注册表自描述——
        含健康度阈值口径)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "channelCount": len(CHANNEL_IDS),
            "channels": [
                dict(
                    channelId=cid,
                    **CHANNEL_REGISTRY[cid],
                )
                for cid in CHANNEL_IDS
            ],
            "healthThresholds": dict(HEALTH_THRESHOLDS),
            "healthStates": list(HEALTH_STATES),
        }

    def channel_detail(self, channel_id: str) -> dict:
        """单通道详情(注册表+实时健康度)

        Raises:
            KeyError: 通道域外
        """
        if channel_id not in CHANNEL_REGISTRY:
            raise KeyError(
                f"通道不存在(channelId={channel_id})")
        meta = dict(CHANNEL_REGISTRY[channel_id])
        meta["channelId"] = channel_id
        return meta

    # ============================================================
    # ② 健康度观测面
    # ============================================================

    async def health_view(self) -> dict:
        """七通道健康度观测(未观测通道
        默认 healthy——观测面零影响)"""
        channels = []
        for cid in CHANNEL_IDS:
            rec = await self.repo.get_channel(cid)
            if rec is None:
                channels.append({
                    "channelId": cid,
                    "state": "healthy",
                    "frozen": False,
                    "observed": False,
                })
            else:
                rec["observed"] = True
                channels.append(rec)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "channels": channels,
            "frozenCount": sum(
                1 for c in channels
                if c.get("frozen")),
        }

    async def report_health(
            self, channel_id: str,
            attempt_count: int,
            success_count: int,
            avg_latency_ms: float = 0) -> dict:
        """通道健康度上报(快环观测——
        确定性统计基线, 域内自动+留痕)

        观测上报不受 PAY69_MODE 影响
        (纯统计, 不含路由/决策); 统计
        仅为基线, 权重与阈值调整走慢环
        审批(规划 §5.1 快环边界)

        Raises:
            KeyError: 通道域外
            ValueError: 参数非法(次数负数/
                成功数>尝试数)
        """
        if channel_id not in CHANNEL_REGISTRY:
            raise KeyError(
                f"通道不存在(channelId={channel_id})")
        attempt = int(attempt_count or 0)
        success = int(success_count or 0)
        if attempt < 0 or success < 0:
            raise ValueError("次数不可为负")
        if success > attempt:
            raise ValueError(
                f"成功数不可大于尝试数"
                f"({success}>{attempt})")
        rate = round(
            success / attempt, 4) if attempt else 1.0
        # frozen 不可由成功率导出——
        # 上报永不改 frozen(人工专属)
        existing = await self.repo.get_channel(
            channel_id)
        frozen = bool(existing and existing.get(
            "frozen"))
        record = {
            "channelId": channel_id,
            "state": health_of(rate),
            "successRate": rate,
            "attemptCount": attempt,
            "successCount": success,
            "avgLatencyMs": round(
                float(avg_latency_ms or 0), 2),
            "frozen": frozen,
            "reportedAt": ts(),
        }
        await self.repo.save_channel(
            channel_id, record)
        # 观测事件埋点(归因可审计)
        await self.repo.save_event({
            "type": "health_report",
            "channelId": channel_id,
            "detail": {
                "attempt": attempt,
                "success": success,
                "rate": rate,
            },
            "at": ts(),
        })
        return record

    async def set_frozen(
            self, channel_id: str,
            frozen: bool) -> dict:
        """通道冻结/解冻(人工专属——
        frozen 永不可由成功率导出铁律)

        冻结=P1 路由永不选中该通道的
        前置标记(安全方向动作)

        Raises:
            KeyError: 通道域外
        """
        if channel_id not in CHANNEL_REGISTRY:
            raise KeyError(
                f"通道不存在(channelId={channel_id})")
        existing = await self.repo.get_channel(
            channel_id) or {}
        record = dict(existing)
        record.update({
            "channelId": channel_id,
            "frozen": bool(frozen),
            "state": ("frozen" if frozen
                      else existing.get(
                          "state", "healthy")),
            "frozenAt": ts() if frozen else "",
        })
        await self.repo.save_channel(
            channel_id, record)
        await self.repo.save_event({
            "type": "freeze_toggle",
            "channelId": channel_id,
            "detail": {"frozen": bool(frozen)},
            "at": ts(),
        })
        return record

    # ============================================================
    # ③ 意图标签解析(规则轨——LLM 禁入)
    # ============================================================

    # 关键词规则轨(封闭——确定性归属)
    INTENT_KEYWORDS: ClassVar[dict] = {
        "fast_needed": ("快", "急", "马上",
                        "立刻", "赶时间"),
        "large_amount": ("大额", "大笔",
                        "贵重", "大单"),
        "privacy_needed": ("隐私", "不留痕",
                           "匿名", "不想暴露"),
        "social_sharing": ("微信", "群里",
                           "分享", "代付"),
        "promo_hunting": ("优惠", "便宜",
                          "折扣", "活动",
                          "省钱"),
        "credit_preference": ("信用", "信值",
                              "赊", "后付",
                              "闪银"),
        "biometric_habit": ("刷脸", "指纹",
                            "面容", "指纹付"),
    }

    def parse_intent(self, intent_text: str,
                     member_id: int = 0) -> dict:
        """意图标签解析(规则轨——关键词
        确定性归属, 多标签可并存; 归属
        不明→default)

        LLM 禁入判定链铁律: 本函数为
        纯字符串规则轨(55号 clarify 范式),
        LLM 仅可消费本结果生成澄清建议,
        永不参与归属判定
        """
        text = str(intent_text or "")
        tags = []
        for tag, keywords in self.INTENT_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                tags.append(tag)
        if not tags:
            tags = ["default"]
        # 亲和通道(封闭映射——注册表内)
        candidates = []
        for tag in tags:
            for ch in INTENT_AFFINITY.get(tag, ()):
                if ch not in candidates:
                    candidates.append(ch)
        return {
            "memberId": int(member_id or 0),
            "intentText": text,
            "tags": tags,
            "candidateChannels": candidates,
            "engine": "rule_based",
            "parsedAt": ts(),
        }

    async def record_intent(
            self, intent_text: str,
            member_id: int = 0,
            intent_id: int = 0,
            session_id: int = 0) -> dict:
        """意图标签留痕(60号归因链消费——
        payId/intentId/sessionId 透传)

        Raises:
            ValueError: 文本空
        """
        text = str(intent_text or "").strip()
        if not text:
            raise ValueError("意图文本不可为空")
        parsed = self.parse_intent(
            text, member_id)
        record = dict(parsed)
        record.update({
            "intentId": int(intent_id or 0),
            "sessionId": int(session_id or 0),
        })
        await self.repo.save_intent(record)
        await self.repo.save_event({
            "type": "intent_parsed",
            "memberId": int(member_id or 0),
            "detail": {
                "tags": record["tags"],
                "candidates": record[
                    "candidateChannels"],
            },
            "at": ts(),
        })
        return record

    async def intent_view(
            self, limit: int = 50) -> dict:
        """意图留痕视图(观测面)"""
        intents = await self.repo.list_intents(
            limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "intentTags": list(INTENT_TAGS),
            "count": len(intents),
            "intents": intents,
        }

    # ============================================================
    # ④ 模型状态(第36档案口径——观测面)
    # ============================================================

    async def model_status(self) -> dict:
        """模型状态(观测面——off 不受影响)"""
        channels = await self.repo.list_channels()
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "channelCount": len(CHANNEL_IDS),
            "observedChannels": sum(
                1 for c in channels
                if c.get("observed", True)
                and c.get("attemptCount")),
            "healthStates": list(HEALTH_STATES),
            "intentTagCount": len(INTENT_TAGS),
        }
