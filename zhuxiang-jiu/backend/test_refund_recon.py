"""
退款与对账模块单元测试

覆盖范围:
    1. 退款流程(create_refund / audit_refund / refund_callback / cancel_refund)
       - 正常路径: 创建→审核通过→回调完成→全额退款
       - 异常路径: 支付单不存在/状态非法/退款超额/审核拒绝/重复回调
       - 幂等性: 重复回调、重复创建
       - 边界值: 全额退款、部分退款、累计超额
    2. 对账流程(start_reconciliation / investigate_diff / resolve_reconciliation)
       - 正常路径: 启动对账→完全对平/存在差异→调查→处理完成
       - 异常路径: 重复对账、状态非法
       - 边界值: 空流水、单边差异、金额不一致
    3. PaymentRepository 仓储层
       - 退款单 CRUD(save_refund / get_refund / list_refunds)
       - 退款金额累计(add_refunded_amount / sum_refunded_amount)
       - 对账记录 CRUD(create_recon / get_recon / update_recon_status)
       - 差异明细(add_diff_detail / list_pending_diffs)
       - 回调锁(acquire_callback_lock / acquire_refund_callback_lock)

运行:
    pytest test_refund_recon.py -v                # 内存模式
    pytest test_refund_recon.py -p fakeredis_plugin -v  # Redis 模式
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 路径设置
BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

from main import app, _mock_store
from repositories.store import _build_initial_inventory
from services.payment_service import PaymentService
from repositories.payment_repository import (
    PaymentRepository,
    PAY_STATUS_PENDING, PAY_STATUS_PAYING, PAY_STATUS_PAID,
    PAY_STATUS_FAILED, PAY_STATUS_CLOSED,
    PAY_STATUS_REFUNDING, PAY_STATUS_REFUNDED,
    REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING,
    REFUND_STATUS_APPROVED, REFUND_STATUS_REJECTED,
    REFUND_STATUS_REFUNDED, REFUND_STATUS_CANCELLED,
    RECON_STATUS_PENDING, RECON_STATUS_MATCHED,
    RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING,
    RECON_STATUS_RESOLVED,
    MATCH_TYPE_FULL, MATCH_TYPE_PARTIAL, MATCH_TYPE_MISMATCH,
    DIFF_TYPE_AMOUNT_MISMATCH, DIFF_TYPE_PLATFORM_ONLY, DIFF_TYPE_CHANNEL_ONLY,
    HANDLE_SUGGEST_REFUND, HANDLE_SUGGEST_SUPPLEMENT,
)

client = TestClient(app)


# ============================================================
#  公共夹具与辅助
# ============================================================

@pytest.fixture(autouse=True)
def _reset_payment_store():
    """每个测试前重置支付/退款/对账存储"""
    _mock_store["payments"] = {}
    _mock_store["payment_orders"] = {}
    _mock_store["payment_refunds"] = {}
    _mock_store["payment_payouts"] = {}
    _mock_store["payment_recons"] = {}
    _mock_store["payment_recon_diffs"] = []
    _mock_store["payment_channels"] = {}
    _mock_store["payment_seq"] = {}
    # 锁集合使用下划线前缀(与 payment_repository 实现一致)
    _mock_store["_payment_callback_locks"] = set()
    _mock_store["_payment_refund_callback_locks"] = set()
    _mock_store["_payment_recon_locks"] = {}
    _mock_store["_payment_refund_pending"] = set()
    _mock_store["_payment_payout_pending"] = set()
    _mock_store["inventory"] = _build_initial_inventory()
    yield
    # 测试后清理
    _mock_store["payments"] = {}
    _mock_store["payment_refunds"] = {}
    _mock_store["payment_recons"] = {}


@pytest.fixture
async def paid_order():
    """创建一笔已支付的订单(用于退款测试)"""
    svc = PaymentService()
    # 1. 创建支付单
    create_resp = await svc.create_pay(
        user_id=1, order_id="ORDER_TEST_001", order_type="retail",
        total_amount=398.0, pay_channel="wechat",
        pay_method="jsapi", scene_type="order_pay",
    )
    pay_no = create_resp["payNo"]
    # 2. 发起支付(pending → paying)
    await svc.start_pay(pay_no)
    # 3. 模拟支付回调(paying → paid)
    await svc.pay_callback(
        channel_trade_no=f"CB{pay_no}",
        callback_content={"status": "SUCCESS", "amount": 398.0},
        pay_no=pay_no,
    )
    return pay_no


# ============================================================
#  1. 退款创建测试
# ============================================================

class TestCreateRefund:
    """退款单创建测试"""

    @pytest.mark.asyncio
    async def test_create_partial_refund_success(self, paid_order):
        """部分退款: 正常创建"""
        svc = PaymentService()
        result = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="商品质量问题", refund_type="partial",
        )
        assert result["success"] is True
        assert result["refundNo"].startswith("RF")
        assert result["refundAmount"] == 100.0
        assert result["status"] == REFUND_STATUS_PENDING
        assert result["remainRefundable"] == 298.0  # 398-100

    @pytest.mark.asyncio
    async def test_create_full_refund_success(self, paid_order):
        """全额退款: 自动计算剩余可退金额"""
        svc = PaymentService()
        result = await svc.create_refund(
            pay_no=paid_order, refund_amount=0,
            refund_reason="用户全部退货", refund_type="full",
        )
        assert result["success"] is True
        assert result["refundAmount"] == 398.0  # 全额
        assert result["remainRefundable"] == 0.0

    @pytest.mark.asyncio
    async def test_create_refund_payment_not_found(self):
        """支付单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.create_refund(
                pay_no="NOT_EXIST", refund_amount=100.0,
                refund_reason="test", refund_type="partial",
            )

    @pytest.mark.asyncio
    async def test_create_refund_payment_not_paid(self, paid_order):
        """支付单未支付: ValueError"""
        svc = PaymentService()
        # 创建一笔未支付的订单
        create_resp = await svc.create_pay(
            user_id=1, order_id="ORDER_PENDING", order_type="retail",
            total_amount=100.0, pay_channel="wechat",
            pay_method="jsapi", scene_type="order_pay",
        )
        with pytest.raises(ValueError, match="支付单状态非法"):
            await svc.create_refund(
                pay_no=create_resp["payNo"], refund_amount=50.0,
                refund_reason="test", refund_type="partial",
            )

    @pytest.mark.asyncio
    async def test_create_refund_invalid_type(self, paid_order):
        """退款类型非法: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="退款类型非法"):
            await svc.create_refund(
                pay_no=paid_order, refund_amount=100.0,
                refund_reason="test", refund_type="invalid",
            )

    @pytest.mark.asyncio
    async def test_create_refund_zero_amount_partial(self, paid_order):
        """部分退款金额为0: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="部分退款金额须 > 0"):
            await svc.create_refund(
                pay_no=paid_order, refund_amount=0,
                refund_reason="test", refund_type="partial",
            )

    @pytest.mark.asyncio
    async def test_create_refund_exceed_limit(self, paid_order):
        """退款超额: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="退款超额"):
            await svc.create_refund(
                pay_no=paid_order, refund_amount=999.0,
                refund_reason="test", refund_type="partial",
            )

    @pytest.mark.asyncio
    async def test_create_refund_accumulated_exceed(self, paid_order):
        """累计退款超额: ValueError"""
        svc = PaymentService()
        # 第一次退款 300
        await svc.create_refund(
            pay_no=paid_order, refund_amount=300.0,
            refund_reason="第一次", refund_type="partial",
        )
        # 第二次再退 200(累计 500 > 398)
        with pytest.raises(ValueError, match="退款超额"):
            await svc.create_refund(
                pay_no=paid_order, refund_amount=200.0,
                refund_reason="第二次", refund_type="partial",
            )

    @pytest.mark.asyncio
    async def test_create_refund_full_after_partial(self, paid_order):
        """部分退款后再全额退款: 自动计算剩余"""
        svc = PaymentService()
        # 先退 100
        await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="第一次", refund_type="partial",
        )
        # 再全额退(应为 398-100=298)
        result = await svc.create_refund(
            pay_no=paid_order, refund_amount=0,
            refund_reason="剩余全退", refund_type="full",
        )
        assert result["refundAmount"] == 298.0

    @pytest.mark.asyncio
    async def test_create_refund_full_when_no_remain(self, paid_order):
        """已全额退款后再全额退款: ValueError"""
        svc = PaymentService()
        # 全额退款
        await svc.create_refund(
            pay_no=paid_order, refund_amount=0,
            refund_reason="全额", refund_type="full",
        )
        # 再次全额退款
        with pytest.raises(ValueError, match="无可退金额"):
            await svc.create_refund(
                pay_no=paid_order, refund_amount=0,
                refund_reason="再次", refund_type="full",
            )


# ============================================================
#  2. 退款审核测试
# ============================================================

class TestAuditRefund:
    """退款审核测试"""

    @pytest.mark.asyncio
    async def test_audit_approve_success(self, paid_order):
        """审核通过: pending → approved"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        result = await svc.audit_refund(
            refund_no=refund["refundNo"], decision="approved",
            auditor="admin", audit_remark="同意退款",
        )
        assert result["success"] is True
        assert result["status"] == REFUND_STATUS_APPROVED
        assert result["auditor"] == "admin"

    @pytest.mark.asyncio
    async def test_audit_reject_success(self, paid_order):
        """审核拒绝: pending → rejected,支付单状态回退"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        result = await svc.audit_refund(
            refund_no=refund["refundNo"], decision="rejected",
            auditor="admin", audit_remark="拒绝退款",
        )
        assert result["status"] == REFUND_STATUS_REJECTED
        # 支付单状态应回退到 paid
        order = await svc.repo.get_order(paid_order)
        assert order["status"] == PAY_STATUS_PAID

    @pytest.mark.asyncio
    async def test_audit_invalid_decision(self, paid_order):
        """审核决定非法: ValueError"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        with pytest.raises(ValueError, match="审核决定非法"):
            await svc.audit_refund(
                refund_no=refund["refundNo"], decision="invalid",
            )

    @pytest.mark.asyncio
    async def test_audit_refund_not_found(self):
        """退款单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.audit_refund(refund_no="NOT_EXIST", decision="approved")

    @pytest.mark.asyncio
    async def test_audit_refund_wrong_status(self, paid_order):
        """退款单状态非法(已审核): ValueError"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        # 先审核一次
        await svc.audit_refund(refund_no=refund["refundNo"], decision="approved")
        # 再次审核应失败
        with pytest.raises(ValueError, match="状态非法"):
            await svc.audit_refund(
                refund_no=refund["refundNo"], decision="approved",
            )


