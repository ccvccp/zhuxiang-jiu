"""财务管理业务服务

功能:
    - 收入核算引擎(订单 → 自动凭证)
    - 退款记账(红字凭证, 借贷反转)
    - 税额计算(增值税/消费税/企业所得税/附加税)
    - 对账引擎(三方对账: 订单系统 + 支付渠道 + 银行)
    - 付款审批(多级审批: 一级/二级/三级)
    - 利润表生成
    - 月末结账(计提/摊销/结转)

并发安全:
    - 凭证操作使用 finance:voucher:{voucherNo} 锁
    - 付款操作使用 finance:payment:{paymentNo} 锁
    - 申报操作使用 finance:tax:{declarationNo} 锁

异常约定(与 order_service 一致):
    - KeyError → 404(资源不存在)
    - ValueError → 409(状态/业务冲突)
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from core.helpers import ts
from core.locks import get_lock
from repositories.finance_repository import FinanceRepository
from repositories.order_repository import OrderRepository


# ============================================================
# 常量
# ============================================================

# 时区(Asia/Shanghai)
_TZ = timezone(timedelta(hours=8))

# 凭证状态机: 草稿 → 已审核 → 已过账
VOUCHER_DRAFT = "draft"
VOUCHER_AUDITED = "audited"
VOUCHER_POSTED = "posted"
VOUCHER_STATUS_CN = {
    VOUCHER_DRAFT: "草稿",
    VOUCHER_AUDITED: "已审核",
    VOUCHER_POSTED: "已过账",
}

# 申报状态机: 待申报 → 已申报 → 已缴款
TAX_PENDING = "pending"
TAX_DECLARED = "declared"
TAX_PAID = "paid"
TAX_STATUS_CN = {
    TAX_PENDING: "待申报",
    TAX_DECLARED: "已申报",
    TAX_PAID: "已缴款",
}

# 付款状态机: 待审批 → 审批中 → 已批准 → 已付款 (任一级可拒绝)
PAYMENT_PENDING = "pending"
PAYMENT_APPROVING = "approving"
PAYMENT_APPROVED = "approved"
PAYMENT_PAID = "paid"
PAYMENT_REJECTED = "rejected"
PAYMENT_STATUS_CN = {
    PAYMENT_PENDING: "待审批",
    PAYMENT_APPROVING: "审批中",
    PAYMENT_APPROVED: "已批准",
    PAYMENT_PAID: "已付款",
    PAYMENT_REJECTED: "已拒绝",
}

# 发票状态
INVOICE_ISSUED = "issued"
INVOICE_RED = "red"
INVOICE_VOID = "void"
INVOICE_STATUS_CN = {
    INVOICE_ISSUED: "已开具",
    INVOICE_RED: "已红冲",
    INVOICE_VOID: "已作废",
}

# 对账状态
RECON_MATCHED = "matched"
RECON_DIFF = "diff"
RECON_RESOLVED = "resolved"
RECON_STATUS_CN = {
    RECON_MATCHED: "一致",
    RECON_DIFF: "有差异",
    RECON_RESOLVED: "已处理",
}

# 税率
VAT_RATE = 0.13              # 增值税率 13%
CONSUMPTION_AD_VALOREM = 0.20  # 消费税从价 20%
CONSUMPTION_PER_JIN = 0.5      # 消费税从量 0.5元/斤
INCOME_TAX_RATE = 0.25         # 企业所得税率 25%
INCOME_TAX_RATE_SMALL = 0.05   # 小微企业 5%
SURCHARGE_CITY = 0.07         # 城建税 7%
SURCHARGE_EDU = 0.03           # 教育费附加 3%
SURCHARGE_LOCAL_EDU = 0.02     # 地方教育附加 2%

# 每斤对应的毫升数(白酒密度≈0.92 g/ml, 500ml ≈ 460g ≈ 0.92斤)
ML_PER_JIN = 500.0 / 1.0  # 默认按 500ml ≈ 1 斤估算(简化)

# 付款审批阈值
APPROVAL_TIER1 = 10000       # 1万以下一级审批
APPROVAL_TIER2 = 100000      # 1万-10万二级审批, >10万三级审批

# 支付渠道枚举
PAYMENT_CHANNELS = ("wechat", "alipay", "unionpay", "bank")


def _current_period() -> str:
    """当前账期 YYYYMM"""
    return datetime.now(_TZ).strftime("%Y%m")


def _current_date() -> str:
    """当前日期 YYYY-MM-DD"""
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _period_from_date(date_str: str) -> str:
    """从 YYYY-MM-DD 提取 YYYYMM"""
    return date_str.replace("-", "")[:6]


def _round2(v: float) -> float:
    """保留两位小数"""
    return round(float(v), 2)


class FinanceService:
    """财务管理业务服务"""

    def __init__(self):
        self.finance_repo = FinanceRepository()
        self.order_repo = OrderRepository()

    # ============================================================
    # 1. 凭证管理
    # ============================================================

    async def list_vouchers(self, period: str = None, voucher_type: str = None,
                             source: str = None, status: str = None) -> dict:
        """凭证列表(支持多维筛选)"""
        vouchers = await self.finance_repo.list_vouchers(
            period=period, voucher_type=voucher_type,
            source=source, status=status,
        )
        # 列表不带分录(性能考虑)
        for v in vouchers:
            v.setdefault("entries", [])
            v["statusName"] = VOUCHER_STATUS_CN.get(v.get("status"), v.get("status", ""))
        return {
            "success": True,
            "count": len(vouchers),
            "vouchers": vouchers,
            "logs": [],
        }

    async def get_voucher(self, voucher_no: str) -> dict:
        """凭证详情(含分录)

        Raises:
            KeyError: 凭证不存在
        """
        voucher = await self.finance_repo.get_voucher(voucher_no)
        if not voucher:
            raise KeyError(f"凭证 {voucher_no} 不存在")
        voucher.setdefault("entries", [])
        voucher["statusName"] = VOUCHER_STATUS_CN.get(
            voucher.get("status"), voucher.get("status", ""))
        return {
            "success": True,
            "voucher": voucher,
            "logs": [],
        }

    async def auto_voucher_from_order(self, order_id: str) -> dict:
        """按订单自动生成收入凭证

        会计分录:
            借: 银行存款        实付金额
                贷: 主营业务收入    不含税收入
                    应交税费-增值税  税额

        不含税收入 = 实付金额 ÷ 1.13
        增值税额 = 实付金额 - 不含税收入

        Raises:
            KeyError: 订单不存在
            ValueError: 订单未支付/已生成凭证
        """
        async with get_lock(f"finance:voucher:order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")

            # 仅 PAID 及以后状态才可入账(需实付)
            payable_statuses = ("PAID", "SHIPPED", "RECEIVED", "COMPLETED", "RETURNING")
            if order.get("status") not in payable_statuses:
                raise ValueError(
                    f"订单状态异常: 仅 {payable_statuses} 可入账, 当前 {order.get('status')}"
                )

            # 重复生成校验(同 source + orderId 已存在则抛错)
            existing = await self.finance_repo.list_vouchers(
                source="order", status=None,
            )
            for v in existing:
                if v.get("sourceId") == order_id:
                    raise ValueError(f"订单 {order_id} 已生成凭证 {v['voucherNo']}")

            actual_amount = _round2(order["priceDetail"]["actualAmount"])
            amount_without_tax = _round2(actual_amount / (1 + VAT_RATE))
            vat_amount = _round2(actual_amount - amount_without_tax)

            # 计算消费税(白酒: 从价 20% + 从量 0.5元/斤)
            # 数量按订单 items.quantity 求和, 默认每瓶 500ml = 1斤
            total_quantity = sum(int(i.get("quantity", 0)) for i in order.get("items", []))
            consumption_ad_valorem = _round2(amount_without_tax * CONSUMPTION_AD_VALOREM)
            consumption_per_unit = _round2(total_quantity * ML_PER_JIN * CONSUMPTION_PER_JIN / ML_PER_JIN)
            # 简化: 每件按 1 斤计
            consumption_per_unit = _round2(total_quantity * CONSUMPTION_PER_JIN)
            consumption_tax = _round2(consumption_ad_valorem + consumption_per_unit)

            voucher_no = await self.finance_repo.next_voucher_no()
            now = ts()
            paid_at = order.get("payment", {}).get("paidAt", "") or now
            period = _period_from_date(paid_at[:10]) if paid_at else _current_period()

            entries = [
                {
                    "direction": "debit",
                    "subject": "银行存款",
                    "amount": actual_amount,
                    "summary": f"收 {order.get('payment', {}).get('method', '')} {order_id}",
                },
                {
                    "direction": "credit",
                    "subject": "主营业务收入",
                    "amount": amount_without_tax,
                    "summary": f"销售商品 {order_id}",
                },
                {
                    "direction": "credit",
                    "subject": "应交税费-应交增值税(销项税额)",
                    "amount": vat_amount,
                    "summary": f"销项税 {order_id}",
                },
            ]

            voucher = {
                "voucherNo": voucher_no,
                "period": period,
                "date": paid_at[:10] if paid_at else _current_date(),
                "type": "income",
                "source": "order",
                "sourceId": order_id,
                "memberId": order.get("memberId"),
                "status": VOUCHER_DRAFT,
                "amount": actual_amount,
                "amountWithoutTax": amount_without_tax,
                "taxAmount": vat_amount,
                "consumptionTaxAmount": consumption_tax,
                "consumptionPerUnit": consumption_per_unit,
                "entries": entries,
                "auditedBy": "",
                "postedBy": "",
                "createdAt": now,
                "updatedAt": now,
            }
            saved = await self.finance_repo.save_voucher(voucher)

            logs = [
                {"step": "收入核算", "level": "INFO",
                 "msg": f"订单 {order_id} 实付 ¥{actual_amount:.2f}"},
                {"step": "税额分离", "level": "INFO",
                 "msg": f"不含税 ¥{amount_without_tax:.2f}, 增值税 ¥{vat_amount:.2f}"},
                {"step": "消费税预提", "level": "INFO",
                 "msg": f"从价 ¥{consumption_ad_valorem:.2f} + 从量 ¥{consumption_per_unit:.2f}"},
                {"step": "凭证生成", "level": "INFO",
                 "msg": f"凭证号 {voucher_no}, 状态 草稿"},
            ]

            return {
                "success": True,
                "voucherNo": voucher_no,
                "voucher": saved,
                "logs": logs,
            }

    async def auto_voucher_from_refund(self, order_id: str) -> dict:
        """按退款自动生成红字凭证(借贷反转)

        会计分录(红字):
            借: 主营业务收入    不含税收入
                应交税费-增值税  税额
                贷: 银行存款        实付金额

        Raises:
            KeyError: 订单不存在
            ValueError: 订单未退款/已生成红字凭证
        """
        async with get_lock(f"finance:voucher:refund:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")

            if order.get("status") != "REFUNDED":
                raise ValueError(
                    f"订单状态异常: 仅 REFUNDED 可生成退款凭证, 当前 {order.get('status')}"
                )

            # 重复生成校验
            existing = await self.finance_repo.list_vouchers(source="refund", status=None)
            for v in existing:
                if v.get("sourceId") == order_id:
                    raise ValueError(f"订单 {order_id} 已生成红字凭证 {v['voucherNo']}")

            refund_amount = _round2(
                order.get("refund", {}).get("refundedAmount")
                or order["priceDetail"]["actualAmount"]
            )
            amount_without_tax = _round2(refund_amount / (1 + VAT_RATE))
            vat_amount = _round2(refund_amount - amount_without_tax)

            # 退款冲减消费税从量(与收入凭证对冲, 取负)
            total_quantity = sum(int(i.get("quantity", 0)) for i in order.get("items", []))
            consumption_per_unit = _round2(-total_quantity * CONSUMPTION_PER_JIN)

            voucher_no = await self.finance_repo.next_voucher_no()
            now = ts()
            refunded_at = order.get("refund", {}).get("refundedAt", "") or now
            period = _period_from_date(refunded_at[:10]) if refunded_at else _current_period()

            entries = [
                {
                    "direction": "debit",
                    "subject": "主营业务收入",
                    "amount": amount_without_tax,
                    "summary": f"红字冲销收入 {order_id}",
                    "isRed": True,
                },
                {
                    "direction": "debit",
                    "subject": "应交税费-应交增值税(销项税额)",
                    "amount": vat_amount,
                    "summary": f"红字冲销销项税 {order_id}",
                    "isRed": True,
                },
                {
                    "direction": "credit",
                    "subject": "银行存款",
                    "amount": refund_amount,
                    "summary": f"退款支出 {order_id}",
                    "isRed": True,
                },
            ]

            voucher = {
                "voucherNo": voucher_no,
                "period": period,
                "date": refunded_at[:10] if refunded_at else _current_date(),
                "type": "refund",
                "source": "refund",
                "sourceId": order_id,
                "memberId": order.get("memberId"),
                "status": VOUCHER_DRAFT,
                "amount": refund_amount,
                "amountWithoutTax": amount_without_tax,
                "taxAmount": vat_amount,
                "consumptionPerUnit": consumption_per_unit,
                "isRed": True,
                "entries": entries,
                "auditedBy": "",
                "postedBy": "",
                "createdAt": now,
                "updatedAt": now,
            }
            saved = await self.finance_repo.save_voucher(voucher)

            logs = [
                {"step": "退款记账", "level": "WARN",
                 "msg": f"订单 {order_id} 退款 ¥{refund_amount:.2f}"},
                {"step": "红字凭证", "level": "WARN",
                 "msg": f"凭证号 {voucher_no}, 借贷反转"},
            ]

            return {
                "success": True,
                "voucherNo": voucher_no,
                "voucher": saved,
                "logs": logs,
            }

    async def audit_voucher(self, voucher_no: str) -> dict:
        """凭证审核: 草稿 → 已审核 → 已过账

        Raises:
            KeyError: 凭证不存在
            ValueError: 状态异常(已过账不可再审核)
        """
        async with get_lock(f"finance:voucher:{voucher_no}"):
            voucher = await self.finance_repo.get_voucher(voucher_no)
            if not voucher:
                raise KeyError(f"凭证 {voucher_no} 不存在")

            current = voucher.get("status", VOUCHER_DRAFT)
            now = ts()
            logs = []

            if current == VOUCHER_DRAFT:
                # 草稿 → 已审核
                new_status = VOUCHER_AUDITED
                await self.finance_repo.update_voucher_fields(voucher_no, {
                    "status": new_status,
                    "auditedBy": "admin",
                    "auditedAt": now,
                    "updatedAt": now,
                })
                logs.append({"step": "审核", "level": "INFO",
                             "msg": f"凭证 {voucher_no}: 草稿 → 已审核"})
            elif current == VOUCHER_AUDITED:
                # 已审核 → 已过账
                new_status = VOUCHER_POSTED
                await self.finance_repo.update_voucher_fields(voucher_no, {
                    "status": new_status,
                    "postedBy": "admin",
                    "postedAt": now,
                    "updatedAt": now,
                })
                logs.append({"step": "过账", "level": "WARN",
                             "msg": f"凭证 {voucher_no}: 已审核 → 已过账"})
            else:
                raise ValueError(f"凭证状态异常: 已过账不可再审核, 当前 {current}")

            return {
                "success": True,
                "voucherNo": voucher_no,
                "status": new_status,
                "statusName": VOUCHER_STATUS_CN[new_status],
                "logs": logs,
            }

    async def month_end_closing(self, period: str) -> dict:
        """月末结账: 计提 + 摊销 + 结转

        Args:
            period: 账期, 形如 2026-07 或 202607

        流程:
            1. 计提税金(基于该账期已过账凭证的应交税费)
            2. 摊销待摊费用(Mock: 0)
            3. 结转损益(收入/成本/税金 → 本年利润)
        """
        clean_period = period.replace("-", "").replace("/", "")
        logs = [
            {"step": "月末结账", "level": "WARN",
             "msg": f"账期 {clean_period} 开始月末结账"},
        ]

        # 1. 汇总该账期已过账凭证
        posted = await self.finance_repo.list_vouchers(
            period=clean_period, status=VOUCHER_POSTED,
        )
        revenue_without_tax = 0.0   # 主营业务收入(不含税)
        refund_without_tax = 0.0    # 退款冲减收入
        vat_collected = 0.0         # 销项税额
        vat_refunded = 0.0         # 退款冲减销项税
        consumption_tax = 0.0       # 消费税(预提)

        for v in posted:
            if v.get("type") == "income":
                revenue_without_tax += float(v.get("amountWithoutTax", 0))
                vat_collected += float(v.get("taxAmount", 0))
                consumption_tax += float(v.get("consumptionTaxAmount", 0))
            elif v.get("type") == "refund":
                refund_without_tax += float(v.get("amountWithoutTax", 0))
                vat_refunded += float(v.get("taxAmount", 0))

        net_revenue = _round2(revenue_without_tax - refund_without_tax)
        net_vat = _round2(vat_collected - vat_refunded)
        consumption_tax = _round2(consumption_tax)

        # 2. 计提附加税(基于增值税+消费税)
        surcharge_city = _round2((net_vat + consumption_tax) * SURCHARGE_CITY)
        surcharge_edu = _round2((net_vat + consumption_tax) * SURCHARGE_EDU)
        surcharge_local_edu = _round2((net_vat + consumption_tax) * SURCHARGE_LOCAL_EDU)
        surtax_total = _round2(surcharge_city + surcharge_edu + surcharge_local_edu)

        logs.append({"step": "税金汇总", "level": "INFO",
                     "msg": f"应交增值税 ¥{net_vat:.2f}, 消费税 ¥{consumption_tax:.2f}, "
                            f"附加税 ¥{surtax_total:.2f}"})

        # 3. 结转损益到本年利润
        # 简化: 假设成本=收入的 60%, 费用=收入的 10%(Mock, 生产业务由其他模块提供)
        cost = _round2(net_revenue * 0.60)
        expense = _round2(net_revenue * 0.10)
        tax_and_surcharge = _round2(consumption_tax + surtax_total)
        profit_before_tax = _round2(net_revenue - cost - tax_and_surcharge - expense)
        income_tax = _round2(profit_before_tax * INCOME_TAX_RATE if profit_before_tax > 0 else 0)
        net_profit = _round2(profit_before_tax - income_tax)

        logs.append({"step": "结转损益", "level": "INFO",
                     "msg": f"收入 ¥{net_revenue:.2f} - 成本 ¥{cost:.2f} - "
                            f"税金 ¥{tax_and_surcharge:.2f} - 费用 ¥{expense:.2f} "
                            f"= 利润总额 ¥{profit_before_tax:.2f}"})
        logs.append({"step": "计提所得税", "level": "INFO",
                     "msg": f"利润总额 ¥{profit_before_tax:.2f} × 25% = 所得税 ¥{income_tax:.2f}"})
        logs.append({"step": "净利润", "level": "WARN",
                     "msg": f"净利润 ¥{net_profit:.2f}"})

        closing = {
            "period": clean_period,
            "voucherCount": len(posted),
            "summary": {
                "netRevenue": net_revenue,
                "cost": cost,
                "vat": net_vat,
                "consumptionTax": consumption_tax,
                "surtax": {
                    "city": surcharge_city,
                    "education": surcharge_edu,
                    "localEducation": surcharge_local_edu,
                    "total": surtax_total,
                },
                "expense": expense,
                "profitBeforeTax": profit_before_tax,
                "incomeTax": income_tax,
                "netProfit": net_profit,
            },
            "closedAt": ts(),
        }

        logs.append({"step": "月末结账", "level": "WARN",
                     "msg": f"账期 {clean_period} 月末结账完成"})

        return {
            "success": True,
            "period": clean_period,
            "closing": closing,
            "logs": logs,
        }

    # ============================================================
    # 2. 发票管理
    # ============================================================

    async def list_invoices(self, status: str = None, invoice_type: str = None,
                             period: str = None) -> dict:
        """发票列表"""
        invoices = await self.finance_repo.list_invoices(
            status=status, invoice_type=invoice_type, period=period,
        )
        for inv in invoices:
            inv["statusName"] = INVOICE_STATUS_CN.get(inv.get("status"), inv.get("status", ""))
        return {
            "success": True,
            "count": len(invoices),
            "invoices": invoices,
            "logs": [],
        }

    async def get_invoice(self, invoice_no: str) -> dict:
        """发票详情

        Raises:
            KeyError: 发票不存在
        """
        invoice = await self.finance_repo.get_invoice(invoice_no)
        if not invoice:
            raise KeyError(f"发票 {invoice_no} 不存在")
        invoice["statusName"] = INVOICE_STATUS_CN.get(
            invoice.get("status"), invoice.get("status", ""))
        return {
            "success": True,
            "invoice": invoice,
            "logs": [],
        }

    async def issue_invoice(self, member_id, order_id: str, title_type: str = "personal",
                            title: str = "", tax_no: str = "", amount: float = None) -> dict:
        """开具发票

        Args:
            member_id: 操作会员 ID
            order_id: 关联订单号
            title_type: 抬头类型 personal/company
            title: 发票抬头
            tax_no: 税号(企业抬头条必填)
            amount: 开票金额(默认取订单实付)

        Raises:
            KeyError: 订单不存在
            ValueError: 抬头缺失/已开票
        """
        if not title:
            raise ValueError("发票抬头不能为空")
        if title_type == "company" and not tax_no:
            raise ValueError("企业抬头条需提供税号")

        async with get_lock(f"finance:invoice:order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")

            if order.get("memberId") != member_id:
                raise ValueError(f"订单 {order_id} 不属于会员 {member_id}")

            # 重复开票校验
            existing = await self.finance_repo.list_invoices()
            for inv in existing:
                if inv.get("orderId") == order_id and inv.get("status") == INVOICE_ISSUED:
                    raise ValueError(f"订单 {order_id} 已开具发票 {inv['invoiceNo']}")

            invoice_amount = _round2(
                amount if amount is not None else order["priceDetail"]["actualAmount"]
            )
            amount_without_tax = _round2(invoice_amount / (1 + VAT_RATE))
            tax_amount = _round2(invoice_amount - amount_without_tax)

            invoice_no = await self.finance_repo.next_invoice_no()
            now = ts()
            paid_at = order.get("payment", {}).get("paidAt", "") or now
            period = _period_from_date(paid_at[:10]) if paid_at else _current_period()

            invoice = {
                "invoiceNo": invoice_no,
                "orderId": order_id,
                "memberId": member_id,
                "titleType": title_type,
                "title": title,
                "taxNo": tax_no,
                "type": "normal",
                "status": INVOICE_ISSUED,
                "amount": invoice_amount,
                "amountWithoutTax": amount_without_tax,
                "taxAmount": tax_amount,
                "period": period,
                "date": paid_at[:10] if paid_at else _current_date(),
                "issuedBy": member_id,
                "issuedAt": now,
                "redOriginalNo": "",
                "redReason": "",
                "createdAt": now,
                "updatedAt": now,
            }
            saved = await self.finance_repo.save_invoice(invoice)

            logs = [
                {"step": "开具发票", "level": "INFO",
                 "msg": f"发票号 {invoice_no}, 金额 ¥{invoice_amount:.2f}"},
                {"step": "抬头", "level": "INFO",
                 "msg": f"抬头: {title} ({title_type})"},
            ]

            return {
                "success": True,
                "invoiceNo": invoice_no,
                "invoice": saved,
                "logs": logs,
            }

    async def red_invoice(self, invoice_no: str, reason: str = "业务冲红") -> dict:
        """红字冲红: 已开具 → 已红冲

        Raises:
            KeyError: 发票不存在
            ValueError: 状态异常
        """
        if not reason:
            raise ValueError("冲红原因不能为空")

        async with get_lock(f"finance:invoice:{invoice_no}"):
            invoice = await self.finance_repo.get_invoice(invoice_no)
            if not invoice:
                raise KeyError(f"发票 {invoice_no} 不存在")

            if invoice.get("status") != INVOICE_ISSUED:
                raise ValueError(
                    f"发票状态异常: 仅 {INVOICE_ISSUED} 可红冲, 当前 {invoice.get('status')}"
                )

            now = ts()
            # 开具红字发票(金额取负)
            red_no = await self.finance_repo.next_invoice_no()
            red_invoice = {
                "invoiceNo": red_no,
                "orderId": invoice.get("orderId", ""),
                "memberId": invoice.get("memberId"),
                "titleType": invoice.get("titleType", "personal"),
                "title": invoice.get("title", ""),
                "taxNo": invoice.get("taxNo", ""),
                "type": "red",
                "status": INVOICE_RED,
                "amount": -_round2(invoice.get("amount", 0)),
                "amountWithoutTax": -_round2(invoice.get("amountWithoutTax", 0)),
                "taxAmount": -_round2(invoice.get("taxAmount", 0)),
                "period": invoice.get("period", _current_period()),
                "date": _current_date(),
                "issuedBy": "admin",
                "issuedAt": now,
                "redOriginalNo": invoice_no,
                "redReason": reason,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.finance_repo.save_invoice(red_invoice)

            # 原发票置为已红冲
            await self.finance_repo.update_invoice_fields(invoice_no, {
                "status": INVOICE_RED,
                "redBy": red_no,
                "redReason": reason,
                "redAt": now,
                "updatedAt": now,
            })

            logs = [
                {"step": "红字冲红", "level": "WARN",
                 "msg": f"原发票 {invoice_no} 已红冲"},
                {"step": "红字发票", "level": "WARN",
                 "msg": f"红字发票号 {red_no}, 原因: {reason}"},
            ]

            return {
                "success": True,
                "originalInvoiceNo": invoice_no,
                "redInvoiceNo": red_no,
                "redInvoice": red_invoice,
                "logs": logs,
            }

    # ============================================================
    # 3. 税务管理
    # ============================================================

    async def list_tax_declarations(self, tax_type: str = None, period: str = None,
                                       status: str = None) -> dict:
        """税务申报记录列表"""
        decls = await self.finance_repo.list_tax_declarations(
            tax_type=tax_type, period=period, status=status,
        )
        for d in decls:
            d["statusName"] = TAX_STATUS_CN.get(d.get("status"), d.get("status", ""))
        return {
            "success": True,
            "count": len(decls),
            "declarations": decls,
            "logs": [],
        }

    async def get_tax_declaration(self, decl_no: str) -> dict:
        """税务申报详情

        Raises:
            KeyError: 申报不存在
        """
        decl = await self.finance_repo.get_tax_declaration(decl_no)
        if not decl:
            raise KeyError(f"申报 {decl_no} 不存在")
        decl["statusName"] = TAX_STATUS_CN.get(decl.get("status"), decl.get("status", ""))
        return {
            "success": True,
            "declaration": decl,
            "logs": [],
        }

    async def calc_tax(self, period: str) -> dict:
        """按账期计算各税种

        计算口径:
            - 增值税: 不含税销售额 × 13% - 退款冲减
            - 消费税(白酒): 从价 20% + 从量 0.5元/斤
            - 附加税: (增值税 + 消费税) × (7% + 3% + 2%)
            - 企业所得税: 应纳税所得额 × 25% (小微 5%)

        返回包含 4 个税种的明细 + 创建对应待申报记录(若已存在则覆盖)
        """
        clean_period = period.replace("-", "").replace("/", "")
        logs = [
            {"step": "税额计算", "level": "INFO",
             "msg": f"账期 {clean_period}"},
        ]

        # 1. 汇总该账期已过账凭证
        posted = await self.finance_repo.list_vouchers(
            period=clean_period, status=VOUCHER_POSTED,
        )
        revenue_with_tax = 0.0   # 含税销售额
        revenue_without_tax = 0.0
        vat_collected = 0.0
        refund_without_tax = 0.0
        vat_refunded = 0.0
        consumption_tax_accrued = 0.0
        consumption_per_unit_accrued = 0.0  # 从量消费税累计(收入+退款冲减)

        for v in posted:
            if v.get("type") == "income":
                revenue_with_tax += float(v.get("amount", 0))
                revenue_without_tax += float(v.get("amountWithoutTax", 0))
                vat_collected += float(v.get("taxAmount", 0))
                consumption_tax_accrued += float(v.get("consumptionTaxAmount", 0))
                consumption_per_unit_accrued += float(v.get("consumptionPerUnit", 0))
            elif v.get("type") == "refund":
                refund_without_tax += float(v.get("amountWithoutTax", 0))
                vat_refunded += float(v.get("taxAmount", 0))
                consumption_per_unit_accrued += float(v.get("consumptionPerUnit", 0))

        net_revenue_without_tax = _round2(revenue_without_tax - refund_without_tax)
        net_vat = _round2(vat_collected - vat_refunded)

        # 2. 消费税(白酒): 从价 20% + 从量 0.5元/斤
        consumption_ad_valorem = _round2(net_revenue_without_tax * CONSUMPTION_AD_VALOREM)
        consumption_per_unit = _round2(consumption_per_unit_accrued)
        consumption_tax = _round2(consumption_ad_valorem + consumption_per_unit)

        # 3. 附加税
        surcharge_base = net_vat + consumption_tax
        surcharge_city = _round2(surcharge_base * SURCHARGE_CITY)
        surcharge_edu = _round2(surcharge_base * SURCHARGE_EDU)
        surcharge_local_edu = _round2(surcharge_base * SURCHARGE_LOCAL_EDU)
        surtax_total = _round2(surcharge_city + surcharge_edu + surcharge_local_edu)

        # 4. 企业所得税(简化: 应纳税所得额 = 利润总额, 暂按 25%)
        # 实际业务需汇总成本/费用, 此处简化估算
        estimated_cost = _round2(net_revenue_without_tax * 0.60)
        estimated_expense = _round2(net_revenue_without_tax * 0.10)
        taxable_income = _round2(
            net_revenue_without_tax - estimated_cost - consumption_tax - surtax_total - estimated_expense
        )
        # 小微企业判定(简化: 应纳税所得额 ≤ 300万 视为小微)
        is_small_micro = taxable_income <= 3000000
        income_tax_rate = INCOME_TAX_RATE_SMALL if is_small_micro else INCOME_TAX_RATE
        income_tax = _round2(max(0, taxable_income) * income_tax_rate)

        logs.append({"step": "增值税", "level": "INFO",
                     "msg": f"销项 ¥{vat_collected:.2f} - 退款 ¥{vat_refunded:.2f} "
                            f"= 应纳 ¥{net_vat:.2f}"})
        logs.append({"step": "消费税", "level": "INFO",
                     "msg": f"从价 ¥{consumption_ad_valorem:.2f} + 从量 ¥{consumption_per_unit:.2f} "
                            f"= ¥{consumption_tax:.2f}"})
        logs.append({"step": "附加税", "level": "INFO",
                     "msg": f"城建 ¥{surcharge_city:.2f} + 教育 ¥{surcharge_edu:.2f} "
                            f"+ 地方教育 ¥{surcharge_local_edu:.2f} = ¥{surtax_total:.2f}"})
        logs.append({"step": "企业所得税", "level": "INFO",
                     "msg": f"应纳税所得额 ¥{taxable_income:.2f} × "
                            f"{'5%(小微)' if is_small_micro else '25%'} = ¥{income_tax:.2f}"})

        detail = {
            "period": clean_period,
            "revenueWithTax": _round2(revenue_with_tax),
            "revenueWithoutTax": net_revenue_without_tax,
            "vat": {
                "output": _round2(vat_collected),
                "refunded": _round2(vat_refunded),
                "payable": net_vat,
                "rate": VAT_RATE,
            },
            "consumptionTax": {
                "adValorem": consumption_ad_valorem,
                "perUnit": consumption_per_unit,
                "total": consumption_tax,
                "rate": CONSUMPTION_AD_VALOREM,
                "unitRate": CONSUMPTION_PER_JIN,
            },
            "surtax": {
                "base": _round2(surcharge_base),
                "city": surcharge_city,
                "education": surcharge_edu,
                "localEducation": surcharge_local_edu,
                "total": surtax_total,
            },
            "incomeTax": {
                "taxableIncome": taxable_income,
                "rate": income_tax_rate,
                "isSmallMicro": is_small_micro,
                "payable": income_tax,
            },
            "totalTax": _round2(net_vat + consumption_tax + surtax_total + income_tax),
            "voucherCount": len(posted),
            "calculatedAt": ts(),
        }

        # 为各税种创建/更新待申报记录(4 个税种 + 1 个汇总)
        tax_types = [
            ("vat", "增值税", net_vat),
            ("consumption", "消费税", consumption_tax),
            ("surtax", "附加税", surtax_total),
            ("income", "企业所得税", income_tax),
        ]
        created_decls = []
        now = ts()
        for ttype, tname, payable in tax_types:
            decl_no = await self.finance_repo.next_declaration_no(clean_period)
            decl = {
                "declarationNo": decl_no,
                "taxType": ttype,
                "taxTypeName": tname,
                "period": clean_period,
                "status": TAX_PENDING,
                "payableAmount": _round2(payable),
                "paidAmount": 0,
                "detail": detail,
                "declaredBy": "",
                "declaredAt": "",
                "paidBy": "",
                "paidAt": "",
                "createdAt": now,
                "updatedAt": now,
            }
            await self.finance_repo.save_tax_declaration(decl)
            created_decls.append(decl_no)
            logs.append({"step": "申报记录", "level": "INFO",
                         "msg": f"{tname} {decl_no}: 待申报 ¥{payable:.2f}"})

        return {
            "success": True,
            "period": clean_period,
            "detail": detail,
            "declarationNos": created_decls,
            "logs": logs,
        }

    async def declare_tax(self, decl_no: str) -> dict:
        """提交申报: 待申报 → 已申报 → 已缴款

        Raises:
            KeyError: 申报不存在
            ValueError: 状态异常
        """
        async with get_lock(f"finance:tax:{decl_no}"):
            decl = await self.finance_repo.get_tax_declaration(decl_no)
            if not decl:
                raise KeyError(f"申报 {decl_no} 不存在")

            current = decl.get("status", TAX_PENDING)
            now = ts()
            logs = []

            if current == TAX_PENDING:
                new_status = TAX_DECLARED
                await self.finance_repo.update_tax_fields(decl_no, {
                    "status": new_status,
                    "declaredBy": "admin",
                    "declaredAt": now,
                    "updatedAt": now,
                })
                logs.append({"step": "提交申报", "level": "INFO",
                             "msg": f"{decl_no}: 待申报 → 已申报"})
            elif current == TAX_DECLARED:
                new_status = TAX_PAID
                payable = float(decl.get("payableAmount", 0))
                await self.finance_repo.update_tax_fields(decl_no, {
                    "status": new_status,
                    "paidAmount": _round2(payable),
                    "paidBy": "admin",
                    "paidAt": now,
                    "updatedAt": now,
                })
                logs.append({"step": "缴款", "level": "WARN",
                             "msg": f"{decl_no}: 已申报 → 已缴款, 缴款 ¥{payable:.2f}"})
            else:
                raise ValueError(f"申报状态异常: 已缴款不可再申报, 当前 {current}")

            return {
                "success": True,
                "declarationNo": decl_no,
                "status": new_status,
                "statusName": TAX_STATUS_CN[new_status],
                "logs": logs,
            }

    # ============================================================
    # 4. 资金对账
    # ============================================================

    async def list_reconciliations(self, date: str = None, recon_type: str = None,
                                      status: str = None) -> dict:
        """对账记录列表"""
        recs = await self.finance_repo.list_reconciliations(
            date=date, recon_type=recon_type, status=status,
        )
        for r in recs:
            r["statusName"] = RECON_STATUS_CN.get(r.get("status"), r.get("status", ""))
        return {
            "success": True,
            "count": len(recs),
            "reconciliations": recs,
            "logs": [],
        }

    async def daily_reconciliation(self, date: str) -> dict:
        """执行日终对账(三方: 订单系统 + 支付渠道 + 银行)

        比对:
            - 订单系统: PAID 状态订单实付总额
            - 支付渠道: 各渠道(Mock 用订单汇总)
            - 银行: 实际到账(Mock 与订单一致, 模拟微小差异)

        差异识别:
            - 若三方一致 → status=matched
            - 若有差异 → status=diff, 记录 differences
        """
        async with get_lock(f"finance:recon:{date}"):
            now = ts()
            logs = [
                {"step": "日终对账", "level": "INFO",
                 "msg": f"日期 {date}"},
            ]

            # 1. 订单系统侧: 当日 PAID 订单
            all_orders = await self.order_repo.list_all()
            day_orders = []
            for o in all_orders:
                paid_at = (o.get("payment") or {}).get("paidAt", "")
                if paid_at.startswith(date) and o.get("status") in ("PAID", "SHIPPED", "RECEIVED", "COMPLETED", "RETURNING"):
                    day_orders.append(o)

            order_amount = _round2(
                sum(o["priceDetail"]["actualAmount"] for o in day_orders)
            )
            order_count = len(day_orders)

            # 2. 支付渠道侧(模拟与订单系统一致)
            pay_amount = order_amount
            pay_count = order_count

            # 3. 银行侧(模拟: 95% 概率一致, 此处简化为完全一致)
            bank_amount = order_amount
            bank_count = order_count

            # 4. 差异识别
            differences = []
            if abs(order_amount - pay_amount) > 0.01:
                differences.append({
                    "side": "order-vs-pay",
                    "amount": _round2(order_amount - pay_amount),
                    "desc": f"订单系统 ¥{order_amount:.2f} vs 支付渠道 ¥{pay_amount:.2f}",
                })
            if abs(pay_amount - bank_amount) > 0.01:
                differences.append({
                    "side": "pay-vs-bank",
                    "amount": _round2(pay_amount - bank_amount),
                    "desc": f"支付渠道 ¥{pay_amount:.2f} vs 银行 ¥{bank_amount:.2f}",
                })

            recon_status = RECON_MATCHED if not differences else RECON_DIFF

            logs.append({"step": "订单侧", "level": "INFO",
                         "msg": f"{order_count} 笔, ¥{order_amount:.2f}"})
            logs.append({"step": "支付侧", "level": "INFO",
                         "msg": f"{pay_count} 笔, ¥{pay_amount:.2f}"})
            logs.append({"step": "银行侧", "level": "INFO",
                         "msg": f"{bank_count} 笔, ¥{bank_amount:.2f}"})

            if differences:
                logs.append({"step": "差异识别", "level": "WARN",
                             "msg": f"发现 {len(differences)} 项差异"})
            else:
                logs.append({"step": "对账结果", "level": "INFO",
                             "msg": "三方一致"})

            recon = {
                "reconId": f"{date}:daily",
                "date": date,
                "type": "daily",
                "status": recon_status,
                "orderSide": {"count": order_count, "amount": order_amount},
                "paySide": {"count": pay_count, "amount": pay_amount},
                "bankSide": {"count": bank_count, "amount": bank_amount},
                "diffAmount": _round2(
                    sum(abs(d["amount"]) for d in differences)
                ),
                "differences": differences,
                "resolvedBy": "",
                "resolvedAt": "",
                "resolveNote": "",
                "createdAt": now,
                "updatedAt": now,
            }
            saved = await self.finance_repo.save_reconciliation(recon)

            return {
                "success": True,
                "reconId": recon["reconId"],
                "date": date,
                "reconciliation": saved,
                "logs": logs,
            }

    async def resolve_reconciliation(self, recon_id: str, reason: str, handler: str) -> dict:
        """差异处理(标记为已处理)

        Args:
            recon_id: 对账记录 ID, 形如 YYYY-MM-DD:daily 或 YYYY-MM-DD:type
            reason: 处理原因
            handler: 处理人

        Raises:
            KeyError: 对账记录不存在
            ValueError: 无差异/已处理
        """
        if not reason:
            raise ValueError("处理原因不能为空")
        if not handler:
            raise ValueError("处理人不能为空")

        # 解析 recon_id → date + type
        parts = recon_id.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"对账 ID 格式错误: {recon_id}, 应为 date:type")
        date, recon_type = parts

        async with get_lock(f"finance:recon:{date}:{recon_type}"):
            recon = await self.finance_repo.get_reconciliation(date, recon_type)
            if not recon:
                raise KeyError(f"对账记录 {recon_id} 不存在")

            if recon.get("status") == RECON_MATCHED:
                raise ValueError("该对账记录无差异, 无需处理")
            if recon.get("status") == RECON_RESOLVED:
                raise ValueError("该对账记录已处理")

            now = ts()
            await self.finance_repo.update_recon_fields(date, recon_type, {
                "status": RECON_RESOLVED,
                "resolvedBy": handler,
                "resolvedAt": now,
                "resolveNote": reason,
                "updatedAt": now,
            })

            logs = [
                {"step": "差异处理", "level": "WARN",
                 "msg": f"对账 {recon_id} 已处理"},
                {"step": "处理记录", "level": "INFO",
                 "msg": f"处理人: {handler}, 原因: {reason}"},
            ]

            return {
                "success": True,
                "reconId": recon_id,
                "status": RECON_RESOLVED,
                "statusName": RECON_STATUS_CN[RECON_RESOLVED],
                "resolvedBy": handler,
                "resolveNote": reason,
                "logs": logs,
            }

    # ============================================================
    # 5. 付款管理
    # ============================================================

    async def list_payments(self, payment_type: str = None, status: str = None) -> dict:
        """付款记录列表"""
        payments = await self.finance_repo.list_payments(
            payment_type=payment_type, status=status,
        )
        for p in payments:
            p["statusName"] = PAYMENT_STATUS_CN.get(p.get("status"), p.get("status", ""))
        return {
            "success": True,
            "count": len(payments),
            "payments": payments,
            "logs": [],
        }

    async def apply_payment(self, payment_type: str, payee: str, amount: float,
                              description: str = "") -> dict:
        """发起付款申请

        审批级别(按金额):
            - <1万:    一级审批(直接 → approved)
            - 1万-10万: 二级审批(需 2 次通过)
            - >10万:   三级审批(需 3 次通过)

        Raises:
            ValueError: 参数非法
        """
        if not payment_type:
            raise ValueError("付款类型不能为空")
        if not payee:
            raise ValueError("收款方不能为空")
        if amount <= 0:
            raise ValueError("付款金额必须大于 0")

        payment_no = await self.finance_repo.next_payment_no()
        now = ts()

        # 审批级别
        if amount < APPROVAL_TIER1:
            required_level = 1
        elif amount < APPROVAL_TIER2:
            required_level = 2
        else:
            required_level = 3

        payment = {
            "paymentNo": payment_no,
            "type": payment_type,
            "payee": payee,
            "amount": _round2(amount),
            "description": description,
            "status": PAYMENT_PENDING,
            "requiredLevel": required_level,
            "currentLevel": 0,
            "approvals": [],
            "appliedAt": now,
            "paidAt": "",
            "rejectedBy": "",
            "rejectedAt": "",
            "rejectReason": "",
            "createdAt": now,
            "updatedAt": now,
        }
        saved = await self.finance_repo.save_payment(payment)

        logs = [
            {"step": "发起付款", "level": "INFO",
             "msg": f"付款编号 {payment_no}, 类型 {payment_type}"},
            {"step": "审批配置", "level": "INFO",
             "msg": f"金额 ¥{amount:.2f}, 需 {required_level} 级审批"},
        ]

        return {
            "success": True,
            "paymentNo": payment_no,
            "payment": saved,
            "logs": logs,
        }

    async def approve_payment(self, payment_no: str, level: int,
                                approver: str = "admin",
                                decision: str = "approve",
                                reason: str = "") -> dict:
        """付款审批

        状态机:
            pending → approving(一级通过) → approved(二级/三级通过) → paid
            任一级可拒绝 → rejected

        Args:
            payment_no: 付款编号
            level: 当前审批级别 1/2/3
            approver: 审批人
            decision: approve/reject
            reason: 拒绝原因

        Raises:
            KeyError: 付款不存在
            ValueError: 级别越界/状态异常
        """
        if level not in (1, 2, 3):
            raise ValueError("审批级别必须为 1/2/3")

        async with get_lock(f"finance:payment:{payment_no}"):
            payment = await self.finance_repo.get_payment(payment_no)
            if not payment:
                raise KeyError(f"付款 {payment_no} 不存在")

            required = int(payment.get("requiredLevel", 1))
            current = int(payment.get("currentLevel", 0))
            status = payment.get("status", PAYMENT_PENDING)

            # 终态校验
            if status in (PAYMENT_PAID, PAYMENT_REJECTED):
                raise ValueError(
                    f"付款状态异常: 当前 {status}, 不可再审批"
                )

            # 拒绝: 任何级别可拒绝
            if decision == "reject":
                if not reason:
                    raise ValueError("拒绝需填写原因")
                now = ts()
                await self.finance_repo.update_payment_fields(payment_no, {
                    "status": PAYMENT_REJECTED,
                    "rejectedBy": approver,
                    "rejectedAt": now,
                    "rejectReason": reason,
                    "updatedAt": now,
                })
                approvals = list(payment.get("approvals") or [])
                approvals.append({
                    "level": level, "approver": approver,
                    "decision": "reject", "reason": reason, "at": now,
                })
                await self.finance_repo.update_payment_fields(payment_no, {
                    "approvals": approvals,
                })
                logs = [
                    {"step": "审批拒绝", "level": "WARN",
                     "msg": f"付款 {payment_no} 第 {level} 级审批拒绝"},
                    {"step": "拒绝原因", "level": "INFO", "msg": reason},
                ]
                return {
                    "success": True,
                    "paymentNo": payment_no,
                    "status": PAYMENT_REJECTED,
                    "statusName": PAYMENT_STATUS_CN[PAYMENT_REJECTED],
                    "logs": logs,
                }

            # 级别连贯性: 必须按 1→2→3 顺序审批
            expected_level = current + 1
            if level != expected_level:
                raise ValueError(
                    f"审批级别跳跃: 当前应审批第 {expected_level} 级, 实际请求第 {level} 级"
                )
            if level > required:
                raise ValueError(
                    f"超出所需审批级别: 需 {required} 级, 请求第 {level} 级"
                )

            now = ts()
            approvals = list(payment.get("approvals") or [])
            approvals.append({
                "level": level, "approver": approver,
                "decision": "approve", "reason": reason, "at": now,
            })

            # 判定下一状态
            if level < required:
                # 还有更高级别待审批
                new_status = PAYMENT_APPROVING if level == 1 else PAYMENT_APPROVING
                logs_msg = f"第 {level} 级审批通过, 待第 {level + 1} 级审批"
            else:
                # 最后一级通过
                new_status = PAYMENT_APPROVED
                logs_msg = f"第 {level} 级审批通过, 已批准(共 {required} 级)"

            await self.finance_repo.update_payment_fields(payment_no, {
                "status": new_status,
                "currentLevel": level,
                "approvals": approvals,
                "updatedAt": now,
            })

            logs = [
                {"step": "审批通过", "level": "INFO", "msg": logs_msg},
            ]

            return {
                "success": True,
                "paymentNo": payment_no,
                "status": new_status,
                "statusName": PAYMENT_STATUS_CN[new_status],
                "currentLevel": level,
                "requiredLevel": required,
                "logs": logs,
            }

    # ============================================================
    # 6. 财务报表
    # ============================================================

    async def profit_statement(self, period: str) -> dict:
        """利润表

        结构:
            营业收入(主营+其他)
              - 营业成本(生产+采购+物流+包装)
              - 税金及附加(消费税+附加税)
              - 销售费用(营销+佣金)
              - 管理费用(人力+平台)
              - 财务费用(支付手续费+利息)
              = 利润总额
                - 所得税费用
                = 净利润
        """
        clean_period = period.replace("-", "").replace("/", "")
        logs = [
            {"step": "利润表", "level": "INFO",
             "msg": f"账期 {clean_period}"},
        ]

        # 汇总已过账凭证
        posted = await self.finance_repo.list_vouchers(
            period=clean_period, status=VOUCHER_POSTED,
        )
        main_revenue = 0.0       # 主营业务收入(不含税)
        other_revenue = 0.0     # 其他业务收入
        refund_revenue = 0.0     # 退款冲减
        vat_amount = 0.0
        consumption_tax = 0.0
        vat_refunded = 0.0

        for v in posted:
            if v.get("type") == "income":
                main_revenue += float(v.get("amountWithoutTax", 0))
                vat_amount += float(v.get("taxAmount", 0))
                consumption_tax += float(v.get("consumptionTaxAmount", 0))
            elif v.get("type") == "refund":
                refund_revenue += float(v.get("amountWithoutTax", 0))
                vat_refunded += float(v.get("taxAmount", 0))

        net_revenue = _round2(main_revenue - refund_revenue)
        net_vat = _round2(vat_amount - vat_refunded)

        # 附加税
        surcharge_base = net_vat + consumption_tax
        surcharge_city = _round2(surcharge_base * SURCHARGE_CITY)
        surcharge_edu = _round2(surcharge_base * SURCHARGE_EDU)
        surcharge_local_edu = _round2(surcharge_base * SURCHARGE_LOCAL_EDU)
        surtax_total = _round2(surcharge_city + surcharge_edu + surcharge_local_edu)

        # 成本/费用(Mock 估算)
        cost = _round2(net_revenue * 0.60)
        sales_expense = _round2(net_revenue * 0.05)
        admin_expense = _round2(net_revenue * 0.05)
        finance_expense = _round2(net_revenue * 0.005)  # 支付手续费 0.5%

        tax_surcharge = _round2(consumption_tax + surtax_total)
        profit_before_tax = _round2(
            net_revenue + other_revenue - cost - tax_surcharge
            - sales_expense - admin_expense - finance_expense
        )
        is_small_micro = profit_before_tax <= 3000000
        income_tax_rate = INCOME_TAX_RATE_SMALL if is_small_micro else INCOME_TAX_RATE
        income_tax = _round2(max(0, profit_before_tax) * income_tax_rate)
        net_profit = _round2(profit_before_tax - income_tax)

        logs.append({"step": "收入汇总", "level": "INFO",
                     "msg": f"主营业务收入 ¥{net_revenue:.2f}"})
        logs.append({"step": "税金及附加", "level": "INFO",
                     "msg": f"消费税 ¥{consumption_tax:.2f} + 附加税 ¥{surtax_total:.2f}"})
        logs.append({"step": "利润总额", "level": "WARN",
                     "msg": f"利润总额 ¥{profit_before_tax:.2f}"})
        logs.append({"step": "净利润", "level": "WARN",
                     "msg": f"净利润 ¥{net_profit:.2f}"})

        statement = {
            "period": clean_period,
            "revenue": {
                "mainRevenue": net_revenue,
                "otherRevenue": other_revenue,
                "total": _round2(net_revenue + other_revenue),
            },
            "cost": {
                "production": _round2(cost * 0.50),
                "purchase": _round2(cost * 0.30),
                "logistics": _round2(cost * 0.15),
                "packaging": _round2(cost * 0.05),
                "total": cost,
            },
            "taxAndSurcharge": {
                "consumptionTax": consumption_tax,
                "surtax": {
                    "city": surcharge_city,
                    "education": surcharge_edu,
                    "localEducation": surcharge_local_edu,
                    "total": surtax_total,
                },
                "total": tax_surcharge,
            },
            "expenses": {
                "sales": {
                    "marketing": _round2(sales_expense * 0.60),
                    "commission": _round2(sales_expense * 0.40),
                    "total": sales_expense,
                },
                "admin": {
                    "hr": _round2(admin_expense * 0.70),
                    "platform": _round2(admin_expense * 0.30),
                    "total": admin_expense,
                },
                "finance": {
                    "paymentFee": finance_expense,
                    "interest": 0,
                    "total": finance_expense,
                },
                "total": _round2(sales_expense + admin_expense + finance_expense),
            },
            "profitBeforeTax": profit_before_tax,
            "incomeTax": {
                "taxable": max(0, profit_before_tax),
                "rate": income_tax_rate,
                "isSmallMicro": is_small_micro,
                "amount": income_tax,
            },
            "netProfit": net_profit,
            "voucherCount": len(posted),
            "generatedAt": ts(),
        }

        return {
            "success": True,
            "period": clean_period,
            "statement": statement,
            "logs": logs,
        }

    async def management_report(self, date: str) -> dict:
        """管理报表(日度)

        汇总当日:
            - 订单数/销售额/退款数/退款额
            - 应收/应付(Mock)
            - 库存价值估算
        """
        logs = [
            {"step": "管理报表", "level": "INFO",
             "msg": f"日期 {date}"},
        ]

        all_orders = await self.order_repo.list_all()
        day_orders = []
        day_refunds = []
        for o in all_orders:
            created = o.get("createdAt", "")
            if created.startswith(date):
                day_orders.append(o)
            refund_at = (o.get("refund") or {}).get("refundedAt", "")
            if refund_at.startswith(date) and o.get("status") == "REFUNDED":
                day_refunds.append(o)

        sales_count = len(day_orders)
        sales_amount = _round2(
            sum(o["priceDetail"]["actualAmount"] for o in day_orders)
        )
        refund_count = len(day_refunds)
        refund_amount = _round2(
            sum((o.get("refund") or {}).get("refundedAmount", 0) for o in day_refunds)
        )
        net_amount = _round2(sales_amount - refund_amount)

        # 凭证/发票/付款 汇总(当日)
        day_vouchers = await self.finance_repo.list_vouchers()
        day_voucher_count = sum(
            1 for v in day_vouchers
            if (v.get("createdAt", "")).startswith(date)
        )
        day_invoices = await self.finance_repo.list_invoices()
        day_invoice_count = sum(
            1 for inv in day_invoices
            if (inv.get("createdAt", "")).startswith(date)
        )
        day_payments = await self.finance_repo.list_payments()
        day_payment_count = sum(
            1 for p in day_payments
            if (p.get("createdAt", "")).startswith(date)
        )

        # Mock 应收/应付
        receivable = _round2(sales_amount * 0.05)  # 假设 5% 未到账
        payable = _round2(sales_amount * 0.50)     # 假设 50% 待付供应商

        logs.append({"step": "销售汇总", "level": "INFO",
                     "msg": f"销售 {sales_count} 笔 ¥{sales_amount:.2f}, 退款 {refund_count} 笔 ¥{refund_amount:.2f}"})
        logs.append({"step": "财务活动", "level": "INFO",
                     "msg": f"凭证 {day_voucher_count} 张, 发票 {day_invoice_count} 张, 付款 {day_payment_count} 笔"})

        report = {
            "date": date,
            "sales": {
                "orderCount": sales_count,
                "orderAmount": sales_amount,
                "refundCount": refund_count,
                "refundAmount": refund_amount,
                "netAmount": net_amount,
            },
            "finance": {
                "voucherCount": day_voucher_count,
                "invoiceCount": day_invoice_count,
                "paymentCount": day_payment_count,
            },
            "balance": {
                "receivable": receivable,
                "payable": payable,
                "netCash": _round2(net_amount - payable),
            },
            "generatedAt": ts(),
        }

        return {
            "success": True,
            "date": date,
            "report": report,
            "logs": logs,
        }
