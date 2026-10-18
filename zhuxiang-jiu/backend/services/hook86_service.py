"""86号·合规引流钩子 Agent 服务层(2026-10-18 外部方案甄别落地)

定位对齐外部方案核心理念: Agent=服务交付器而非引流机器人——
用户在公域(小程序竹香酒馆 webview 内 H5=zyjiu.com, 自有备案域名)
与钩子 Agent 交互获得选酒/品鉴价值 → 意图命中触发原生留资动作
→ 用户主动留资完成闭环。全程无外链无敏感词无强制诱导。

甄别后只做真缺口(75号已有意图编排/LLM网关, 酒类合规闸已有
local_guardrail+age_gate, 均复用不重建):
    ① C端留资表(手机号+渠道码+意图标签)
    ② 用户级 24h 频控(留资触发 ≤2 次/24h, 对齐方案频控红线)
    ③ 渠道码归因(留资落 channel_code, 渠道汇总)

设计原则:
    - 意图引擎 LLM 禁入(确定性关键词路由, 对齐 73号分级路由哲学)
    - 输入输出双向过小竹合规闸(check_input/filter_output)
    - 所有话术强制附"理性饮酒"声明(未成年禁酒+过量有害)
"""
from __future__ import annotations

import re

from repositories.hook86_repository import (
    LEAD_NEW, Hook86Repository)
from services.local_guardrail_service import get_guardrail

# 意图(确定性关键词路由; confidence=命中词数归一)
INTENTS = {
    "select_wine": ("选酒咨询", (
        "选酒", "推荐", "送人", "礼物", "礼盒", "哪种", "买什么",
        "入门", "新手", "口感", "香型", "浓香", "酱香", "米酒")),
    "taste": ("品鉴交流", (
        "品鉴", "尝", "回味", "度数", "酒精度", "酿造", "工艺",
        "原料", "存放", "陈酿", "年份")),
    "price": ("价格优惠", (
        "价格", "多少钱", "优惠", "折扣", "便宜", "活动价", "券")),
    "activity": ("活动福利", (
        "活动", "福利", "抽奖", "会员", "签到", "积分")),
}

# 意图 → 钩子动作(P0: 留资触发仅 select_wine/price 两类高意向)
ACTION_NONE = "none"
ACTION_LEAD = "lead_form"        # 引导留资(1v1 选酒顾问回访)
ACTION_GUIDE = "guide_content"   # 引导浏览内容(低意向)

_INTENT_ACTION = {
    "select_wine": ACTION_LEAD,
    "price": ACTION_LEAD,
    "taste": ACTION_GUIDE,
    "activity": ACTION_GUIDE,
    "other": ACTION_NONE,
}

REPLIES = {
    "select_wine": ("可以根据您的场景(送礼/自饮/宴请)和口感偏好帮您缩小范围, "
                    "竹香酒有浓香型与米香型多款。想要 1v1 选酒建议的话, "
                    "可预约顾问回访(留下手机号即可, 24小时内联系)。"),
    "taste": ("竹香酒以竹叶与糯米复合发酵著称, 入口柔、回甘带竹香; "
              "酒精度覆盖 28°-52°。可在「竹香故事」栏目查看酿造工艺详解。"),
    "price": ("当前在售款价格区间 68-398 元, 会员首单有礼。"
              "如需具体报价与优惠组合, 可预约顾问回访获取。"),
    "activity": ("本月有会员签到积分与佳节礼盒活动, "
                 "详见首页活动栏目。"),
    "other": ("我是竹香酒馆的选酒助手, 可以帮您选酒、了解品鉴知识、"
              "查询价格与活动。请问想了解哪方面?"),
}

DISCLAIMER = "(理性饮酒: 未成年人禁止饮酒, 过量饮酒有害健康)"

# 频控红线: 同一用户 24h 内留资触发 ≤2 次(外部方案对齐)
LEAD_FREQ_LIMIT = 2
_PHONE_RE = re.compile(r"^1[3-9][0-9]{9}$")
# PII 治理: 出口脱敏 138****1234; 明文仅存储层
# (mask_phone 已提取至 core.pii 公共件推广全站, 此处保持兼容再导出)
from core.pii import mask_phone  # noqa: E402


