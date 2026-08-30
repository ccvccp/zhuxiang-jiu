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


def _build_initial_agents() -> dict:
    """构建 2 个代理商初始数据(扩展档案字段)

    保持 id/level/wallet 与既有测试约束一致(test_list_all_returns_all_agents
    断言恰好 2 个代理商 ids={1,2}; test_get_level_existing_agent 断言
    agent1 level="C"; test_get_wallet_existing_agent 断言 agent1 wallet=50000):
        - agent 1: level="C", wallet=50000
        - agent 2: level="B", wallet=120000
    扩展字段: status/contact_name/contact_phone/region/address/
              created_at/updated_at/total_sales/total_purchases
    风控扩展: agent 2 的 sales_region 与授权 region 不一致(窜货预警测试)
    """
    now = "2026-08-21T00:00:00+00:00"
    return {
        1: {
            "id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000,
            "status": "active", "contact_name": "王经理",
            "contact_phone": "13800001001", "region": "山东省泰安市",
            "address": "泰安市泰山区竹香路 1 号",
            "created_at": now, "updated_at": now,
            "total_sales": 0.0, "total_purchases": 0.0,
        },
        2: {
            "id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000,
            "status": "active", "contact_name": "李总",
            "contact_phone": "13800001002", "region": "山东省济南市",
            "address": "济南市历下区竹香大厦 5 层",
            "created_at": now, "updated_at": now,
            "total_sales": 0.0, "total_purchases": 0.0,
            # 实际销售区域与授权区域不一致 → 窜货预警
            "sales_region": "山东省德州市",
        },
    }


def _build_initial_agent_rebates() -> dict:
    """构建代理商返利初始数据(agent 1: 2026-07 月度返利, T1 档)

    返利计算(超额累进制):
        30 万进货额 = 0-20万(0%) + 20-30万(15%) = 0 + 10万×15% = 15000
    """
    return {
        "RB20260701001": {
            "rebateId": "RB20260701001",
            "agentId": 1,
            "period": "2026-07",
            "tier": "T1",
            "purchaseAmount": 300000.0,
            "rebateRate": 0.15,
            "rebateAmount": 15000.0,
            "status": "pending",
            "withdrawnAt": "",
            "createdAt": "2026-08-21T00:00:00+00:00",
        },
    }


def _build_initial_agent_risks() -> dict:
    """构建代理商风控初始数据(agent 1: 信用评级记录)

    agent 1 无进货记录 → 信用分 60(基础分), 风险等级 medium
    """
    return {
        "RK20260701001": {
            "riskId": "RK20260701001",
            "agentId": 1,
            "type": "assessment",
            "creditScore": 60.0,
            "riskLevel": "medium",
            "indicators": {
                "purchaseCount": 0,
                "totalPurchases": 0.0,
                "returnRate": 0.0,
                "paymentDelayRate": 0.0,
                "purchaseStability": 0.0,
            },
            "alerts": [],
            "createdAt": "2026-08-21T00:00:00+00:00",
        },
    }


