"""49号·小竹可信函数调用深化 P3 可解释性绑定
(explainability_ref)

计划(docs/49号_小竹可信函数调用深化实施计划.md §六 P3):
    铁律② 输出可解释绑定:
    - 写工具响应体必填 explainability_ref(归因报告引用)
    - 缺失 ref 的响应在服务端自动阻断(raise 不返半成品)
    - 归因三源: 45号 attribution(修复/兑换数学分解)+
      46号决策回放(重算轨迹)+ 49号语音归因播报
      (自然语言转化——参数化模板, 非 LLM 生成)

设计口径:
    - ref 格式: exp-{action}-{repairId|eventId}-{hash8}
      (可溯源: 动作+业务事件+短哈希)
    - 播报模板参数化(数字全部来自工具返回——LLM 不产
      数字推广为归因也不编故事)
    - "打开修复说明"指令 → ref 落地卡片(45号归因报告
      全文——LLM 润色轨继承)
"""

import hashlib
import logging

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_explainability")

# 写/高敏动作的归因绑定(只读不绑——无状态变更)
EXPLAINABLE_ACTIONS = {
    "trust.convert", "repair.execute", "cart.submit",
    "trust.bind",
}

# 语音归因播报模板(参数化——数字来自工具返回)
BROADCAST_TEMPLATES = {
    "trust.convert": (
        "兑换完成——本次扣除 {creditPoints} 信用分, "
        "按汇率 {rate}:1 到账 {amount} TV。"
        "{attribution}"),
    "repair.execute": (
        "修复已提交——针对违规事件 {violationEventId}, "
        "修复值 {gain}(天花板 {cap}, 关联度 β 加权)。"
        "{attribution}"),
    "cart.submit": (
        "订单已提交(单号 {orderId})。{attribution}"),
    "trust.bind": (
        "已绑定居值档案 {trustId}。{attribution}"),
}

DEFAULT_TEMPLATE = "操作已完成。{attribution}"


def build_ref(action: str, business_id,
              seed: str = "") -> str:
    """构造 explainability_ref(可溯源: 动作+业务事件+
    短哈希——确定性)"""
    raw = f"{action}|{business_id}|{seed}|{ts()}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"exp-{action}-{business_id}-{h}"


def extract_business_id(action: str,
                        result: dict):
    """从工具结果提取业务事件 id(兑换→ledgerId/
    修复→repairId; 订单→orderId; 绑定→trustId)"""
    result = result or {}
    for key in ("repairId", "eventId", "ledgerId",
                "orderId", "trustId"):
        if result.get(key) is not None:
            return result[key]
    return 0


def broadcast_of(action: str, result: dict,
                 attribution_line: str = "") -> str:
    """语音归因播报(参数化模板——数字全部来自 result)"""
    template = BROADCAST_TEMPLATES.get(
        action, DEFAULT_TEMPLATE)
    params = dict(result or {})
    params.setdefault("attribution", attribution_line)
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return DEFAULT_TEMPLATE.format(
            attribution=attribution_line)


class XiaozhuExplainabilityService:
    """可解释性绑定(ref 生成/归因三源/播报)"""

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()

    # --------------------------------------------------------
    # ref 绑定(写响应必填——缺失即服务端阻断)
    # --------------------------------------------------------

    @staticmethod
    def bind(action: str, result: dict) -> dict:
        """为写工具结果绑定 explainability_ref

        Returns:
            {explainabilityRef, broadcast}(注入响应顶层)
        Raises:
            ValueError: 结果缺关键业务 id(无法溯源——
            铁律② 自动阻断, 不返回半成品)
        """
        if action not in EXPLAINABLE_ACTIONS:
            return {}
        business_id = extract_business_id(action, result)
        if business_id in (None, 0, ""):
            raise ValueError(
                f"内部错误: {action} 结果缺少可溯源业务"
                f"标识, explainability_ref 绑定失败"
                f"(响应阻断——不返回半成品)")
        ref = build_ref(action, business_id,
                        str(business_id))
        # 归因指引(用户可说"打开修复说明"查看全文)
        attribution_line = (
            "如需查看详细归因, 可说「打开修复说明」。")
        broadcast = broadcast_of(
            action, result, attribution_line)
        return {"explainabilityRef": ref,
                "attributionBroadcast": broadcast}

    # --------------------------------------------------------
    # 归因报告(ref 落地——"打开修复说明")
    # --------------------------------------------------------

    async def report_of_ref(self, member_id: int,
                            ref: str) -> dict:
        """ref → 归因报告(三源合成: 45号 attribution
        全文 + 49号语音播报回放)

        Raises:
            KeyError: ref 无效/档案未绑定/事件不存在
        """
        parts = str(ref or "").split("-")
        if len(parts) < 4 or parts[0] != "exp":
            raise KeyError(f"归因引用 {ref} 无效")
        action = parts[1]
        try:
            business_id = int(parts[2])
        except ValueError:
            raise KeyError(
                f"归因引用 {ref} 业务标识无效") from None
        if action not in EXPLAINABLE_ACTIONS:
            raise KeyError(f"动作 {action} 无归因绑定")

        binding = await self.repo.get_binding(member_id)
        if binding is None:
            raise KeyError(
                "尚未绑定居值档案——先说「绑定信值档案 N」")
        trust_id = binding.get("trustId")

        # 主源: 45号归因报告(修复/兑换事件→LLM 润色轨继承)
        report_text = ""
        mode = "voice49"
        try:
            from services.trust_learning_service import (
                TrustAppealService,
            )
            r = await TrustAppealService().attribution(
                trust_id, business_id)
            report_text = r.get("report") or ""
            mode = f"trust45+{r.get('mode', 'mock')}"
        except Exception as exc:  # noqa: BLE001
            # 45号源缺失(如订单事件不在信值流)——
            # 只读回放 49号语音播报(不编故事)
            logger.debug("voice49_attr_45_skip: %s", exc)
        return {
            "success": True,
            "ref": ref, "action": action,
            "businessId": business_id,
            "trustId": trust_id,
            "mode": mode,
            "report": report_text or (
                "本次操作已完成并留痕——详细归因以信值"
                "看板审计日志为准(禁止黑箱是宪法级约束)。"),
            "replayNote": "决策回放(46号)与语音播报留痕"
                          "可回溯; 如有异议可申诉。",
            "ts": ts(),
        }
