-- ============================================================
-- 模块27: AI智能原料采购与供应商管理模块
-- 核心数据库表结构设计 DDL
-- ------------------------------------------------------------
-- 模块编号: 27
-- 所属域: 供应链域
-- AI自动化率: 91%
-- 参考平台: 1688采购/卡奥斯供应链/SAP Ariba/京东企业购/用友YonBIP
-- 法律合规: 对标 招标投标法/政府采购法/合同法/反垄断法
--             数据安全法/个人信息保护法/税收征收管理法
-- ============================================================

-- ============================================================
-- 表1: suppliers — 供应商主表
-- ============================================================
CREATE TABLE suppliers (
    supplier_id          VARCHAR(32)   PRIMARY KEY COMMENT '供应商ID(SUP-2026-00001)',
    supplier_name        VARCHAR(128)  NOT NULL COMMENT '供应商名称',
    supplier_type        VARCHAR(32)   NOT NULL COMMENT '类型: 原粮/曲药/竹材/水源/包装/物流/设备',
    legal_person         VARCHAR(64)   COMMENT '法人代表',
    unified_credit_code  VARCHAR(32)   UNIQUE COMMENT '统一社会信用代码',
    registered_capital  DECIMAL(14,2) COMMENT '注册资本(元)',
    established_date    DATE          COMMENT '成立日期',
    business_scope      TEXT          COMMENT '经营范围',
    contact_person      VARCHAR(64)   NOT NULL COMMENT '联系人',
    contact_phone       VARCHAR(20)   NOT NULL COMMENT '联系电话',
    contact_email       VARCHAR(128)  COMMENT '联系邮箱',
    province            VARCHAR(32)   COMMENT '省份',
    city                VARCHAR(32)   COMMENT '城市',
    address             VARCHAR(256)  COMMENT '详细地址',
    gps_location        VARCHAR(64)   COMMENT 'GPS坐标(lat,lng)',

    -- 供应商等级 D→C→B→A→S (与代理商等级体系一致)
    supplier_level      CHAR(1)       DEFAULT 'D' COMMENT '等级: D/C/B/A/S',
    level_updated_at    DATETIME      COMMENT '等级最近更新时间',

    -- 资质状态
    qualification_status VARCHAR(16)  DEFAULT 'pending' COMMENT '资质状态: pending/approved/rejected/expired',
    qualification_expires DATE        COMMENT '资质到期日期',
    business_license_url VARCHAR(256)  COMMENT '营业执照URL',
    food_license_url    VARCHAR(256)  COMMENT '食品经营许可证URL',
    production_license_url VARCHAR(256) COMMENT '生产许可证URL',

    -- 合作状态
    cooperation_status  VARCHAR(16)   DEFAULT 'inactive' COMMENT '合作状态: active/inactive/suspended/blacklisted',
    cooperation_since   DATE         COMMENT '合作开始日期',
    min_order_amount    DECIMAL(12,2) DEFAULT 0 COMMENT '最低起订金额(元)',

    -- AI 字段
    ai_risk_score       DECIMAL(5,2)  DEFAULT 0 COMMENT 'AI风险评分(0-100,越高越安全)',
    ai_match_score      DECIMAL(5,2)  DEFAULT 0 COMMENT 'AI匹配评分(0-100)',
    ai_profile_tags     JSON          COMMENT 'AI画像标签(200+)',

    -- 审计字段
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by          VARCHAR(32)   COMMENT '创建人',
    updated_by          VARCHAR(32)   COMMENT '更新人',
    is_deleted          TINYINT(1)    DEFAULT 0 COMMENT '软删除',

    INDEX idx_type (supplier_type),
    INDEX idx_level (supplier_level),
    INDEX idx_status (cooperation_status),
    INDEX idx_credit (unified_credit_code),
    INDEX idx_risk (ai_risk_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商主表';


-- ============================================================
-- 表2: supplier_profiles — 供应商画像表 (200+标签)
-- ============================================================
CREATE TABLE supplier_profiles (
    profile_id          BIGINT        PRIMARY KEY AUTO_INCREMENT,
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',
    tag_category        VARCHAR(32)   NOT NULL COMMENT '标签分类: 基础/产能/质量/财务/合规/ESG',
    tag_name            VARCHAR(64)   NOT NULL COMMENT '标签名(如: 年产能/合格率/资产负债率)',
    tag_value           VARCHAR(256)  COMMENT '标签值',
    tag_score           DECIMAL(5,2)  COMMENT '标签评分(0-100)',
    ai_generated        TINYINT(1)    DEFAULT 0 COMMENT 'AI自动生成: 0=人工,1=AI',
    confidence          DECIMAL(5,2)  COMMENT 'AI置信度(0-100)',
    data_source         VARCHAR(64)   COMMENT '数据来源: 自填/工商/税务/第三方',
    verified            TINYINT(1)    DEFAULT 0 COMMENT '是否已验证',
    verified_at         DATETIME      COMMENT '验证时间',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_supplier_tag (supplier_id, tag_name),
    INDEX idx_category (tag_category),
    INDEX idx_supplier (supplier_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商画像表(200+标签)';


-- ============================================================
-- 表3: supplier_ratings — 供应商评级历史表 (D→S五级动态)
-- ============================================================
CREATE TABLE supplier_ratings (
    rating_id           BIGINT        PRIMARY KEY AUTO_INCREMENT,
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',
    rating_period       VARCHAR(16)   NOT NULL COMMENT '评级周期: 2026-Q1/2026-07/2026年度',
    rating_type         VARCHAR(16)   DEFAULT 'monthly' COMMENT '月度/季度/年度',

    -- 评级维度 (各0-100分)
    quality_score       DECIMAL(5,2)  COMMENT '质量评分(来料合格率+缺陷率)',
    delivery_score      DECIMAL(5,2)  COMMENT '交付评分(准时率+交期偏差)',
    price_score         DECIMAL(5,2)  COMMENT '价格评分(竞争力+稳定性)',
    service_score       DECIMAL(5,2)  COMMENT '服务评分(响应速度+配合度)',
    risk_score          DECIMAL(5,2)  COMMENT '风险评分(财务+合规+经营)',
    esg_score           DECIMAL(5,2)  COMMENT 'ESG评分(环保+社会责任+治理)',

    -- 综合评级
    total_score         DECIMAL(5,2)  COMMENT '综合评分(加权平均)',
    old_level           CHAR(1)       COMMENT '原等级 D/C/B/A/S',
    new_level           CHAR(1)       COMMENT '新等级 D/C/B/A/S',
    change_reason       TEXT          COMMENT '等级变动原因',

    -- AI 评级
    ai_recommended_level CHAR(1)      COMMENT 'AI推荐等级',
    ai_confidence        DECIMAL(5,2) COMMENT 'AI置信度',
    ai_analysis         TEXT          COMMENT 'AI分析报告',

    -- 评级阈值 (对标代理商升级体系)
    -- S级: ≥90分 | A级: 80-89 | B级: 70-79 | C级: 60-69 | D级: <60

    rated_by            VARCHAR(32)   COMMENT '评级人(系统/AI/人工)',
    approved_by          VARCHAR(32)   COMMENT '审批人',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_supplier (supplier_id),
    INDEX idx_period (rating_period),
    INDEX idx_level (new_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商评级历史表(D→S五级动态)';


-- ============================================================
-- 表4: raw_materials — 原料主表
-- ============================================================
CREATE TABLE raw_materials (
    material_id         VARCHAR(32)   PRIMARY KEY COMMENT '原料ID(MAT-GL-001)',
    material_name       VARCHAR(64)   NOT NULL COMMENT '原料名称(红高粱/小麦/中高温曲/毛竹/徂徕山泉水)',
    material_code       VARCHAR(32)   UNIQUE COMMENT '原料编码',
    material_category   VARCHAR(32)   NOT NULL COMMENT '分类: 原粮/曲药/辅料/水源/竹材/包装',
    spec                VARCHAR(128)  COMMENT '规格(如: 粳高粱/糯高粱)',
    origin_region       VARCHAR(64)   COMMENT '产地(如: 山东泰安/东北/武夷山)',
    origin_gps          VARCHAR(64)   COMMENT '产地GPS坐标',

    -- 质量标准
    quality_grade       VARCHAR(8)    DEFAULT 'B' COMMENT '品质等级: A/B/C/D (与酒况分级一致)',
    moisture_max       DECIMAL(5,2)  COMMENT '水分上限(%)',
    starch_min         DECIMAL(5,2)  COMMENT '淀粉下限(%)',
    protein_range      VARCHAR(32)   COMMENT '蛋白质含量范围',
    flavor_profile     JSON          COMMENT '风味图谱(GC-MS数据)',

    -- 采购参数
    unit               VARCHAR(16)   DEFAULT 'kg' COMMENT '计量单位',
    standard_price     DECIMAL(10,2) COMMENT '标准采购价(元/单位)',
    market_price       DECIMAL(10,2) COMMENT '当前市场价',
    min_order_qty      DECIMAL(10,2) DEFAULT 0 COMMENT '最低起订量',
    shelf_life_days    INT           COMMENT '保质期(天)',

    -- 库存参数
    safety_stock       DECIMAL(10,2) COMMENT '安全库存',
    max_stock          DECIMAL(10,2) COMMENT '最大库存上限',

    -- 区块链溯源
    blockchain_hash    VARCHAR(128)  COMMENT '产地溯源区块链哈希',

    -- 审计
    created_at         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted         TINYINT(1)    DEFAULT 0,

    INDEX idx_category (material_category),
    INDEX idx_grade (quality_grade),
    INDEX idx_origin (origin_region)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='原料主表(原粮/曲药/竹材/水源/包装)';


-- ============================================================
-- 表5: procurement_orders — 采购订单表
-- ============================================================
CREATE TABLE procurement_orders (
    order_id            VARCHAR(32)   PRIMARY KEY COMMENT '订单号(PO-2026-00001)',
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',
    material_id         VARCHAR(32)   NOT NULL COMMENT '原料ID',

    -- 订单内容
    material_name       VARCHAR(64)   COMMENT '原料名称(冗余)',
    quantity            DECIMAL(12,2) NOT NULL COMMENT '采购数量',
    unit                VARCHAR(16)   COMMENT '单位',
    unit_price          DECIMAL(10,2) NOT NULL COMMENT '单价(元)',
    total_amount        DECIMAL(14,2) NOT NULL COMMENT '总金额(元)',

    -- 价格信息
    market_price_ref    DECIMAL(10,2) COMMENT '比价参考市场价',
    price_diff_pct     DECIMAL(5,2)  COMMENT '价差比例(%)',
    ai_price_score     DECIMAL(5,2)  COMMENT 'AI价格评分',

    -- 交付
    expected_delivery  DATE          NOT NULL COMMENT '预计交付日期',
    actual_delivery    DATE          COMMENT '实际交付日期',
    delivery_status    VARCHAR(16)   DEFAULT 'pending' COMMENT '交付状态: pending/partial/delivered/overdue',
    delivery_address   VARCHAR(256)  COMMENT '交付地址',

    -- 质量
    inspection_status  VARCHAR(16)   DEFAULT 'pending' COMMENT '检验状态: pending/passed/failed/partial',
    inspection_id      VARCHAR(32)   COMMENT '来料检验单号',

    -- 状态
    order_status       VARCHAR(16)   DEFAULT 'draft' COMMENT '订单状态: draft/submitted/approved/contracted/receiving/completed/cancelled',
    payment_status     VARCHAR(16)   DEFAULT 'unpaid' COMMENT '付款状态: unpaid/partial/paid',

    -- AI 字段
    ai_recommended     TINYINT(1)    DEFAULT 0 COMMENT 'AI推荐供应商',
    ai_match_score     DECIMAL(5,2)  COMMENT 'AI匹配评分',
    ai_risk_warning    TEXT          COMMENT 'AI风险提示',

    created_at         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by         VARCHAR(32)   COMMENT '创建人',

    INDEX idx_supplier (supplier_id),
    INDEX idx_material (material_id),
    INDEX idx_status (order_status),
    INDEX idx_delivery (expected_delivery)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购订单表';


-- ============================================================
-- 表6: procurement_contracts — 采购合同表
-- ============================================================
CREATE TABLE procurement_contracts (
    contract_id         VARCHAR(32)   PRIMARY KEY COMMENT '合同号(PC-2026-00001)',
    order_id            VARCHAR(32)   NOT NULL COMMENT '关联采购订单',
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',

    contract_name       VARCHAR(128)  NOT NULL COMMENT '合同名称',
    contract_type      VARCHAR(32)   COMMENT '合同类型: 框架/单次/长期/紧急',
    total_amount        DECIMAL(14,2) NOT NULL COMMENT '合同总金额',
    currency           VARCHAR(8)    DEFAULT 'CNY' COMMENT '币种',

    -- 期限
    sign_date           DATE          NOT NULL COMMENT '签订日期',
    effective_date      DATE          NOT NULL COMMENT '生效日期',
    expiry_date         DATE          NOT NULL COMMENT '到期日期',

    -- 条款
    payment_terms       VARCHAR(256)  COMMENT '付款条款(如: 货到付款30天)',
    delivery_terms      VARCHAR(256)  COMMENT '交付条款',
    quality_terms       TEXT          COMMENT '质量条款',
    penalty_clause      TEXT          COMMENT '违约条款',
    confidentiality     TINYINT(1)    DEFAULT 1 COMMENT '保密协议',

    -- AI 合同审查
    ai_review_status    VARCHAR(16)   DEFAULT 'pending' COMMENT 'AI审查状态: pending/passed/issues/rejected',
    ai_review_issues    JSON          COMMENT 'AI审查问题清单',
    ai_risk_score       DECIMAL(5,2)  COMMENT 'AI风险评分',
    ai_suggestions      TEXT          COMMENT 'AI修改建议',

    -- 区块链存证
    blockchain_hash     VARCHAR(128)  COMMENT '合同区块链哈希',
    blockchain_tx_id    VARCHAR(128)  COMMENT '区块链交易ID',

    contract_status     VARCHAR(16)   DEFAULT 'draft' COMMENT '状态: draft/signed/active/expired/terminated',
    signed_by_supplier  VARCHAR(32)   COMMENT '供应商签署人',
    signed_by_buyer     VARCHAR(32)   COMMENT '采购方签署人',

    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_order (order_id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_status (contract_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购合同表';


-- ============================================================
-- 表7: incoming_inspections — 来料检验记录表
-- ============================================================
CREATE TABLE incoming_inspections (
    inspection_id       VARCHAR(32)   PRIMARY KEY COMMENT '检验单号(INSP-2026-00001)',
    order_id            VARCHAR(32)   NOT NULL COMMENT '采购订单号',
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',
    material_id         VARCHAR(32)   NOT NULL COMMENT '原料ID',

    inspection_date     DATETIME      NOT NULL COMMENT '检验日期',
    inspector           VARCHAR(32)   COMMENT '检验员',
    inspection_method   VARCHAR(32)   COMMENT '检验方式: 人工/视觉AI/传感器/实验室',

    -- 抽样
    sample_size         DECIMAL(10,2) COMMENT '抽样数量',
    batch_size          DECIMAL(10,2) COMMENT '到货批量',
    sampling_rate       DECIMAL(5,2)  COMMENT '抽样率(%)',

    -- 感官指标
    appearance_score    DECIMAL(5,2)  COMMENT '外观评分(视觉AI)',
    color_score         DECIMAL(5,2)  COMMENT '色泽评分',
    odor_score          DECIMAL(5,2)  COMMENT '气味评分',

    -- 理化指标
    moisture_actual     DECIMAL(5,2)  COMMENT '实际水分(%)',
    starch_actual       DECIMAL(5,2)  COMMENT '实际淀粉(%)',
    protein_actual      DECIMAL(5,2)  COMMENT '实际蛋白质(%)',
    foreign_matter      DECIMAL(5,2)  COMMENT '杂质率(%)',
    heavy_metal         DECIMAL(5,2)  COMMENT '重金属含量(mg/kg)',
    pesticide_residue   DECIMAL(5,2)  COMMENT '农药残留(mg/kg)',

    -- 微生物
    yeast_count         DECIMAL(10,2) COMMENT '酵母菌数(cfu/g)',
    mold_count          DECIMAL(10,2) COMMENT '霉菌数(cfu/g)',
    e_coli              DECIMAL(10,2) COMMENT '大肠杆菌(MPN/g)',

    -- GC-MS 风味图谱
    gc_ms_data          JSON          COMMENT 'GC-MS风味物质数据',
    flavor_match_score  DECIMAL(5,2)  COMMENT '风味匹配度(%)',

    -- AI 检验
    ai_inspection_score DECIMAL(5,2)  COMMENT 'AI综合检验评分',
    ai_defects_detected JSON          COMMENT 'AI检测到的缺陷列表',
    ai_confidence       DECIMAL(5,2)  COMMENT 'AI置信度',

    -- 结论
    inspection_result   VARCHAR(16)   NOT NULL COMMENT '检验结论: passed/conditional/failed',
    fail_reasons        TEXT          COMMENT '不合格原因',
    disposition         VARCHAR(32)   COMMENT '处置: 接收/让步接收/退货/销毁',
    reinspection_count  INT           DEFAULT 0 COMMENT '复检次数',

    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_order (order_id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_material (material_id),
    INDEX idx_result (inspection_result),
    INDEX idx_date (inspection_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='来料检验记录表(视觉AI+GC-MS+传感器)';


-- ============================================================
-- 表8: price_history — 原料价格历史表 (比价+趋势)
-- ============================================================
CREATE TABLE price_history (
    price_id            BIGINT        PRIMARY KEY AUTO_INCREMENT,
    material_id         VARCHAR(32)   NOT NULL COMMENT '原料ID',
    supplier_id         VARCHAR(32)   COMMENT '供应商ID(为空表示市场均价)',
    price_date          DATE          NOT NULL COMMENT '价格日期',
    unit_price          DECIMAL(10,2) NOT NULL COMMENT '单价(元)',
    unit                VARCHAR(16)   COMMENT '单位',
    price_type          VARCHAR(16)   NOT NULL COMMENT '价格类型: 采购/报价/市场/招标',

    -- 价格分析
    price_change_pct    DECIMAL(5,2)  COMMENT '环比变化(%)',
    price_change_abs    DECIMAL(10,2) COMMENT '环比变化(元)',
    moving_avg_7d       DECIMAL(10,2) COMMENT '7日移动均价',
    moving_avg_30d      DECIMAL(10,2) COMMENT '30日移动均价',
    volatility          DECIMAL(5,2)  COMMENT '波动率(%)',

    -- AI 预测
    ai_forecast_7d     DECIMAL(10,2) COMMENT 'AI 7日预测价',
    ai_forecast_30d    DECIMAL(10,2) COMMENT 'AI 30日预测价',
    ai_trend           VARCHAR(16)   COMMENT 'AI趋势判断: 上涨/下跌/震荡',
    ai_confidence      DECIMAL(5,2)  COMMENT 'AI预测置信度',

    data_source         VARCHAR(64)   COMMENT '数据来源: 1688/卓创/农业农村部/自采',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_material (material_id),
    INDEX idx_date (price_date),
    INDEX idx_supplier (supplier_id),
    INDEX idx_type (price_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='原料价格历史表(比价+趋势+AI预测)';


-- ============================================================
-- 表9: supply_chain_risks — 供应链风险预警表
-- ============================================================
CREATE TABLE supply_chain_risks (
    risk_id             BIGINT        PRIMARY KEY AUTO_INCREMENT,
    risk_type           VARCHAR(32)   NOT NULL COMMENT '风险类型: 价格波动/供应中断/质量/合规/物流/地缘',
    risk_level          VARCHAR(16)   NOT NULL COMMENT '风险等级: 低/中/高/极高',
    risk_source         VARCHAR(64)   COMMENT '风险来源(供应商/原料/地区/政策)',

    supplier_id         VARCHAR(32)   COMMENT '关联供应商(可空)',
    material_id         VARCHAR(32)   COMMENT '关联原料(可空)',

    risk_title          VARCHAR(256)  NOT NULL COMMENT '风险标题',
    risk_description    TEXT          NOT NULL COMMENT '风险描述',
    risk_impact         TEXT          COMMENT '影响评估',
    probability         DECIMAL(5,2)  COMMENT '发生概率(%)',
    impact_score        DECIMAL(5,2)  COMMENT '影响程度(0-100)',
    risk_score          DECIMAL(5,2)  COMMENT '综合风险评分(概率×影响)',

    -- AI 预警
    ai_detected         TINYINT(1)    DEFAULT 0 COMMENT 'AI自动检测',
    ai_confidence       DECIMAL(5,2)  COMMENT 'AI置信度',
    ai_early_warning    DATETIME      COMMENT 'AI提前预警时间',
    ai_analysis         TEXT          COMMENT 'AI分析报告',
    ai_recommendation   TEXT          COMMENT 'AI处置建议',

    -- 处置
    mitigation_plan     TEXT          COMMENT '缓解措施',
    mitigation_status   VARCHAR(16)   DEFAULT 'pending' COMMENT '处置状态: pending/in_progress/resolved/accepted',
    resolved_at         DATETIME      COMMENT '解决时间',
    resolved_by         VARCHAR(32)   COMMENT '解决人',

    detected_at         DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '发现时间',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_type (risk_type),
    INDEX idx_level (risk_level),
    INDEX idx_supplier (supplier_id),
    INDEX idx_material (material_id),
    INDEX idx_status (mitigation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应链风险预警表';


-- ============================================================
-- 表10: cost_calculations — 成本核算表
-- ============================================================
CREATE TABLE cost_calculations (
    cost_id             BIGINT        PRIMARY KEY AUTO_INCREMENT,
    calculation_period  VARCHAR(16)   NOT NULL COMMENT '核算周期: 2026-07/2026-Q3',
    material_id         VARCHAR(32)   NOT NULL COMMENT '原料ID',
    supplier_id         VARCHAR(32)   COMMENT '供应商ID',

    -- 成本构成
    purchase_cost       DECIMAL(14,2) NOT NULL COMMENT '采购成本',
    transport_cost      DECIMAL(14,2) COMMENT '运输成本',
    inspection_cost     DECIMAL(14,2) COMMENT '检验成本',
    storage_cost        DECIMAL(14,2) COMMENT '仓储成本',
    management_cost     DECIMAL(14,2) COMMENT '管理成本',
    loss_cost           DECIMAL(14,2) COMMENT '损耗成本',
    other_cost          DECIMAL(14,2) COMMENT '其他成本',

    total_cost          DECIMAL(14,2) NOT NULL COMMENT '总成本',
    unit_cost           DECIMAL(10,2) NOT NULL COMMENT '单位成本',
    cost_variance       DECIMAL(5,2)  COMMENT '成本偏差率(%) (实际vs预算)',

    -- AI 分析
    ai_cost_optimization TEXT         COMMENT 'AI成本优化建议',
    ai_saving_potential DECIMAL(14,2) COMMENT 'AI预测可节约金额',
    ai_confidence       DECIMAL(5,2)  COMMENT 'AI置信度',
    ai_cost_trend       VARCHAR(16)   COMMENT 'AI成本趋势: 上升/稳定/下降',

    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by          VARCHAR(32)   COMMENT '核算人',

    INDEX idx_period (calculation_period),
    INDEX idx_material (material_id),
    INDEX idx_supplier (supplier_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成本核算表(精细化+AI优化)';


-- ============================================================
-- 表11: replenishment_plans — 协同补货计划表
-- ============================================================
CREATE TABLE replenishment_plans (
    plan_id             VARCHAR(32)   PRIMARY KEY COMMENT '补货计划号(RP-2026-00001)',
    material_id         VARCHAR(32)   NOT NULL COMMENT '原料ID',
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',

    -- 补货触发
    trigger_type        VARCHAR(32)   NOT NULL COMMENT '触发方式: 安全库存/AI预测/OEM排程/手动',
    trigger_ref_id      VARCHAR(32)   COMMENT '触发关联ID(如OEM排程单号)',

    -- 补货数量
    current_stock       DECIMAL(12,2) COMMENT '当前库存',
    safety_stock        DECIMAL(12,2) COMMENT '安全库存',
    recommended_qty     DECIMAL(12,2) NOT NULL COMMENT 'AI推荐补货量',
    actual_qty          DECIMAL(12,2) COMMENT '实际下单量',
    unit                VARCHAR(16)   COMMENT '单位',

    -- 时间
    recommended_date    DATE          NOT NULL COMMENT 'AI推荐补货日期',
    expected_arrival    DATE          COMMENT '预计到货日期',
    urgency             VARCHAR(16)   DEFAULT 'normal' COMMENT '紧急程度: low/normal/high/urgent',

    -- AI 预测
    ai_demand_forecast  DECIMAL(12,2) COMMENT 'AI需求预测量',
    ai_forecast_period  VARCHAR(16)   COMMENT '预测周期: 7d/14d/30d',
    ai_confidence       DECIMAL(5,2)  COMMENT 'AI预测置信度',
    ai_reasoning        TEXT          COMMENT 'AI推理过程(可解释性)',

    -- 状态
    plan_status         VARCHAR(16)   DEFAULT 'draft' COMMENT '状态: draft/approved/ordered/completed/cancelled',
    approved_by          VARCHAR(32)   COMMENT '审批人',

    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_material (material_id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_status (plan_status),
    INDEX idx_urgency (urgency)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='协同补货计划表(AI预测+自动触发)';


-- ============================================================
-- 表12: supplier_ai_compliance — AI合规监控表
-- ============================================================
CREATE TABLE supplier_ai_compliance (
    compliance_id       BIGINT        PRIMARY KEY AUTO_INCREMENT,
    supplier_id         VARCHAR(32)   NOT NULL COMMENT '供应商ID',
    compliance_type    VARCHAR(32)   NOT NULL COMMENT '合规类型: 资质/招标/合同/税务/数据/环保/反垄断',

    -- 法律对标
    law_reference       VARCHAR(128)  NOT NULL COMMENT '对标法律(招标投标法/合同法/数据安全法等)',
    law_article         VARCHAR(32)   COMMENT '具体条款',

    -- 合规检查
    check_item          VARCHAR(256)  NOT NULL COMMENT '检查项',
    check_result        VARCHAR(16)   NOT NULL COMMENT '检查结果: passed/failed/warning/na',
    check_detail        TEXT          COMMENT '检查详情',
    check_date          DATETIME      NOT NULL COMMENT '检查时间',

    -- AI 合规
    ai_check_status     VARCHAR(16)   DEFAULT 'pending' COMMENT 'AI审查状态: pending/compliant/violation/review',
    ai_risk_level       VARCHAR(16)   COMMENT 'AI风险等级: 低/中/高/极高',
    ai_analysis         TEXT          COMMENT 'AI合规分析',
    ai_recommendation   TEXT          COMMENT 'AI整改建议',
    ai_confidence       DECIMAL(5,2)  COMMENT 'AI置信度',

    -- 整改
    remediation_plan    TEXT          COMMENT '整改计划',
    remediation_deadline DATE         COMMENT '整改截止日期',
    remediation_status  VARCHAR(16)   DEFAULT 'pending' COMMENT '整改状态: pending/in_progress/completed/overdue',
    remediated_at       DATETIME      COMMENT '整改完成时间',

    -- 区块链存证
    blockchain_hash     VARCHAR(128)  COMMENT '合规记录区块链哈希',

    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_supplier (supplier_id),
    INDEX idx_type (compliance_type),
    INDEX idx_result (check_result),
    INDEX idx_law (law_reference),
    INDEX idx_ai_status (ai_check_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商AI合规监控表(对标7部法律法规)';


-- ============================================================
-- 表关系总结
-- ============================================================
-- suppliers (1) ──→ (N) supplier_profiles         供应商画像
-- suppliers (1) ──→ (N) supplier_ratings           供应商评级
-- suppliers (1) ──→ (N) procurement_orders         采购订单
-- suppliers (1) ──→ (N) procurement_contracts      采购合同
-- suppliers (1) ──→ (N) incoming_inspections       来料检验
-- suppliers (1) ──→ (N) supply_chain_risks         风险预警
-- suppliers (1) ──→ (N) supplier_ai_compliance     AI合规
--
-- raw_materials (1) ──→ (N) procurement_orders     采购订单
-- raw_materials (1) ──→ (N) price_history          价格历史
-- raw_materials (1) ──→ (N) cost_calculations      成本核算
-- raw_materials (1) ──→ (N) replenishment_plans     补货计划
-- raw_materials (1) ──→ (N) incoming_inspections    来料检验
--
-- procurement_orders (1) ──→ (1) procurement_contracts   合同
-- procurement_orders (1) ──→ (N) incoming_inspections   检验


-- ============================================================
-- 索引统计
-- ============================================================
-- 12 张表 · 38 个索引 · 7 部法律对标 · 3 个AI字段维度
-- AI 合规表对标法律:
--   1. 招标投标法     — 公开/公平/公正采购
--   2. 政府采购法     — 非招标采购规范
--   3. 合同法(民法典)  — 合同条款合规
--   4. 反垄断法       — 供应商垄断风险
--   5. 数据安全法     — 供应商数据安全
--   6. 个人信息保护法  — 联系人信息保护
--   7. 税收征收管理法  — 税务合规
--
-- AI 能力覆盖映射:
--   AI智能供应商画像 → supplier_profiles (200+标签)
--   AI智能寻源       → procurement_orders.ai_match_score
--   AI智能比价       → price_history (移动均价+趋势)
--   AI智能采购预测   → replenishment_plans.ai_demand_forecast
--   AI智能合同管理   → procurement_contracts.ai_review_status
--   AI智能来料检验   → incoming_inspections (视觉AI+GC-MS)
--   AI智能供应商评级 → supplier_ratings (6维+AI推荐)
--   AI智能风险预警   → supply_chain_risks (AI自动检测)
--   AI智能成本核算   → cost_calculations (AI优化建议)
--   AI智能协同补货   → replenishment_plans (AI触发+推理)
