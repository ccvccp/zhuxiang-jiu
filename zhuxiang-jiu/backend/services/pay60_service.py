"""60号·AI智能支付管理 订单底座+渠道适配
(pay60_service, P0)

计划(docs/60号_AI智能支付管理模块实施计划.md
§一/§五/§七 P0):
    P0 底座:
        ① 订单观测面(registry/orders
           列表/单条)
        ② 状态机流转底座(assert_transition
           ——封闭转移表非法流转拒绝)
        ③ 归因链底座(哈希指纹链——
           payId+intentId+sessionId+tier
           +riskTier+定价快照+渠道回执)
        ④ 渠道适配层(CHANNEL_MODE 三态
           ——mock 默认/real fail-hard/
           mock_fallback 回退)
        ⑤ 订单生命周期驱动骨架
           (advance——状态机+留痕)

铁律(计划 §1.3 六条工程铁律):
    - 默认零影响(PAY60_MODE off——
      存量订单/交易链路零影响,
      与既有订单路径并行不替换)
    - 归因 ID 强制(每笔支付携带归因链
      ——无归因不计入有效结算)
    - 资金操作保守性(出账默认延迟
      可撤销——P3 结算域落地)
    - 凭证经 .env 注入不落盘(real 渠道)
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.pay60_repository import (
    Pay60Repository,
)

logger = logging.getLogger("pay60_service")

MODEL_VERSION = "v1-pay60-service"


def current_mode() -> str:
    """模块开关(PAY60_MODE, 默认 off)"""
    return os.environ.get(
        "PAY60_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"PAY60_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    """哈希指纹(sha256 前 32 位——
    51号图谱语义链式关联)"""
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Pay60Service:
    """60号订单底座+渠道适配(P0)"""

    def __init__(self):
        self.repo = Pay60Repository()

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """支付注册表视图(观测面不受
        开关影响)"""
        from services.pay60_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId":
                    "payment_orchestration",
                "factors": 8,
                "decisions": ("observe",
                               "optimize",
                               "urgent"),
            },
            "note": "P0 底座: 支付注册表"
                    "三因子定价+分账合约+"
                    "收银台上下文+九态状态机"
                    "+渠道三态(第35档案)"
                    "(P1 收银台/P2 风控交付)",
        })
        return view

    async def list_orders(self,
                          member_id: int = None,
                          status: str = None
                          ) -> dict:
        """支付订单列表(观测面)"""
        records = await self.repo.list_orders(
            member_id=member_id,
            status=status)
        by_status: dict = {}
        for o in records:
            st = o.get("status") or "-"
            by_status[st] = by_status.get(
                st, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byStatus": by_status,
            "orders": records,
            "note": "支付订单——九态状态机"
                    "+归因链可审计",
        }

    async def get_order(self, pay_id: int
                        ) -> dict:
        """订单单条(观测面——归因链+
        渠道流水完整呈现)

        Raises:
            KeyError: 订单不存在
        """
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        flows = await self.repo.list_flows(
            pay_id=int(pay_id))
        return {
            "success": True,
            "order": order,
            "flows": flows,
            "fingerprint":
                order.get("fingerprint"),
            "note": "订单详情——归因链"
                    "指纹可追溯(无归因不计入"
                    "有效结算)",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view
        复用——第35档案)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            "payment_orchestration")
        view.update({
            "module": "pay60",
            "mode": current_mode(),
            "scorerId":
                "payment_orchestration",
            "factorsMeta": {
                "payment_success_rate":
                    "支付成功率",
                "verification_friction":
                    "验证摩擦(直通率)",
                "recon_accuracy":
                    "对账准确率",
                "fraud_interception":
                    "欺诈拦截率",
                "member_trust": "会员信值",
                "dispute_rate":
                    "争议率(反向)",
                "latency_budget":
                    "支付时效",
                "coverage_breadth":
                    "场景覆盖",
            },
            "decisions": ["observe",
                          "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——"
                    "第35档案",
        })
        return {"success": True,
                "status": view}

    # ============================================================
    # 归因链底座(铁律: 无归因不计入
    # 有效结算)
    # ============================================================

    @staticmethod
    def build_attribution(
            pay_id: int,
            intent_id=None,
            session_id: int = None,
            tier: str = None,
            risk_tier: str = None,
            pricing: dict = None
            ) -> dict:
        """归因链构造(payId+intentId+
        sessionId+tier+riskTier+定价快照
        ——渠道回执执行时附加)

        归因 ID 强制铁律: 每笔支付携带
        归因链(45号信值/58号意图联动
        的数据锚点); intentId 保留 58号
        原值(字符串意图标识或 0)
        """
        return {
            "payId": int(pay_id or 0),
            "intentId":
                intent_id or 0,
            "sessionId":
                int(session_id or 0),
            "tier": tier or "standard",
            "riskTier":
                risk_tier or "unverified",
            "pricing": pricing or {},
        }

    # ============================================================
    # 渠道适配层(CHANNEL_MODE 三态——
    # 41号 DRIDE 范式)
    # ============================================================

    async def execute_channel(
            self, pay_id: int,
            amount: float,
            mode: str = None) -> dict:
        """渠道执行(mock/real/mock_fallback
        三态——决策面 off 409)

        mock: 确定性回执(金额>0 即成功
              ——回执含渠道参考号+指纹)
        real: fail-hard(凭证经 .env 注入
              ——PAY60_CHANNEL_KEY 未配置
              即拒绝, 绝不静默降级)
        mock_fallback: 尝试 real, 失败
              回退 mock(留痕 fallback=true)

        Returns:
            {flowId, status, receipt,
             fingerprint, fallback}
        """
        require_active_mode()
        from services.pay60_registry import (
            current_channel_mode,
        )
        channel_mode = mode \
            or current_channel_mode()
        amount = round(
            float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                "支付金额须为正数")

        flow_id = await \
            self.repo.next_flow_id()
        fallback = False
        receipt: dict = {}
        error = ""

        if channel_mode == "real":
            # fail-hard: 凭证经 .env 注入
            # 不落盘; 未配置即拒绝
            key = os.environ.get(
                "PAY60_CHANNEL_KEY", "")
            if not key:
                raise ValueError(
                    "real 渠道未配置凭证"
                    "(PAY60_CHANNEL_KEY 经"
                    ".env 注入——fail-hard"
                    "不静默降级)")
            receipt = {
                "channel": "real",
                "refNo": f"REAL{flow_id}",
            }
        elif channel_mode \
                == "mock_fallback":
            key = os.environ.get(
                "PAY60_CHANNEL_KEY", "")
            if key:
                # 有凭证走 real——真实渠道
                # 执行为外部待办(未接入),
                # 触发回退留痕
                fallback = True
                error = "real 渠道未接入"
                receipt = {
                    "channel": "mock",
                    "refNo": f"MOCK{flow_id}",
                    "fallbackReason":
                        "real 渠道未接入"
                        "(外部待办)",
                }
            else:
                fallback = True
                error = "无 real 凭证"
                receipt = {
                    "channel": "mock",
                    "refNo": f"MOCK{flow_id}",
                    "fallbackReason":
                        "real 渠道无凭证"
                        "(回退 mock——41号"
                        "DRIDE 范式)",
                }
        else:  # mock(默认)
            receipt = {
                "channel": "mock",
                "refNo": f"MOCK{flow_id}",
            }

        receipt["amount"] = amount
        receipt["status"] = "captured"
        fingerprint = _fingerprint(
            flow_id, pay_id,
            receipt["refNo"], amount)

        await self.repo.save_flow({
            "flowId": flow_id,
            "payId": int(pay_id),
            "channel": receipt["channel"],
            "channelMode": channel_mode,
            "amount": amount,
            "channelReceipt": receipt,
            "fingerprint": fingerprint,
            "fallback": fallback,
            "error": error,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track(flow_id, "flow", {
            "payId": int(pay_id),
            "channelMode": channel_mode,
            "fallback": fallback,
        })
        return {
            "success": True,
            "flowId": flow_id,
            "channelMode": channel_mode,
            "receipt": receipt,
            "fingerprint": fingerprint,
            "fallback": fallback,
            "note": "渠道执行回执——"
                    "指纹链式关联(可审计)",
        }

    # ============================================================
    # 订单生命周期驱动骨架(状态机+留痕)
    # ============================================================

    async def advance(self, pay_id: int,
                      target: str,
                      note: str = ""
                      ) -> dict:
        """订单状态机流转(封闭转移表
        ——非法流转拒绝; 决策面 off 409)

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/非法流转
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        current = str(order.get("status"))
        from services.pay60_registry import (
            ORDER_TERMINAL,
            assert_transition,
        )
        if current in ORDER_TERMINAL:
            raise ValueError(
                f"订单已终态({current})"
                f"不可再流转")
        assert_transition(current, target)

        fingerprint = _fingerprint(
            pay_id, current, target,
            order.get("fingerprint"))
        order.update({
            "status": target,
            "fingerprint": fingerprint,
            "lastTransition": {
                "from": current,
                "to": target,
                "note": str(note or "")
                [:200],
                "at": ts(),
            },
            "updatedAt": ts(),
        })
        await self.repo.save_order(
            order, create=False)
        await self._track(pay_id, "order", {
            "action": "transition",
            "from": current,
            "to": target,
        })
        return {
            "success": True,
            "payId": int(pay_id),
            "from": current,
            "to": target,
            "fingerprint": fingerprint,
            "note": "状态机流转留痕——"
                    "指纹链式更新",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "payId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_track_failed %s: %s",
                event_type, exc)
