"""内存态 Mock 存储单例

生产环境应替换为数据库(SQLAlchemy/MongoDB),只需保留 Repository 接口。
当前为开发/测试用途:进程内字典,重启即丢失。

注意:
    - _mock_store 是模块级单例,所有 Repository 默认共享此实例
    - 测试文件通过 `from main import _mock_store` 直接修改,
      与 Repository 通过此单例间接读写保持一致
    - 多进程部署时每个 worker 持有独立副本(状态分裂),
      方案 B 锁仅保证跨进程互斥,不解决存储分裂
"""


def _hash_member_pwd(password: str) -> str:
    """会员密码哈希(Mock, 与 member_service._hash_password 一致)"""
    import hashlib
    salt = "zhuxiang_member_salt_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _build_initial_products() -> dict:
    """构建 11 款产品初始数据字典(product_id → product)

    与 repositories/product_repository.py 的 _INITIAL_PRODUCTS 数据源一致,
    通过导入复用避免数据重复维护。
    """
    # 局部导入避免循环依赖(product_repository 依赖 backend, backend 依赖本模块)
    from repositories.product_repository import _initial_products
    return {p["product_id"]: p for p in _initial_products()}


def _build_initial_product_reviews() -> dict:
    """构建产品评价初始数据字典(product_id → [review, ...])"""
    from repositories.product_repository import _initial_reviews
    import copy
    return copy.deepcopy(_initial_reviews())


