"""69号·AI智能支付大模型 多模态普惠服务
(pay69_modality_service, P6)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.6/§七 P6):
    ① 多模态入口(语音/手势/眼动/文本)
       →意图规则轨三态解析(55号 P0
       direct/confirm/clarify 范式)
    ② 无障碍适配(群体→渲染档案——
       老年大字体/语音确认优先, 运动
       障碍手势优先, 视障播报优先)
    ③ 资金确认显式铁律(确认令牌
       TTL+金额回显+确认词匹配——
       确认后仅生成 60号开单建议包,
       实际开单由 60号收银台显式调用)
    ④ 群体×模态×结果三轴统计(快环
       ——意图解析准确率基线)

铁律(规划 §4.6):
    - 意图解析规则轨(LLM 仅澄清
      问句生成, 对齐 55号 clarify 域
      ——engine=rule_based)
    - 资金确认永远显式(语音支付最后
      一步回显金额+确认词)
    - 49号隐私预算联动为消费方声明
      (49号零改动——实际接入在语音
      真实链路层)
"""

import logging
import re
import secrets

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    ACCESS_GROUPS,
    ACCESSIBILITY_PROFILES,
    CHANNEL_KEYWORDS,
    CONFIRM_WORDS,
    INTENT_OUTCOMES,
    MODALITIES,
    MODALITY_CONFIRM_TTL,
    PRODUCT_KEYWORDS,
    MODEL_VERSION,
)

logger = logging.getLogger("pay69_modality")

# 支付动词域(规则轨——意图判定前置)
PAY_VERBS = (
    "付", "支付", "买单", "结账", "下单",
)

# 金额提取正则(双分支: ¥123 免元
# / 123元 / 123.5元——无前缀必须带
# 元, 防误捕普通数字)
_AMOUNT_RE = re.compile(
    r"(?:¥|￥)(\d+(?:\.\d{1,2})?)"
    r"|(\d+(?:\.\d{1,2})?)\s*元")