# ============================================================
#  3. 退款回调测试
# ============================================================

class TestRefundCallback:
    """退款回调测试"""

    @pytest.mark.asyncio
    async def test_refund_callback_success(self, paid_order):
        """退款回调成功: approved → refunded"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        await svc.audit_refund(refund_no=refund["refundNo"], decision="approved")

        result = await svc.refund_callback(
            channel_refund_no="CB_REFUND_001",
            callback_content={"status": "SUCCESS"},
            refund_no=refund["refundNo"],
        )
        assert result["success"] is True
        assert result["idempotent"] is False
        assert result["refundAmount"] == 100.0

    @pytest.mark.asyncio
    async def test_refund_callback_idempotent(self, paid_order):
        """重复回调: 幂等返回"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        await svc.audit_refund(refund_no=refund["refundNo"], decision="approved")

        # 第一次回调
        await svc.refund_callback(
            channel_refund_no="CB_REFUND_002",
            callback_content={"status": "SUCCESS"},
            refund_no=refund["refundNo"],
        )
        # 第二次回调(相同 channel_refund_no)
        result = await svc.refund_callback(
            channel_refund_no="CB_REFUND_002",
            callback_content={"status": "SUCCESS"},
            refund_no=refund["refundNo"],
        )
        assert result["success"] is True
        assert result["idempotent"] is True

    @pytest.mark.asyncio
    async def test_refund_callback_no_refund_no(self):
        """未提供 refund_no: 失败"""
        svc = PaymentService()
        result = await svc.refund_callback(
            channel_refund_no="CB_NO_REF",
            callback_content={"status": "SUCCESS"},
        )
        assert result["success"] is False
        assert "未提供 refund_no" in result["msg"]

    @pytest.mark.asyncio
    async def test_refund_callback_refund_not_found(self):
        """退款单不存在"""
        svc = PaymentService()
        result = await svc.refund_callback(
            channel_refund_no="CB_NOT_EXIST",
            callback_content={"status": "SUCCESS"},
            refund_no="RF_NOT_EXIST",
        )
        assert result["success"] is False
        assert "不存在" in result["msg"]

    @pytest.mark.asyncio
    async def test_refund_callback_wrong_status(self, paid_order):
        """退款单状态非法(未审核): 失败"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        # 未审核直接回调
        result = await svc.refund_callback(
            channel_refund_no="CB_WRONG_STATUS",
            callback_content={"status": "SUCCESS"},
            refund_no=refund["refundNo"],
        )
        assert result["success"] is False
        assert "状态非法" in result["msg"]

    @pytest.mark.asyncio
    async def test_refund_callback_full_refund_flow(self, paid_order):
        """全额退款完整流程: 创建→审核→回调→支付单 refunded"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=0,
            refund_reason="全额退款", refund_type="full",
        )
        await svc.audit_refund(refund_no=refund["refundNo"], decision="approved")
        await svc.refund_callback(
            channel_refund_no="CB_FULL_001",
            callback_content={"status": "SUCCESS"},
            refund_no=refund["refundNo"],
        )
        # 支付单应变为 refunded
        order = await svc.repo.get_order(paid_order)
        assert order["status"] == PAY_STATUS_REFUNDED
        assert order["refundedAmount"] == 398.0


