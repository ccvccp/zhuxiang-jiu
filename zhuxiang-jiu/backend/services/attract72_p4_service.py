"""72号·AI智能自动引流大模型 P4 短链记忆服务
(attract72_p4_service)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§四 4.2 + §八 P4 3 端点):
    - ctx 上下文编解码(?ctx={base64}, 脱敏画像
      片段——无 PII 铁律)
    - 动态落地页决策(画像×变体匹配分查表,
      LLM 禁入判定链)
    - 跨会话记忆(deviceFingerprint→行为画像
      累积, 注册后归并继承)
    - 反作弊隔离(同指纹异常频次→隔离,
      模式沉淀红队记忆)

铁律(宪法十则 §九):
    - ctx 仅脱敏画像片段(无 PII)
    - AB 分流零改动(ctx 与 AB 正交)
    - 向后兼容(无 ctx 行为与 v1.0 一致)
"""

import base64
import binascii
import json
import logging
import time

from core.helpers import ts
from repositories.attract72_repository import (
    Attract72Repository,
)
from services.attract72_registry import (
    FINGERPRINT_RATE_LIMIT,
    CTX_TTL_SECONDS,
    DWELL_HIGH_SECONDS,
)

logger = logging.getLogger("attract72_p4")


class Attract72P4Service:
    """P4 短链记忆服务(ctx/动态落地页/跨会话/
    反作弊——观测/快环口径, 不受 MODE 门控)"""

    def __init__(self):
        self.repo = Attract72Repository()

    # ============================================================
    # ① ctx 编解码(公开——短链入口消费)
    # ============================================================

    @staticmethod
    def encode_ctx(fingerprint: str,
                   content_id: str = "",
                   intent_tags: list = None) -> str:
        """编码 ctx(脱敏画像片段——base64)

        仅携带: 指纹摘要/来源内容/意图标签
        (无 PII 铁律——不含手机号/姓名/地址)
        """
        payload = {
            "fp": (fingerprint or "")[:16],
            "cid": str(content_id or "")[:32],
            "it": sorted(set(intent_tags or []))[:6],
            "ts": int(time.time()),
        }
        raw = json.dumps(payload,
                         ensure_ascii=False,
                         separators=(",", ":"))
        return base64.urlsafe_b64encode(
            raw.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode_ctx(ctx: str) -> dict | None:
        """解码 ctx(无效/超期返回 None→default 变体)

        超期降级: ctx 龄 > CTX_TTL_SECONDS
        (向后兼容: None 时行为与 v1.0 一致)
        """
        if not ctx or not ctx.strip():
            return None
        try:
            raw = base64.urlsafe_b64decode(
                ctx.strip().encode("ascii"))
            payload = json.loads(
                raw.decode("utf-8"))
        except (binascii.Error, ValueError,
                UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        # 超期降级
        born = payload.get("ts") or 0
        try:
            born = int(born)
        except (TypeError, ValueError):
            return None
        if time.time() - born > CTX_TTL_SECONDS:
            return None
        return {
            "fp": str(payload.get("fp") or "")[:16],
            "cid": str(payload.get("cid") or "")[:32],
            "it": payload.get("it") or [],
        }

    # ============================================================
    # ② 动态落地页决策(确定性匹配分查表)
    # ============================================================

    async def decide_landing(
            self, code: str,
            fingerprint: str = "",
            ctx: str = "") -> dict:
        """动态落地页决策(观测面——快环)

        变体选择(确定性查表, LLM 禁入):
            - 反作弊隔离态 → default(隔离窗内
              拒个性化, 不拒服务)
            - 高参与信号(dwell≥120s 或
              high_engagement 意图) →
              benefit_first(老客直显权益)
            - 新指纹(零记忆)或 bounce_risk →
              trust_first(新客强化信任)
            - 其余 → default

        Args:
            code: 短链码(v1.0 兼容——仅透传)
            fingerprint: 设备指纹(空则 ctx.fp)
            ctx: base64 上下文(可选)

        Raises:
            ValueError: code 为空
        """
        if not code or not code.strip():
            raise ValueError("短链码不能为空")

        decoded = self.decode_ctx(ctx)
        fp = fingerprint or (decoded or {}).get("fp", "")

        # 反作弊前置(隔离态→default)
        memory = await self.repo.get_memory(fp) \
            if fp else None
        isolated = bool(memory
                        and memory.get("isolated"))

        intent_tags = (decoded or {}).get("it", [])
        variant = "default"
        reason = "无指纹兜底"

        if isolated:
            variant = "default"
            reason = "反作弊隔离窗内(不个性化)"
        elif memory and memory.get("clicksTotal", 0) > 0:
            # 老客: 高参与→权益优先
            if ("high_engagement" in intent_tags
                    or (memory.get("avgDwell") or 0)
                    >= DWELL_HIGH_SECONDS):
                variant = "benefit_first"
                reason = "高参与老客(直显权益)"
            elif "bounce_risk" in intent_tags:
                variant = "trust_first"
                reason = "跳出风险(强化信任)"
            else:
                variant = "benefit_first"
                reason = "回访用户(直显权益)"
        elif fp:
            variant = "trust_first"
            reason = "新指纹(强化信任)"
            if "bounce_risk" in intent_tags:
                reason = "新指纹+跳出风险(强化信任)"

        # 变体分布统计( impressions 计数)
        await self._record_variant_imp(variant)

        return {
            "code": code,
            "variant": variant,
            "reason": reason,
            "fingerprint": fp,
            "intentTags": intent_tags,
            "isolated": isolated,
            "at": ts(),
        }

    async def _record_variant_imp(
            self, variant: str) -> None:
        """变体曝光计数(确定性滚动)"""
        record = await self.repo \
            .find_variant_by_name(variant)
        if record is None:
            vid = await self.repo.next_id("variant")
            record = {
                "variantId": vid,
                "variant": variant,
                "impressions": 0,
                "conversions": 0,
                "at": ts(),
            }
        record["impressions"] = \
            int(record.get("impressions", 0)) + 1
        await self.repo.save_variant(record)

    # ============================================================
    # ③ 跨会话记忆(指纹→行为画像累积)
    # ============================================================

    async def record_visit(
            self, fingerprint: str,
            dwell_seconds: float = 0.0,
            converted: bool = False,
            registered: bool = False,
            intent_tags: list = None) -> dict:
        """跨会话记忆累积(指纹 upsert——快环)

        注册归并: registered=True 时记忆标记
        归属(后续个性化继承); 反作弊: 窗口内
        频次超线→隔离。

        Raises:
            ValueError: 指纹为空
        """
        if not fingerprint or \
                not fingerprint.strip():
            raise ValueError("设备指纹不能为空")
        fp = fingerprint.strip()[:16]

        record = await self.repo.get_memory(fp)
        if record is None:
            record = {
                "deviceFingerprint": fp,
                "clicksTotal": 0,
                "conversions": 0,
                "avgDwell": 0.0,
                "registered": False,
                "isolated": False,
                "isolatedAt": "",
                "seenVariants": [],
                "intentTags": sorted(
                    set(intent_tags or []))[:8],
                "firstAt": ts(),
                "lastAt": ts(),
            }

        total = int(record.get("clicksTotal", 0)) + 1
        record["clicksTotal"] = total
        # 滚动均值(确定性)
        prev = float(record.get("avgDwell") or 0)
        record["avgDwell"] = round(
            (prev * (total - 1)
             + float(dwell_seconds or 0)) / total, 2)
        if converted:
            record["conversions"] = \
                int(record.get("conversions", 0)) + 1
        if registered:
            record["registered"] = True
        if intent_tags:
            merged = set(record.get("intentTags")
                         or []) | set(intent_tags)
            record["intentTags"] = sorted(merged)[:8]

        # 反作弊: 窗口频次(小时窗口近似——
        # clicksTotal/隔离窗估算)
        window_clicks = total - int(
            record.get("windowBaseClicks", 0))
        if (not record.get("isolated")
                and window_clicks
                > FINGERPRINT_RATE_LIMIT):
            record["isolated"] = True
            record["isolatedAt"] = ts()
            logger.warning(
                "attract72_p4_fingerprint_isolated"
                " fp=%s clicks=%s", fp[:4], total)
        record["lastAt"] = ts()
        await self.repo.save_memory(record)
        return record

    async def get_memory(self,
                          fingerprint: str) -> dict:
        """跨会话画像查询

        Raises:
            KeyError: 记忆不存在(新指纹)
        """
        record = await self.repo.get_memory(
            fingerprint)
        if record is None:
            raise KeyError(
                f"跨会话记忆不存在("
                f"fingerprint={fingerprint[:4]}**)")
        return record

    # ============================================================
    # ④ 反作弊(隔离/解冻——admin)
    # ============================================================

    async def isolate_fingerprint(
            self, fingerprint: str) -> dict:
        """人工/监控隔离指纹(admin)

        Raises:
            KeyError: 记忆不存在
        """
        record = await self.get_memory(fingerprint)
        record["isolated"] = True
        record["isolatedAt"] = ts()
        await self.repo.save_memory(record)
        logger.warning(
            "attract72_p4_manual_isolate fp=%s",
            fingerprint[:4])
        return record

    async def release_fingerprint(
            self, fingerprint: str) -> dict:
        """人工解冻(admin——解冻人工专属铁律)

        Raises:
            KeyError: 记忆不存在
        """
        record = await self.get_memory(fingerprint)
        record["isolated"] = False
        record["isolatedAt"] = ""
        # 窗口基准重置(解冻后重新计数)
        record["windowBaseClicks"] = \
            int(record.get("clicksTotal", 0))
        await self.repo.save_memory(record)
        return record

    async def list_variants(
            self, limit: int = 100) -> list[dict]:
        """变体分布台账(观测面)"""
        return await self.repo.list_variants(limit)
