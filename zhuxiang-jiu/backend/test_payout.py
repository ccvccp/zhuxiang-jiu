"""
付款(Payout)模块单元测试

覆盖范围:
    1. 付款流程(create_payout / audit_payout / execute_payout / payout_callback / retry_payout)
       - 正常路径: 创建→审核→执行→回调成功(paid)
       - 异常路径: 参数非法/重复创建/状态非法/付款单不存在
       - 幂等性: 重复回调
       - 边界值: 小额自动审核/大额手动审核/失败重试/重试上限
    2. 付款查询(get_payout / list_payouts / list_pending_payouts)
    3. PaymentRepository 付款仓储层
       - 付款单 CRUD(save_payout / get_payout / list_payouts)
       - 来源查重(find_by_source)
       - 重试次数累加(increment_payout_retry)
       - 字段更新(update_payout_fields)
       - 待审核列表(list_pending_payouts)

运行:
    pytest test_payout.py -v                          # 内存模式
    pytest test_payout.py -p fakeredis_plugin -v       # Redis 模式
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
    PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING,
    PAYOUT_STATUS_APPROVED, PAYOUT_STATUS_PAYING,
    PAYOUT_STATUS_PAID, PAYOUT_STATUS_FAILED,
    PAYOUT_STATUS_REJECTED, PAYOUT_STATUS_CANCELLED,
)

client = TestClient(app)

# 小额自动审核阈值
AUTO_THRESHOLD = 5000.0
MAX_RETRY = 3


# ============================================================
#  公共夹具
# ============================================================

@pytest.fixture(autouse=True)
def _reset_payout_store():
    """每个测试前重置付款存储"""
    _mock_store["payment_payouts"] = {}
    _mock_store["payments"] = {}
    _mock_store["payment_refunds"] = {}
    _mock_store["payment_seq"] = {}
    _mock_store["_payment_payout_pending"] = set()
    _mock_store["_payment_callback_locks"] = set()
    _mock_store["_payment_refund_callback_locks"] = set()
    _mock_store["_payment_recon_locks"] = {}
    _mock_store["inventory"] = _build_initial_inventory()
    yield
    _mock_store["payment_payouts"] = {}
    _mock_store["payments"] = {}


@pytest.fixture
def small_payout():
    """创建小额付款单(自动审核通过)"""
    import asyncio
    svc = PaymentService()
    return asyncio.get_event_loop().run_until_complete(svc.create_payout(
        payout_type="supplier", source_id="PO_SMALL_001",
        payee_name="供应商A", payee_account="6222001234567890",
        payee_bank="ICBC", amount=1000.0,
    ))


@pytest.fixture
def large_payout():
    """创建大额付款单(需手动审核)"""
    import asyncio
    svc = PaymentService()
    return asyncio.get_event_loop().run_until_complete(svc.create_payout(
        payout_type="logistics", source_id="PO_LARGE_001",
        payee_name="物流公司B", payee_account="6222009876543210",
        payee_bank="CCB", amount=10000.0,
    ))


# ============================================================
#  1. 付款创建测试
# ============================================================

class TestCreatePayout:
    """付款单创建测试"""

    @pytest.mark.asyncio
    async def test_create_small_payout_auto_approved(self):
        """小额付款: 自动审核通过(< 5000)"""
        svc = PaymentService()
        result = await svc.create_payout(
            payout_type="supplier", source_id="PO_001",
            payee_name="供应商A", payee_account="6222001234567890",
            payee_bank="ICBC", amount=1000.0,
        )
        assert result["success"] is True
        assert result["payoutNo"].startswith("PO")
        assert result["amount"] == 1000.0
        assert result["status"] == PAYOUT_STATUS_APPROVED  # 自动审核
        assert result["statusName"] == "审核通过"

    @pytest.mark.asyncio
    async def test_create_large_payout_pending(self):
        """大额付款: 需手动审核(>= 5000)"""
        svc = PaymentService()
        result = await svc.create_payout(
            payout_type="logistics", source_id="PO_002",
            payee_name="物流B", payee_account="6222009876543210",
            payee_bank="CCB", amount=10000.0,
        )
        assert result["success"] is True
        assert result["status"] == PAYOUT_STATUS_PENDING  # 待审核

    @pytest.mark.asyncio
    async def test_create_payout_with_tax(self):
        """带代扣税费的付款"""
        svc = PaymentService()
        result = await svc.create_payout(
            payout_type="commission", source_id="PO_TAX",
            payee_name="代理C", payee_account="alipay@test.com",
            payee_bank="ALIPAY", amount=2000.0, tax_amount=200.0,
        )
        assert result["success"] is True
        assert result["taxAmount"] == 200.0
        assert result["actualAmount"] == 1800.0  # 2000 - 200

    @pytest.mark.asyncio
    async def test_create_payout_zero_amount(self):
        """金额为0: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="付款金额须 > 0"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_ZERO",
                payee_name="A", payee_account="123",
                payee_bank="ICBC", amount=0,
            )

    @pytest.mark.asyncio
    async def test_create_payout_negative_amount(self):
        """负数金额: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="付款金额须 > 0"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_NEG",
                payee_name="A", payee_account="123",
                payee_bank="ICBC", amount=-100.0,
            )

    @pytest.mark.asyncio
    async def test_create_payout_invalid_tax(self):
        """税费非法(> 金额): ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="代扣税费非法"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_TAX_BAD",
                payee_name="A", payee_account="123",
                payee_bank="ICBC", amount=100.0, tax_amount=200.0,
            )

    @pytest.mark.asyncio
    async def test_create_payout_missing_payee_name(self):
        """缺失收款人名称: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="收款人名称/账号不能为空"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_NO_NAME",
                payee_name="", payee_account="123",
                payee_bank="ICBC", amount=100.0,
            )

    @pytest.mark.asyncio
    async def test_create_payout_missing_payee_account(self):
        """缺失收款账号: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="收款人名称/账号不能为空"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_NO_ACCT",
                payee_name="A", payee_account="",
                payee_bank="ICBC", amount=100.0,
            )

    @pytest.mark.asyncio
    async def test_create_payout_invalid_channel(self):
        """付款渠道非法: ValueError"""
        svc = PaymentService()
        with pytest.raises(ValueError, match="付款渠道非法"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_BAD_CH",
                payee_name="A", payee_account="123",
                payee_bank="ICBC", amount=100.0,
                pay_channel="invalid_channel",
            )

    @pytest.mark.asyncio
    async def test_create_payout_duplicate_source(self):
        """重复创建(同来源): ValueError"""
        svc = PaymentService()
        await svc.create_payout(
            payout_type="supplier", source_id="PO_DUP",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        with pytest.raises(ValueError, match="已存在付款单"):
            await svc.create_payout(
                payout_type="supplier", source_id="PO_DUP",
                payee_name="B", payee_account="456",
                payee_bank="CCB", amount=200.0,
            )

    @pytest.mark.asyncio
    async def test_create_payout_different_channels(self):
        """不同付款渠道"""
        svc = PaymentService()
        for ch in ["bank_transfer", "alipay_transfer", "wechat_transfer"]:
            result = await svc.create_payout(
                payout_type="supplier", source_id=f"PO_CH_{ch}",
                payee_name="A", payee_account="123",
                payee_bank="BANK", amount=100.0,
                pay_channel=ch,
            )
            assert result["success"] is True


# ============================================================
#  2. 付款审核测试
# ============================================================

class TestAuditPayout:
    """付款审核测试"""

    @pytest.mark.asyncio
    async def test_audit_approve_success(self):
        """审核通过: pending → approved"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_AUDIT_001",
            payee_name="B", payee_account="123",
            payee_bank="CCB", amount=10000.0,  # 大额,需审核
        )
        result = await svc.audit_payout(
            payout_no=create["payoutNo"], decision="approved",
            auditor="admin", audit_remark="同意付款",
        )
        assert result["success"] is True
        assert result["status"] == PAYOUT_STATUS_APPROVED
        assert result["auditor"] == "admin"

    @pytest.mark.asyncio
    async def test_audit_reject_success(self):
        """审核拒绝: pending → rejected"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_AUDIT_002",
            payee_name="B", payee_account="123",
            payee_bank="CCB", amount=10000.0,
        )
        result = await svc.audit_payout(
            payout_no=create["payoutNo"], decision="rejected",
            auditor="admin", audit_remark="拒绝付款",
        )
        assert result["status"] == PAYOUT_STATUS_REJECTED

    @pytest.mark.asyncio
    async def test_audit_invalid_decision(self):
        """审核决定非法: ValueError"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_AUDIT_003",
            payee_name="B", payee_account="123",
            payee_bank="CCB", amount=10000.0,
        )
        with pytest.raises(ValueError, match="审核决定非法"):
            await svc.audit_payout(
                payout_no=create["payoutNo"], decision="invalid",
            )

    @pytest.mark.asyncio
    async def test_audit_payout_not_found(self):
        """付款单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.audit_payout(payout_no="NOT_EXIST", decision="approved")

    @pytest.mark.asyncio
    async def test_audit_payout_wrong_status(self):
        """审核状态非法(已审核): ValueError"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_AUDIT_004",
            payee_name="B", payee_account="123",
            payee_bank="CCB", amount=10000.0,
        )
        # 先审核一次
        await svc.audit_payout(create["payoutNo"], decision="approved")
        # 再次审核应失败
        with pytest.raises(ValueError, match="状态非法"):
            await svc.audit_payout(create["payoutNo"], decision="approved")


# ============================================================
#  3. 付款执行测试
# ============================================================

class TestExecutePayout:
    """付款执行测试"""

    @pytest.mark.asyncio
    async def test_execute_payout_success(self):
        """执行打款: approved → paying"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_EXE_001",
            payee_name="B", payee_account="123",
            payee_bank="CCB", amount=10000.0,
        )
        await svc.audit_payout(create["payoutNo"], decision="approved")
        result = await svc.execute_payout(create["payoutNo"])
        assert result["success"] is True
        assert result["status"] == PAYOUT_STATUS_PAYING

    @pytest.mark.asyncio
    async def test_execute_payout_not_found(self):
        """付款单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.execute_payout("NOT_EXIST")

    @pytest.mark.asyncio
    async def test_execute_payout_wrong_status(self):
        """执行状态非法(未审核): ValueError"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_EXE_002",
            payee_name="B", payee_account="123",
            payee_bank="CCB", amount=10000.0,
        )
        # 未审核直接执行
        with pytest.raises(ValueError, match="状态非法"):
            await svc.execute_payout(create["payoutNo"])

    @pytest.mark.asyncio
    async def test_execute_payout_already_paid(self):
        """已打款再次执行: ValueError"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_EXE_003",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,  # 小额自动审核
        )
        await svc.execute_payout(create["payoutNo"])
        await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_PAID_001",
            callback_content={"status": "SUCCESS"},
        )
        # 已 paid,再次执行应失败
        with pytest.raises(ValueError, match="状态非法"):
            await svc.execute_payout(create["payoutNo"])


# ============================================================
#  4. 付款回调测试
# ============================================================

class TestPayoutCallback:
    """付款回调测试"""

    @pytest.mark.asyncio
    async def test_payout_callback_success(self):
        """回调成功: paying → paid"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_CB_001",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.execute_payout(create["payoutNo"])
        result = await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_PAID_002",
            callback_content={"status": "SUCCESS"},
        )
        assert result["success"] is True
        assert result["idempotent"] is False
        assert result["status"] == PAYOUT_STATUS_PAID

    @pytest.mark.asyncio
    async def test_payout_callback_idempotent(self):
        """重复回调: 幂等返回"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_CB_002",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.execute_payout(create["payoutNo"])
        await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_PAID_003",
            callback_content={"status": "SUCCESS"},
        )
        # 第二次回调
        result = await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_PAID_003",
            callback_content={"status": "SUCCESS"},
        )
        assert result["success"] is True
        assert result["idempotent"] is True

    @pytest.mark.asyncio
    async def test_payout_callback_failure(self):
        """回调失败: paying → failed(retry_count +1)"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_CB_003",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.execute_payout(create["payoutNo"])
        result = await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_FAIL_001",
            callback_content={"status": "FAIL"},
            success=False, fail_reason="渠道超时",
        )
        assert result["success"] is False
        assert result["status"] == PAYOUT_STATUS_FAILED
        assert result["retryCount"] == 1
        assert "渠道超时" in result["reason"]

    @pytest.mark.asyncio
    async def test_payout_callback_max_retry_rejected(self):
        """失败重试达到上限: 自动 rejected"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_CB_004",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.execute_payout(create["payoutNo"])
        # 模拟 MAX_RETRY 次失败回调
        for i in range(MAX_RETRY):
            # 前 MAX_RETRY-1 次返回 failed,最后一次返回 rejected
            result = await svc.payout_callback(
                payout_no=create["payoutNo"],
                channel_payout_no=f"CH_FAIL_{i}",
                callback_content={"status": "FAIL"},
                success=False, fail_reason=f"失败{i}",
            )
            if i < MAX_RETRY - 1:
                assert result["status"] == PAYOUT_STATUS_FAILED
                # 重试(回到 paying)
                await svc.retry_payout(create["payoutNo"])
            else:
                # 最后一次达到上限,rejected
                assert result["status"] == PAYOUT_STATUS_REJECTED

    @pytest.mark.asyncio
    async def test_payout_callback_not_found(self):
        """付款单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.payout_callback(
                payout_no="NOT_EXIST",
                channel_payout_no="CH",
                callback_content={},
            )

    @pytest.mark.asyncio
    async def test_payout_callback_wrong_status(self):
        """回调状态非法(未执行): ValueError"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_CB_005",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        # 未执行直接回调
        with pytest.raises(ValueError, match="状态非法"):
            await svc.payout_callback(
                payout_no=create["payoutNo"],
                channel_payout_no="CH",
                callback_content={},
            )


# ============================================================
#  5. 付款重试测试
# ============================================================

class TestRetryPayout:
    """付款重试测试"""

    @pytest.mark.asyncio
    async def test_retry_payout_success(self):
        """重试打款: failed → paying"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_RT_001",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.execute_payout(create["payoutNo"])
        # 失败一次
        await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_FAIL",
            callback_content={"status": "FAIL"},
            success=False, fail_reason="超时",
        )
        # 重试
        result = await svc.retry_payout(create["payoutNo"])
        assert result["success"] is True
        assert result["status"] == PAYOUT_STATUS_PAYING

    @pytest.mark.asyncio
    async def test_retry_payout_not_found(self):
        """付款单不存在: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.retry_payout("NOT_EXIST")

    @pytest.mark.asyncio
    async def test_retry_payout_wrong_status(self):
        """重试状态非法(未失败): ValueError"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_RT_002",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        # 未失败直接重试
        with pytest.raises(ValueError, match="状态非法"):
            await svc.retry_payout(create["payoutNo"])