def _build_initial_supply_chain() -> dict:
    """构建供应链四件套扩展数据域(P4.4: 对齐前端 mock 契约)

    域清单(前端契约来源: js/checkout-service.js / inventory-service.js /
    warehouse-service.js / agent-shipping-service.js 的 mock DB):
        - inventory_logs / stock_alerts:  库存流水与低库存预警(inventory)
        - checkout_coupons / points_accounts / checkout_orders /
          profit_records / service_fees:  结算事务域(checkout)
        - shipping_claim_details:         认领富记录(shipping, 活跃映射仍在
                                          shipping_claims {region: agent_id})
        - 仓储域: supply_warehouses / warehouse_locations / warehouse_stock /
          inbound_orders / outbound_orders / stock_movements /
          stocktaking_records / loss_records / transfer_orders /
          cross_dock_records / environment_monitoring
    """
    now = "2026-08-30T00:00:00+00:00"
    # 仓库主表(4 仓, 对齐前端 warehouse mock)
    warehouses = [
        {"id": 1, "warehouse_code": "WH-FACTORY-01", "warehouse_name": "山东泰安工厂仓",
         "warehouse_type": "factory", "ai_warehouse_score": 92.5,
         "ai_utilization_rate": 78.0, "ai_efficiency_score": 90.0, "status": "active"},
        {"id": 2, "warehouse_code": "WH-REGION-01", "warehouse_name": "华东区域仓",
         "warehouse_type": "regional", "ai_warehouse_score": 88.0,
         "ai_utilization_rate": 65.0, "ai_efficiency_score": 85.0, "status": "active"},
        {"id": 3, "warehouse_code": "WH-RETAIL-01", "warehouse_name": "上海零售仓",
         "warehouse_type": "retail", "ai_warehouse_score": 85.0,
         "ai_utilization_rate": 70.0, "ai_efficiency_score": 82.0, "status": "active"},
        {"id": 4, "warehouse_code": "WH-AGING-01", "warehouse_name": "陈酿仓",
         "warehouse_type": "aging", "ai_warehouse_score": 95.0,
         "ai_utilization_rate": 60.0, "ai_efficiency_score": 88.0, "status": "active"},
    ]
    # 库位表(仓1: 3区×5排×4列×3层 = 180 库位, 对齐前端)
    locations = []
    loc_id = 1
    for z in range(1, 4):
        for r in range(1, 6):
            for col in range(1, 5):
                for f in range(1, 4):
                    locations.append({
                        "id": loc_id,
                        "warehouse_id": 1,
                        "location_code": f"A{z}-{r}-{col}-{f}",
                        "zone_type": "hot" if z == 1 else ("warm" if z == 2 else "cold"),
                        "abc_class": "A" if z == 1 else ("B" if z == 2 else "C"),
                        "status": "empty",
                    })
                    loc_id += 1
    # 仓储库存: 仓1 放全部 11 款(前 11 个库位), 仓2 放 3 款(供多仓调拨演示)
    inv = _build_initial_inventory()
    stock = []
    for idx, pid in enumerate(inv):
        stock.append({
            "id": idx + 1, "warehouse_id": 1, "location_id": idx + 1,
            "product_id": pid, "material_id": None,
            "stock_qty": inv[pid]["stock"],
            "ai_recommended_safety": 20, "ai_turnover_rate": 2.5,
            "ai_stock_status": "sufficient" if inv[pid]["stock"] > 50 else "normal",
            "abc_class": "A" if idx < 4 else ("B" if idx < 8 else "C"),
            "batch_no": f"BLC-{pid}-150001",
            "life_code_activated_at": "2026-07-01T00:00:00Z",
        })
        locations[idx]["status"] = "occupied"
    for idx, (pid, qty) in enumerate([
        ("ZX42-2026L07", 50), ("ZX42-2026B01", 30), ("ZX52-2026X01", 40),
    ]):
        stock.append({
            "id": 12 + idx, "warehouse_id": 2, "location_id": None,
            "product_id": pid, "material_id": None, "stock_qty": qty,
            "ai_recommended_safety": 20, "ai_turnover_rate": 2.0,
            "ai_stock_status": "normal", "abc_class": "B",
            "batch_no": f"BLC-{pid}-160001",
            "life_code_activated_at": "2026-08-01T00:00:00Z",
        })
    return {
        # inventory 域
        "inventory_logs": [],
        "stock_alerts": [],
        # checkout 域(优惠券/积分/订单/分润/服务费)
        # 注意: 积分域键名用 checkout_points(不能叫 points_accounts,
        # 与积分模块 points_repository 的惰性初始化探针键冲突)
        "checkout_coupons": {
            "NEW10": {"id": "C001", "code": "NEW10", "discount": 0.10,
                       "status": "未使用", "desc": "新人9折"},
            "SVIP20": {"id": "C002", "code": "SVIP20", "discount": 0.20,
                        "status": "未使用", "desc": "SVIP8折"},
        },
        "checkout_points": {"L1": 1000, "L2": 2000, "L3": 5000,
                             "L4": 8000, "L5": 12000},
        "checkout_orders": [],
        "profit_records": [],
        "service_fees": [],
        # shipping 域(认领富记录; 活跃映射沿用 shipping_claims {region: agent_id})
        "shipping_claim_details": {},
        # warehouse 域
        "supply_warehouses": warehouses,
        "warehouse_locations": locations,
        "warehouse_stock": stock,
        "inbound_orders": [],
        "outbound_orders": [],
        "stock_movements": [],
        "stocktaking_records": [],
        "loss_records": [],
        "transfer_orders": [],
        "cross_dock_records": [],
        "environment_monitoring": [],
        "_sc_init_marker": now,
    }