class Pay69ModalityService:
    """69号多模态普惠(P6)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # ① 意图规则轨解析(三态)
    # ============================================================

    @staticmethod
    def _extract_amount(text: str):
        """金额提取(规则轨——正则双分支:
        ¥前缀免元/无前缀须元)"""
        m = _AMOUNT_RE.search(text)
        if not m:
            return None
        raw = m.group(1) or m.group(2)
        amount = round(float(raw), 2)
        return amount if amount > 0 \
            else None

    @staticmethod
    def _extract_product(text: str):
        """商品提取(规则轨——关键词域,
        长词优先)"""
        for kw in sorted(
                PRODUCT_KEYWORDS,
                key=len, reverse=True):
            if kw in text:
                return kw
        return None

    @staticmethod
    def _extract_channel(text: str):
        """通道偏好提取(规则轨——关键词
        映射, 长词优先)"""
        for cid, kws in sorted(
                CHANNEL_KEYWORDS.items(),
                key=lambda kv: len(kv[1][0]),
                reverse=True):
            if any(kw in text
                   for kw in kws):
                return cid
        return None

    def parse(self, member_id: int,
              text: str,
              modality: str = "voice",
              access_group: str
              = "standard") -> dict:
        """多模态意图解析(规则轨三态——
        LLM 禁入判定链, engine 标识实证)

        三态:
            direct: 金额+商品/方式齐→
                    直出 60号开单建议参数
            confirm: 金额明确, 商品/方式
                    缺→待确认清单
            clarify: 无支付意图/金额缺→
                    澄清问句(候选列表)

        Raises:
            ValueError: 模态/群体域外
        """
        if modality not in MODALITIES:
            raise ValueError(
                f"模态域外: {modality}"
                f"(须 {MODALITIES})")
        if access_group not in ACCESS_GROUPS:
            raise ValueError(
                f"无障碍群体域外: "
                f"{access_group}")
        text = str(text or "")
        has_pay = any(
            v in text for v in PAY_VERBS)
        amount = self._extract_amount(text)
        product = self._extract_product(text)
        channel = self._extract_channel(text)

        if not has_pay or amount is None:
            outcome = "clarify"
            question = (
                "请问您要支付多少金额?"
                if has_pay
                else "请问您需要支付吗?"
                " 可说: 帮我支付 99 元")
            missing = (["amount"]
                       if has_pay
                       else ["pay_intent"])
        elif product is None \
                and channel is None:
            outcome = "confirm"
            question = ""
            missing = ["product", "channel"]
        else:
            outcome = "direct"
            question = ""
            missing = []

        profile = dict(
            ACCESSIBILITY_PROFILES[
                access_group])
        record = {
            "memberId": int(member_id or 0),
            "modality": modality,
            "accessGroup": access_group,
            "outcome": outcome,
            "amount": amount,
            "product": product,
            "channelId": channel,
            "missing": missing,
            "clarifyQuestion": question,
            "accessibility": profile,
            "engine": "rule_based",
            "parsedAt": ts(),
        }
        return record

    async def parse_logged(
            self, member_id: int, text: str,
            modality: str = "voice",
            access_group: str
            = "standard") -> dict:
        """多模态意图解析+留痕+群体统计
        (决策面——三轴快环计数)

        Raises:
            ValueError: 模态/群体域外
        """
        record = self.parse(
            member_id, text, modality,
            access_group)
        await self.repo.save_modality_event(
            dict(record))
        await self.repo.bump_group_stat(
            access_group, modality,
            record["outcome"])
        await self.repo.save_event({
            "type": "modality_parsed",
            "memberId": int(member_id or 0),
            "detail": {
                "modality": modality,
                "outcome": record["outcome"],
            },
            "at": ts(),
        })
        return record

    # ============================================================
    # ③ 资金确认显式(令牌+回显+确认词)
    # ============================================================

    async def request_confirmation(
            self, member_id: int, amount: float,
            product: str = "",
            channel_id: str = "",
            modality: str = "voice",
            access_group: str
            = "standard") -> dict:
        """确认请求(金额回显+确认令牌——
        资金确认显式铁律第一步)

        回显文本必须含金额与商品(老年
        群体慢速播报口径由前端消费
        accessibility 档案实现)

        Raises:
            ValueError: 金额非法/域外
        """
        amount = round(
            float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        if modality not in MODALITIES:
            raise ValueError(
                f"模态域外: {modality}")
        if access_group not in ACCESS_GROUPS:
            raise ValueError(
                f"无障碍群体域外: "
                f"{access_group}")
        token = secrets.token_hex(16)
        product_label = (
            product or "所选商品")
        record = {
            "memberId": int(member_id or 0),
            "amount": amount,
            "product": str(product or ""),
            "channelId": str(
                channel_id or ""),
            "modality": modality,
            "accessGroup": access_group,
        }
        await self.repo.save_confirm_token(
            token, dict(record),
            ttl=MODALITY_CONFIRM_TTL)
        echo_text = (
            f"您将支付 ¥{amount} 购买"
            f"{product_label}, 请说"
            f"\"确认支付\"完成付款")
        return {
            "confirmToken": token,
            "ttlSeconds":
                MODALITY_CONFIRM_TTL,
            "echoText": echo_text,
            "amount": amount,
            "product": product_label,
            "channelId": channel_id,
            "accessibility": dict(
                ACCESSIBILITY_PROFILES[
                    access_group]),
            "modelVersion": MODEL_VERSION,
            "requestedAt": ts(),
        }

    async def confirm_payment(
            self, token: str,
            word: str) -> dict:
        """确认支付(确认词匹配+令牌单次
        消费——资金确认显式铁律第二步)

        确认通过仅生成 60号开单建议包
        (checkoutSuggestion)——实际开单
        由 60号收银台显式调用, 本模块
        永不自动执行资金操作

        Raises:
            KeyError: 令牌不存在/过期
            ValueError: 确认词不匹配
                (令牌保留可重试)
        """
        record = await self.repo\
            .peek_confirm_token(token)
        if not record:
            raise KeyError(
                f"确认令牌无效或已过期"
                f"(token={token[:8]}…)")
        word = str(word or "").strip()
        if word not in CONFIRM_WORDS:
            # 词不匹配不消费令牌
            # (允许重试——令牌随机
            # 32 字节+TTL 为防伪核心)
            raise ValueError(
                f"确认词不匹配: {word!r}"
                f"(须含 "
                f"{CONFIRM_WORDS[0]})")
        # 确认成功才消费(单次)
        await self.repo\
            .pop_confirm_token(token)
        # 60号开单建议包(60号收银台
        # create_order 参数口径——
        # 永不自动执行)
        suggestion = {
            "memberId": record[
                "memberId"],
            "basePrice": record["amount"],
            "scene": "purchase",
            "role": "member",
            "renderHint": {
                "product": record[
                    "product"],
                "channelId": record[
                    "channelId"],
                "modality": record[
                    "modality"],
            },
        }
        result = {
            "confirmed": True,
            "executed": False,
            "checkoutSuggestion":
                suggestion,
            "note": ("建议包——实际开单"
                     "由 60号收银台显式"
                     "调用(资金永不自动"
                     "铁律)"),
            "confirmedAt": ts(),
            "modelVersion": MODEL_VERSION,
        }
        await self.repo.save_modality_event({
            "memberId": record["memberId"],
            "modality": record["modality"],
            "accessGroup": record[
                "accessGroup"],
            "outcome": "direct",
            "amount": record["amount"],
            "product": record["product"],
            "channelId": record[
                "channelId"],
            "confirmed": True,
            "parsedAt": ts(),
        })
        await self.repo.save_event({
            "type": "modality_confirmed",
            "memberId": record["memberId"],
            "detail": {
                "amount": record["amount"],
                "executed": False,
            },
            "at": ts(),
        })
        return result

    # ============================================================
    # ④ 观测面
    # ============================================================

    def modality_dict(self) -> dict:
        """多模态字典公示(模态/群体/
        三态+档案+确认词——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "modalities": list(MODALITIES),
            "accessGroups": list(
                ACCESS_GROUPS),
            "accessibilityProfiles": {
                g: dict(p)
                for g, p in
                ACCESSIBILITY_PROFILES
                .items()},
            "intentOutcomes": list(
                INTENT_OUTCOMES),
            "confirmWords": list(
                CONFIRM_WORDS),
            "confirmTtl":
                MODALITY_CONFIRM_TTL,
            "ironRules": (
                "意图解析规则轨"
                "(LLM 仅澄清问句)",
                "资金确认永远显式"
                "(回显金额+确认词)",
                "确认后仅建议包"
                "(60号开单显式调用)"),
        }

    async def modality_events_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """多模态事件视图(观测面)"""
        events = await self.repo\
            .list_modality_events(
                member_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(events),
            "events": events,
        }

    async def group_stats_view(self) -> dict:
        """群体×模态×结果三轴统计(快环
        基线——观测面)"""
        stats = await self.repo\
            .get_group_stats()
        # 解析准确率(direct 占比——
        # 快环基线指标)
        accuracy = {}
        for group, axes in stats.items():
            total = sum(axes.values())
            direct = sum(
                v for k, v in
                axes.items()
                if k.endswith(":direct"))
            accuracy[group] = round(
                direct / total, 4) \
                if total else 0.0
        return {
            "modelVersion": MODEL_VERSION,
            "stats": stats,
            "directRateByGroup": accuracy,
        }