class HookIntentError(ValueError):
    """非法输入(手机号格式/参数缺失)"""


class Hook86Service:
    """钩子 Agent: 意图路由 + 合规闸 + 频控 + 留资登记"""

    def __init__(self) -> None:
        self.repo = Hook86Repository()

    # ---------- 意图对话(输入输出双向合规闸) ----------

    def classify(self, message: str) -> dict:
        """确定性意图分类(关键词命中数 → 意图+置信度)"""
        text = str(message or "").lower()
        best, best_hits = "other", 0
        for intent, (_, words) in INTENTS.items():
            hits = sum(1 for w in words if w in text)
            if hits > best_hits:
                best, best_hits = intent, hits
        conf = min(1.0, best_hits / 3) if best_hits else 0.0
        return {"intent": best, "label": INTENTS.get(
            best, ("其他", None))[0], "confidence": round(conf, 2)}

    async def chat(self, message: str, *, subject: str,
                   channel_code: str = "") -> dict:
        """对话入口: 合规闸包夹 + 意图路由 + 动作建议(带频控预检)"""
        g = get_guardrail()
        verdict = g.check_input(message, context={
            "sessionId": subject, "traceId": "hook86"})
        if verdict["blocked"]:
            await self.repo.save_chat({
                "subject": subject, "channelCode": channel_code,
                "intent": "", "action": ACTION_NONE,
                "blocked": True, "confidence": 0,
                "text": mask_phone(str(message))[:120],
                "at": __import__("datetime").datetime.utcnow().isoformat()})
            return {"blocked": True,
                    "category": verdict["category"],
                    "reply": verdict["response"] + DISCLAIMER,
                    "intent": "", "action": ACTION_NONE}
        result = self.classify(message)
        intent = result["intent"]
        action = _INTENT_ACTION[intent]
        freq = {"allowed": True, "count": 0, "retryIn": 0}
        if action == ACTION_LEAD:
            # 留资类动作先预检频控(不计数, 提交留资时才计数)
            freq = await self.repo.freq_hit(
                subject, "lead_precheck", limit=99)
        reply = g.filter_output(REPLIES[intent],
                                context={"sessionId": subject})
        # P1 对话留痕(脱敏快照): 互动质量指标数据源
        await self.repo.save_chat({
            "subject": subject, "channelCode": channel_code,
            "intent": intent, "action": action,
            "blocked": False, "confidence": result["confidence"],
            "text": mask_phone(str(message))[:120],
            "at": __import__("datetime").datetime.utcnow().isoformat()})
        return {"blocked": False, **result, "action": action,
                "reply": reply + DISCLAIMER, "channelCode": channel_code,
                "freq": freq}

    # ---------- 留资登记(幂等+频控硬闸) ----------

    async def submit_lead(self, phone: str, *, channel_code: str = "",
                          intent: str = "", subject: str = "") -> dict:
        phone = str(phone or "").strip()
        if not _PHONE_RE.match(phone):
            raise HookIntentError("手机号格式不正确(须11位大陆手机号)")
        channel_code = str(channel_code or "").strip()
        subj = subject or f"phone:{phone[-4:]}"
        # 幂等优先: 同手机号+渠道 24h 内重复提交不消耗频控额度
        if await self.repo.find_dup(phone, channel_code):
            return {"ok": True, "duplicate": True, "leadId": 0,
                    "status": LEAD_NEW}
        # 频控硬闸: 24h ≤2 次(超限拒绝, 对齐骚扰红线)
        freq = await self.repo.freq_hit(
            subj, "lead", limit=LEAD_FREQ_LIMIT)
        if not freq["allowed"]:
            return {"ok": False, "reason": "freq_limited",
                    "retryInSec": freq["retryIn"]}
        lead_id = await self.repo.next_lead_id()
        lead = {"leadId": lead_id, "phone": phone,
                "channelCode": channel_code,
                "intent": intent, "status": LEAD_NEW,
                "memberId": subj if subj.startswith("m:") else "",
                "createdAt": __import__("datetime").datetime.utcnow().isoformat(),
                "source": "douyin_miniapp_h5"}
        await self.repo.save_lead(lead)
        return {"ok": True, "duplicate": False,
                "leadId": lead_id, "status": LEAD_NEW}

    # ---------- 查询/归因(admin; PII 出口一律脱敏) ----------

    async def leads(self, *, status: str = "", channel: str = "",
                    limit: int = 100) -> list[dict]:
        rows = await self.repo.list_leads(
            status=status, channel=channel, limit=limit)
        for x in rows:                      # PIPL 展示最小化
            if x.get("phone"):
                x["phone"] = mask_phone(x["phone"])
        return rows

    async def channels(self) -> list[dict]:
        return await self.repo.channel_summary()

    async def delete_lead(self, lead_id: int) -> bool:
        """数据主体删除权(PIPL §47): 物理删除留资+幂等索引"""
        return await self.repo.delete_lead(lead_id)

    async def export_lead(self, phone: str, *, operator: str = "") -> dict:
        """数据主体导出权(PIPL §45, admin 代执行)

        返回该手机号全部留资记录(完整明文——交付数据主体本人的
        副本); 敏感动作强制审计留痕(operator+phone 掩码+时间)。
        """
        import datetime as _dt
        phone = str(phone or "").strip()
        rows = await self.repo.find_leads_by_phone(phone)
        await self.repo.log_export({
            "operator": operator or "admin",
            "phoneMasked": mask_phone(phone),
            "count": len(rows),
            "at": _dt.datetime.utcnow().isoformat()})
        return {"phone": phone, "count": len(rows), "leads": rows}

    async def metrics(self) -> dict:
        """互动质量指标(2026-10-18 外部方案·四层归因的最小实现)

        chats=对话总量; intentHits=意图命中(非other)占比即
        有效对话率; leadForms=高意向动作数; chat→lead 转化率;
        blockedRate=合规闸拦截率(负向互动占比)。
        """
        chats = await self.repo.list_chats(limit=5000)
        leads = await self.repo.list_leads(limit=10000)
        n = len(chats)
        intent_hits = sum(1 for c in chats
                          if c.get("intent") and c["intent"] != "other")
        blocked = sum(1 for c in chats if c.get("blocked"))
        lead_forms = sum(1 for c in chats
                         if c.get("action") == ACTION_LEAD)
        by_intent: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        for c in chats:
            by_intent[c.get("intent") or "other"] = (
                by_intent.get(c.get("intent") or "other", 0) + 1)
            ch = c.get("channelCode") or "(direct)"
            by_channel[ch] = by_channel.get(ch, 0) + 1
        return {
            "chats": n,
            "validChatRate": round(intent_hits / n, 4) if n else 0,
            "blockedRate": round(blocked / n, 4) if n else 0,
            "leadFormTriggers": lead_forms,
            "leads": len(leads),
            "chatToLeadRate": (
                round(len(leads) / n, 4) if n else 0),
            "intentDist": by_intent, "channelChats": by_channel}

    async def metrics_with_trend(self) -> dict:
        """metrics 单点快照 + recent7 日落点趋势(P3)"""
        current = await self.metrics()
        current["recent7"] = await self.repo.list_metric_snapshots(7)
        return current

    def status(self) -> dict:
        from services.hook86_scheduler import (
            last_run, retention_days, scheduler_enabled)
        return {"module": "hook86", "storage": "redis"
                if __import__(
                    "repositories.backend", fromlist=["x"]).is_redis_mode()
                else "memory",
                "leadFreqLimit24h": LEAD_FREQ_LIMIT,
                "intents": [v[0] for v in INTENTS.values()],
                "disclaimer": DISCLAIMER,
                "ttl": {"auto": scheduler_enabled(),
                        "retentionDays": retention_days(),
                        "lastRun": last_run()}}
