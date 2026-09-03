"""42号·AI无感开票模块·核心服务(设计文档 §2)

无感开票流水线(订单 COMPLETED 钩子, best-effort 火后不管):
    幂等(订单已决策过) → 金额下限 → 抬头簿取默认抬头
    → 无默认 → collect 留痕返回
    → InvoiceDecisionScorer(第25档案) 评分
    → auto_issue: finance.issue_invoice(同一锁同一发票池)
        → compliance 存证(evidenceType=invoice)
        → 抬头簿使用计数+1
    → manual_queue: 入待确认队列(抬头快照)
    → reject: 拦截留痕

自动红冲(退款 REFUNDED 钩子):
    查订单已开发票 → 未红冲 → finance.red_invoice
    (原因自动填"订单退款联动")

异常约定(遵循项目约定):
    - KeyError → 404(抬头/决策/队列不存在)
    - ValueError → 409(状态非法/重复/参数无效)
"""

import json
import logging
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.invoice_repository import (
    Invoice42Repository,
    TITLE_TYPE_PERSONAL, TITLE_TYPE_COMPANY, TITLE_TYPES,
    DECISION_AUTO_ISSUE, DECISION_MANUAL_QUEUE,
    DECISION_REJECT, DECISION_COLLECT,
    DECISION_AUTO_SCORE, DECISION_MANUAL_SCORE,
    INVOICE_AUTO_MODE, INVOICE_FREQ_WINDOW_HOURS,
    INVOICE_FREQ_THRESHOLD, INVOICE_MIN_AMOUNT,
    QUEUE_PENDING, QUEUE_DONE, QUEUE_EXPIRED,
    APPEAL_STATUS_PENDING, APPEAL_STATUS_APPROVED,
    APPEAL_STATUS_REJECTED,
)
from services.ai_scoring_service import SCORERS


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def title_confidence(use_count: int, is_default: bool) -> float:
    """抬头置信度(纯函数): 默认标记 0.3 底 + 使用次数线性至 1.0

    use_count ≥ 3 次且默认 → 1.0 满分(设计文档 §2.1)。
    """
    base = 0.3 if is_default else 0.0
    return round(min(1.0, base + (1 - base)
                     * min(use_count, 3) / 3), 2)