_mock_store: dict = {
    "agents": _build_initial_agents(),
    # 代理商扩展存储(申请记录 / 进货记录 / 自增序列)
    "agent_applications": {},
    "agent_purchases": {},
    "_agent_seq": 2,            # 已有最大代理商 ID(新增档案从 3 起)
    "_agent_apply_seq": 0,      # 申请单自增序列
    # 代理商返利记录(返利结算模块)
    "agent_rebates": _build_initial_agent_rebates(),
    # 代理商风控记录(风控管理模块)
    "agent_risks": _build_initial_agent_risks(),
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
            "status": 1, "reg_source": "phone", "role": "member",
            "ageConfirmed": True, "birthdate": "1990-01-01", "ageVerified": True,
            "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
        },
        2: {
            "id": 2, "phone": "13800000002", "password": _hash_member_pwd("test123456"),
            "nickname": "站点管理员", "avatar": "", "gender": 1,
            "level": 3, "growth_value": 600, "points": 100,
            "status": 1, "reg_source": "phone", "role": "admin",
            "ageConfirmed": True, "birthdate": "1988-06-15", "ageVerified": True,
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
    "_member_seq": 2,
    # 产品展示模块(11 款产品 + 评价)
    "products": _build_initial_products(),
    "product_reviews": _build_initial_product_reviews(),
    # 供应链四件套扩展域(P4.4)
    **_build_initial_supply_chain(),
    # 财务管理模块: 内存模式由 FinanceRepository._ensure_store() 懒创建空结构,
    # 生产 Redis 模式由 scripts/seed_redis.py 调用 _build_initial_finance() 灌注
}


def reset_store() -> dict:
    """重置 _mock_store 到初始状态(测试辅助)

    注意:此函数仅用于测试 setup,生产环境不应调用。
    """
    import copy
    initial = {
        "agents": _build_initial_agents(),
        # 代理商扩展存储(申请记录 / 进货记录 / 自增序列)
        "agent_applications": {},
        "agent_purchases": {},
        "_agent_seq": 2,
        "_agent_apply_seq": 0,
        # 代理商返利记录(返利结算模块)
        "agent_rebates": _build_initial_agent_rebates(),
        # 代理商风控记录(风控管理模块)
        "agent_risks": _build_initial_agent_risks(),
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
                "status": 1, "reg_source": "phone", "role": "member",
                "ageConfirmed": True, "birthdate": "1990-01-01", "ageVerified": True,
                "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
            },
            2: {
                "id": 2, "phone": "13800000002", "password": _hash_member_pwd("test123456"),
                "nickname": "站点管理员", "avatar": "", "gender": 1,
                "level": 3, "growth_value": 600, "points": 100,
                "status": 1, "reg_source": "phone", "role": "admin",
                "ageConfirmed": True, "birthdate": "1988-06-15", "ageVerified": True,
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
        "_member_seq": 2,
        # 产品展示模块(11 款产品 + 评价)
        "products": _build_initial_products(),
        "product_reviews": _build_initial_product_reviews(),
        # 供应链四件套扩展域(P4.4)
        **_build_initial_supply_chain(),
        # 财务管理模块: 内存模式由 FinanceRepository._ensure_store() 懒创建空结构
    }
    _mock_store.clear()
    _mock_store.update(copy.deepcopy(initial))
    return _mock_store