def _build_initial_finance() -> dict:
    """构建财务模块初始数据(凭证/发票/申报/付款/对账 各若干示例)

    与 FinanceRepository._ensure_store() 中维护的 5 个 dict 键对齐:
        finance_vouchers / finance_invoices / finance_tax_declarations
        finance_payments / finance_reconciliations
    同时初始化序列号计数器与索引(_finance_seq / _finance_*_index)。
    """
    now = "2026-08-20T08:00:00+00:00"
    # 示例凭证(1 笔收入 + 1 笔退款红字,均 posted)
    vouchers = {
        "FZ20260820001": {
            "voucherNo": "FZ20260820001",
            "period": "202608",
            "date": "2026-08-20",
            "type": "income",
            "source": "order",
            "sourceId": "RT20260820001",
            "memberId": 1,
            "status": "posted",
            "amount": 536.00,
            "amountWithoutTax": 474.34,
            "taxAmount": 61.66,
            "consumptionTaxAmount": 95.87,
            "entries": [
                {"direction": "debit", "subject": "银行存款", "amount": 536.00,
                 "summary": "收 wechat RT20260820001"},
                {"direction": "credit", "subject": "主营业务收入", "amount": 474.34,
                 "summary": "销售商品 RT20260820001"},
                {"direction": "credit", "subject": "应交税费-应交增值税(销项税额)",
                 "amount": 61.66, "summary": "销项税 RT20260820001"},
            ],
            "auditedBy": "admin",
            "postedBy": "admin",
            "auditedAt": now,
            "postedAt": now,
            "createdAt": now,
            "updatedAt": now,
        },
    }
    # 示例发票
    invoices = {
        "FP20260820001": {
            "invoiceNo": "FP20260820001",
            "orderId": "RT20260820001",
            "memberId": 1,
            "titleType": "personal",
            "title": "测试会员小竹",
            "taxNo": "",
            "type": "normal",
            "status": "issued",
            "amount": 536.00,
            "amountWithoutTax": 474.34,
            "taxAmount": 61.66,
            "period": "202608",
            "date": "2026-08-20",
            "issuedBy": 1,
            "issuedAt": now,
            "redOriginalNo": "",
            "redReason": "",
            "createdAt": now,
            "updatedAt": now,
        },
    }
    # 示例税务申报(2 个待申报 + 1 个已申报)
    tax_declarations = {
        "SB202608001": {
            "declarationNo": "SB202608001",
            "taxType": "vat",
            "taxTypeName": "增值税",
            "period": "202608",
            "status": "pending",
            "payableAmount": 61.66,
            "paidAmount": 0,
            "detail": {"vat": {"payable": 61.66, "rate": 0.13}},
            "declaredBy": "",
            "declaredAt": "",
            "paidBy": "",
            "paidAt": "",
            "createdAt": now,
            "updatedAt": now,
        },
        "SB202608002": {
            "declarationNo": "SB202608002",
            "taxType": "consumption",
            "taxTypeName": "消费税",
            "period": "202608",
            "status": "declared",
            "payableAmount": 95.87,
            "paidAmount": 0,
            "detail": {"consumptionTax": {"total": 95.87}},
            "declaredBy": "admin",
            "declaredAt": now,
            "paidBy": "",
            "paidAt": "",
            "createdAt": now,
            "updatedAt": now,
        },
        "SB202608003": {
            "declarationNo": "SB202608003",
            "taxType": "surtax",
            "taxTypeName": "附加税",
            "period": "202608",
            "status": "paid",
            "payableAmount": 11.03,
            "paidAmount": 11.03,
            "detail": {"surtax": {"total": 11.03}},
            "declaredBy": "admin",
            "declaredAt": now,
            "paidBy": "admin",
            "paidAt": now,
            "createdAt": now,
            "updatedAt": now,
        },
    }
    # 示例付款(2 笔: 1 笔一级已批准, 1 笔二级审批中)
    payments = {
        "FK20260820001": {
            "paymentNo": "FK20260820001",
            "type": "supplier",
            "payee": "竹香原料供应商",
            "amount": 5000.00,
            "description": "8月原料采购",
            "status": "approved",
            "requiredLevel": 1,
            "currentLevel": 1,
            "approvals": [
                {"level": 1, "approver": "主管", "decision": "approve",
                 "reason": "", "at": now},
            ],
            "appliedAt": now,
            "paidAt": "",
            "rejectedBy": "",
            "rejectedAt": "",
            "rejectReason": "",
            "createdAt": now,
            "updatedAt": now,
        },
        "FK20260820002": {
            "paymentNo": "FK20260820002",
            "type": "logistics",
            "payee": "顺丰速运",
            "amount": 50000.00,
            "description": "8月物流运费",
            "status": "approving",
            "requiredLevel": 2,
            "currentLevel": 1,
            "approvals": [
                {"level": 1, "approver": "主管", "decision": "approve",
                 "reason": "", "at": now},
            ],
            "appliedAt": now,
            "paidAt": "",
            "rejectedBy": "",
            "rejectedAt": "",
            "rejectReason": "",
            "createdAt": now,
            "updatedAt": now,
        },
    }
    # 示例对账记录(1 笔一致, 1 笔差异已处理)
    reconciliations = {
        "2026-08-19:daily": {
            "reconId": "2026-08-19:daily",
            "date": "2026-08-19",
            "type": "daily",
            "status": "matched",
            "orderSide": {"count": 3, "amount": 1500.00},
            "paySide": {"count": 3, "amount": 1500.00},
            "bankSide": {"count": 3, "amount": 1500.00},
            "diffAmount": 0,
            "differences": [],
            "resolvedBy": "",
            "resolvedAt": "",
            "resolveNote": "",
            "createdAt": now,
            "updatedAt": now,
        },
        "2026-08-18:daily": {
            "reconId": "2026-08-18:daily",
            "date": "2026-08-18",
            "type": "daily",
            "status": "resolved",
            "orderSide": {"count": 2, "amount": 1000.00},
            "paySide": {"count": 2, "amount": 1000.00},
            "bankSide": {"count": 1, "amount": 800.00},
            "diffAmount": 200.00,
            "differences": [
                {"side": "pay-vs-bank", "amount": 200.00,
                 "desc": "支付渠道与银行不一致"},
            ],
            "resolvedBy": "财务-王五",
            "resolvedAt": now,
            "resolveNote": "银行T+1到账延迟",
            "createdAt": now,
            "updatedAt": now,
        },
    }
    return {
        "finance_vouchers": vouchers,
        "finance_invoices": invoices,
        "finance_tax_declarations": tax_declarations,
        "finance_payments": payments,
        "finance_reconciliations": reconciliations,
        "_finance_seq": {
            "voucher:FZ20260820": 1,
            "invoice:FP20260820": 1,
            "tax:SB202608": 3,
            "payment:FK20260820": 2,
        },
        "_finance_voucher_index": {"202608": {"FZ20260820001"}},
        "_finance_invoice_index": {"202608": {"FP20260820001"}},
        "_finance_tax_index": {"202608": {"SB202608001", "SB202608002", "SB202608003"}},
        "_finance_payment_index": {
            "supplier": {"FK20260820001"},
            "logistics": {"FK20260820002"},
        },
    }