class Invoice42Service:
    """无感开票: 抬头簿 + 决策 + 自动开具/红冲 + 队列"""

    def __init__(self):
        self.repo = Invoice42Repository()

    # --------------------------------------------------------
    # 抬头簿
    # --------------------------------------------------------

    async def add_title(self, member_id: int, title_type: str,
                        title: str, tax_no: str = "",
                        is_default: bool = None) -> dict:
        """新增抬头(首个自动成为默认; 显式默认则切换)

        Raises:
            ValueError: 类型/抬头非法, 企业缺税号
        """
        if title_type not in TITLE_TYPES:
            raise ValueError(f"抬头类型非法: {title_type}"
                             f"(允许: {'/'.join(TITLE_TYPES)})")
        title = str(title or "").strip()
        if not title:
            raise ValueError("抬头不能为空")
        if title_type == TITLE_TYPE_COMPANY and not str(tax_no or "").strip():
            raise ValueError("企业抬头必须提供税号")
        # 同抬头去重
        book = await self.repo.ensure_book(int(member_id))
        for t in book["titles"]:
            if t.get("title") == title:
                raise ValueError(f"抬头已存在(titleId={t.get('titleId')})")

        title_id = await self.repo.next_id("title")
        # 首个抬头自动默认; 显式 is_default 切换默认(一人一默认)
        make_default = (is_default is True
                        or not book["titles"])
        if make_default:
            for t in book["titles"]:
                t["isDefault"] = False
        book["titles"].append({
            "titleId": title_id,
            "titleType": title_type,
            "title": title,
            "taxNo": str(tax_no or "").strip(),
            "isDefault": make_default,
            "useCount": 0,
            "createdAt": _now_iso(),
        })
        await self.repo.save_book(book)
        logger.info("invoice_title_added member=%s titleId=%s "
                    "default=%s", member_id, title_id, make_default)
        return book

    async def set_default_title(self, member_id: int,
                                title_id: int) -> dict:
        """切换默认抬头"""
        book = await self.repo.ensure_book(int(member_id))
        target = next((t for t in book["titles"]
                       if t["titleId"] == int(title_id)), None)
        if target is None:
            raise KeyError(f"抬头 {title_id} 不存在")
        for t in book["titles"]:
            t["isDefault"] = (t["titleId"] == int(title_id))
        await self.repo.save_book(book)
        return book

    async def remove_title(self, member_id: int, title_id: int) -> dict:
        """删除抬头(默认被删则首个剩余顶替)"""
        book = await self.repo.ensure_book(int(member_id))
        before = len(book["titles"])
        book["titles"] = [t for t in book["titles"]
                          if t["titleId"] != int(title_id)]
        if len(book["titles"]) == before:
            raise KeyError(f"抬头 {title_id} 不存在")
        if book["titles"] and not any(
                t.get("isDefault") for t in book["titles"]):
            book["titles"][0]["isDefault"] = True
        await self.repo.save_book(book)
        return book

    async def get_book(self, member_id: int) -> dict:
        book = await self.repo.ensure_book(int(member_id))
        return {"success": True, "memberId": int(member_id),
                "titles": book["titles"]}

    def _default_title(self, book: dict) -> dict | None:
        return next((t for t in (book or {}).get("titles", [])
                     if t.get("isDefault")), None)

    # --------------------------------------------------------
    # 无感开票决策流水线(订单完成钩子)
    # --------------------------------------------------------

    async def on_order_completed(self, order_id: str,
                                 member_id: int = None,
                                 amount: float = None,
                                 order_risk_action: str = "pass") -> dict:
        """订单 COMPLETED → 无感开票决策与执行

        幂等: 同订单重复触发直接返回既有决策。
        金额下限/无默认抬头/总开关 off → 对应留痕或静默。
        """
        # 总开关 off → 纯手动回到 19号现状(静默跳过)
        if INVOICE_AUTO_MODE != "on":
            return {"success": True, "skipped": True,
                    "reason": "无感开票总开关关闭"}

        # 幂等: 订单已决策过
        existing = await self.repo.get_decision(order_id)
        if existing is not None:
            return {"success": True, "skipped": True,
                    "reason": "该订单已决策过(幂等)",
                    "decision": existing}

        from repositories.order_repository import OrderRepository
        order = await OrderRepository().get_by_id(order_id)
        if order is None:
            return {"success": False, "skipped": True,
                    "reason": f"订单 {order_id} 不存在"}
        member_id = int(member_id or order.get("memberId"))
        # 注意: amount=0.0 为合法显式口径, 不能用 or 短路
        amount = float(amount if amount is not None
                       else (order.get("priceDetail") or {})
                       .get("actualAmount") or 0)

        # 金额下限: 零元/极小单不开
        if amount < INVOICE_MIN_AMOUNT:
            decision = await self._save_decision(
                order_id, member_id, DECISION_REJECT, 0.0,
                factors=[], snapshot=None,
                detail=f"金额 ¥{amount:.2f} 低于开票下限 "
                       f"¥{INVOICE_MIN_AMOUNT}")
            return {"success": True, "decision": decision}

        # 抬头簿: 无默认 → collect(偏好收集, 不阻断)
        book = await self.repo.ensure_book(member_id)
        default_title = self._default_title(book)
        if default_title is None:
            decision = await self._save_decision(
                order_id, member_id, DECISION_COLLECT, 0.0,
                factors=[], snapshot=None,
                detail="无默认抬头, 待会员完善抬头簿")
            return {"success": True, "decision": decision}

        # 评分(第25档案)
        invoices_24h = await self._count_invoices_24h(member_id)
        member_avg = await self._member_avg_amount(member_id)
        ctx = {
            "orderId": order_id, "memberId": member_id,
            "titleConfidence": title_confidence(
                int(default_title.get("useCount") or 0),
                bool(default_title.get("isDefault"))),
            "amount": amount,
            "memberAvgAmount": member_avg,
            "invoices24h": invoices_24h,
            "freqThreshold": INVOICE_FREQ_THRESHOLD,
            "orderRiskAction": str(order_risk_action or "pass"),
        }
        scoring = await SCORERS["invoice_decision_gate"].score(ctx)
        action = scoring["action"]

        decision = await self._save_decision(
            order_id, member_id, action, scoring["score"],
            factors=scoring.get("factors") or [],
            snapshot=scoring, detail="")

        # 执行
        if action == DECISION_AUTO_ISSUE:
            result = await self._auto_issue(order_id, member_id,
                                           default_title, decision)
            decision.update(result)
        elif action == DECISION_MANUAL_QUEUE:
            await self._enqueue(order_id, member_id, default_title,
                                decision)
        # reject: 仅留痕(可申诉)
        logger.info("invoice_decision order=%s member=%s action=%s "
                    "score=%s", order_id, member_id, action,
                    scoring["score"])
        return {"success": True, "decision": decision,
                "scoring": scoring}

    async def _save_decision(self, order_id: str, member_id: int,
                             action: str, score: float,
                             factors: list, snapshot: dict,
                             detail: str) -> dict:
        decision = {
            "orderId": order_id,
            "memberId": member_id,
            "action": action,
            "score": score,
            "factors": factors,
            "scoreSnapshot": snapshot or {},
            "detail": detail,
            "invoiceNo": "",
            "evidenceHash": "",
            "redInvoiceNo": "",
            "decidedAt": _now_iso(),
        }
        await self.repo.save_decision(decision)
        return decision

    async def _auto_issue(self, order_id: str, member_id: int,
                          title: dict, decision: dict) -> dict:
        """自动开具: finance.issue_invoice(同一锁同一发票池)
        + compliance 存证 + 抬头簿计数"""
        from services.finance_service import FinanceService
        try:
            invoice = await FinanceService().issue_invoice(
                member_id, order_id,
                title_type=title.get("titleType"),
                title=title.get("title", ""),
                tax_no=title.get("taxNo", ""))
        except ValueError as exc:
            # 手动已开/重复开票等业务冲突 → 决策留痕不阻断
            logger.info("invoice_auto_issue_skip order=%s: %s",
                        order_id, exc)
            return {"invoiceNo": "", "issueSkipped": str(exc)}

        decision["invoiceNo"] = invoice.get("invoiceNo", "")

        # 存证(发票全量快照上链, 42号自动化率留痕)
        evidence = None
        try:
            from services.compliance_service import ComplianceService
            evidence = await ComplianceService() \
                .add_blockchain_evidence(
                    "invoice",
                    json.dumps(invoice, ensure_ascii=False,
                               default=str),
                    ai_automation_rate=100.0)
            decision["evidenceHash"] = evidence.get("evidenceHash", "")
        except Exception as exc:
            logger.warning("invoice_evidence_failed order=%s: %s",
                          order_id, exc)   # 存证 best-effort

        # 抬头簿使用计数 +1
        book = await self.repo.get_book(member_id)
        for t in (book or {}).get("titles", []):
            if t.get("titleId") == title.get("titleId"):
                t["useCount"] = int(t.get("useCount") or 0) + 1
        if book:
            await self.repo.save_book(book)

        await self.repo.save_decision(decision)
        logger.info("invoice_auto_issued order=%s invoice=%s "
                    "evidence=%s", order_id,
                    invoice.get("invoiceNo"),
                    bool(decision.get("evidenceHash")))
        return {"invoiceNo": invoice.get("invoiceNo"),
                "invoice": invoice, "evidence": evidence}

    async def _enqueue(self, order_id: str, member_id: int,
                       title: dict, decision: dict) -> None:
        """manual_queue → 待确认队列(抬头快照)"""
        item = {
            "decisionId": order_id,       # 以订单号为队列主键
            "orderId": order_id,
            "memberId": member_id,
            "status": QUEUE_PENDING,
            "titleSnapshot": {
                "titleType": title.get("titleType"),
                "title": title.get("title", ""),
                "taxNo": title.get("taxNo", ""),
            },
            "score": decision.get("score"),
            "createdAt": _now_iso(),
        }
        await self.repo.save_queue_item(item)
        await self.repo.save_decision(decision)

    async def confirm_queue(self, member_id: int,
                            order_id: str) -> dict:
        """待确认队列一键开票(用户端)

        Raises:
            KeyError: 队列条目不存在
            ValueError: 非本人/非 pending
        """
        item = await self.repo.get_queue_item(order_id)
        if item is None:
            raise KeyError(f"队列条目 {order_id} 不存在")
        if int(item.get("memberId") or 0) != int(member_id):
            raise ValueError("仅本人可确认开票")
        if item.get("status") != QUEUE_PENDING:
            raise ValueError(f"队列状态 {item.get('status')}, 不可确认")
        snap = item.get("titleSnapshot") or {}
        from services.finance_service import FinanceService
        invoice = await FinanceService().issue_invoice(
            int(member_id), order_id,
            title_type=snap.get("titleType") or "personal",
            title=snap.get("title", ""),
            tax_no=snap.get("taxNo", ""))
        item["status"] = QUEUE_DONE
        item["invoiceNo"] = invoice.get("invoiceNo", "")
        item["confirmedAt"] = _now_iso()
        await self.repo.save_queue_item(item)
        # 决策流水回写发票号
        decision = await self.repo.get_decision(order_id)
        if decision is not None:
            decision["invoiceNo"] = invoice.get("invoiceNo", "")
            await self.repo.save_decision(decision)
        return {"success": True, "invoice": invoice}

    # --------------------------------------------------------
    # 自动红冲(退款钩子)
    # --------------------------------------------------------

    async def on_order_refunded(self, order_id: str) -> dict:
        """订单 REFUNDED → 自动红冲已开发票

        Returns:
            {red, invoiceNo, redInvoiceNo, skipped}
        """
        decision = await self.repo.get_decision(order_id)
        # 决策流水找不到 → 可能手动开的, 查 finance 发票池
        from services.finance_service import FinanceService
        finance = FinanceService()
        invoices = (await finance.list_invoices()) \
            .get("invoices") or []
        target = next((i for i in invoices
                       if i.get("orderId") == order_id
                       and i.get("status") == "issued"
                       and i.get("type") == "normal"), None)

        # 队列 pending 条目置 expired(退款单不再待开, 无论有无发票)
        item = await self.repo.get_queue_item(order_id)
        if item is not None and item.get("status") == QUEUE_PENDING:
            item["status"] = QUEUE_EXPIRED
            item["expiredAt"] = _now_iso()
            await self.repo.save_queue_item(item)

        if target is None:
            return {"success": True, "red": False, "skipped": True,
                    "reason": "该订单无可红冲发票"}

        red = await finance.red_invoice(
            target["invoiceNo"], reason="订单退款联动(42号自动红冲)")
        red_no = (red or {}).get("redInvoiceNo", "")
        if decision is not None:
            decision["redInvoiceNo"] = red_no
            await self.repo.save_decision(decision)
        logger.info("invoice_auto_red order=%s invoice=%s red=%s",
                    order_id, target["invoiceNo"], red_no)
        return {"success": True, "red": True,
                "invoiceNo": target["invoiceNo"],
                "redInvoiceNo": red_no, "invoice": red}

    # --------------------------------------------------------
    # 统计辅助
    # --------------------------------------------------------

    async def _count_invoices_24h(self, member_id: int) -> int:
        """会员 24h 已开发票数(频次因子)"""
        from services.finance_service import FinanceService
        invoices = (await FinanceService().list_invoices()) \
            .get("invoices") or []
        cutoff = datetime.now(UTC) - timedelta(
            hours=INVOICE_FREQ_WINDOW_HOURS)
        count = 0
        for i in invoices:
            if int(i.get("memberId") or 0) != int(member_id):
                continue
            if i.get("type") != "normal":
                continue
            try:
                issued = datetime.fromisoformat(
                    i.get("issuedAt") or "")
            except ValueError:
                continue
            if issued >= cutoff:
                count += 1
        return count

    async def _member_avg_amount(self, member_id: int) -> float | None:
        """会员历史均单金额(金额合理性因子; 无历史返回 None 中性)"""
        from repositories.order_repository import OrderRepository
        orders = await OrderRepository().get_by_member(member_id, None)
        amounts = [float(((o.get("priceDetail") or {})
                          .get("actualAmount") or 0))
                   for o in (orders or [])]
        amounts = [a for a in amounts if a > 0]
        if not amounts:
            return None
        return round(sum(amounts) / len(amounts), 2)

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    async def my_invoices(self, member_id: int) -> list[dict]:
        """我的发票(finance 池按 memberId 过滤)"""
        from services.finance_service import FinanceService
        invoices = (await FinanceService().list_invoices()) \
            .get("invoices") or []
        return [i for i in invoices
                if int(i.get("memberId") or 0) == int(member_id)]

    async def my_queue(self, member_id: int) -> list[dict]:
        return await self.repo.list_queue(member_id=int(member_id),
                                          status=QUEUE_PENDING)

    async def request_invoice(self, member_id: int, order_id: str,
                              title_id: int = None) -> dict:
        """手动触发兜底(无感漏网: collect 档补开)

        Raises:
            KeyError: 订单/抬头不存在
            ValueError: 订单状态/参数非法
        """
        from repositories.order_repository import OrderRepository
        order = await OrderRepository().get_by_id(order_id)
        if order is None:
            raise KeyError(f"订单 {order_id} 不存在")
        if int(order.get("memberId") or 0) != int(member_id):
            raise ValueError("仅本人可申请开票")
        book = await self.repo.ensure_book(int(member_id))
        title = None
        if title_id is not None:
            title = next((t for t in book["titles"]
                          if t["titleId"] == int(title_id)), None)
            if title is None:
                raise KeyError(f"抬头 {title_id} 不存在")
        else:
            title = self._default_title(book)
        if title is None:
            raise ValueError("无可用抬头, 请先维护抬头簿")

        from services.finance_service import FinanceService
        invoice = await FinanceService().issue_invoice(
            int(member_id), order_id,
            title_type=title.get("titleType"),
            title=title.get("title", ""),
            tax_no=title.get("taxNo", ""))
        # 决策流水回写(存在 collect 档时升级)
        decision = await self.repo.get_decision(order_id)
        if decision is not None:
            decision["invoiceNo"] = invoice.get("invoiceNo", "")
            if decision.get("action") == DECISION_COLLECT:
                decision["action"] = DECISION_AUTO_ISSUE
                decision["detail"] = "用户手动补开"
            await self.repo.save_decision(decision)
        return {"success": True, "invoice": invoice}

    async def admin_decisions(self, action: str = None,
                              limit: int = 100) -> list[dict]:
        return await self.repo.list_decisions(action=action,
                                              limit=limit)

    async def admin_stats(self) -> dict:
        """自动化率统计(管理端看板)"""
        decisions = await self.repo.list_decisions(limit=2000)
        by_action = {a: 0 for a in (DECISION_AUTO_ISSUE,
                                    DECISION_MANUAL_QUEUE,
                                    DECISION_REJECT,
                                    DECISION_COLLECT)}
        issued = 0
        for d in decisions:
            if d.get("action") in by_action:
                by_action[d["action"]] += 1
            if d.get("invoiceNo"):
                issued += 1
        total = len(decisions)
        # P1: 申诉与误拦截率(拦截面板核心指标)
        appeals = await self.repo.list_appeals(limit=2000)
        appeal_approved = sum(1 for a in appeals
                               if a.get("status")
                               == APPEAL_STATUS_APPROVED)
        appeal_pending = sum(1 for a in appeals
                              if a.get("status")
                              == APPEAL_STATUS_PENDING)
        reject_total = by_action[DECISION_REJECT]
        return {
            "success": True,
            "total": total,
            "byAction": by_action,
            "invoicesIssued": issued,
            "automationRate": round(
                by_action[DECISION_AUTO_ISSUE] / total, 4)
            if total else 0.0,
            "appeals": {"total": len(appeals),
                        "pending": appeal_pending,
                        "approved": appeal_approved},
            "falsePositiveRate": round(
                appeal_approved / reject_total, 4)
            if reject_total else 0.0,
            "thresholds": {"auto": DECISION_AUTO_SCORE,
                          "manual": DECISION_MANUAL_SCORE},
        }

    # --------------------------------------------------------
    # P1: 申诉与裁决(拦截面板 §三 第 2/3 步)
    # --------------------------------------------------------

    async def submit_appeal(self, member_id: int, order_id: str,
                            reason: str = "") -> dict:
        """会员对 reject 拦截决策提交申诉

        Raises:
            KeyError: 决策不存在
            ValueError: 非本人/非 reject 档/已申诉
        """
        decision = await self.repo.get_decision(order_id)
        if decision is None:
            raise KeyError(f"订单 {order_id} 无开票决策")
        if int(decision.get("memberId") or 0) != int(member_id):
            raise ValueError("仅本人可申诉")
        if decision.get("action") != DECISION_REJECT:
            raise ValueError(f"决策档位 {decision.get('action')}, "
                             "仅拦截(reject)决策可申诉")
        existing = await self.repo.get_appeal_by_order(order_id)
        if existing is not None:
            raise ValueError(f"该订单已有申诉(appealId="
                             f"{existing.get('appealId')}, 状态 "
                             f"{existing.get('status')})")

        appeal_id = await self.repo.next_id("appeal")
        appeal = {
            "appealId": appeal_id,
            "orderId": order_id,
            "memberId": int(member_id),
            "reason": str(reason or "").strip()
                      or "会员对拦截决策有异议",
            "status": APPEAL_STATUS_PENDING,
            "reviewer": "",
            "reviewNote": "",
            "scoreAtDecision": decision.get("score"),
            "createdAt": _now_iso(),
            "decidedAt": None,
        }
        await self.repo.save_appeal(appeal)
        logger.info("invoice_appeal_submitted appeal=%s order=%s "
                    "member=%s", appeal_id, order_id, member_id)
        return {"success": True, "appeal": appeal}

    async def decide_appeal(self, appeal_id: int, approve: bool,
                            reviewer: str = "admin",
                            note: str = "") -> dict:
        """管理员裁决申诉

        approve=True(误拦恢复): 申诉置 approved; 决策流水 detail
        标注"申诉恢复", 会员经手动触发端点补开(四步法第 3 步路径A)。
        approve=False(维持拦截): 申诉置 rejected 归档。

        Raises:
            KeyError: 申诉不存在
            ValueError: 已裁决
        """
        appeal = await self.repo.get_appeal(int(appeal_id))
        if appeal is None:
            raise KeyError(f"申诉 {appeal_id} 不存在")
        if appeal.get("status") != APPEAL_STATUS_PENDING:
            raise ValueError(f"申诉状态 {appeal.get('status')}, "
                             "仅待裁决申诉可处理")

        appeal["status"] = (APPEAL_STATUS_APPROVED if approve
                            else APPEAL_STATUS_REJECTED)
        appeal["reviewer"] = reviewer
        appeal["reviewNote"] = note
        appeal["decidedAt"] = _now_iso()
        await self.repo.save_appeal(appeal)

        # 误拦恢复: 决策流水标注(补开走会员手动触发, 升级口径已有)
        decision = await self.repo.get_decision(appeal["orderId"])
        if approve and decision is not None:
            decision["detail"] = (f"申诉恢复(appealId={appeal_id}, "
                                   f"{reviewer})")
            await self.repo.save_decision(decision)

        logger.info("invoice_appeal_decided appeal=%s approve=%s "
                    "reviewer=%s", appeal_id, approve, reviewer)
        return {"success": True, "appeal": appeal}

    async def my_appeals(self, member_id: int) -> list[dict]:
        return await self.repo.list_appeals(member_id=int(member_id))

    async def admin_appeals(self, status: str = None,
                            limit: int = 100) -> list[dict]:
        return await self.repo.list_appeals(status=status,
                                             limit=limit)

    # --------------------------------------------------------
    # P2: 学习回流(申诉裁决真值 → 第25档案)
    # --------------------------------------------------------

    async def collect_appeal_feedback(self) -> dict:
        """批量回流: 已裁决且未回流的申诉 → 决策正确性反馈

        真值口径: 申诉裁决为管理员人工复核真值——
        approved(误拦) = AI 拦错了, 期望 auto_issue;
        rejected(维持拦截) = AI 拦对了, 期望 reject。
        单条失败不阻断批量。

        Returns:
            {submitted, skipped, results}
        """
        from services.ai_learning_service import submit_feedback

        appeals = await self.repo.list_appeals(limit=1000)
        submitted, skipped, results = 0, 0, []
        for appeal in appeals:
            if appeal.get("appealFed"):
                skipped += 1
                continue
            if appeal.get("status") == APPEAL_STATUS_PENDING:
                skipped += 1
                continue
            decision = await self.repo.get_decision(
                appeal.get("orderId", ""))
            if decision is None:
                skipped += 1
                continue
            snapshot = decision.get("scoreSnapshot") or {}
            factors = snapshot.get("factors") or []
            if not factors:
                skipped += 1
                continue
            actual = "reject"
            expected = ("auto_issue"
                        if appeal.get("status") == APPEAL_STATUS_APPROVED
                        else "reject")
            correct = actual == expected
            # 决策为可行性向(高分=可自动): 误拦(reject→应auto)影响
            # 会员体验, 弱负; 拦对强正
            reward = 0.5 if correct else -0.5
            try:
                result = await submit_feedback({
                    "scorerId": "invoice_decision_gate",
                    "factors": factors,
                    "scoreAtDecision": float(
                        snapshot.get("score") or 0),
                    "actualAction": actual,
                    "expectedAction": expected,
                    "correct": correct,
                    "reward": reward,
                    "note": (f"appealId={appeal.get('appealId')} "
                             f"decision={appeal.get('status')}"),
                    "source": "invoice42",
                })
                appeal["appealFed"] = True    # 幂等标记
                await self.repo.save_appeal(appeal)
                results.append(result)
                submitted += 1
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("invoice_appeal_feed_skip appeal=%s: %s",
                               appeal.get("appealId"), exc)
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    async def run_learning(self) -> dict:
        """触发第25档案一轮 Hedge 学习(反馈不足抛 ValueError)"""
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle("invoice_decision_gate")

    async def learning_status(self) -> dict:
        """第25档案学习状态(裁决申诉回流计数/幂等标记统计)"""
        from services.ai_learning_service import (
            SCORER_REGISTRY, get_weights_view,
        )
        appeals = await self.repo.list_appeals(limit=2000)
        decided = [a for a in appeals
                   if a.get("status") != APPEAL_STATUS_PENDING]
        return {
            "success": True,
            "scorer": "invoice_decision_gate",
            "registry": SCORER_REGISTRY.get("invoice_decision_gate"),
            "appeals": {
                "total": len(appeals),
                "decided": len(decided),
                "fed": sum(1 for a in appeals if a.get("appealFed")),
            },
            "weights": await get_weights_view(
                "invoice_decision_gate"),
        }
