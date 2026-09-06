"""64号·信值兑换管理 智能体验层
(xx64_experience_service, P2)

计划(docs/64号_信值兑换商品服务AI智能管理模块
实施计划.md §4.2/§八 P2):
    ① 最优支付组合: 刚性 30/70
       结构内计算最低现金支出
       ——积分差额自动换算补足
       (不足 30% 时)+互斥对比卡
       ("信值支付省 X 元 vs 优惠
        活动省 Y 元"让用户自选)
    ② 智能凑单: 信值密度
       (信值抵扣额/价格)降序推荐
       ——帮用户高效利用余额
       (确定性排序, 非 LLM)
    ③ 规则可视化解释: 结算页
       "为什么这样算"——逐条展示
       R1-R6 应用过程(数字可溯源)
    ④ 积分一键转换预览: 结算页
       缺口自动填充所需积分数

铁律(计划 §二/§八):
    - 刚性结构不可变(组合计算
      在 30/70 框架内——不改变
      R1 结构)
    - 数字全部来自计算层
      (可溯源——LLM 不进判定链,
      AV64_LLM_MODE 仅文案润色位)
    - 确定性算法(同输入同输出)
"""

import logging
import os

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_exp")

MODEL_VERSION = "v1-xx64-experience"

SCORER_ID = "value_exchange"


def current_mode() -> str:
    """模块开关(XX64_MODE——同底座)"""
    return os.environ.get(
        "XX64_MODE", "off")