def _build_initial_inventory() -> dict:
    """构建库存初始数据(11 款产品)

    保留 ZX42-2026L07(500)/ZX42-2026L05(300) 与原值一致以兼容订单测试,
    其余 9 款产品补充初始库存。
    """
    return {
        "ZX42-2026L07": {"stock": 500, "reserved": 0},
        "ZX42-2026L05": {"stock": 300, "reserved": 0},
        "ZX53-2026Z01": {"stock": 200, "reserved": 0},
        "ZX53-2026N10": {"stock": 120, "reserved": 0},
        "ZX53-2026N20": {"stock": 40, "reserved": 0},
        "ZX52-2026L02": {"stock": 150, "reserved": 0},
        "ZX42-2026B01": {"stock": 800, "reserved": 0},
        "ZX50-2026D01": {"stock": 100, "reserved": 0},
        "ZX52-2026X01": {"stock": 300, "reserved": 0},
        "ZX52-2026X02": {"stock": 180, "reserved": 0},
        "ZX52-2026X03": {"stock": 60, "reserved": 0},
    }


_mock_store: dict = {
    "agents": {
        1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
        2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
    },
    "inventory": _build_initial_inventory(),
    "warehouse": {
        "slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05"},
        "inbound_log": [],
        "outbound_log": [],
    },
    "orders": [],
    "orders_v2": {},
    "shipping_claims": {},
    # 会员模块(MemberRepository 自管理 seq, 此处仅初始数据)
    "members": {
        1: {
            "id": 1, "phone": "13800000001", "password": _hash_member_pwd("test123456"),
            "nickname": "测试会员小竹", "avatar": "", "gender": 1,
            "level": 1, "growth_value": 0, "points": 100,
            "status": 1, "reg_source": "phone",
            "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
        },
    },
    "member_addresses": {
        1: {
            "addr_seed_001": {
                "id": "addr_seed_001", "user_id": 1, "name": "张三",
                "phone": "13800000001", "province": "山东省", "city": "泰安市",
                "district": "泰山区", "detail": "竹香路 1 号", "is_default": 1,
                "created_at": "2026-08-21T00:00:00+00:00",
            },
        },
    },
    "_member_seq": 1,
    # 产品展示模块(11 款产品 + 评价)
    "products": _build_initial_products(),
    "product_reviews": _build_initial_product_reviews(),
    # 财务管理模块: 内存模式由 FinanceRepository._ensure_store() 懒创建空结构,
    # 生产 Redis 模式由 scripts/seed_redis.py 调用 _build_initial_finance() 灌注
}


def reset_store() -> dict:
    """重置 _mock_store 到初始状态(测试辅助)

    注意:此函数仅用于测试 setup,生产环境不应调用。
    """
    import copy
    initial = {
        "agents": {
            1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
            2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
        },
        "inventory": _build_initial_inventory(),
        "warehouse": {
            "slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05"},
            "inbound_log": [],
            "outbound_log": [],
        },
        "orders": [],
    "orders_v2": {},
        "shipping_claims": {},
        "members": {
            1: {
                "id": 1, "phone": "13800000001", "password": _hash_member_pwd("test123456"),
                "nickname": "测试会员小竹", "avatar": "", "gender": 1,
                "level": 1, "growth_value": 0, "points": 100,
                "status": 1, "reg_source": "phone",
                "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
            },
        },
        "member_addresses": {
            1: {
                "addr_seed_001": {
                    "id": "addr_seed_001", "user_id": 1, "name": "张三",
                    "phone": "13800000001", "province": "山东省", "city": "泰安市",
                    "district": "泰山区", "detail": "竹香路 1 号", "is_default": 1,
                    "created_at": "2026-08-21T00:00:00+00:00",
                },
            },
        },
        "_member_seq": 1,
        # 产品展示模块(11 款产品 + 评价)
        "products": _build_initial_products(),
        "product_reviews": _build_initial_product_reviews(),
        # 财务管理模块: 内存模式由 FinanceRepository._ensure_store() 懒创建空结构
    }
    _mock_store.clear()
    _mock_store.update(copy.deepcopy(initial))
    return _mock_store