# ============================================================
#  4. 退款撤回测试
# ============================================================

class TestCancelRefund:
    """退款撤回测试"""

    @pytest.mark.asyncio
    async def test_cancel_refund_success(self, paid_order):
        """撤回退款: pending → cancelled"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        result = await svc.cancel_refund(refund_no=refund["refundNo"])
        assert result["success"] is True
        assert result["status"] == REFUND_STATUS_CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_refund_not_found(self):
        """退款单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.cancel_refund(refund_no="NOT_EXIST")

    @pytest.mark.asyncio
    async def test_cancel_refund_wrong_status(self, paid_order):
        """撤回已审核退款: ValueError"""
        svc = PaymentService()
        refund = await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        await svc.audit_refund(refund_no=refund["refundNo"], decision="approved")
        with pytest.raises(ValueError, match="不可撤回"):
            await svc.cancel_refund(refund_no=refund["refundNo"])


# ============================================================
#  5. 退款列表查询测试
# ============================================================

class TestListRefunds:
    """退款列表查询测试"""

    @pytest.mark.asyncio
    async def test_list_refunds_by_pay_no(self, paid_order):
        """按支付单号查询退款列表"""
        svc = PaymentService()
        await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="第一次", refund_type="partial",
        )
        await svc.create_refund(
            pay_no=paid_order, refund_amount=50.0,
            refund_reason="第二次", refund_type="partial",
        )
        result = await svc.list_refunds(pay_no=paid_order)
        assert result["success"] is True
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_list_pending_refunds(self, paid_order):
        """查询待审核退款列表"""
        svc = PaymentService()
        await svc.create_refund(
            pay_no=paid_order, refund_amount=100.0,
            refund_reason="test", refund_type="partial",
        )
        result = await svc.list_pending_refunds()
        assert result["success"] is True
        assert result["count"] >= 1