class Xx64ExperienceService:
    """64号智能体验层(P2——组合
    推荐+凑单+解释)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 最优支付组合(plan)
    # ============================================================

    async def payment_plan(self,
                          trust_id: int,
                          price: float,
                          discount_value: float = 0.0
                          ) -> dict:
        """最优支付组合(刚性 30/70 结构内
        ——最低现金支出计算)

        两种方案对比(R2 互斥——
        用户自选):
            方案 A: 信值支付
                信值=价格×30%(积分缺口
                可换算补足)+现金 70%
                不享其他优惠
            方案 B: 优惠活动
                现金=价格-优惠值
                不用信值

        Args:
            price: 商品/服务价格
            discount_value: 可享优惠
                金额(方案 B——互斥)

        观测面(不落库——纯计算)。
        """
        from services.xx64_registry import (
            CASH_PORTION,
            POINTS_PER_TRUST,
            TRUST_PORTION,
        )
        price = round(
            float(price or 0), 2)
        if price <= 0:
            raise ValueError(
                "价格须为正")

        # 信值余额(45号纯读取)
        from services.xx64_service import (
            get_trust_balance,
        )
        bal = await get_trust_balance(
            trust_id)
        balance = float(
            bal["balance"])

        # 预校验(方案 A 可行性)
        from services.xx64_service import (
            Xx64Service,
        )
        check = await (
            Xx64Service().precheck(
                trust_id, price))
        plan_a_feasible = bool(
            check.get("passed"))

        # 方案 A: 信值支付
        trust_value = round(
            price * TRUST_PORTION, 2)
        cash_a = round(
            price * CASH_PORTION, 2)
        # 积分缺口补足(余额不足 30%
        # 时——所需积分换算)
        gap = round(
            max(trust_value
                - balance, 0), 2)
        gap_points = round(
            gap * POINTS_PER_TRUST, 0)

        # 方案 B: 优惠活动
        discount_value = round(
            max(float(
                discount_value or 0),
                0.0), 2)
        discount_value = min(
            discount_value, price)
        cash_b = round(
            price - discount_value, 2)

        # 对比(省=方案 A 现金支出
        # 相对全价的减少)
        saving_a = round(
            price - cash_a, 2)
        better = "A" if cash_a \
            < cash_b else "B"

        return {
            "success": True,
            "trustId": int(trust_id),
            "price": price,
            "balance": balance,
            "planA": {
                "label": "信值支付"
                         "(30% 信值+70% 现付)",
                "feasible":
                    plan_a_feasible,
                "trustValue":
                    trust_value,
                "cash": cash_a,
                "saving": saving_a,
                "gap": gap,
                "gapPoints":
                    gap_points,
                "exclusive":
                    "不享其他优惠"
                    "(R2 整单互斥)",
                "precheck":
                    check.get(
                        "checks"),
            },
            "planB": {
                "label": "优惠活动"
                         "(纯现付)",
                "discount":
                    discount_value,
                "cash": cash_b,
                "saving":
                    discount_value,
                "exclusive":
                    "不使用信值",
            },
            "comparison": {
                "betterPlan": better,
                "cashDiff": round(
                    abs(cash_a
                        - cash_b), 2),
                "note": f"方案 A 现金 "
                        f"{cash_a} vs "
                        f"方案 B 现金 "
                        f"{cash_b}——"
                        + ("信值支付更省"
                           if better == "A"
                           else "优惠活动"
                                "更省"),
            },
            "note": "最优支付组合——"
                    "刚性 30/70 结构内"
                    "计算(R2 互斥——"
                    "用户自选)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ② 智能凑单(信值密度排序)
    # ============================================================

    async def smart_fill(self,
                         trust_id: int,
                         candidates: list = None
                         ) -> dict:
        """智能凑单(信值密度
        =信值抵扣额/价格 降序推荐
        ——帮用户高效利用余额)

        Args:
            candidates: [{name,
                price}] 候选商品列表
                (观测面纯计算——不落库)

        确定性排序(同输入同输出)。
        """
        from services.xx64_registry import (
            TRUST_PORTION,
        )
        items = []
        for c in (candidates or []):
            if not isinstance(c, dict):
                continue
            name = str(
                c.get("name")
                or c.get("product")
                or "未命名")
            try:
                price = round(
                    float(
                        c.get("price")
                        or 0), 2)
            except (TypeError,
                    ValueError):
                continue
            if price <= 0:
                continue
            trust_value = round(
                price * TRUST_PORTION,
                2)
            density = round(
                trust_value / price, 4)
            items.append({
                "name": name,
                "price": price,
                "trustValue":
                    trust_value,
                "density": density,
            })
        # 信值密度降序(确定性
        # ——密度同则价格降序)
        items.sort(key=lambda i: (
            -i["density"],
            -i["price"]))

        # 余额内可兑(前 N 个累计
        # 信值 ≤ 余额×20% 单次上限)
        from services.xx64_service import (
            get_trust_balance,
        )
        bal = await get_trust_balance(
            trust_id)
        balance = float(
            bal["balance"])
        single_quota = round(
            balance * 0.20, 2)
        affordable = []
        used = 0.0
        for i in items:
            if used + i["trustValue"] \
                    <= single_quota:
                affordable.append(i)
                used = round(
                    used
                    + i["trustValue"], 2)
        return {
            "success": True,
            "trustId": int(trust_id),
            "balance": balance,
            "singleQuota":
                single_quota,
            "ranked": items,
            "affordable": affordable,
            "affordableTrustTotal":
                used,
            "note": "智能凑单——信值密度"
                    "(抵扣额/价格)降序"
                    "+单次限额内组合"
                    "(确定性排序)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ③ 规则可视化解释(explain)
    # ============================================================

    async def explain_order(self,
                            order_id: int
                            ) -> dict:
        """订单规则可视化解释
        ("为什么这样算"——逐条
        R1-R6 应用过程, 数字可溯源)

        Raises:
            KeyError: 订单不存在
        """
        order = await self.repo.get_order(
            int(order_id))
        if not order:
            raise KeyError(
                f"订单 {order_id} 不存在")
        from services.xx64_registry import (
            CASH_PORTION,
            CUMULATIVE_QUOTA_RATIO,
            SINGLE_QUOTA_RATIO,
            TRUST_PORTION,
        )
        price = float(
            order.get("price") or 0)
        trust_value = float(
            order.get("trustValue")
            or 0)
        cash_value = float(
            order.get("cashValue")
            or 0)
        balance = float(
            order.get(
                "balanceSnapshot")
            or 0)
        single_pct = f"{SINGLE_QUOTA_RATIO:.0%}"
        cum_pct = f"{CUMULATIVE_QUOTA_RATIO:.0%}"
        trust_pct = f"{TRUST_PORTION:.0%}"
        cash_pct = f"{CASH_PORTION:.0%}"
        single_quota = order.get(
            "singleQuota")
        window_used = order.get(
            "windowUsedAtCreation")
        cum_quota = order.get(
            "cumulativeQuotaAtCreation")
        steps = [
            {
                "rule": "R1",
                "label": "混合支付结构",
                "calc": f"价格 {price} × "
                        f"{trust_pct} = 信值 "
                        f"{trust_value}; "
                        f"价格 {price} × "
                        f"{cash_pct} = 现付 "
                        f"{cash_value}",
                "source": "order.price"
                          "→trustValue/"
                          "cashValue",
            },
            {
                "rule": "R2",
                "label": "整单互斥",
                "calc": "本订单使用信值"
                        "支付, 不再叠加"
                        "其他优惠活动",
                "source":
                    "order.exclusive",
            },
            {
                "rule": "R4",
                "label": "单次限额",
                "calc": f"信值 "
                        f"{trust_value} ≤ "
                        f"余额快照 "
                        f"{balance} × "
                        f"{single_pct} = "
                        f"{single_quota}",
                "source":
                    "order."
                    "balanceSnapshot"
                    "→singleQuota",
            },
            {
                "rule": "R5",
                "label": "累计限额",
                "calc": f"创建时窗口已用 "
                        f"{window_used} + "
                        f"本次 "
                        f"{trust_value} ≤ "
                        f"最大快照 × "
                        f"{cum_pct} = "
                        f"{cum_quota}",
                "source": "order."
                          "windowUsed"
                          "AtCreation/"
                          "cumulativeQuota"
                          "AtCreation",
            },
            {
                "rule": "R6",
                "label": "积分入口",
                "calc": "100 积分 = 1 信值"
                        "(如需补足缺口: "
                        "缺口×100 积分)",
                "source": "registry",
            },
            {
                "rule": "R7",
                "label": "负值禁止",
                "calc": f"锁值前余额 "
                        f"{balance} ≥ "
                        f"{trust_value}"
                        "(非负校验通过)",
                "source":
                    "order."
                    "balanceSnapshot",
            },
        ]
        return {
            "success": True,
            "orderId": int(order_id),
            "order": {
                "status": order.get(
                    "status"),
                "price": price,
                "trustValue":
                    trust_value,
                "cashValue":
                    cash_value,
                "balanceSnapshot":
                    balance,
            },
            "steps": steps,
            "note": "规则可视化解释——"
                    "每个数字可溯源到"
                    "订单字段(R1-R6 "
                    "逐条)",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