# ============================================================
#  6. 付款查询测试
# ============================================================

class TestQueryPayout:
    """付款查询测试"""

    @pytest.mark.asyncio
    async def test_get_payout_success(self):
        """查询付款详情"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_GET_001",
            payee_name="A", payee_account="6222001234567890",
            payee_bank="ICBC", amount=1000.0,
        )
        result = await svc.get_payout(create["payoutNo"])
        assert result["success"] is True
        assert result["payoutNo"] == create["payoutNo"]
        assert result["payeeAccountMasked"]  # 账号已脱敏
        assert "****" in result["payeeAccountMasked"]

    @pytest.mark.asyncio
    async def test_get_payout_not_found(self):
        """查询不存在的付款单: KeyError"""
        svc = PaymentService()
        with pytest.raises(KeyError):
            await svc.get_payout("NOT_EXIST")

    @pytest.mark.asyncio
    async def test_list_payouts_all(self):
        """查询全部付款列表"""
        svc = PaymentService()
        await svc.create_payout(
            payout_type="supplier", source_id="PO_L1",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.create_payout(
            payout_type="logistics", source_id="PO_L2",
            payee_name="B", payee_account="456",
            payee_bank="CCB", amount=200.0,
        )
        result = await svc.list_payouts()
        assert result["success"] is True
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_list_payouts_filter_by_type(self):
        """按类型过滤付款列表"""
        svc = PaymentService()
        await svc.create_payout(
            payout_type="supplier", source_id="PO_T1",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.create_payout(
            payout_type="logistics", source_id="PO_T2",
            payee_name="B", payee_account="456",
            payee_bank="CCB", amount=200.0,
        )
        result = await svc.list_payouts(payout_type="supplier")
        assert result["count"] == 1
        assert result["items"][0]["payoutType"] == "supplier"

    @pytest.mark.asyncio
    async def test_list_payouts_filter_by_status(self):
        """按状态过滤付款列表"""
        svc = PaymentService()
        # 小额自动审核(approved)
        await svc.create_payout(
            payout_type="supplier", source_id="PO_S1",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        # 大额待审核(pending)
        await svc.create_payout(
            payout_type="logistics", source_id="PO_S2",
            payee_name="B", payee_account="456",
            payee_bank="CCB", amount=10000.0,
        )
        result = await svc.list_payouts(status=PAYOUT_STATUS_APPROVED)
        assert all(p["status"] == PAYOUT_STATUS_APPROVED for p in result["items"])

    @pytest.mark.asyncio
    async def test_list_pending_payouts(self):
        """查询待审核付款列表"""
        svc = PaymentService()
        # 大额待审核
        await svc.create_payout(
            payout_type="logistics", source_id="PO_PEND",
            payee_name="B", payee_account="456",
            payee_bank="CCB", amount=10000.0,
        )
        result = await svc.list_pending_payouts()
        assert result["success"] is True
        assert result["count"] >= 1


# ============================================================
#  7. PaymentRepository 付款仓储层测试
# ============================================================

class TestPayoutRepository:
    """付款仓储层直接调用测试"""

    @pytest.mark.asyncio
    async def test_save_and_get_payout(self):
        """保存并获取付款单"""
        repo = PaymentRepository()
        payout = {
            "payoutNo": "PO_REPO_001",
            "payoutType": "supplier",
            "sourceId": "SRC_001",
            "payeeName": "供应商A",
            "payeeAccount": "6222001234567890",
            "amount": 1000.0,
            "actualAmount": 1000.0,
            "status": PAYOUT_STATUS_PENDING,
            "retryCount": 0,
        }
        await repo.save_payout(payout)
        result = await repo.get_payout("PO_REPO_001")
        assert result is not None
        assert result["payoutNo"] == "PO_REPO_001"
        assert result["status"] == PAYOUT_STATUS_PENDING

    @pytest.mark.asyncio
    async def test_get_payout_not_found(self):
        """获取不存在的付款单"""
        repo = PaymentRepository()
        result = await repo.get_payout("NOT_EXIST")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_by_source(self):
        """按来源查重"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_FIND_001",
            "payoutType": "supplier",
            "sourceId": "SRC_FIND_001",
            "status": PAYOUT_STATUS_PENDING,
            "retryCount": 0,
        })
        # 查重应返回已有记录
        result = await repo.find_by_source("SRC_FIND_001", "supplier")
        assert result is not None
        assert result["payoutNo"] == "PO_FIND_001"
        # 不同来源应返回 None
        result2 = await repo.find_by_source("NOT_EXIST", "supplier")
        assert result2 is None

    @pytest.mark.asyncio
    async def test_find_by_source_different_type(self):
        """同来源不同类型: 不算重复"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_FIND_002",
            "payoutType": "supplier",
            "sourceId": "SRC_DUP",
            "status": PAYOUT_STATUS_PENDING,
            "retryCount": 0,
        })
        # 同来源不同类型,不算重复
        result = await repo.find_by_source("SRC_DUP", "logistics")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_payout_fields(self):
        """更新付款单字段"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_UPD",
            "status": PAYOUT_STATUS_PENDING,
            "retryCount": 0,
        })
        await repo.update_payout_fields("PO_UPD", {
            "status": PAYOUT_STATUS_APPROVED,
            "auditor": "admin",
        })
        result = await repo.get_payout("PO_UPD")
        assert result["status"] == PAYOUT_STATUS_APPROVED
        assert result["auditor"] == "admin"

    @pytest.mark.asyncio
    async def test_increment_payout_retry(self):
        """重试次数累加"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_RETRY",
            "status": PAYOUT_STATUS_FAILED,
            "retryCount": 0,
        })
        count1 = await repo.increment_payout_retry("PO_RETRY")
        assert count1 == 1
        count2 = await repo.increment_payout_retry("PO_RETRY")
        assert count2 == 2

    @pytest.mark.asyncio
    async def test_list_payouts_by_type(self):
        """按类型查询付款列表"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_LT1", "payoutType": "supplier",
            "sourceId": "S1", "status": PAYOUT_STATUS_PENDING, "retryCount": 0,
        })
        await repo.save_payout({
            "payoutNo": "PO_LT2", "payoutType": "logistics",
            "sourceId": "S2", "status": PAYOUT_STATUS_PAID, "retryCount": 0,
        })
        result = await repo.list_payouts(payout_type="supplier")
        assert len(result) == 1
        assert result[0]["payoutType"] == "supplier"

    @pytest.mark.asyncio
    async def test_list_payouts_by_status(self):
        """按状态查询付款列表"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_LS1", "payoutType": "supplier",
            "sourceId": "S1", "status": PAYOUT_STATUS_PENDING, "retryCount": 0,
        })
        await repo.save_payout({
            "payoutNo": "PO_LS2", "payoutType": "supplier",
            "sourceId": "S2", "status": PAYOUT_STATUS_PAID, "retryCount": 0,
        })
        result = await repo.list_payouts(status=PAYOUT_STATUS_PAID)
        assert all(p["status"] == PAYOUT_STATUS_PAID for p in result)

    @pytest.mark.asyncio
    async def test_list_pending_payouts(self):
        """查询待审核付款列表"""
        repo = PaymentRepository()
        await repo.save_payout({
            "payoutNo": "PO_LP1", "payoutType": "supplier",
            "sourceId": "S1", "status": PAYOUT_STATUS_PENDING, "retryCount": 0,
        })
        await repo.save_payout({
            "payoutNo": "PO_LP2", "payoutType": "supplier",
            "sourceId": "S2", "status": PAYOUT_STATUS_PAID, "retryCount": 0,
        })
        result = await repo.list_pending_payouts()
        statuses = [p["status"] for p in result]
        assert PAYOUT_STATUS_PAID not in statuses


# ============================================================
#  8. 完整付款流程端到端测试
# ============================================================

class TestPayoutFlowE2E:
    """付款完整流程端到端测试"""

    @pytest.mark.asyncio
    async def test_full_payout_flow_success(self):
        """完整流程: 创建→审核→执行→回调成功"""
        svc = PaymentService()
        # 1. 创建大额付款(需审核)
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_E2E_001",
            payee_name="供应商A", payee_account="6222001234567890",
            payee_bank="ICBC", amount=8000.0, tax_amount=800.0,
        )
        assert create["status"] == PAYOUT_STATUS_PENDING

        # 2. 审核通过
        audit = await svc.audit_payout(create["payoutNo"], decision="approved")
        assert audit["status"] == PAYOUT_STATUS_APPROVED

        # 3. 执行打款
        exe = await svc.execute_payout(create["payoutNo"])
        assert exe["status"] == PAYOUT_STATUS_PAYING

        # 4. 回调成功
        cb = await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_E2E_001",
            callback_content={"status": "SUCCESS"},
        )
        assert cb["status"] == PAYOUT_STATUS_PAID
        assert cb["idempotent"] is False

        # 5. 查询验证
        detail = await svc.get_payout(create["payoutNo"])
        assert detail["status"] == PAYOUT_STATUS_PAID
        assert detail["actualAmount"] == 7200.0  # 8000 - 800

    @pytest.mark.asyncio
    async def test_full_payout_flow_with_retry(self):
        """完整流程(含重试): 创建→执行→失败→重试→成功"""
        svc = PaymentService()
        # 小额自动审核
        create = await svc.create_payout(
            payout_type="commission", source_id="PO_E2E_002",
            payee_name="代理C", payee_account="alipay@test.com",
            payee_bank="ALIPAY", amount=500.0,
        )
        # 执行
        await svc.execute_payout(create["payoutNo"])
        # 失败一次
        fail = await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_FAIL_E2E",
            callback_content={"status": "FAIL"},
            success=False, fail_reason="网络超时",
        )
        assert fail["status"] == PAYOUT_STATUS_FAILED
        assert fail["retryCount"] == 1
        # 重试
        retry = await svc.retry_payout(create["payoutNo"])
        assert retry["status"] == PAYOUT_STATUS_PAYING
        # 回调成功
        cb = await svc.payout_callback(
            payout_no=create["payoutNo"],
            channel_payout_no="CH_SUCCESS_E2E",
            callback_content={"status": "SUCCESS"},
        )
        assert cb["status"] == PAYOUT_STATUS_PAID

    @pytest.mark.asyncio
    async def test_full_payout_flow_rejected(self):
        """完整流程(审核拒绝): 创建→审核拒绝(终态)"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="logistics", source_id="PO_E2E_003",
            payee_name="物流B", payee_account="6222009876543210",
            payee_bank="CCB", amount=15000.0,
        )
        # 审核拒绝
        result = await svc.audit_payout(create["payoutNo"], decision="rejected")
        assert result["status"] == PAYOUT_STATUS_REJECTED
        # 验证不能继续执行
        with pytest.raises(ValueError, match="状态非法"):
            await svc.execute_payout(create["payoutNo"])

    @pytest.mark.asyncio
    async def test_full_payout_flow_max_retry_rejected(self):
        """完整流程(重试上限): 创建→执行→失败N次→自动拒绝"""
        svc = PaymentService()
        create = await svc.create_payout(
            payout_type="supplier", source_id="PO_E2E_004",
            payee_name="A", payee_account="123",
            payee_bank="ICBC", amount=100.0,
        )
        await svc.execute_payout(create["payoutNo"])
        # 失败 MAX_RETRY 次
        for i in range(MAX_RETRY):
            result = await svc.payout_callback(
                payout_no=create["payoutNo"],
                channel_payout_no=f"CH_FAIL_{i}",
                callback_content={"status": "FAIL"},
                success=False, fail_reason=f"失败{i}",
            )
            if result["status"] == PAYOUT_STATUS_REJECTED:
                # 达到上限自动拒绝
                break
            # 重试
            await svc.retry_payout(create["payoutNo"])
        # 最终应为 rejected
        detail = await svc.get_payout(create["payoutNo"])
        assert detail["status"] == PAYOUT_STATUS_REJECTED