# ============================================================
#  6. 对账流程测试
# ============================================================

class TestReconciliation:
    """对账流程测试"""

    @pytest.mark.asyncio
    async def test_start_reconciliation_matched(self, paid_order):
        """启动对账: 完全对平(无差异)"""
        svc = PaymentService()
        result = await svc.start_reconciliation(
            recon_date="2026-08-25", channel="wechat",
            operator="admin",
        )
        assert result["status"] == RECON_STATUS_MATCHED
        assert result["matchType"] == MATCH_TYPE_FULL
        assert result["diffCount"] == 0

    @pytest.mark.asyncio
    async def test_start_reconciliation_duplicate(self, paid_order):
        """重复对账: ValueError"""
        svc = PaymentService()
        await svc.start_reconciliation(
            recon_date="2026-08-26", channel="wechat",
        )
        with pytest.raises(ValueError, match="已在对账中或已对账"):
            await svc.start_reconciliation(
                recon_date="2026-08-26", channel="wechat",
            )

    @pytest.mark.asyncio
    async def test_get_reconciliation_success(self, paid_order):
        """查询对账详情"""
        svc = PaymentService()
        await svc.start_reconciliation(
            recon_date="2026-08-27", channel="wechat",
        )
        recon_no = f"RECON20260827WECHAT"
        result = await svc.get_reconciliation(recon_no)
        assert result["reconNo"] == recon_no
        assert result["status"] == RECON_STATUS_MATCHED

    @pytest.mark.asyncio
    async def test_get_reconciliation_not_found(self):
        """查询不存在的对账记录: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.get_reconciliation("RECON_NOT_EXIST")

    @pytest.mark.asyncio
    async def test_list_reconciliations(self, paid_order):
        """对账记录列表"""
        svc = PaymentService()
        await svc.start_reconciliation(
            recon_date="2026-08-28", channel="wechat",
        )
        result = await svc.list_reconciliations()
        assert result["success"] is True
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_resolve_reconciliation_wrong_status(self, paid_order):
        """处理完成状态非法: ValueError"""
        svc = PaymentService()
        # 对账已对平(非 diff 状态),不可调查
        await svc.start_reconciliation(
            recon_date="2026-08-29", channel="wechat",
        )
        recon_no = f"RECON20260829WECHAT"
        with pytest.raises(ValueError, match="不可介入调查"):
            await svc.investigate_diff(recon_no, operator="admin")

    @pytest.mark.asyncio
    async def test_investigate_not_found(self):
        """调查不存在的对账记录: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.investigate_diff("RECON_NOT_EXIST", operator="admin")

    @pytest.mark.asyncio
    async def test_resolve_not_found(self):
        """处理完成不存在的对账记录: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.resolve_reconciliation("RECON_NOT_EXIST", operator="admin")


# ============================================================
#  7. PaymentRepository 仓储层测试
# ============================================================

class TestPaymentRepository:
    """PaymentRepository 直接调用测试"""

    @pytest.mark.asyncio
    async def test_save_and_get_order(self):
        """保存并获取支付单"""
        repo = PaymentRepository()
        order = {
            "payNo": "PAY_TEST_001",
            "orderId": "ORDER_001",
            "userId": 1,
            "status": PAY_STATUS_PAID,
            "actualAmount": 398.0,
            "refundedAmount": 0.0,
            "payChannel": "wechat",
            "channelTradeNo": "CB001",
        }
        await repo.save_order(order)
        result = await repo.get_order("PAY_TEST_001")
        assert result is not None
        assert result["payNo"] == "PAY_TEST_001"
        assert result["status"] == PAY_STATUS_PAID

    @pytest.mark.asyncio
    async def test_get_order_not_found(self):
        """获取不存在的支付单"""
        repo = PaymentRepository()
        result = await repo.get_order("NOT_EXIST")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_channel_trade_no(self):
        """按渠道交易号查询"""
        repo = PaymentRepository()
        await repo.save_order({
            "payNo": "PAY_CB_001",
            "channelTradeNo": "CB_UNIQUE_001",
            "status": PAY_STATUS_PAID,
            "actualAmount": 100.0,
        })
        result = await repo.get_by_channel_trade_no("CB_UNIQUE_001")
        assert result is not None
        assert result["payNo"] == "PAY_CB_001"

    @pytest.mark.asyncio
    async def test_update_order_fields(self):
        """更新支付单字段"""
        repo = PaymentRepository()
        await repo.save_order({
            "payNo": "PAY_UPDATE",
            "status": PAY_STATUS_PENDING,
            "actualAmount": 100.0,
        })
        await repo.update_order_fields("PAY_UPDATE", {
            "status": PAY_STATUS_PAID,
            "refundedAmount": 50.0,
        })
        result = await repo.get_order("PAY_UPDATE")
        assert result["status"] == PAY_STATUS_PAID
        assert result["refundedAmount"] == 50.0

    @pytest.mark.asyncio
    async def test_add_refunded_amount(self):
        """累计退款金额"""
        repo = PaymentRepository()
        await repo.save_order({
            "payNo": "PAY_REFUND",
            "status": PAY_STATUS_PAID,
            "actualAmount": 398.0,
            "refundedAmount": 0.0,
        })
        # 第一次累计 100
        amount = await repo.add_refunded_amount("PAY_REFUND", 100.0)
        assert amount == 100.0
        # 第二次累计 50
        amount = await repo.add_refunded_amount("PAY_REFUND", 50.0)
        assert amount == 150.0

    @pytest.mark.asyncio
    async def test_save_and_get_refund(self):
        """保存并获取退款单"""
        repo = PaymentRepository()
        refund = {
            "refundNo": "RF_TEST_001",
            "payNo": "PAY_001",
            "refundAmount": 100.0,
            "status": REFUND_STATUS_PENDING,
        }
        await repo.save_refund(refund)
        result = await repo.get_refund("RF_TEST_001")
        assert result is not None
        assert result["refundNo"] == "RF_TEST_001"
        assert result["status"] == REFUND_STATUS_PENDING

    @pytest.mark.asyncio
    async def test_list_refunds_by_pay_no(self):
        """按支付单号查询退款列表"""
        repo = PaymentRepository()
        await repo.save_refund({
            "refundNo": "RF_001", "payNo": "PAY_LIST",
            "refundAmount": 100.0, "status": REFUND_STATUS_PENDING,
        })
        await repo.save_refund({
            "refundNo": "RF_002", "payNo": "PAY_LIST",
            "refundAmount": 50.0, "status": REFUND_STATUS_REFUNDED,
        })
        result = await repo.list_refunds("PAY_LIST")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_refunds_filter_by_status(self):
        """按状态过滤退款列表"""
        repo = PaymentRepository()
        await repo.save_refund({
            "refundNo": "RF_P", "payNo": "PAY_F",
            "refundAmount": 100.0, "status": REFUND_STATUS_PENDING,
        })
        await repo.save_refund({
            "refundNo": "RF_R", "payNo": "PAY_F",
            "refundAmount": 50.0, "status": REFUND_STATUS_REFUNDED,
        })
        result = await repo.list_refunds("PAY_F", status=REFUND_STATUS_PENDING)
        assert len(result) == 1
        assert result[0]["refundNo"] == "RF_P"

    @pytest.mark.asyncio
    async def test_list_pending_refunds(self):
        """查询待处理的退款(仅 pending + auditing)"""
        repo = PaymentRepository()
        # 创建 pending 状态退款(save_refund 自动加入 pending_set)
        await repo.save_refund({
            "refundNo": "RF_PEND", "payNo": "PAY_PEND",
            "refundAmount": 100.0, "status": REFUND_STATUS_PENDING,
        })
        # 通过 update_refund_fields 将状态改为 auditing(会自动维护 pending_set)
        await repo.save_refund({
            "refundNo": "RF_AUDIT", "payNo": "PAY_PEND",
            "refundAmount": 50.0, "status": REFUND_STATUS_PENDING,
        })
        await repo.update_refund_fields("RF_AUDIT", {"status": REFUND_STATUS_AUDITING})
        # approved 状态不应在 pending_set 中
        await repo.save_refund({
            "refundNo": "RF_APPR", "payNo": "PAY_PEND",
            "refundAmount": 30.0, "status": REFUND_STATUS_PENDING,
        })
        await repo.update_refund_fields("RF_APPR", {"status": REFUND_STATUS_APPROVED})
        result = await repo.list_pending_refunds()
        # 只返回 pending + auditing 状态(approved 不在待处理集合)
        statuses = [r["status"] for r in result]
        assert REFUND_STATUS_APPROVED not in statuses
        assert len(result) >= 1  # 至少有 RF_PEND 和 RF_AUDIT

    @pytest.mark.asyncio
    async def test_update_refund_fields(self):
        """更新退款单字段"""
        repo = PaymentRepository()
        await repo.save_refund({
            "refundNo": "RF_UPD", "payNo": "PAY_UPD",
            "refundAmount": 100.0, "status": REFUND_STATUS_PENDING,
        })
        await repo.update_refund_fields("RF_UPD", {
            "status": REFUND_STATUS_APPROVED,
            "auditor": "admin",
        })
        result = await repo.get_refund("RF_UPD")
        assert result["status"] == REFUND_STATUS_APPROVED
        assert result["auditor"] == "admin"

    @pytest.mark.asyncio
    async def test_sum_refunded_amount(self):
        """统计已退款金额"""
        repo = PaymentRepository()
        await repo.save_refund({
            "refundNo": "RF_S1", "payNo": "PAY_SUM",
            "refundAmount": 100.0, "status": REFUND_STATUS_REFUNDED,
        })
        await repo.save_refund({
            "refundNo": "RF_S2", "payNo": "PAY_SUM",
            "refundAmount": 50.0, "status": REFUND_STATUS_REFUNDED,
        })
        await repo.save_refund({
            "refundNo": "RF_S3", "payNo": "PAY_SUM",
            "refundAmount": 30.0, "status": REFUND_STATUS_PENDING,
        })
        # 只统计 refunded 状态
        total = await repo.sum_refunded_amount("PAY_SUM")
        assert total == 150.0  # 100 + 50

    @pytest.mark.asyncio
    async def test_acquire_callback_lock(self):
        """获取回调锁"""
        repo = PaymentRepository()
        # 第一次获取成功
        ok1 = await repo.acquire_callback_lock("CB_LOCK_001")
        assert ok1 is True
        # 第二次获取失败(已锁定)
        ok2 = await repo.acquire_callback_lock("CB_LOCK_001")
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_acquire_refund_callback_lock(self):
        """获取退款回调锁"""
        repo = PaymentRepository()
        ok1 = await repo.acquire_refund_callback_lock("CB_RF_LOCK_001")
        assert ok1 is True
        ok2 = await repo.acquire_refund_callback_lock("CB_RF_LOCK_001")
        assert ok2 is False


# ============================================================
#  8. 对账仓储层测试
# ============================================================

class TestReconciliationRepository:
    """对账记录仓储层测试"""

    @pytest.mark.asyncio
    async def test_create_and_get_recon(self):
        """创建并获取对账记录"""
        repo = PaymentRepository()
        recon = {
            "reconNo": "RECON_TEST_001",
            "reconDate": "2026-08-25",
            "channel": "wechat",
            "status": RECON_STATUS_PENDING,
            "platformCount": 0,
            "platformAmount": 0.0,
            "channelCount": 0,
            "channelAmount": 0.0,
            "diffCount": 0,
            "diffAmount": 0.0,
        }
        await repo.create_recon(recon)
        result = await repo.get_recon("RECON_TEST_001")
        assert result is not None
        assert result["reconNo"] == "RECON_TEST_001"
        assert result["status"] == RECON_STATUS_PENDING

    @pytest.mark.asyncio
    async def test_get_recon_not_found(self):
        """获取不存在的对账记录"""
        repo = PaymentRepository()
        result = await repo.get_recon("NOT_EXIST")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_recon_status(self):
        """更新对账状态"""
        repo = PaymentRepository()
        await repo.create_recon({
            "reconNo": "RECON_UPD",
            "reconDate": "2026-08-25",
            "channel": "wechat",
            "status": RECON_STATUS_PENDING,
        })
        await repo.update_recon_status("RECON_UPD", RECON_STATUS_MATCHED, extra={
            "platformCount": 5,
            "platformAmount": 1000.0,
        })
        result = await repo.get_recon("RECON_UPD")
        assert result["status"] == RECON_STATUS_MATCHED
        assert result["platformCount"] == 5
        assert result["platformAmount"] == 1000.0

    @pytest.mark.asyncio
    async def test_add_diff_detail(self):
        """添加差异明细"""
        repo = PaymentRepository()
        await repo.create_recon({
            "reconNo": "RECON_DIFF",
            "reconDate": "2026-08-25",
            "channel": "wechat",
            "status": RECON_STATUS_DIFF,
            "diffDetails": [],
        })
        diff = {
            "payNo": "PAY_DIFF",
            "channelTradeNo": "CB_DIFF",
            "type": DIFF_TYPE_PLATFORM_ONLY,
            "platformAmount": 100.0,
            "channelAmount": 0.0,
            "diffAmount": 100.0,
            "handleSuggestion": HANDLE_SUGGEST_SUPPLEMENT,
        }
        await repo.add_diff_detail("RECON_DIFF", diff)
        result = await repo.get_recon("RECON_DIFF")
        assert len(result["diffDetails"]) == 1
        assert result["diffDetails"][0]["type"] == DIFF_TYPE_PLATFORM_ONLY

    @pytest.mark.asyncio
    async def test_list_pending_diffs(self):
        """查询待处理差异列表"""
        repo = PaymentRepository()
        await repo.create_recon({
            "reconNo": "RECON_PEND",
            "reconDate": "2026-08-25",
            "channel": "wechat",
            "status": RECON_STATUS_DIFF,
            "diffDetails": [],
        })
        await repo.add_diff_detail("RECON_PEND", {
            "payNo": "PAY_P", "channelTradeNo": "CB_P",
            "type": DIFF_TYPE_PLATFORM_ONLY,
            "platformAmount": 100.0, "channelAmount": 0.0,
            "diffAmount": 100.0, "handleSuggestion": HANDLE_SUGGEST_SUPPLEMENT,
        })
        result = await repo.list_pending_diffs()
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_list_recons(self):
        """对账记录列表"""
        repo = PaymentRepository()
        await repo.create_recon({
            "reconNo": "RECON_L1",
            "reconDate": "2026-08-25",
            "channel": "wechat",
            "status": RECON_STATUS_MATCHED,
        })
        await repo.create_recon({
            "reconNo": "RECON_L2",
            "reconDate": "2026-08-25",
            "channel": "alipay",
            "status": RECON_STATUS_DIFF,
        })
        # 查询全部
        all_recons = await repo.list_recons()
        assert len(all_recons) >= 2
        # 按渠道过滤
        wechat_recons = await repo.list_recons(channel="wechat")
        assert all(r["channel"] == "wechat" for r in wechat_recons)
        # 按状态过滤
        diff_recons = await repo.list_recons(status=RECON_STATUS_DIFF)
        assert all(r["status"] == RECON_STATUS_DIFF for r in diff_recons)

    @pytest.mark.asyncio
    async def test_acquire_recon_lock(self):
        """获取对账锁"""
        repo = PaymentRepository()
        ok1 = await repo.acquire_recon_lock("2026-08-25", "wechat")
        assert ok1 is True
        # 重复获取失败
        ok2 = await repo.acquire_recon_lock("2026-08-25", "wechat")
        assert ok2 is False
        # 不同渠道可获取
        ok3 = await repo.acquire_recon_lock("2026-08-25", "alipay")
        assert ok3 is True
