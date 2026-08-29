"""AI智能管理模块(角色经济中枢)端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 RoleService 等方法, 模拟 18 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_role_routes.py

覆盖:
    1. 角色目录:   种子加载/维护
    2. 认领:       AI预审(信用门槛)/重复认领/审批
    3. 契约:       签约(试用期)/转正/冻结/退出/重复签署拒绝
    4. 派单:       无候选fallback/最优客服分配/幂等
    5. 服务分润:   D-8公式(1%×断崖满意度×信用×时效×试用期)/单笔封顶/
                   月度封顶/≤3星零分润/幂等/钱包入账与重试
    6. 信用总线:   事件入账→竹信分联动
    7. chat联动:   转人工自动建source=ai工单+真实派单(修复断链)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.role_service import RoleService
from services.ticket_service import TicketService
from services.credit_service import CreditService
from services.wallet_service import WalletService
from services.chat_service import ChatService
from repositories.role_repository import (
    RoleRepository,
    ROLE_CUSTOMER_SERVICE, ROLE_AGENT, ROLE_PARTNER, ROLE_PROMOTER,
    ROLE_PRODUCTION_WORKER, ROLE_PLATFORM,
    CONTRACT_STATUS_PROBATION, CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_SUSPENDED, CONTRACT_STATUS_TERMINATED,
    CLAIM_STATUS_PENDING, CLAIM_STATUS_APPROVED, CLAIM_STATUS_REJECTED,
    LEDGER_STATUS_PENDING, LEDGER_STATUS_SETTLED, LEDGER_STATUS_REVERSED,
    SINGLE_CAP, MONTHLY_CAP, SERVICE_PROFIT_RATE,
    CLAWBACK_FREEZE_THRESHOLD, STAGE_PROFIT_RATES,
    WORKER_LEDGER_PREFIX,
)
from repositories.ticket_repository import (
    SOURCE_AI, TICKET_STATUS_PROCESSING, TICKET_STATUS_RESOLVED,
)
from repositories.chat_repository import (
    SESSION_STATUS_HUMAN, SENDER_USER, MESSAGE_TYPE_TEXT,
)
from repositories.member_repository import MemberRepository

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    from repositories.store import reset_store as _reset
    _reset()


# 测试数据
MEMBER_ID = 1001        # 普通会员(下单用户)
CS_USER_ID = 2001       # 认领人工客服的用户
CS_USER_ID_2 = 2002     # 第二客服(派单对比)


async def _setup_member(user_id: int, growth: int = 600) -> None:
    """创建会员并设置成长值(钱包开通前置条件)"""
    repo = MemberRepository()
    member = await repo.create({
        "id": user_id, "nickname": f"测试用户{user_id}",
        "phone": f"1380000{user_id:04d}", "password": "test123456",
    })
    await repo.save(user_id, {**member, "growth_value": growth})


async def _make_customer_service(svc: RoleService, user_id: int,
                                 activate: bool = False) -> dict:
    """全流程造一个持契约的客服(认领→审批→签约)"""
    claim = await svc.create_claim(user_id, ROLE_CUSTOMER_SERVICE,
                                   statement="测试客服认领")
    await svc.admin_review_claim(claim["id"], approved=True)
    contract = await svc.sign_contract(claim["id"], user_id)
    if activate:
        await svc.admin_contract_action(contract["id"], "activate")
    return contract


async def _resolved_ticket(ticket_svc: TicketService, user_id: int,
                           handler_id: int, order_id: str = "",
                           satisfaction: int = 5) -> str:
    """创建→分配→解决→确认 的工单, 返回工单号"""
    ticket = await ticket_svc.create_ticket(
        user_id=user_id, ticket_type="presale", priority="medium",
        description="测试工单", order_id=order_id)
    ticket_no = ticket["ticketNo"]
    await ticket_svc.assign_ticket(ticket_no, handler_id, "客服")
    await ticket_svc.resolve_ticket(ticket_no, handler_id, "已解决")
    await ticket_svc.confirm_ticket(ticket_no, user_id, satisfaction)
    return ticket_no


# ============================================================
# 1. 角色目录
# ============================================================

class TestCatalog:

    async def run(self):
        reset_store()
        svc = RoleService()

        catalog = await svc.list_catalog()
        record("目录-种子角色加载(7个)",
               len(catalog) == 7, f"实际{len(catalog)}")

        cs = next((r for r in catalog
                   if r["roleCode"] == ROLE_CUSTOMER_SERVICE), None)
        record("目录-人工客服角色存在(含分润说明/责任条款)",
               cs is not None and cs.get("profitDesc") and cs.get("dutyTerms"))

        created = await svc.upsert_catalog_role(
            role_code="test_role", role_name="测试角色", quota=10)
        catalog2 = await svc.list_catalog()
        record("目录-新增角色(共8个)",
               len(catalog2) == 8 and created["roleCode"] == "test_role")


# ============================================================
# 2. 角色认领(AI预审)
# ============================================================

class TestClaim:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()

        # 新会员竹信分350(member起始) < 客服门槛400 → AI预审拒绝
        claim_low = await svc.create_claim(CS_USER_ID, ROLE_CUSTOMER_SERVICE)
        record("认领-AI预审拒绝(信用350<门槛400)",
               claim_low["status"] == CLAIM_STATUS_REJECTED
               and claim_low["aiPrecheck"]["passed"] is False)

        # 调分至750(L4)后重新认领(另一用户) → pending
        await credit.adjust_score(CS_USER_ID_2, 400, reason="测试调分",
                                  operator="admin")
        claim_ok = await svc.create_claim(CS_USER_ID_2, ROLE_CUSTOMER_SERVICE)
        record("认领-AI预审通过(信用750≥门槛400)",
               claim_ok["status"] == CLAIM_STATUS_PENDING
               and claim_ok["aiPrecheck"]["passed"] is True)

        # 重复认领拒绝
        try:
            await svc.create_claim(CS_USER_ID_2, ROLE_CUSTOMER_SERVICE)
            record("认领-重复认领拒绝", False, "未抛出异常")
        except ValueError:
            record("认领-重复认领拒绝", True)

        # 审批
        approved = await svc.admin_review_claim(claim_ok["id"], approved=True)
        record("认领-管理员审批通过",
               approved["status"] == CLAIM_STATUS_APPROVED)

        # 已审批后再审批 → 409
        try:
            await svc.admin_review_claim(claim_ok["id"], approved=False)
            record("认领-重复审批拒绝", False, "未抛出异常")
        except ValueError:
            record("认领-重复审批拒绝", True)

        # 角色不存在 → 404
        try:
            await svc.create_claim(CS_USER_ID, "no_such_role")
            record("认领-角色不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("认领-角色不存在拒绝", True)


# ============================================================
# 3. 契约(权责利三合一)
# ============================================================

class TestContract:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")
        contract = await _make_customer_service(svc, CS_USER_ID)

        record("契约-签署进入试用期(30天)",
               contract["status"] == CONTRACT_STATUS_PROBATION
               and contract["probationEndsAt"] > contract["signedAt"])
        record("契约-三合一条款快照(责任+分润+版本)",
               bool(contract["dutyTerms"]) and bool(contract["profitTerms"])
               and contract["profitTemplateVersion"] == "v1.1")

        # 非本人签署拒绝
        await credit.adjust_score(CS_USER_ID_2, 50, reason="测试调分",
                                  operator="admin")  # 400 → 可过预审
        claim_other = await svc.create_claim(CS_USER_ID_2,
                                             ROLE_CUSTOMER_SERVICE)
        await svc.admin_review_claim(claim_other["id"], approved=True)
        try:
            await svc.sign_contract(claim_other["id"], CS_USER_ID)
            record("契约-非本人签署拒绝", False, "未抛出异常")
        except ValueError:
            record("契约-非本人签署拒绝", True)

        # 管理端动作: 转正/冻结/清退
        activated = await svc.admin_contract_action(contract["id"], "activate")
        record("契约-转正", activated["status"] == CONTRACT_STATUS_ACTIVE)
        suspended = await svc.admin_contract_action(contract["id"], "suspend")
        record("契约-冻结", suspended["status"] == CONTRACT_STATUS_SUSPENDED)
        terminated = await svc.admin_contract_action(contract["id"], "terminate")
        record("契约-清退", terminated["status"] == CONTRACT_STATUS_TERMINATED)

        # 冻结契约不可接单(派单候选排除)
        claims = await svc.list_contracts(role_code=ROLE_CUSTOMER_SERVICE)
        record("契约-列表动态标记(effectiveStatus)",
               all("effectiveStatus" in c for c in claims))

        # 用户主动退出
        contract2 = await _make_customer_service(svc, CS_USER_ID_2)
        quit_result = await svc.terminate_contract(contract2["id"], CS_USER_ID_2)
        record("契约-用户主动退出",
               quit_result["status"] == CONTRACT_STATUS_TERMINATED
               and quit_result.get("terminateBy") == "self")


# ============================================================
# 4. AI服务调度中枢(派单)
# ============================================================

class TestDispatch:

    async def run(self):
        # 场景1: 无客服契约 → fallback(工单仍创建, 留待分配池)
        reset_store()
        svc = RoleService()
        session = {"sessionId": "S001", "userId": MEMBER_ID}
        dispatch = await svc.dispatch_customer_service(session, reason="测试")
        record("派单-无候选fallback(工单已建)",
               dispatch["fallback"] is True and dispatch["assigneeId"] is None
               and dispatch["ticketNo"].startswith("GD"))

        ticket_svc = TicketService()
        ticket = await ticket_svc.repo.get_ticket(dispatch["ticketNo"])
        record("派单-自动创建source=ai工单(修复chat-ticket断链)",
               ticket is not None and ticket["source"] == SOURCE_AI)

        # 幂等: 同会话重复派单不重复建单
        dispatch2 = await svc.dispatch_customer_service(session, reason="再次")
        record("派单-同会话幂等(不重复建单)",
               dispatch2["ticketNo"] == dispatch["ticketNo"])

        # 场景2: 有客服契约 → 最优分配
        reset_store()
        svc = RoleService()
        credit = CreditService()
        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")  # 750 → 可过预审
        await _make_customer_service(svc, CS_USER_ID)

        session2 = {"sessionId": "S002", "userId": MEMBER_ID}
        dispatch3 = await svc.dispatch_customer_service(session2, reason="转人工")
        record("派单-最优客服分配(有候选)",
               dispatch3["assigneeId"] == CS_USER_ID
               and dispatch3["fallback"] is False)

        detail = dispatch3["scoreDetail"]
        record("派单-四维打分(信用/技能/负载/满意度)",
               detail is not None and all(k in detail for k in (
                   "creditScore", "skillScore", "loadScore",
                   "satisfactionScore", "total")))

        ticket = await ticket_svc.repo.get_ticket(dispatch3["ticketNo"])
        record("派单-工单已分配(处理中)",
               ticket["status"] == TICKET_STATUS_PROCESSING
               and ticket["handlerId"] == CS_USER_ID)

        # 冻结契约后不再派单
        contracts = await svc.list_contracts(
            role_code=ROLE_CUSTOMER_SERVICE, status=CONTRACT_STATUS_PROBATION)
        await svc.admin_contract_action(contracts[0]["id"], "suspend")
        session3 = {"sessionId": "S003", "userId": MEMBER_ID}
        dispatch4 = await svc.dispatch_customer_service(session3, reason="再转")
        record("派单-冻结契约剔除候选(fallback)",
               dispatch4["fallback"] is True)


# ============================================================
# 5. 服务分润结算(D-8)
# ============================================================

class TestServiceProfit:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        wallet = WalletService()
        ticket_svc = TicketService()
        role_repo = RoleRepository()

        await _setup_member(CS_USER_ID, growth=600)
        await _setup_member(MEMBER_ID, growth=600)
        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")  # 750 → L4(1.15)
        contract = await _make_customer_service(svc, CS_USER_ID)

        # 5.1 试用期+5星+SLA内+关联订单(显式金额)
        ticket_no = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD001",
            satisfaction=5)
        ledger = await svc.settle_service_profit(ticket_no,
                                                 order_amount=1200)
        # 期望: 1200×1%×2.0×1.15×1.2×0.5(试用) = 16.56
        record("分润-试用期公式(1200×1%×2.0×1.15×1.2×0.5=16.56)",
               abs(ledger["amount"] - 16.56) < 0.01,
               f"实际{ledger['amount']}")
        record("分润-双轨口径标记(sale_price)",
               ledger["basis"] == "sale_price")
        record("分润-系数明细留存",
               ledger["coefficients"]["creditLevel"] == "L4"
               and ledger["coefficients"]["probation"] is True)

        # 未开通钱包 → pending + 可重试
        record("分润-钱包未开通挂pending",
               ledger["status"] == LEDGER_STATUS_PENDING
               and ledger.get("walletError"))

        # 5.2 幂等
        try:
            await svc.settle_service_profit(ticket_no, order_amount=1200)
            record("分润-重复结算拒绝(幂等)", False, "未抛出异常")
        except ValueError:
            record("分润-重复结算拒绝(幂等)", True)

        # 5.3 转正后: 单笔封顶
        await svc.admin_contract_action(contract["id"], "activate")
        ticket_no2 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD002",
            satisfaction=5)
        ledger2 = await svc.settle_service_profit(ticket_no2,
                                                  order_amount=1000000)
        # 满系数理论: 100万×1%×2.0×1.15×1.2=27600 → 封顶50
        record("分润-单笔封顶¥50(理论27600→50)",
               abs(ledger2["amount"] - SINGLE_CAP) < 0.01,
               f"实际{ledger2['amount']}")

        # 5.4 差评≤3星: 零分润+信用扣减
        score_before = (await credit.get_score(CS_USER_ID))["bambooScore"]
        ticket_no3 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD003",
            satisfaction=2)
        ledger3 = await svc.settle_service_profit(ticket_no3,
                                                  order_amount=1200)
        score_after = (await credit.get_score(CS_USER_ID))["bambooScore"]
        record("分润-差评零分润(2星)",
               ledger3["amount"] == 0
               and ledger3["coefficients"]["satCoeff"] == 0)
        record("分润-差评信用扣减(-5)",
               score_after - score_before == -5,
               f"{score_before}→{score_after}")

        # 5.5 好评信用加分
        score_before = (await credit.get_score(CS_USER_ID))["bambooScore"]
        ticket_no4 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD004",
            satisfaction=4)
        ledger4 = await svc.settle_service_profit(ticket_no4,
                                                  order_amount=800)
        score_after = (await credit.get_score(CS_USER_ID))["bambooScore"]
        # 4星: 800×1%×1.2×1.15×1.2=13.25
        record("分润-4星系数1.2(800×1%×1.2×1.15×1.2=13.25)",
               abs(ledger4["amount"] - 13.25) < 0.01,
               f"实际{ledger4['amount']}")
        record("分润-好评信用加分(+5)",
               score_after - score_before == 5,
               f"{score_before}→{score_after}")

        # 5.6 未关联订单且无金额 → 拒绝
        ticket_no5 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="", satisfaction=5)
        try:
            await svc.settle_service_profit(ticket_no5)
            record("分润-缺订单金额拒绝", False, "未抛出异常")
        except ValueError:
            record("分润-缺订单金额拒绝", True)

        # 5.7 月度封顶: 手工注入已结算2980 → 本笔截断至20
        await role_repo.create_ledger({
            "ledgerNo": "SVC-MOCK-MONTH", "ticketNo": "GDMOCK",
            "orderId": "", "roleCode": ROLE_CUSTOMER_SERVICE,
            "userId": CS_USER_ID, "basis": "sale_price", "base": 0,
            "rate": SERVICE_PROFIT_RATE, "coefficients": {},
            "rawAmount": 2980, "amount": 2980, "capped": False,
            "status": LEDGER_STATUS_SETTLED, "sourceModule": "test",
            "walletTxNo": "", "walletError": "", "createdAt": _now_ts(),
            "settledAt": _now_ts(),
        })
        ticket_no6 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD006",
            satisfaction=5)
        ledger6 = await svc.settle_service_profit(ticket_no6,
                                                   order_amount=1200)
        record("分润-月度封顶(已用2980/3000 → 截断至20)",
               abs(ledger6["amount"] - 20) < 0.01 and ledger6["capped"] is True,
               f"实际{ledger6['amount']}")

        # 5.8 钱包入账与重试
        await wallet.open(CS_USER_ID)
        retried = await svc.retry_ledger_settlement(ledger["ledgerNo"])
        record("分润-重试入账(开通钱包后settled)",
               retried["status"] == LEDGER_STATUS_SETTLED
               and retried.get("walletTxNo"))

        reward = await wallet.get_reward_balance(CS_USER_ID)
        record("分润-钱包奖励余额到账(16.56)",
               abs(reward.get("rewardBalance", 0) - 16.56) < 0.01,
               f"实际{reward.get('rewardBalance')}")

        # 收益查询
        earnings = await svc.list_my_earnings(CS_USER_ID)
        record("分润-我的收益流水(含系数明细)",
               len(earnings) >= 4
               and all("coefficients" in e for e in earnings))

        # 风控汇总
        summary = await svc.get_risk_summary()
        record("分润-AI风控汇总(总账/契约/口径分布)",
               summary["ledgerTotal"] >= 4
               and "sale_price" in summary["basisSplit"])


def _now_ts() -> str:
    from core.helpers import ts
    return ts()


# ============================================================
# 6. 信用行为总线
# ============================================================

class TestCreditBus:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()

        before = (await credit.get_score(CS_USER_ID))["bambooScore"]
        event = await svc.publish_credit_event(
            CS_USER_ID, ROLE_CUSTOMER_SERVICE, "cs_satisfaction_good",
            "role", "GD-TEST-1")
        after = (await credit.get_score(CS_USER_ID))["bambooScore"]
        record("信用总线-事件入账并联动竹信分(+5)",
               event["delta"] == 5 and after - before == 5,
               f"{before}→{after}")

        events = await svc.list_my_credit_events(CS_USER_ID)
        record("信用总线-我的信用事件记录",
               len(events) == 1 and events[0]["behaviorCode"]
               == "cs_satisfaction_good")

        # 未定义行为码拒绝
        try:
            await svc.publish_credit_event(
                CS_USER_ID, ROLE_CUSTOMER_SERVICE, "no_such_behavior", "role")
            record("信用总线-未定义行为码拒绝", False, "未抛出异常")
        except ValueError:
            record("信用总线-未定义行为码拒绝", True)


# ============================================================
# 7. chat 转人工联动(闭环验证)
# ============================================================

class TestChatIntegration:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        chat = ChatService()
        ticket_svc = TicketService()

        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")
        await _make_customer_service(svc, CS_USER_ID)

        # 用户会话发送"转人工" → 触发 _do_transfer → 调度中枢
        session = await chat.create_session(user_id=MEMBER_ID)
        session_id = session["sessionId"]
        await chat.send_message(session_id, SENDER_USER, MEMBER_ID,
                                 MESSAGE_TYPE_TEXT, "转人工")

        updated = await chat.get_session(session_id)
        record("chat联动-会话转人工且真实派单(非固定ID=1)",
               updated["status"] == SESSION_STATUS_HUMAN
               and updated["customerServiceId"] == CS_USER_ID
               and updated.get("ticketNo", "") != "")

        ticket = await ticket_svc.repo.get_ticket(updated["ticketNo"])
        record("chat联动-source=ai工单自动创建并分配",
               ticket["source"] == SOURCE_AI
               and ticket["handlerId"] == CS_USER_ID
               and ticket["status"] == TICKET_STATUS_PROCESSING)

        # 主动转人工接口返回实际客服与工单号
        session2 = await chat.create_session(user_id=MEMBER_ID)
        result = await chat.transfer_to_human(session2["sessionId"],
                                              reason="测试主动转接")
        record("chat联动-主动转人工返回派单结果(客服+工单号)",
               result["customerServiceId"] == CS_USER_ID
               and result.get("ticketNo", "") != "")


# ============================================================
# 8. 退款追回负账(D-8 细化)
# ============================================================

class TestClawback:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        wallet = WalletService()
        ticket_svc = TicketService()
        role_repo = RoleRepository()

        await _setup_member(CS_USER_ID, growth=600)
        await _setup_member(MEMBER_ID, growth=600)
        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")
        contract = await _make_customer_service(svc, CS_USER_ID)
        await svc.admin_contract_action(contract["id"], "activate")
        await wallet.open(CS_USER_ID)

        # 8.1 同人自导拒绝(防套利)
        same_person_ticket = await _resolved_ticket(
            ticket_svc, CS_USER_ID, CS_USER_ID, order_id="ORD-SAME",
            satisfaction=5)
        try:
            await svc.settle_service_profit(same_person_ticket,
                                            order_amount=1200)
            record("追回-同人自导拒绝结算", False, "未抛出异常")
        except ValueError as e:
            record("追回-同人自导拒绝结算", "同人" in str(e))

        # 8.2 正常结算后追回 → reversed + 负账
        ticket_no = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-CB1",
            satisfaction=5)
        ledger = await svc.settle_service_profit(ticket_no, order_amount=1200)
        # 转正后: 1200×1%×2.0×1.15×1.2=33.12
        record("追回-前置结算(转正33.12)",
               abs(ledger["amount"] - 33.12) < 0.01,
               f"实际{ledger['amount']}")

        reversed_ledger = await svc.reverse_service_profit(
            ticket_no, reason="测试退款")
        record("追回-流水置reversed",
               reversed_ledger["status"] == LEDGER_STATUS_REVERSED
               and reversed_ledger.get("reverseReason") == "测试退款")

        clawback = await svc.get_clawback(CS_USER_ID)
        record("追回-负账入账(payable=33.12)",
               abs(clawback["clawbackBalance"] - 33.12) < 0.01,
               f"实际{clawback['clawbackBalance']}")

        # 8.3 重复追回拒绝(幂等)
        try:
            await svc.reverse_service_profit(ticket_no)
            record("追回-重复追回拒绝", False, "未抛出异常")
        except ValueError:
            record("追回-重复追回拒绝", True)

        # 8.4 负账抵扣: 下次结算先抵扣
        ticket_no2 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-CB2",
            satisfaction=5)
        ledger2 = await svc.settle_service_profit(ticket_no2, order_amount=1200)
        # 应得33.12, 抵扣负账33.12 → payable=0
        record("追回-负账抵扣(应得33.12全额抵扣)",
               abs(ledger2["amount"] - 33.12) < 0.01
               and abs(ledger2["clawbackDeducted"] - 33.12) < 0.01
               and ledger2["payable"] == 0,
               f"amount={ledger2['amount']}, "
               f"deducted={ledger2['clawbackDeducted']}, "
               f"payable={ledger2['payable']}")

        clawback2 = await svc.get_clawback(CS_USER_ID)
        record("追回-负账清零", clawback2["clawbackBalance"] == 0)

        # 8.5 部分抵扣(负账小于应得)
        ticket_no3 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-CB3",
            satisfaction=4)
        await svc.settle_service_profit(ticket_no3, order_amount=800)
        # 4星: 800×1%×1.2×1.15×1.2=13.25
        await svc.reverse_service_profit(ticket_no3, reason="部分抵扣测试")
        ticket_no4 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-CB4",
            satisfaction=5)
        ledger4 = await svc.settle_service_profit(ticket_no4, order_amount=1200)
        # 应得33.12, 抵扣13.25 → payable=19.87
        record("追回-部分抵扣(33.12抵13.25→付19.87)",
               abs(ledger4["payable"] - 19.87) < 0.01,
               f"实际{ledger4['payable']}")

        # 8.6 负账超阈冻结派单
        await role_repo.adjust_clawback(
            CS_USER_ID, ROLE_CUSTOMER_SERVICE, 600)  # 负账613.25 > 500
        session = {"sessionId": "S-CB", "userId": MEMBER_ID}
        dispatch = await svc.dispatch_customer_service(session, reason="测试")
        record("追回-负账超¥500冻结接单(派单fallback)",
               dispatch["fallback"] is True)

        # 8.7 追回不存在流水 → 404
        try:
            await svc.reverse_service_profit("GD-NOT-EXIST")
            record("追回-流水不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("追回-流水不存在拒绝", True)


# ============================================================
# 9. 月度封顶溢出记账(D-8 细化: 滚入季度考核奖励池)
# ============================================================

class TestDeferredPool:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        wallet = WalletService()
        ticket_svc = TicketService()

        await _setup_member(CS_USER_ID, growth=600)
        await _setup_member(MEMBER_ID, growth=600)
        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")
        contract = await _make_customer_service(svc, CS_USER_ID)
        await svc.admin_contract_action(contract["id"], "activate")
        await wallet.open(CS_USER_ID)

        ticket_no = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-DF1",
            satisfaction=5)
        ledger = await svc.settle_service_profit(ticket_no, order_amount=1200)

        # 月度剩余 3000-33.12=2966.88, 本笔33.12未超 → 无溢出
        record("季度池-未超月度无溢出",
               ledger["capped"] is False
               and ledger["deferredAmount"] == 0)

        # 追回后手工注入2990已结算 → 月度剩10, 本笔33.12 → 截断10, 溢出23.12
        await svc.reverse_service_profit(ticket_no, reason="腾月度空间")
        from core.helpers import ts as _ts
        await svc.repo.create_ledger({
            "ledgerNo": "SVC-MOCK-DF", "ticketNo": "GDMOCKDF",
            "orderId": "", "roleCode": ROLE_CUSTOMER_SERVICE,
            "userId": CS_USER_ID, "basis": "sale_price", "base": 0,
            "rate": SERVICE_PROFIT_RATE, "coefficients": {},
            "rawAmount": 2990, "amount": 2990, "payable": 2990,
            "clawbackDeducted": 0, "clawbackAfter": 33.12,
            "deferredAmount": 0, "capped": False,
            "status": LEDGER_STATUS_SETTLED, "sourceModule": "test",
            "walletTxNo": "", "walletError": "", "createdAt": _ts(),
            "settledAt": _ts(),
        })
        ticket_no2 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-DF2",
            satisfaction=5)
        ledger2 = await svc.settle_service_profit(ticket_no2,
                                                  order_amount=1200)
        # 应得33.12, 月度剩余3000-2990=10 → 发10, 溢出23.12
        record("季度池-月度封顶溢出记账(10+23.12)",
               abs(ledger2["amount"] - 10) < 0.01
               and abs(ledger2["deferredAmount"] - 23.12) < 0.01,
               f"amount={ledger2['amount']}, "
               f"deferred={ledger2['deferredAmount']}")

        summary = await svc.get_risk_summary()
        record("季度池-风控汇总统计溢出总额",
               abs(summary["deferredPoolTotal"] - 23.12) < 0.01,
               f"实际{summary['deferredPoolTotal']}")


# ============================================================
# 10. 试用期到期自动转正 sweep(D-8 细化)
# ============================================================

class TestProbationSweep:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        role_repo = RoleRepository()

        await credit.adjust_score(CS_USER_ID, 400, reason="测试调分",
                                  operator="admin")
        contract1 = await _make_customer_service(svc, CS_USER_ID)
        await credit.adjust_score(CS_USER_ID_2, 50, reason="测试调分",
                                  operator="admin")
        contract2 = await _make_customer_service(svc, CS_USER_ID_2)

        # 未到期 → 不处理
        result = await svc.admin_probation_sweep()
        record("sweep-未到期不动", result["total"] == 0)

        # 手工把两个契约改为已到期
        past = (datetime.now(timezone.utc)
                - timedelta(days=1)).isoformat()
        await role_repo.update_contract(
            contract1["id"], {"probationEndsAt": past})
        await role_repo.update_contract(
            contract2["id"], {"probationEndsAt": past})

        # contract2 用户加一个负面信用事件 → held
        await svc.publish_credit_event(
            CS_USER_ID_2, ROLE_CUSTOMER_SERVICE,
            "cs_satisfaction_bad", "role", "GD-NEG-1")

        result2 = await svc.admin_probation_sweep()
        record("sweep-到期无负面自动转正(1个)",
               len(result2["activated"]) == 1
               and contract1["contractNo"] in result2["activated"])
        record("sweep-有负面保持试用留人工(1个)",
               len(result2["held"]) == 1
               and contract2["contractNo"] in result2["held"])

        contracts = await svc.list_contracts(role_code=ROLE_CUSTOMER_SERVICE)
        status_map = {c["userId"]: c["status"] for c in contracts}
        record("sweep-契约状态正确(2001转正/2002仍试用)",
               status_map.get(CS_USER_ID) == CONTRACT_STATUS_ACTIVE
               and status_map.get(CS_USER_ID_2) == CONTRACT_STATUS_PROBATION)


# ============================================================
# 11. 统一分润总账接入(P1: venue/agent/traffic 回写)
# ============================================================

class TestLedgerIntegration:

    async def run(self):
        reset_store()
        svc = RoleService()

        # 11.1 venue 结算 → 3 条 diff_profit 流水(平台/合作商/代理)
        from services.venue_service import VenueService
        from repositories.venue_repository import VenueRepository
        venue_repo = VenueRepository()
        await venue_repo.save_partner({
            "id": 11, "partnerType": "hotel", "partnerName": "测试酒店",
            "creditCode": "TEST11", "partnerLevel": "A",
            "status": "active", "agentId": 77,
        })
        await venue_repo.save_stocking({
            "id": 1101, "partnerId": 11, "venueId": 1,
            "productId": "P1", "productName": "竹香经典",
            "quantity": 100, "svipPrice": 852.0, "retailPrice": 1000.0,
            "profitDiff": 14800.0, "supplyMode": "agent", "agentId": 77,
            "status": "active",
        })
        settle = await VenueService().settle_commission(partner_id=11)
        record("总账-venue结算回写标记", settle.get("ledgerRecorded") is True)
        # 有代理: 14800 → 本站60%=8880 / 代理20%=2960 / 酒店20%=2960
        record("总账-venue份额计算(60/20/20)",
               abs(settle["platformShare"] - 8880) < 0.01
               and abs(settle["agentShare"] - 2960) < 0.01
               and abs(settle["partnerShare"] - 2960) < 0.01)

        ledgers = await svc.repo.list_ledgers(limit=1000)
        venue_entries = [l for l in ledgers
                        if l.get("sourceModule") == "venue"]
        role_codes = {l["roleCode"] for l in venue_entries}
        record("总账-venue三角色流水(平台/合作商/代理)",
               len(venue_entries) == 3
               and role_codes == {ROLE_PARTNER, ROLE_AGENT, ROLE_PLATFORM},
               f"实际{len(venue_entries)}条/{role_codes}")
        record("总账-venue口径diff_profit(D-7旧轨道)",
               all(l["basis"] == "diff_profit" for l in venue_entries))
        partner_entry = next(l for l in venue_entries
                             if l["roleCode"] == ROLE_PARTNER)
        record("总账-venue合作商份额金额(2960)",
               abs(partner_entry["amount"] - 2960) < 0.01
               and partner_entry["userId"] == 11)
        agent_entry = next(l for l in venue_entries
                          if l["roleCode"] == ROLE_AGENT)
        record("总账-venue代理userId取自铺货(77)",
               agent_entry["userId"] == 77)

        # 11.2 agent 返利 → purchase_amount 轨道
        from services.agent_service import AgentService
        from repositories.agent_repository import AgentRepository
        await AgentRepository().save(21, {
            "agentId": 21, "agentName": "测试代理商", "agentLevel": "B",
            "wallet": 0.0,
        })
        rebate = await AgentService().rebate_calc(21, 600000)
        # 60万: 30万×15% + 10万×25% = 70000
        record("总账-agent返利计算(60万→70000, T2)",
               rebate["rebateAmount"] == 70000 and rebate["tier"] == "T2")
        record("总账-agent回写标记",
               rebate.get("ledgerRecorded") is True)
        agent_ledger = await svc.repo.get_ledger(f"AGT-{rebate['rebateId']}")
        record("总账-agent流水存在(purchase_amount轨道)",
               agent_ledger is not None
               and agent_ledger["basis"] == "purchase_amount"
               and agent_ledger["amount"] == 70000
               and agent_ledger["userId"] == 21)

        # T0 未达门槛 → 不产生噪音流水
        rebate_t0 = await AgentService().rebate_calc(21, 100000)
        record("总账-agent未达门槛无流水",
               rebate_t0["rebateAmount"] == 0
               and "ledgerRecorded" not in rebate_t0)

        # 11.3 traffic 佣金 → sale_price 轨道
        from services.traffic_service import TrafficService
        promoter = await TrafficService().create_promoter(
            user_id=31, name="测试推广员")
        commission = await TrafficService().calculate_commission(
            promoter_id=promoter["id"], order_id="ORD-TRF-1",
            order_amount=1000, user_id=99)
        # 见习5%: 1000×5% = 50
        record("总账-traffic佣金计算(1000×5%=50)",
               commission["commission"] == 50
               and commission.get("ledgerRecorded") is True)
        traffic_ledger = await svc.repo.get_ledger(
            f"TRF-{commission['commissionId']}")
        record("总账-traffic流水存在(sale_price轨道)",
               traffic_ledger is not None
               and traffic_ledger["basis"] == "sale_price"
               and traffic_ledger["amount"] == 50
               and traffic_ledger["userId"] == promoter["id"])

        # 11.4 记账幂等
        again = await svc.record_external_settlement(
            ledger_no=f"TRF-{commission['commissionId']}",
            source_module="traffic", role_code=ROLE_PROMOTER,
            user_id=promoter["id"], basis="sale_price",
            base=1000, rate=0.05, amount=50)
        record("总账-重复记账幂等(不新增)", again["created"] is False)

        # 11.5 风控汇总口径三轨道分布
        summary = await svc.get_risk_summary()
        record("总账-风控汇总三轨道分布",
               summary["basisSplit"].get("diff_profit") == 3
               and summary["basisSplit"].get("purchase_amount") == 1
               and summary["basisSplit"].get("sale_price") == 1,
               f"实际{summary['basisSplit']}")


# ============================================================
# 12. 生产工人分润(P1: 生命码打卡留痕联动)
# ============================================================

# 测试工人: 3001 负责工段1-3, 3002 负责工段4-5, 3003 负责工段6-7
WORKER_A, WORKER_B, WORKER_C = 3001, 3002, 3003
_STAGE_WORKERS = {
    "STG-BREW": WORKER_A, "STG-STOR": WORKER_A, "STG-BLEND": WORKER_A,
    "STG-FILL": WORKER_B, "STG-PACK": WORKER_B,
    "STG-WARE": WORKER_C, "STG-OUT": WORKER_C,
}


async def _setup_released_batch(batch_no: str) -> None:
    """构造已放行批次 + 7工段打卡留痕(直接写仓储, 绕过权限校验)"""
    from repositories.trace_prod_repository import (
        TraceProdRepository, RESULT_PASS, SEED_STAGES,
    )
    trace_repo = TraceProdRepository()
    batch_id = await trace_repo.next_id("batch")
    await trace_repo.save_batch({
        "batchId": batch_id, "batchNo": batch_no, "productId": 1,
        "plannedQty": 100, "currentStageSeq": 7,
        "status": "released", "lifeCodes": [],
        "createdBy": 1, "createdAt": "2026-08-29T00:00:00+00:00",
        "releasedAt": "2026-08-29T10:00:00+00:00", "releasedBy": 1,
    })
    for i, stage in enumerate(SEED_STAGES, 1):
        punch_id = await trace_repo.next_id("punch")
        await trace_repo.save_punch({
            "punchId": punch_id, "batchNo": batch_no,
            "stageCode": stage["code"], "stageName": stage["name"],
            "stageSeq": stage["seq"],
            "memberId": _STAGE_WORKERS[stage["code"]],
            "result": RESULT_PASS, "qcConclusion": "合格",
            "params": {}, "anomalies": [], "aiQcReview": None,
            "punchedAt": f"2026-08-28T0{i}:00:00+00:00",
            "blockHash": "test-hash",
        })


class TestWorkerProfit:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        wallet = WalletService()

        for wid in (WORKER_A, WORKER_B, WORKER_C):
            await _setup_member(wid, growth=600)
            await wallet.open(wid)
        await _setup_released_batch("B-20260829-01")

        # 12.1 预演(只读, 不写账)
        preview = await svc.preview_worker_profit("B-20260829-01", 10000)
        record("工人-预演总份额(10000×15%=1500)",
               abs(preview["totalAmount"] - 1500) < 0.01
               and preview["workersCount"] == 3,
               f"实际{preview['totalAmount']}")
        shares_by_worker = {}
        for s in preview["shares"]:
            shares_by_worker[s["workerId"]] = round(
                shares_by_worker.get(s["workerId"], 0) + s["amount"], 2)
        record("工人-预演按人聚合(A7.5%/B4.5%/C3%)",
               abs(shares_by_worker[WORKER_A] - 750) < 0.01
               and abs(shares_by_worker[WORKER_B] - 450) < 0.01
               and abs(shares_by_worker[WORKER_C] - 300) < 0.01,
               f"实际{shares_by_worker}")
        record("工人-预演不落账",
               not await svc.repo.ledger_exists_prefix(
                   f"{WORKER_LEDGER_PREFIX}B-20260829-01-"))

        # 12.2 结算(pass: 全员全额, 钱包入账+总账+无信用事件)
        result = await svc.settle_worker_profit("B-20260829-01", 10000)
        record("工人-结算总额1500(3工人7工段)",
               abs(result["totalAmount"] - 1500) < 0.01
               and result["workersCount"] == 3)
        record("工人-总账7条流水(每工段一条)",
               all(l["status"] == LEDGER_STATUS_SETTLED
                   for l in result["ledgers"]) and len(result["ledgers"]) == 7)
        record("工人-总账口径sale_price+角色production_worker",
               all(l["basis"] == "sale_price"
                   and l["roleCode"] == ROLE_PRODUCTION_WORKER
                   for l in result["ledgers"]))
        reward_a = await wallet.get_reward_balance(WORKER_A)
        record("工人-钱包到账(工人A=750)",
               abs(reward_a.get("rewardBalance", 0) - 750) < 0.01,
               f"实际{reward_a.get('rewardBalance')}")
        # pass 不产生信用事件
        events = await svc.repo.list_events(role_code=ROLE_PRODUCTION_WORKER)
        record("工人-合格结算无信用事件", len(events) == 0)

        # 12.3 幂等: 批次维度一次结算
        try:
            await svc.settle_worker_profit("B-20260829-01", 10000)
            record("工人-重复结算拒绝", False, "未抛出异常")
        except ValueError:
            record("工人-重复结算拒绝", True)

        # 12.4 premium: ×1.2 + 信用+5
        await _setup_released_batch("B-20260829-02")
        result_p = await svc.settle_worker_profit(
            "B-20260829-02", 10000, quality_grade="premium")
        record("工人-优质批次×1.2(总额1800)",
               abs(result_p["totalAmount"] - 1800) < 0.01,
               f"实际{result_p['totalAmount']}")
        score_a = (await credit.get_score(WORKER_A))["bambooScore"]
        record("工人-优质信用加分(350+5=355)", score_a == 355,
               f"实际{score_a}")

        # 12.5 accident: 零分润 + 信用-15
        await _setup_released_batch("B-20260829-03")
        result_acc = await svc.settle_worker_profit(
            "B-20260829-03", 10000, quality_grade="accident")
        record("工人-事故批次零分润",
               result_acc["totalAmount"] == 0
               and all(s["amount"] == 0 for s in result_acc["shares"]))
        score_a2 = (await credit.get_score(WORKER_A))["bambooScore"]
        record("工人-事故信用扣分(355-15=340)", score_a2 == 340,
               f"实际{score_a2}")

        # 12.6 未放行批次拒绝
        from repositories.trace_prod_repository import TraceProdRepository
        trace_repo = TraceProdRepository()
        await trace_repo.save_batch({
            "batchId": 99, "batchNo": "B-NOT-RELEASED", "productId": 1,
            "plannedQty": 10, "currentStageSeq": 3,
            "status": "producing", "lifeCodes": [],
            "createdBy": 1, "createdAt": "2026-08-29T00:00:00+00:00",
        })
        try:
            await svc.settle_worker_profit("B-NOT-RELEASED", 10000)
            record("工人-未放行批次拒绝", False, "未抛出异常")
        except ValueError:
            record("工人-未放行批次拒绝", True)

        # 12.7 批次不存在 → 404
        try:
            await svc.settle_worker_profit("B-NOT-EXIST", 10000)
            record("工人-批次不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("工人-批次不存在拒绝", True)

        # 12.8 质量等级非法拒绝
        try:
            await svc.settle_worker_profit("B-20260829-01", 10000,
                                           quality_grade="bad")
            record("工人-非法质量等级拒绝", False, "未抛出异常")
        except ValueError:
            record("工人-非法质量等级拒绝", True)


# ============================================================
# 13. AI监管大脑(P2: 满意度预测/异常分润/信用异动)
# ============================================================

class TestAIBrain:

    async def run(self):
        reset_store()
        svc = RoleService()
        chat = ChatService()
        credit = CreditService()

        # 13.1 满意度预测: 纯函数
        calm = svc.predict_session_satisfaction(
            {"unresolvedCount": 0}, [])
        record("AI-平静会话风险低(<60, 无需干预)",
               calm["riskScore"] == 0 and calm["intervention"] is False
               and calm["predictedScore"] == 5)
        angry = svc.predict_session_satisfaction(
            {"unresolvedCount": 2},
            [{"senderType": "user", "content": "太慢了我要投诉, 垃圾服务"}])
        # 2词×20 + 2×15 = 70 ≥ 60
        record("AI-负向情绪+未解决会话高风险(≥60, 干预)",
               angry["riskScore"] >= 60 and angry["intervention"] is True
               and "负向情绪词" in str(angry["factors"]))

        # 13.2 满意度风险扫描(真实会话, emotionScore回写)
        session = await chat.create_session(user_id=MEMBER_ID)
        session_id = session["sessionId"]
        from repositories.chat_repository import (
            SESSION_STATUS_HUMAN, SENDER_SYSTEM, MESSAGE_TYPE_TEXT,
        )
        session["status"] = SESSION_STATUS_HUMAN
        session["customerServiceId"] = CS_USER_ID
        session["unresolvedCount"] = 3   # 3×15=45
        await chat.repo.save_session(session)
        # 写一条含负向词的用户消息(投诉+垃圾=2词×20=40, 合计85≥60)
        await chat.repo.add_message({
            "sessionId": session_id, "senderType": "user",
            "senderId": MEMBER_ID, "messageType": MESSAGE_TYPE_TEXT,
            "content": "我要投诉, 垃圾服务", "mediaUrl": None,
            "mediaThumb": None, "mediaSize": 0, "duration": 0,
            "aiConfidence": None, "isRead": False, "readAt": None,
        })
        scan = await svc.scan_satisfaction_risk()
        record("AI-扫描覆盖进行中会话(1场)",
               scan["scanned"] == 1, f"实际{scan['scanned']}")
        record("AI-高风险会话进入干预名单",
               scan["atRisk"] == 1 and len(scan["alerts"]) == 1)
        updated = await chat.get_session(session_id)
        record("AI-emotionScore空壳字段已激活(>0)",
               updated.get("emotionScore", 0) > 0)
        alerts = await svc.list_alerts(alert_type="satisfaction_risk")
        record("AI-预警落库且open", len(alerts) == 1
               and alerts[0]["status"] == "open")

        # 13.3 信用异动扫描(先签约后扣分, 7天-100 → 冻结+预警)
        await credit.adjust_score(CS_USER_ID, 400, reason="造数",
                                  operator="admin")
        await _make_customer_service(svc, CS_USER_ID)
        for _ in range(4):   # 4次-30 = -120
            await credit.adjust_score(CS_USER_ID, -30, reason="服务差评",
                                      operator="role_module")
        drop_scan = await svc.scan_credit_drop()
        record("AI-信用大幅下滑检出(1人)", drop_scan["dropped"] == 1)
        contracts = await svc.list_contracts(
            role_code=ROLE_CUSTOMER_SERVICE, status="suspended")
        record("AI-异动客服契约被冻结(待人工复核)",
               any(c["userId"] == CS_USER_ID for c in contracts))
        drop_alerts = await svc.list_alerts(alert_type="credit_drop")
        record("AI-异动预警落库", len(drop_alerts) == 1
               and drop_alerts[0]["detail"]["delta"] <= -100)

        # 13.4 预警处置
        resolved = await svc.resolve_alert(
            drop_alerts[0]["id"], operator="admin", resolution="复核完毕")
        record("AI-预警处置(open→resolved)",
               resolved["status"] == "resolved"
               and resolved["resolvedBy"] == "admin")
        try:
            await svc.resolve_alert(drop_alerts[0]["id"])
            record("AI-重复处置拒绝", False, "未抛出异常")
        except ValueError:
            record("AI-重复处置拒绝", True)


class TestProfitAnomalyScan:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        wallet = WalletService()
        ticket_svc = TicketService()

        await _setup_member(CS_USER_ID, growth=600)
        await _setup_member(MEMBER_ID, growth=600)
        await credit.adjust_score(CS_USER_ID, 400, reason="造数",
                                  operator="admin")
        contract = await _make_customer_service(svc, CS_USER_ID)
        await svc.admin_contract_action(contract["id"], "activate")
        await wallet.open(CS_USER_ID)

        # a) 金额离群: 3笔小额(400元4星≈6.62)+1笔大额(10万5星触顶50)
        for i in range(3):
            tno = await _resolved_ticket(
                ticket_svc, MEMBER_ID, CS_USER_ID,
                order_id=f"ORD-A{i}", satisfaction=4)
            ledger = await svc.settle_service_profit(tno, order_amount=400)
        tno_big = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-ABIG",
            satisfaction=5)
        ledger_big = await svc.settle_service_profit(tno_big,
                                                      order_amount=100000)
        # 大额触顶50 vs 小额peer均值6.62 → 50 > 6.62×5 且 >30
        scan = await svc.scan_profit_anomaly()
        kinds = {a["kind"] for a in scan["anomalies"]}
        record("异常-金额离群检出(50 vs peer均值6.62)",
               "amount_outlier" in kinds,
               f"kinds={kinds}")

        # b) 同人高频: 同一会员当月5笔 → pair_frequency
        for i in range(5 - 1):   # 已有4笔, 再补1笔达到5
            tno = await _resolved_ticket(
                ticket_svc, MEMBER_ID, CS_USER_ID,
                order_id=f"ORD-AF{i}", satisfaction=4)
            await svc.settle_service_profit(tno, order_amount=800)
        scan2 = await svc.scan_profit_anomaly()
        kinds2 = {a["kind"] for a in scan2["anomalies"]}
        record("异常-同人高频检出(同会员≥5笔/月)",
               "pair_frequency" in kinds2, f"kinds={kinds2}")

        anomaly_alerts = await svc.list_alerts(alert_type="profit_anomaly")
        record("异常-预警落库(≥2条)", len(anomaly_alerts) >= 2)


# ============================================================
# 14. 抢单模式(P2)
# ============================================================

class TestGrabMode:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        ticket_svc = TicketService()

        await credit.adjust_score(CS_USER_ID, 400, reason="造数",
                                  operator="admin")
        await credit.adjust_score(CS_USER_ID_2, 50, reason="造数",
                                  operator="admin")
        await _make_customer_service(svc, CS_USER_ID)
        await _make_customer_service(svc, CS_USER_ID_2)

        # 建一张待分配工单
        ticket = await ticket_svc.create_ticket(
            user_id=MEMBER_ID, ticket_type="presale", priority="medium",
            description="抢单测试", source="user")
        ticket_no = ticket["ticketNo"]

        pool = await svc.list_grabbable_tickets()
        record("抢单-工单池含待分配单",
               any(t["ticketNo"] == ticket_no for t in pool))

        # 14.1 无契约者拒绝
        try:
            await svc.grab_ticket(ticket_no, 9999)
            record("抢单-无契约拒绝", False, "未抛出异常")
        except ValueError:
            record("抢单-无契约拒绝", True)

        # 14.2 有效客服抢单成功
        grab = await svc.grab_ticket(ticket_no, CS_USER_ID)
        record("抢单-成功(mode=grab留痕)",
               grab["mode"] == "grab" and grab["assigneeId"] == CS_USER_ID)
        grabbed = await ticket_svc.repo.get_ticket(ticket_no)
        record("抢单-工单状态流转(处理中)",
               grabbed["status"] == "processing"
               and grabbed["handlerId"] == CS_USER_ID)

        # 14.3 防双抢: 第二人再抢拒绝
        try:
            await svc.grab_ticket(ticket_no, CS_USER_ID_2)
            record("抢单-双抢拒绝", False, "未抛出异常")
        except ValueError:
            record("抢单-双抢拒绝", True)

        # 14.4 负账冻结者拒绝
        await _setup_member(CS_USER_ID_2, growth=600)
        wallet2 = WalletService()
        await wallet2.open(CS_USER_ID_2)
        tno2 = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID_2, order_id="ORD-G2",
            satisfaction=5)
        await svc.settle_service_profit(tno2, order_amount=1200)
        await svc.reverse_service_profit(tno2, reason="造负账")
        await svc.repo.adjust_clawback(
            CS_USER_ID_2, ROLE_CUSTOMER_SERVICE, 600)  # 负账>500
        ticket2 = await ticket_svc.create_ticket(
            user_id=MEMBER_ID, ticket_type="presale", priority="medium",
            description="抢单测试2", source="user")
        try:
            await svc.grab_ticket(ticket2["ticketNo"], CS_USER_ID_2)
            record("抢单-负账冻结者拒绝", False, "未抛出异常")
        except ValueError:
            record("抢单-负账冻结者拒绝", True)


# ============================================================
# 15. 季度联合结算(P2: 溢出池发放)
# ============================================================

class TestQuarterlyJointSettle:

    async def run(self):
        reset_store()
        svc = RoleService()
        credit = CreditService()
        wallet = WalletService()
        ticket_svc = TicketService()

        await _setup_member(CS_USER_ID, growth=600)
        await _setup_member(MEMBER_ID, growth=600)
        await credit.adjust_score(CS_USER_ID, 400, reason="造数",
                                  operator="admin")
        contract = await _make_customer_service(svc, CS_USER_ID)
        await svc.admin_contract_action(contract["id"], "activate")
        await wallet.open(CS_USER_ID)

        # 造溢出: 手工注入2990已结算 → 本笔33.12溢出23.12
        from core.helpers import ts as _ts
        await svc.repo.create_ledger({
            "ledgerNo": "SVC-MOCK-QJS", "ticketNo": "GDMOCKQJS",
            "orderId": "", "roleCode": ROLE_CUSTOMER_SERVICE,
            "userId": CS_USER_ID, "basis": "sale_price", "base": 0,
            "rate": SERVICE_PROFIT_RATE, "coefficients": {},
            "rawAmount": 2990, "amount": 2990, "payable": 2990,
            "clawbackDeducted": 0, "clawbackAfter": 0,
            "deferredAmount": 0, "capped": False,
            "status": LEDGER_STATUS_SETTLED, "sourceModule": "test",
            "walletTxNo": "", "walletError": "", "createdAt": _ts(),
            "settledAt": _ts(),
        })
        tno = await _resolved_ticket(
            ticket_svc, MEMBER_ID, CS_USER_ID, order_id="ORD-QJS",
            satisfaction=5)
        ledger = await svc.settle_service_profit(tno, order_amount=1200)
        # 月度剩余10 → 本笔发10, 溢出23.12
        record("季结-前置(发10+溢出23.12)",
               abs(ledger["amount"] - 10) < 0.01
               and abs(ledger["deferredAmount"] - 23.12) < 0.01)

        # 季度联合结算
        now = datetime.now(timezone.utc)
        result = await svc.quarterly_joint_settle(
            now.year, (now.month - 1) // 3 + 1)
        record("季结-1名成员获发(23.12)",
               result["members"] == 1
               and abs(result["totalPaid"] - 23.12) < 0.01,
               f"实际{result}")

        reward = await wallet.get_reward_balance(CS_USER_ID)
        # 钱包: 结算10 + 季结溢出23.12 = 33.12
        record("季结-钱包累计到账(10+23.12=33.12)",
               abs(reward.get("rewardBalance", 0) - 33.12) < 0.01,
               f"实际{reward.get('rewardBalance')}")

        # 幂等: 重跑不重复发放
        result2 = await svc.quarterly_joint_settle(
            now.year, (now.month - 1) // 3 + 1)
        reward2 = await wallet.get_reward_balance(CS_USER_ID)
        record("季结-重跑幂等(不重复发放)",
               result2["totalPaid"] == 0
               and abs(reward2.get("rewardBalance", 0) - 33.12) < 0.01)


# ============================================================
# 主入口
# ============================================================

async def main():
    test_classes = [
        ("角色目录", TestCatalog),
        ("角色认领", TestClaim),
        ("契约管理", TestContract),
        ("AI服务调度", TestDispatch),
        ("服务分润结算", TestServiceProfit),
        ("信用行为总线", TestCreditBus),
        ("chat转人工联动", TestChatIntegration),
        ("退款追回负账", TestClawback),
        ("月度封顶溢出记账", TestDeferredPool),
        ("试用期自动转正sweep", TestProbationSweep),
        ("统一分润总账接入", TestLedgerIntegration),
        ("生产工人分润", TestWorkerProfit),
        ("AI监管大脑", TestAIBrain),
        ("异常分润检测", TestProfitAnomalyScan),
        ("抢单模式", TestGrabMode),
        ("季度联合结算", TestQuarterlyJointSettle),
    ]
    print("=" * 62)
    print("AI智能管理模块(角色经济中枢) P0+P1+P2 端到端测试")
    print("=" * 62)
    for name, cls in test_classes:
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(1 if failures else 0)
