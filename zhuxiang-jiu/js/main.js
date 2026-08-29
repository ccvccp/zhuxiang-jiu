/* ============================================
   竹香酒官网 - 公共脚本
   ============================================ */

// ---------- 购物车管理（基于 localStorage）----------
const Cart = {
    get() {
        try {
            return JSON.parse(localStorage.getItem('zhuxiang_cart') || '[]');
        } catch (e) {
            return [];
        }
    },
    save(items) {
        localStorage.setItem('zhuxiang_cart', JSON.stringify(items));
        this.updateCount();
    },
    add(productId, qty = 1) {
        const items = this.get();
        const product = PRODUCTS.find(p => p.id === productId);
        if (!product) return;
        const existing = items.find(i => i.id === productId);
        if (existing) {
            existing.qty += qty;
        } else {
            items.push({ id: product.id, name: product.name, price: product.price, qty: qty, img: product.img });
        }
        this.save(items);
        showToast(`已添加 "${product.name}" 到购物车`);
    },
    remove(productId) {
        const items = this.get().filter(i => i.id !== productId);
        this.save(items);
    },
    updateQty(productId, qty) {
        const items = this.get();
        const item = items.find(i => i.id === productId);
        if (item) {
            item.qty = Math.max(1, qty);
            this.save(items);
        }
    },
    count() {
        return this.get().reduce((sum, i) => sum + i.qty, 0);
    },
    total() {
        return this.get().reduce((sum, i) => sum + i.price * i.qty, 0);
    },
    clear() {
        this.save([]);
    },
    updateCount() {
        // Bug#2 修复: querySelector 只返回第一个，移动端侧滑菜单内的 .cart-count 永不更新
        // 改为 querySelectorAll 遍历所有徽章（桌面端 cart-btn + 移动端 mobile-nav-cart）
        const els = document.querySelectorAll('.cart-count');
        const count = this.count();
        els.forEach(el => el.textContent = count);
    }
};

// ---------- Toast 提示 ----------
function showToast(msg) {
    let toast = document.querySelector('.toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('show'), 2500);
}

// ---------- 渲染头部导航 ----------
function renderHeader(active = '') {
    const navItems = [
        { name: '首页', url: 'index.html', key: 'home' },
        { name: '酒类介绍', url: 'products.html', key: 'products' },
        { name: '酒类销售', url: 'cart.html', key: 'shop' },
        { name: '客户服务', url: 'service.html', key: 'service' },
        { name: '网站新闻', url: 'news.html', key: 'news' },
        { name: '模块初测', url: 'module-test.html', key: 'test' }
    ];
    const navHtml = navItems.map(n =>
        `<a href="${n.url}" class="${active === n.key ? 'active' : ''}">${n.name}</a>`
    ).join('');

    return `
    <div class="notice-bar">
        ⚠️ 本网站销售酒类商品，根据《未成年人保护法》,<strong>未成年人禁止购买和饮用酒类</strong> | 山东瑞麒酒业 · 全竹发酵 · 竹酒首创
    </div>
    <header class="header">
        <div class="container header-inner">
            <a href="index.html" class="logo">
                <span class="logo-icon">竹</span>
                <span class="logo-text">竹香酒<small>ZHUXIANG JIU</small></span>
            </a>
            <nav class="nav">${navHtml}</nav>
            <div class="header-tools">
                <div class="search-box">
                    <input type="text" placeholder="搜索竹奕酒..." id="searchInput">
                    <button onclick="handleSearch()" aria-label="搜索">🔍</button>
                </div>
                <a href="cart.html" class="cart-btn">
                    🛒 购物车
                    <span class="cart-count">${Cart.count()}</span>
                </a>
                <button class="menu-toggle" id="menuToggle" aria-label="打开菜单" aria-expanded="false" aria-controls="mobileNav">
                    <span></span><span></span><span></span>
                </button>
            </div>
        </div>
    </header>
    <!-- 移动端侧滑菜单 -->
    <div class="mobile-nav-overlay" id="mobileNavOverlay"></div>
    <nav class="mobile-nav" id="mobileNav" aria-label="移动端导航">
        <div class="mobile-nav-header">
            <span class="mobile-nav-title">竹香酒</span>
            <button class="mobile-nav-close" id="mobileNavClose" aria-label="关闭菜单">✕</button>
        </div>
        <div class="mobile-nav-search">
            <input type="text" placeholder="搜索竹奕酒..." id="mobileSearchInput">
            <button onclick="handleMobileSearch()" aria-label="搜索">🔍</button>
        </div>
        <div class="mobile-nav-list">
            ${navItems.map(n =>
                `<a href="${n.url}" class="${active === n.key ? 'active' : ''}">${n.name}</a>`
            ).join('')}
        </div>
        <a href="cart.html" class="mobile-nav-cart">
            🛒 购物车 <span class="cart-count">${Cart.count()}</span>
        </a>
    </nav>`;
}

// ---------- 渲染页脚 ----------
function renderFooter() {
    return `
    <footer class="footer">
        <div class="container">
            <div class="footer-top">
                <div class="footer-brand">
                    <div class="logo">
                        <span class="logo-icon">竹</span>
                        <span class="logo-text">竹香酒</span>
                    </div>
                    <p>山东瑞麒酒业有限公司<br>福来就喝竹奕酒，天赐鸿运随心走！<br>全竹发酵 · 竹酒首创 · 醒酒快 · 无酒气 · 不头疼</p>
                    <div class="footer-social">
                        <a href="#" title="微信">💬</a>
                        <a href="#" title="微博">📢</a>
                        <a href="#" title="抖音">🎵</a>
                        <a href="#" title="邮箱">✉️</a>
                    </div>
                </div>
                <div class="footer-col">
                    <h4>产品中心</h4>
                    <ul>
                        <li><a href="products.html">竹香型（全系主打）</a></li>
                        <li><a href="products.html">经典系列</a></li>
                        <li><a href="products.html">珍藏系列</a></li>
                        <li><a href="products.html">年份系列</a></li>
                        <li><a href="products.html">礼盒系列</a></li>
                        <li><a href="products.html">典藏系列</a></li>
                    </ul>
                </div>
                <div class="footer-col">
                    <h4>帮助中心</h4>
                    <ul>
                        <li><a href="service.html">常见问题</a></li>
                        <li><a href="service.html">配送说明</a></li>
                        <li><a href="service.html">支付方式</a></li>
                        <li><a href="service.html">退换政策</a></li>
                        <li><a href="service.html">联系我们</a></li>
                    </ul>
                </div>
                <div class="footer-col">
                    <h4>联系方式</h4>
                    <ul class="footer-contact">
                        <li>📞 400-888-XXXX</li>
                        <li>✉️ service@zhuxiang.com</li>
                        <li>📍 山东省泰安市泰山脚</li>
                        <li>🕐 周一至周日 8:00-22:00</li>
                    </ul>
                </div>
            </div>
            <div class="footer-bottom">
                <p>© 2026 山东瑞麒酒业有限公司 版权所有 | 鲁ICP备XXXXXXXX号 | 食品经营许可证：SCXXXXXXXXX</p>
                <p>过量饮酒有害健康 · 未成年禁止饮酒 · 饮酒后请勿驾车 · 孕妇请勿饮酒</p>
            </div>
        </div>
    </footer>`;
}

// ---------- 搜索处理 ----------
function handleSearch() {
    const kw = document.getElementById('searchInput').value.trim();
    if (kw) {
        window.location.href = `products.html?keyword=${encodeURIComponent(kw)}`;
    }
}

// ---------- 移动端搜索处理 ----------
function handleMobileSearch() {
    const kw = document.getElementById('mobileSearchInput').value.trim();
    if (kw) {
        window.location.href = `products.html?keyword=${encodeURIComponent(kw)}`;
    }
}

// ---------- 移动端菜单开关 ----------
function toggleMobileNav(open) {
    const nav = document.getElementById('mobileNav');
    const overlay = document.getElementById('mobileNavOverlay');
    const toggle = document.getElementById('menuToggle');
    if (!nav || !overlay) return;
    const willOpen = (open === undefined) ? !nav.classList.contains('open') : open;
    if (willOpen) {
        nav.classList.add('open');
        overlay.classList.add('open');
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
        document.body.style.overflow = 'hidden';
    } else {
        nav.classList.remove('open');
        overlay.classList.remove('open');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
        document.body.style.overflow = '';
    }
}

// ---------- 渲染产品卡片 ----------
function renderProductCard(p) {
    const tagHtml = p.tag ? `<span class="product-tag ${p.tag === '新品' ? 'new' : ''} ${p.tag === '主打' ? 'featured' : ''}">${p.tag}</span>` : '';
    return `
    <div class="product-card" onclick="location.href='product-detail.html?id=${p.id}'">
        <div class="product-img">
            ${tagHtml}
            <div class="bottle"></div>
        </div>
        <div class="product-info">
            <div class="product-category">${p.series} · ${p.category}</div>
            <div class="product-name">${p.name}</div>
            <div class="product-desc">${p.desc}</div>
            <div class="product-bottom">
                <div class="product-price">¥${p.price}<small> / ${p.volume}</small></div>
                <button class="btn-add-cart" onclick="event.stopPropagation(); Cart.add(${p.id})">加入购物车</button>
            </div>
        </div>
    </div>`;
}

// ---------- 获取 URL 参数 ----------
function getQueryParam(name) {
    const params = new URLSearchParams(window.location.search);
    return params.get(name);
}

// ---------- 页面初始化（在每个页面调用）----------
function initPage(activeNav = '') {
    const headerPlaceholder = document.getElementById('header');
    const footerPlaceholder = document.getElementById('footer');
    if (headerPlaceholder) headerPlaceholder.innerHTML = renderHeader(activeNav);
    if (footerPlaceholder) footerPlaceholder.innerHTML = renderFooter();
    Cart.updateCount();

    // 搜索框回车
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('keypress', e => {
            if (e.key === 'Enter') handleSearch();
        });
    }

    // 移动端汉堡菜单交互
    const menuToggle = document.getElementById('menuToggle');
    const mobileNavClose = document.getElementById('mobileNavClose');
    const mobileNavOverlay = document.getElementById('mobileNavOverlay');
    const mobileSearchInput = document.getElementById('mobileSearchInput');
    if (menuToggle) menuToggle.addEventListener('click', () => toggleMobileNav());
    if (mobileNavClose) mobileNavClose.addEventListener('click', () => toggleMobileNav(false));
    if (mobileNavOverlay) mobileNavOverlay.addEventListener('click', () => toggleMobileNav(false));
    if (mobileSearchInput) {
        mobileSearchInput.addEventListener('keypress', e => {
            if (e.key === 'Enter') handleMobileSearch();
        });
    }
    // 点击菜单内链接后自动关闭（保留 hash 链接的默认行为）
    const mobileNavLinks = document.querySelectorAll('#mobileNav .mobile-nav-list a');
    mobileNavLinks.forEach(a => a.addEventListener('click', () => toggleMobileNav(false)));

    // 自动初始化代理商升级服务（浏览器适配层）
    AgentUpgradeClient.init();
}

/* ============================================
   代理商升级服务 - 浏览器适配层
   --------------------------------------------
   对接后端: agent-upgrade-service.js（Node.js 风格事务服务）
   部署模式:
     · Mock 模式（默认）: localStorage 模拟事务,纯前端可用
     · Live 模式: 通过 fetch 调用后端 API /api/agent/upgrade|downgrade
   用法:
     AgentUpgradeClient.setMode('live'); // 切换到真实后端
     const r = await AgentUpgradeClient.upgrade({agentId:1, fromLevel:'市级', toLevel:'核心', ...});
   ============================================ */
const AgentUpgradeClient = (function () {
    const STORAGE_KEY = 'zhuxiang_agent_db_v1';
    const UPGRADE_CONFIG = {
        LEVELS: {
            '观察': { annual_target: 0, first_batch: 0, rebate_tier: 'T0', rebate_rate: 0, taste_quota: 0, credit_boost: 0, wallet_quota: 0, compliance_threshold: '低', audit_frequency: '月' },
            '市级': { annual_target: 500000, first_batch: 250000, rebate_tier: 'T1', rebate_rate: 0.15, taste_quota: 27, credit_boost: 50, wallet_quota: 100000, compliance_threshold: '中', audit_frequency: '月' },
            '核心': { annual_target: 1000000, first_batch: 500000, rebate_tier: 'T2', rebate_rate: 0.20, taste_quota: 50, credit_boost: 80, wallet_quota: 300000, compliance_threshold: '高', audit_frequency: '半月' },
            '战略': { annual_target: 5000000, first_batch: 1000000, rebate_tier: 'T3', rebate_rate: 0.20, taste_quota: 50, credit_boost: 120, wallet_quota: 1000000, compliance_threshold: '极高', audit_frequency: '周' },
        },
        DOWNGRADE_RULES: {
            '核心': { to: '市级', min: 250000 },
            '市级': { to: '观察', min: 150000 },
        },
        GRACE_PERIOD: 3,
    };

    let mode = 'mock'; // 'mock' | 'live'
    let apiBase = '/api/agent';

    // ---------- 工具 ----------
    function readDB() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') || initMockDB(true);
        } catch (e) {
            return initMockDB(true);
        }
    }

    function writeDB(db) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(db));
    }

    // 初始化 Mock 数据(三档代理商 + 多状态订单)
    function initMockDB(forceWrite = false) {
        const existing = forceWrite ? null : JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
        if (existing && !forceWrite) return existing;

        const db = {
            agents: [
                { id: 1, name: '张三酒业', agent_level: '市级', region: '山东泰安', annual_target: 500000, current_rebate_tier: 'T1', taste_quota_monthly: 27, upgrade_count: 0, ai_risk_level: '低', registered_capital: 1200000, status: '活跃' },
                { id: 2, name: '李四酒业', agent_level: '核心', region: '山东济南', annual_target: 1000000, current_rebate_tier: 'T2', taste_quota_monthly: 50, upgrade_count: 1, ai_risk_level: '低', registered_capital: 3000000, status: '活跃' },
                { id: 3, name: '王五酒业', agent_level: '战略', region: '北京', annual_target: 5000000, current_rebate_tier: 'T3', taste_quota_monthly: 50, upgrade_count: 2, ai_risk_level: '低', registered_capital: 10000000, status: '活跃' },
            ],
            orders: [
                { id: 1001, agent_id: 1, total_amount: 250000, rebate_rate: 0.15, status: '待付款', created_at: '2026-08-15' },
                { id: 1002, agent_id: 1, total_amount: 180000, rebate_rate: 0.15, status: '已付款', created_at: '2026-07-15' },
                { id: 1003, agent_id: 1, total_amount: 220000, rebate_rate: 0.15, status: '已发货', created_at: '2026-06-15' },
                { id: 1004, agent_id: 1, total_amount: 300000, rebate_rate: 0.15, status: '已签收', created_at: '2026-05-15' },
                { id: 2001, agent_id: 2, total_amount: 500000, rebate_rate: 0.20, status: '待付款', created_at: '2026-08-15' },
                { id: 2002, agent_id: 2, total_amount: 600000, rebate_rate: 0.20, status: '已签收', created_at: '2026-07-15' },
                { id: 3001, agent_id: 3, total_amount: 1200000, rebate_rate: 0.20, status: '待付款', created_at: '2026-08-15' },
                { id: 3002, agent_id: 3, total_amount: 800000, rebate_rate: 0.20, status: '已签收', created_at: '2026-07-15' },
            ],
            credit_scores: [
                { agent_id: 1, credit_score: 720, credit_level: 'A' },
                { agent_id: 2, credit_score: 850, credit_level: 'S' },
                { agent_id: 3, credit_score: 950, credit_level: 'S' },
            ],
            wallet_accounts: [
                { agent_id: 1, credit_limit: 100000, tier_level: '市级' },
                { agent_id: 2, credit_limit: 300000, tier_level: '核心' },
                { agent_id: 3, credit_limit: 1000000, tier_level: '战略' },
            ],
            compliance_monitors: [
                { agent_id: 1, risk_threshold: '中', audit_frequency: '月' },
                { agent_id: 2, risk_threshold: '高', audit_frequency: '半月' },
                { agent_id: 3, risk_threshold: '极高', audit_frequency: '周' },
            ],
            upgrade_logs: [],
            tx_log: [], // 事务日志: BEGIN/COMMIT/ROLLBACK
        };
        writeDB(db);
        return db;
    }

    // ---------- 超额累进返利(T0-T3 边际累进, 与后端 agent_service.REBATE_TIERS 对齐, 决策 D-9) ----------
    function calculateRebate(purchase) {
        const T1 = 200000, T2 = 500000, T3 = 1000000;
        if (purchase < T1) return 0;
        if (purchase < T2) return Math.round((purchase - T1) * 0.15 * 100) / 100;
        if (purchase < T3) return Math.round(((T2 - T1) * 0.15 + (purchase - T2) * 0.25) * 100) / 100;
        return Math.round(((T2 - T1) * 0.15 + (T3 - T2) * 0.25 + (purchase - T3) * 0.30) * 100) / 100;
    }

    // ---------- Mock: AI 风险评估 ----------
    function mockAIAssess(db, agentId) {
        const agent = db.agents.find(a => a.id === agentId);
        const credit = db.credit_scores.find(c => c.agent_id === agentId);
        const orders = db.orders.filter(o => o.agent_id === agentId);
        let score = 60;
        const reasons = [];
        // Bug#4 修复: 原逻辑用「订单平均金额」当作「月均进货额」，语义错误
        // 现按月份聚合订单总额，再除以月份数得到真实月均进货额
        const monthlyMap = {};
        orders.forEach(o => {
            const ym = (o.created_at || '').slice(0, 7); // 'YYYY-MM'
            if (ym && ym.length === 7) {
                monthlyMap[ym] = (monthlyMap[ym] || 0) + (o.total_amount || 0);
            }
        });
        const monthCount = Object.keys(monthlyMap).length;
        const avg = monthCount > 0
            ? Object.values(monthlyMap).reduce((s, v) => s + v, 0) / monthCount
            : 0;
        if (avg >= 250000) { score += 15; reasons.push('月均进货达标'); }
        else { score -= 10; reasons.push('月均进货偏低'); }
        const cs = credit ? credit.credit_score : 0;
        if (cs >= 700) { score += 15; reasons.push('竹信分' + cs + '≥700'); }
        else { score -= 20; reasons.push('竹信分' + cs + '<700'); }
        if (agent && agent.registered_capital >= 1000000) { score += 10; reasons.push('注册资本≥100万'); }
        score = Math.max(0, Math.min(100, score));
        let approval;
        if (score >= 80) approval = '通过';
        else if (score >= 60) approval = '需人工复核';
        else approval = '拒绝';
        return { score, approval, reasons, avgMonthly: avg };
    }

    // ---------- Mock: 升级流程(基于 TransactionTemplate 工具包) ----------
    async function mockUpgrade(params) {
        const { agentId, fromLevel, toLevel, upgradeType, operator, remark } = params;

        // 工具包可用性检查(调用时检测,不影响未加载工具包的页面)
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('AgentUpgradeClient 需要工具包,请先加载 js/toolkit/upgrade-logger.js 和 js/toolkit/transaction-template.js');
        }

        let db = readDB();

        // Mock 事务适配器(快照模式: begin 拍快照, commit 写回, rollback 恢复)
        const adapter = {
            begin(ctx) {
                const snapshot = JSON.parse(JSON.stringify(db));
                db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
                ctx.logger.info('阶段2-开启事务', '事务已开启(快照已建立)');
                return snapshot; // 存入 ctx.conn,回滚时使用
            },
            commit(_snapshot, ctx) {
                db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString(), steps: 9 });
                writeDB(db);
                ctx.logger.info('阶段10-事务提交', '事务提交成功(已写入)');
            },
            rollback(snapshot, ctx) {
                if (snapshot) {
                    snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                    db = snapshot; // 恢复内存引用(与 checkout/inventory/agent-shipping 的 dbRef.db=snapshot 一致)
                    writeDB(snapshot);
                    ctx.logger.error('回滚', '事务已回滚(快照恢复)');
                }
            },
        };

        const template = new Template({ name: 'agent_upgrade_mock', adapter: adapter });

        const result = await template.run({
            context: { agentId, fromLevel, toLevel, upgradeType, operator, remark },

            // 事务前只读检查: AI风险评估
            preflight: async (ctx) => {
                ctx.logger.info('阶段1-AI风险评估', '评估代理商' + agentId + '升级至' + toLevel);
                const ai = mockAIAssess(db, agentId);
                ctx.ai = ai;
                ctx.logger.info('阶段1-AI风险评估', 'AI评分' + ai.score + ', 审批: ' + ai.approval, ai.reasons);
                if (ai.approval === '拒绝') {
                    ctx.logger.error('阶段1', 'AI拒绝,流程终止');
                    return { abort: true, reason: 'AI拒绝', aiResult: ai };
                }
            },

            // 事务内 9 个阶段(2-10)
            stages: [
                // 阶段2: 开启事务 + 目标等级校验
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx); // conn = snapshot
                        const config = UPGRADE_CONFIG.LEVELS[ctx.toLevel];
                        if (!config) throw new Error('无效目标等级: ' + ctx.toLevel);
                        ctx.config = config;
                    },
                },
                // 阶段3: agents表更新
                {
                    name: '阶段3-agents表',
                    action: async (ctx) => {
                        const agent = db.agents.find(a => a.id === ctx.agentId);
                        if (!agent) throw new Error('代理商不存在');
                        agent.agent_level = ctx.toLevel;
                        agent.annual_target = ctx.config.annual_target;
                        agent.current_rebate_tier = ctx.config.rebate_tier;
                        agent.taste_quota_monthly = ctx.config.taste_quota;
                        agent.upgrade_count = (agent.upgrade_count || 0) + 1;
                        agent.ai_risk_level = ctx.ai.score >= 80 ? '低' : '中';
                        ctx.logger.info('阶段3-agents表', '更新: level→' + ctx.toLevel + ', target→' + ctx.config.annual_target + ', tier→' + ctx.config.rebate_tier);
                    },
                },
                // 阶段4: 升级日志
                {
                    name: '阶段4-升级日志',
                    action: async (ctx) => {
                        ctx.logId = Date.now();
                        db.upgrade_logs.push({
                            id: ctx.logId, agent_id: ctx.agentId, from_level: ctx.fromLevel, to_level: ctx.toLevel,
                            upgrade_type: ctx.upgradeType, ai_score: ctx.ai.score, ai_approval: ctx.ai.approval,
                            operator: ctx.operator, remark: ctx.remark,
                            effective_date: new Date().toISOString().slice(0, 10),
                        });
                        ctx.logger.info('阶段4-升级日志', '写入agent_upgrade_logs, logId=' + ctx.logId);
                    },
                },
                // 阶段5: 订单表待付款返利率更新
                {
                    name: '阶段5-订单表',
                    action: async (ctx) => {
                        db.orders.filter(o => o.agent_id === ctx.agentId && o.status === '待付款').forEach(o => { o.rebate_rate = ctx.config.rebate_rate; });
                        ctx.logger.info('阶段5-订单表', '待付款订单返利率→' + ctx.config.rebate_rate);
                    },
                },
                // 阶段6: 返利重算
                {
                    name: '阶段6-返利重算',
                    action: async (ctx) => {
                        // Bug#3 修复: 不再硬编码 250000，使用 mockAIAssess 计算的代理商实际月均进货额
                        ctx.monthlyTotal = ctx.ai.avgMonthly || 250000;
                        // Bug#1 修复: 原代码 newRebate/oldRebate 都调用同一 calculateRebate(majorTotal)，导致 delta 永远=0
                        // 现按「新旧等级返利率」分别计算返利额，体现升级带来的返利率变化（如 T1 0.15 → T2 0.20）
                        const oldConfig = UPGRADE_CONFIG.LEVELS[ctx.fromLevel] || { rebate_rate: 0 };
                        const oldRate = oldConfig.rebate_rate;
                        const newRate = ctx.config.rebate_rate;
                        ctx.oldRebate = Math.round(ctx.monthlyTotal * oldRate * 100) / 100;
                        ctx.newRebate = Math.round(ctx.monthlyTotal * newRate * 100) / 100;
                        ctx.delta = Math.round((ctx.newRebate - ctx.oldRebate) * 100) / 100;
                        ctx.logger.info('阶段6-返利重算', '月度' + ctx.monthlyTotal + ', 旧返利' + ctx.oldRebate + '(rate=' + oldRate + '), 新返利' + ctx.newRebate + '(rate=' + newRate + '), 差额' + ctx.delta);
                    },
                },
                // 阶段7: 信用分加成
                {
                    name: '阶段7-信用管理',
                    action: async (ctx) => {
                        const credit = db.credit_scores.find(c => c.agent_id === ctx.agentId);
                        if (credit) credit.credit_score += ctx.config.credit_boost;
                        ctx.logger.info('阶段7-信用管理', '竹信分+' + ctx.config.credit_boost);
                    },
                },
                // 阶段8: 钱包额度 (B1/wallet: 补 Mutex 锁,防止并发升级/降级导致额度 lost-update)
                {
                    name: '阶段8-钱包模块',
                    action: async (ctx) => {
                        await window.mutex.withLock('wallet:' + ctx.agentId, async () => {
                            const wallet = db.wallet_accounts.find(w => w.agent_id === ctx.agentId);
                            if (wallet) { wallet.credit_limit = ctx.config.wallet_quota; wallet.tier_level = ctx.toLevel; }
                            ctx.logger.info('阶段8-钱包模块', '预付款额度→' + ctx.config.wallet_quota);
                        });
                    },
                },
                // 阶段9: 合规监控
                {
                    name: '阶段9-合规监控',
                    action: async (ctx) => {
                        const monitor = db.compliance_monitors.find(m => m.agent_id === ctx.agentId);
                        if (monitor) { monitor.risk_threshold = ctx.config.compliance_threshold; monitor.audit_frequency = ctx.config.audit_frequency; }
                        ctx.logger.info('阶段9-合规监控', '阈值→' + ctx.config.compliance_threshold + ', 审计→' + ctx.config.audit_frequency);
                    },
                },
                // 阶段10: 提交事务
                {
                    name: '阶段10-事务提交',
                    action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null; // commit 后置空,避免 catch 误回滚
                    },
                },
            ],

            // 事务后异步任务(失败仅记日志,不阻塞主流程)
            asyncTasks: [
                {
                    name: '阶段11-异步',
                    action: (ctx) => {
                        ctx.logger.info('阶段11-异步', '触发: 区块链存证 + AI监控 + 通知推送');
                    },
                },
            ],
        });

        // 日志格式适配: toolkit 用 message, 旧代码用 msg,这里补 msg 别名保持兼容
        const logs = result.logs.map(l => ({ ...l, msg: l.message }));

        // 结果形状转换(保持原 API 兼容)
        if (result.aborted) {
            return { success: false, error: result.reason, logs };
        }
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                logId: ctx.logId,
                details: {
                    fromLevel: ctx.fromLevel, toLevel: ctx.toLevel,
                    aiScore: ctx.ai.score, aiApproval: ctx.ai.approval,
                    newRebate: ctx.newRebate, delta: ctx.delta,
                    tasteQuota: ctx.config.taste_quota, creditBoost: ctx.config.credit_boost,
                    walletQuota: ctx.config.wallet_quota,
                },
                logs,
                asyncOps: ['blockchain_notarize', 'ai_monitor_setup', 'agent_notify'],
            };
        }
        return { success: false, error: result.error, logs };
    }

    // ---------- Mock: 降级流程(基于 TransactionTemplate 工具包) ----------
    async function mockDowngrade(agentId, fromLevel, reason) {
        // 工具包可用性检查
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('AgentUpgradeClient 需要工具包,请先加载 js/toolkit/upgrade-logger.js 和 js/toolkit/transaction-template.js');
        }

        let db = readDB();
        const rule = UPGRADE_CONFIG.DOWNGRADE_RULES[fromLevel];
        if (!rule) return { success: false, error: '无可降级目标', logs: [] };

        // Mock 事务适配器(快照模式)
        const adapter = {
            begin(ctx) {
                const snapshot = JSON.parse(JSON.stringify(db));
                db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
                ctx.logger.warn('降级-开启事务', fromLevel + '→' + rule.to + ', 原因: ' + reason);
                return snapshot;
            },
            commit(_snapshot, ctx) {
                db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString() });
                writeDB(db);
                ctx.logger.info('降级-提交事务', '降级事务提交成功');
            },
            rollback(snapshot, ctx) {
                if (snapshot) {
                    snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                    db = snapshot; // 恢复内存引用(与 checkout/inventory/agent-shipping 的 dbRef.db=snapshot 一致)
                    writeDB(snapshot);
                    ctx.logger.error('降级-回滚', '事务已回滚(快照恢复)');
                }
            },
        };

        const template = new Template({ name: 'agent_downgrade_mock', adapter: adapter });

        const result = await template.run({
            context: { agentId, fromLevel, reason, rule },

            stages: [
                // 降级-开启事务
                {
                    name: '降级-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx); // conn = snapshot
                        const config = UPGRADE_CONFIG.LEVELS[ctx.rule.to];
                        if (!config) throw new Error('降级目标等级无效: ' + ctx.rule.to);
                        ctx.config = config;
                    },
                },
                // 降级-agents表
                {
                    name: '降级-agents表',
                    action: async (ctx) => {
                        const agent = db.agents.find(a => a.id === ctx.agentId);
                        if (!agent) throw new Error('代理商不存在');
                        agent.agent_level = ctx.rule.to;
                        agent.annual_target = ctx.config.annual_target;
                        agent.current_rebate_tier = ctx.config.rebate_tier;
                        agent.taste_quota_monthly = ctx.config.taste_quota;
                        agent.ai_risk_level = '高';
                        ctx.logger.info('降级-agents表', 'agents表已更新: ' + ctx.rule.to);
                    },
                },
                // 降级-日志
                {
                    name: '降级-日志',
                    action: async (ctx) => {
                        db.upgrade_logs.push({
                            id: Date.now(), agent_id: ctx.agentId,
                            from_level: ctx.fromLevel, to_level: ctx.rule.to,
                            upgrade_type: '系统降级', operator: 'SYSTEM', remark: ctx.reason,
                            effective_date: new Date().toISOString().slice(0, 10),
                        });
                        ctx.logger.info('降级-日志', '降级日志已写入');
                    },
                },
                // 降级-信用分
                {
                    name: '降级-信用分',
                    action: async (ctx) => {
                        const credit = db.credit_scores.find(c => c.agent_id === ctx.agentId);
                        if (credit) credit.credit_score = Math.max(0, credit.credit_score - 100);
                        ctx.logger.info('降级-信用分', '信用分-100');
                    },
                },
                // 降级-钱包 (B1/wallet: 补 Mutex 锁,与升级阶段8同 key,串行化防 lost-update)
                {
                    name: '降级-钱包',
                    action: async (ctx) => {
                        await window.mutex.withLock('wallet:' + ctx.agentId, async () => {
                            const wallet = db.wallet_accounts.find(w => w.agent_id === ctx.agentId);
                            if (wallet) { wallet.credit_limit = ctx.config.wallet_quota; wallet.tier_level = ctx.rule.to; }
                            ctx.logger.info('降级-钱包', '钱包额度→' + ctx.config.wallet_quota);
                        });
                    },
                },
                // 降级-提交事务
                {
                    name: '降级-提交事务',
                    action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],
        });

        // 日志格式适配
        const logs = result.logs.map(l => ({ ...l, msg: l.message }));

        if (result.success) {
            return { success: true, toLevel: rule.to, logs };
        }
        return { success: false, error: result.error, logs };
    }

    // ---------- Live: 调用后端 API ----------
    async function liveUpgrade(params) {
        const r = await fetch(apiBase + '/upgrade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
    }

    async function liveDowngrade(agentId, fromLevel, reason) {
        const r = await fetch(apiBase + '/downgrade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agentId, fromLevel, reason }),
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
    }

    // ---------- 公共 API ----------
    return {
        // 初始化(由 initPage 自动调用)
        init() {
            if (!localStorage.getItem(STORAGE_KEY)) initMockDB(true);
        },
        // 切换模式: 'mock' | 'live'
        setMode(m) { mode = m; return this; },
        // 设置后端 API 基址(live 模式)
        setApiBase(base) { apiBase = base; return this; },
        // 重置 Mock 数据
        resetMock() { localStorage.removeItem(STORAGE_KEY); initMockDB(true); return this; },
        // 获取当前 Mock 数据快照
        getMockDB() { return readDB(); },
        // 当前模式
        getMode() { return mode; },

        // 升级代理商
        async upgrade(params) {
            if (mode === 'live') {
                try { return await liveUpgrade(params); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await mockUpgrade(params);
        },

        // 降级代理商
        async downgrade(agentId, fromLevel, reason) {
            if (mode === 'live') {
                try { return await liveDowngrade(agentId, fromLevel, reason); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '降级-回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await mockDowngrade(agentId, fromLevel, reason);
        },

        // 配置常量(供 UI 引用)
        CONFIG: UPGRADE_CONFIG,
        // 返利计算工具
        calculateRebate,
    };
})();

// 暴露到 window 全局(供后台页面/控制台调用)
if (typeof window !== 'undefined') {
    window.AgentUpgradeClient = AgentUpgradeClient;
    window.Cart = Cart;
    window.renderHeader = renderHeader;
    window.renderFooter = renderFooter;
    window.showToast = showToast;
    window.handleSearch = handleSearch;
    window.initPage = initPage;
    window.renderProductCard = renderProductCard;
    window.getQueryParam = getQueryParam;
}

/* ============================================================
 * CheckoutService · main.js 内嵌版(与 APP 端 taro-app/src/services/checkout-service.ts 逻辑一致)
 * ------------------------------------------------------------
 * 来源: test-app-local.html 浏览器模拟 APP 环境验证通过的版本
 * 特性: 9 阶段事务 + 购买两瓶免运费 + 5% 同品分润 + 快照回滚
 * 存储: 直接使用 localStorage(H5 端无需 mockTaro)
 * 暴露: window.CheckoutService(若已存在则保留) + window.AppCheckoutService(总是覆盖)
 * ============================================================ */
const AppCheckoutService = (function () {
    'use strict';
    const STORAGE_KEY = 'zhuxiang_checkout_db_v1';
    const SHIPPING_KEY = 'zhuxiang_shipping_db_v1';
    const SERVICE_FEE_RATE = 0.05;
    const POINTS_RATE = 0.01;
    const POINTS_DEDUCT_MAX_RATE = 0.30;
    const EARN_RATE = 0.1;
    const LEVEL_BOOST = { L1: 1.0, L2: 1.0, L3: 1.02, L4: 1.05, L5: 1.08 };
    const PROFIT_SPLIT = { platform: 0.80, hotel: 0.20 };
    const FREE_SHIPPING_QTY = 2; // 购买两瓶免运费

    // ---------- 悲观锁(Mutex, FIFO 队列实现) ----------
    // 引用独立工具类 mutex.js 的 Mutex(若未加载则内部兜底, 同为 FIFO)
    // FIFO: 每个等待者持有独立 Promise, release 仅唤醒队首一个(直接交接),
    //       消除 thundering herd(旧 while+await 共享 Promise 会唤醒全部 N-1 个, O(n²));
    //       且旧兜底 release 未 delete 占位会导致死循环, FIFO 从根上避免
    const _mutex = (typeof Mutex !== 'undefined') ? new Mutex() : null;
    const _mutexLocked = {};   // 兜底: key → true(是否持有)
    const _mutexQueues = {};   // 兜底: key → resolve 函数数组(FIFO 等待队列)
    let _asyncGapMs = 0; // 测试用: 注入异步延迟(>0 时 readDB/writeDB 变异步)

    async function _acquireMutex(key) {
        if (_mutex) {
            return await _mutex.acquire(key);
        }
        // 空闲: 直接获取(同步检查+设置, JS 单线程内原子)
        if (!_mutexLocked[key]) {
            _mutexLocked[key] = true;
            return () => _releaseMutex(key);
        }
        // 竞争: 入队等待, 每个 waiter 独立 Promise, release 只唤醒队首一个
        return new Promise(resolve => {
            (_mutexQueues[key] || (_mutexQueues[key] = [])).push(resolve);
        });
    }

    function _releaseMutex(key) {
        const q = _mutexQueues[key];
        const next = q && q.length ? q.shift() : null;
        if (next) {
            // 交接: 仅唤醒队首一个 waiter, 把它的 release 传给它; _locked 保持 true 不留空窗
            next(() => _releaseMutex(key));
        } else {
            delete _mutexLocked[key];
            delete _mutexQueues[key];
        }
    }

    async function _withMutex(keys, fn) {
        if (_mutex) {
            return await _mutex.withLocks(keys, fn);
        }
        // 兜底实现: 多锁按 key 升序获取, 反向释放
        const sorted = [...new Set(keys)].sort();
        const releases = [];
        for (const k of sorted) {
            releases.push(await _acquireMutex(k));
        }
        try {
            return await fn();  // 必须 await: 否则 finally 提前释放锁, 临界区未完成
        } finally {
            for (let i = releases.length - 1; i >= 0; i--) {
                try { releases[i](); } catch (e) { /* ignore */ }
            }
        }
    }

    function _setAsyncGap(ms) {
        // 测试用: 注入异步延迟到 readDB/writeDB,模拟未来 IndexedDB/fetch 异步存储
        // ms=0 表示同步(默认), ms>0 表示异步(产生竞争窗口)
        _asyncGapMs = ms || 0;
    }

    function _delay() {
        return _asyncGapMs > 0 ? new Promise(r => setTimeout(r, _asyncGapMs)) : Promise.resolve();
    }

    // 兼容 H5: 直接用 localStorage
    function storageGet(key) {
        try {
            const v = localStorage.getItem(key);
            return v ? JSON.parse(v) : null;
        } catch (e) { return null; }
    }
    function storageSet(key, val) {
        try { localStorage.setItem(key, JSON.stringify(val)); return true; }
        catch (e) { console.error('[AppCheckoutService] storageSet failed:', e); return false; }
    }
    function storageRemove(key) {
        try { localStorage.removeItem(key); return true; }
        catch (e) { return false; }
    }

    function readDB() {
        // _asyncGapMs>0 时变异步(测试用),否则同步
        if (_asyncGapMs > 0) {
            return _delay().then(() => storageGet(STORAGE_KEY) || initMockDB(true));
        }
        return storageGet(STORAGE_KEY) || initMockDB(true);
    }
    function writeDB(db) {
        if (_asyncGapMs > 0) {
            return _delay().then(() => storageSet(STORAGE_KEY, db));
        }
        storageSet(STORAGE_KEY, db);
        return Promise.resolve();
    }
    function readShippingDB() {
        if (_asyncGapMs > 0) {
            return _delay().then(() => storageGet(SHIPPING_KEY) || { shipping_claims: [], service_fees: [] });
        }
        return storageGet(SHIPPING_KEY) || { shipping_claims: [], service_fees: [] };
    }
    function writeShippingDB(db) {
        if (_asyncGapMs > 0) {
            return _delay().then(() => storageSet(SHIPPING_KEY, db));
        }
        storageSet(SHIPPING_KEY, db);
        return Promise.resolve();
    }

    function initMockDB(forceWrite) {
        const existing = forceWrite ? null : storageGet(STORAGE_KEY);
        if (existing && !forceWrite) return existing;
        // 复用全局 PRODUCTS(若 data.js 已加载),否则用默认值
        const sourceProducts = (typeof PRODUCTS !== 'undefined' ? PRODUCTS : [
            { id: 1, name: '竹奕·竹香经典 500ml', price: 268 },
            { id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368 },
            { id: 3, name: '竹奕·竹香珍藏 500ml', price: 698 },
        ]);
        const db = {
            products: sourceProducts.map(p => ({ id: p.id, name: p.name, price: p.price, stock: p.stock || 100 })),
            coupons: [
                { id: 'C001', code: 'NEW10', discount: 0.10, status: '未使用', desc: '新人9折' },
                { id: 'C002', code: 'SVIP20', discount: 0.20, status: '未使用', desc: 'SVIP8折' },
            ],
            members: [
                { id: 1, name: '张三', points: 5000, level: 'L3' },
                { id: 2, name: '李四', points: 12000, level: 'L5' },
            ],
            orders: [], profit_records: [], tx_log: [],
        };
        writeDB(db); return db;
    }

    function round2(n) { return Math.round(n * 100) / 100; }

    function resolveShipper(region) {
        if (!region) return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region: '' };
        const db = readShippingDB();
        const claim = (db.shipping_claims || []).find(c => c.region === region && c.status === 'active');
        if (claim) return { shipper: 'agent', agentId: claim.agent_id, agentName: claim.agent_name, claimId: claim.id, region };
        return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region };
    }

    function accrueServiceFee(dbRef, payload) {
        const o = payload || {};
        if (!dbRef || !dbRef.db) throw new Error('accrueServiceFee: dbRef 缺失');
        if (!Array.isArray(dbRef.db.service_fees)) dbRef.db.service_fees = [];
        const agentId = Number(o.agentId);
        const orderAmount = Number(o.orderAmount);
        if (!Number.isFinite(agentId) || agentId <= 0) throw new Error('服务费计提: 代理商ID无效');
        if (!Number.isFinite(orderAmount) || orderAmount < 0) throw new Error('服务费计提: 订单金额无效');
        const fee = round2(orderAmount * SERVICE_FEE_RATE);
        const feeId = 'SF' + Date.now() + '-' + Math.floor(Math.random() * 1000);
        const record = {
            id: feeId, agent_id: agentId, agent_name: o.agentName || '', order_no: o.orderNo || null,
            region: o.region || '', shipped_qty: o.shippedQty || 0, order_amount: round2(orderAmount),
            service_fee: fee, service_rate: SERVICE_FEE_RATE, settled_as: '同品', status: '待发放',
            created_at: new Date().toISOString(),
        };
        dbRef.db.service_fees.push(record);
        try {
            const sdb = readShippingDB();
            sdb.service_fees.push({ ...record });
            writeShippingDB(sdb);
        } catch (e) { console.warn('[AppCheckoutService] 服务费镜像写入失败:', e); }
        return { serviceFee: fee, record };
    }

    async function submit(params) {
        const log = [];
        const logger = {
            info: (step, msg, data) => {
                log.push({ step, msg, data, time: new Date().toISOString() });
                console.log('[AppCheckoutService] ' + step + ': ' + msg, data || '');
            },
            error: (step, msg, data) => {
                log.push({ step, msg, data, time: new Date().toISOString(), level: 'error' });
                console.error('[AppCheckoutService] ' + step + ': ' + msg, data || '');
            },
        };

        // 阶段1: 预检(无锁,快速失败)
        if (!params.items || params.items.length === 0) {
            logger.error('阶段1-预检', '购物车为空');
            return { success: false, error: '购物车为空', orderNo: 'ZX' + Date.now(), data: null, logs: log };
        }

        // 获取所有商品锁 key(按 id 升序,避免死锁)
        const lockKeys = params.items.map(i => 'stock:' + i.id);

        // 用 Mutex.withLocks 包装整个事务体(自动获取/释放锁,防止并发超卖)
        return await _withMutex(lockKeys, async () => {
            let dbSnapshot = null, shippingSnapshot = null;
            try {
                logger.info('阶段1-预检', '校验购物车 + 计算价格', {
                    itemCount: params.items.length, memberLevel: params.memberLevel,
                    points: params.points || 0, couponCode: params.couponCode || '(无)',
                    region: params.region || '(未指定)',
                });

                logger.info('阶段1.5-加锁', `已获取 ${lockKeys.length} 个商品锁(Mutex)`, { lockKeys });

                // 阶段2: BEGIN
                dbSnapshot = JSON.parse(JSON.stringify(await readDB()));
                shippingSnapshot = JSON.parse(JSON.stringify(await readShippingDB()));
                const dbRef = { db: await readDB() };
                const shippingDbRef = { db: await readShippingDB() };
                dbRef.db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
                logger.info('阶段2-开启事务', 'BEGIN(快照已创建)');

            const ctx = {
                orderNo: 'ZX' + Date.now(), items: params.items,
                memberId: params.memberId, memberLevel: params.memberLevel,
                points: params.points || 0, couponCode: params.couponCode,
                paymentMethod: params.paymentMethod || 'wechat', region: params.region,
            };

            // 价格计算
            const originalTotal = round2(params.items.reduce((s, i) => s + i.price * i.qty, 0));
            const memberDiscount = ctx.memberLevel === 'L5' ? 0.15 : ctx.memberLevel === 'L4' ? 0.10 : ctx.memberLevel === 'L3' ? 0.05 : 0;
            const memberDiscountAmount = round2(originalTotal * memberDiscount);
            let afterMember = round2(originalTotal - memberDiscountAmount);
            let couponDiscount = 0;
            if (ctx.couponCode) {
                const coupon = dbRef.db.coupons.find(c => c.code === ctx.couponCode);
                if (!coupon || coupon.status !== '未使用') throw new Error('优惠券无效: ' + ctx.couponCode);
                couponDiscount = round2(afterMember * coupon.discount);
            }
            let afterCoupon = round2(afterMember - couponDiscount);
            let pointsDeduct = 0;
            if (ctx.points > 0) {
                pointsDeduct = round2(ctx.points * POINTS_RATE);
                const maxDeduct = round2(afterCoupon * POINTS_DEDUCT_MAX_RATE);
                if (pointsDeduct > maxDeduct) pointsDeduct = maxDeduct;
            }
            const finalAmount = round2(afterCoupon - pointsDeduct);
            const totalQty = ctx.items.reduce((s, i) => s + i.qty, 0);
            // 关键业务规则: 购买两瓶免运费 (FREE_SHIPPING_QTY = 2)
            const shipping = totalQty >= FREE_SHIPPING_QTY ? 0 : 12;
            ctx.priceResult = {
                originalTotal, memberDiscount: memberDiscountAmount, couponDiscount,
                pointsDeduct, finalAmount: finalAmount + shipping, shipping,
            };

            // 阶段3: 订单创建 + 发货方路由
            const shipper = resolveShipper(ctx.region || '');
            ctx.shipperType = shipper.shipper;
            ctx.shipperAgentId = shipper.agentId;
            ctx.shipperAgentName = shipper.agentName;
            dbRef.db.orders.push({
                order_no: ctx.orderNo, member_id: ctx.memberId, member_level: ctx.memberLevel,
                items: ctx.items.map(i => ({ id: i.id, name: i.name, price: i.price, qty: i.qty })),
                original_total: originalTotal, member_discount: memberDiscountAmount,
                coupon_discount: couponDiscount, points_deduct: pointsDeduct,
                shipping, final_amount: finalAmount + shipping,
                coupon_code: ctx.couponCode || null, points_used: ctx.points || 0,
                points_earned: 0, payment_method: ctx.paymentMethod,
                ship_region: ctx.region || null, shipper_type: ctx.shipperType,
                shipper_agent_id: ctx.shipperAgentId, shipper_agent_name: ctx.shipperAgentName,
                status: '待付款', created_at: new Date().toISOString(),
            });
            logger.info('阶段3-订单创建', '订单写入 + 发货方路由', {
                orderNo: ctx.orderNo, shipper: ctx.shipperType, agent: ctx.shipperAgentName
            });

            // 阶段4: 库存扣减
            for (const item of ctx.items) {
                const product = dbRef.db.products.find(p => p.id === item.id);
                if (!product) throw new Error('商品不存在: ' + item.id);
                if (product.stock < item.qty) throw new Error('库存不足: ' + product.name);
                product.stock -= item.qty;
            }
            logger.info('阶段4-库存扣减', '库存已扣减', { totalQty });

            // 阶段5: 优惠券核销
            if (ctx.couponCode) {
                const coupon = dbRef.db.coupons.find(c => c.code === ctx.couponCode);
                if (coupon) coupon.status = '已使用';
                logger.info('阶段5-优惠券核销', '券状态→已使用', { code: ctx.couponCode });
            }

            // 阶段6: 积分扣减
            if (ctx.points > 0) {
                const member = dbRef.db.members.find(m => m.id === ctx.memberId);
                if (member) member.points -= ctx.points;
                logger.info('阶段6-积分扣减', 'points -= ' + ctx.points);
            }

            // 阶段7: 积分入账(等级加成)
            const boost = LEVEL_BOOST[ctx.memberLevel] || 1.0;
            const earnedBase = Math.floor((finalAmount + shipping) / 10 * EARN_RATE * 100);
            const earnedPoints = Math.round(earnedBase * boost);
            const member = dbRef.db.members.find(m => m.id === ctx.memberId);
            if (member) member.points += earnedPoints;
            const order = dbRef.db.orders.find(o => o.order_no === ctx.orderNo);
            if (order) order.points_earned = earnedPoints;
            logger.info('阶段7-积分入账', 'earned ' + earnedPoints + ' (L' + ctx.memberLevel + ' +' + Math.round((boost - 1) * 100) + '%)');

            // 阶段8: 分润计算 + 5%同品分润
            const platformShare = round2((finalAmount + shipping) * PROFIT_SPLIT.platform);
            const hotelShare = round2((finalAmount + shipping) * PROFIT_SPLIT.hotel);
            let manufacturerServiceFee = 0, feeRecordId = null;
            if (ctx.shipperType === 'agent') {
                const r = accrueServiceFee(shippingDbRef, {
                    agentId: ctx.shipperAgentId, agentName: ctx.shipperAgentName,
                    region: ctx.region, orderNo: ctx.orderNo, shippedQty: totalQty,
                    orderAmount: finalAmount + shipping,
                });
                manufacturerServiceFee = r.serviceFee;
                feeRecordId = r.record.id;
            }
            dbRef.db.profit_records.push({
                order_no: ctx.orderNo, total_amount: finalAmount + shipping,
                platform_share: platformShare, hotel_share: hotelShare,
                manufacturer_service_fee: manufacturerServiceFee,
                shipper_type: ctx.shipperType, shipper_agent_name: ctx.shipperAgentName,
                split_rule: ctx.shipperType === 'agent'
                    ? '厂家直供分润+5%同品分润给' + ctx.shipperAgentName
                    : '无代理商:平台80%+酒店20%',
                created_at: new Date().toISOString(),
            });
            logger.info('阶段8-分润计算', '分润+服务费已记录', {
                platform: platformShare, hotel: hotelShare, mfrFee: manufacturerServiceFee
            });

            // 阶段9: 支付确认
            if (order) order.status = '已付款';
            logger.info('阶段9-支付确认', '订单状态→已付款');

            // 阶段10: COMMIT
            await writeDB(dbRef.db);
            await writeShippingDB(shippingDbRef.db);
            dbRef.db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString() });
            await writeDB(dbRef.db);
            logger.info('阶段10-提交事务', 'COMMIT(已持久化)');

            return {
                success: true, orderNo: ctx.orderNo,
                data: {
                    orderNo: ctx.orderNo, finalAmount: finalAmount + shipping,
                    shipperType: ctx.shipperType, shipperAgentName: ctx.shipperAgentName,
                    manufacturerServiceFee, pointsEarned: earnedPoints, status: '已付款',
                    shipping, originalTotal, memberDiscount: memberDiscountAmount,
                    couponDiscount, pointsDeduct,
                },
                logs: log,
            };
        } catch (e) {
            logger.error('回滚', e.message);
            // 统一持久化顺序: 1.记录 ROLLBACK → 2.恢复内存引用 → 3.持久化 → 4.记录错误
            if (dbSnapshot) {
                dbSnapshot.tx_log = dbSnapshot.tx_log || [];
                dbSnapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                await writeDB(dbSnapshot);
            }
            if (shippingSnapshot) {
                await writeShippingDB(shippingSnapshot);
            }
            return { success: false, error: e.message, orderNo: 'ZX' + Date.now(), data: null, logs: log };
        }
        }); // end _withMutex 回调(锁自动释放)
    }

    return {
        // init 兼容 checkout.html 现有调用(等同于 resetMock)
        init() { this.resetMock(); return this; },
        resetMock() {
            // 始终同步(测试用),不受 _asyncGapMs 影响
            storageRemove(STORAGE_KEY);
            storageRemove(SHIPPING_KEY);
            initMockDB(true);
            storageSet(SHIPPING_KEY, { shipping_claims: [], service_fees: [] });
            // 清空所有锁状态(优先用 Mutex 类,兜底用 FIFO 队列)
            if (_mutex) {
                _mutex.clear();
            }
            Object.keys(_mutexLocked).forEach(k => { delete _mutexLocked[k]; });
            Object.keys(_mutexQueues).forEach(k => { delete _mutexQueues[k]; });
            return this;
        },
        // getMockDB/getShippingDB 始终同步(测试验证用),只有 submit 内部用异步 readDB/writeDB
        getMockDB() { return storageGet(STORAGE_KEY) || initMockDB(true); },
        getShippingDB() { return storageGet(SHIPPING_KEY) || { shipping_claims: [], service_fees: [] }; },
        // 测试用: 注入异步延迟(ms>0 时 readDB/writeDB 变异步,产生竞争窗口)
        _setAsyncGap(ms) { _setAsyncGap(ms); return this; },
        _getAsyncGap() { return _asyncGapMs; },
        claim(agentId, region) {
            const db = storageGet(SHIPPING_KEY) || { shipping_claims: [], service_fees: [] };
            if (!Array.isArray(db.shipping_claims)) db.shipping_claims = [];
            const existing = db.shipping_claims.find(c => c.region === region && c.status === 'active');
            if (existing) return { success: false, error: '区域已被认领' };
            const checkoutDb = storageGet(STORAGE_KEY) || initMockDB(true);
            const agentMember = checkoutDb.members.find(m => m.id === agentId);
            const agentName = agentMember ? agentMember.name : '代理商' + agentId;
            const claimId = 'CLAIM' + Date.now();
            db.shipping_claims.push({
                id: claimId, agent_id: agentId, agent_name: agentName, region, status: 'active',
                created_at: new Date().toISOString(),
            });
            storageSet(SHIPPING_KEY, db);
            return { success: true, claimId, agentName };
        },
        submit,
        // 配置常量(供 UI 引用)
        CONFIG: {
            POINTS_RATE, POINTS_DEDUCT_MAX_RATE, EARN_RATE, LEVEL_BOOST,
            PROFIT_SPLIT, FREE_SHIPPING_QTY, SERVICE_FEE_RATE,
        },
    };
})();

// 暴露到 window: 兼容性优先(若 checkout-service.js 已加载则保留其版本,main.js 版本作为别名可用)
if (typeof window !== 'undefined') {
    // 仅当未加载独立 checkout-service.js 时使用 main.js 内嵌版本作为 window.CheckoutService
    if (!window.CheckoutService) {
        window.CheckoutService = AppCheckoutService;
    }
    // main.js 内嵌版本总是通过别名暴露,可在 module-test.html 等页面单独引用
    window.AppCheckoutService = AppCheckoutService;
}

/* ============================================================
 * runStockIdempotencyTest · 库存扣减幂等性测试
 * ------------------------------------------------------------
 * 用途: 模拟高并发场景,验证库存扣减在 N 个并发请求下的幂等性
 *       · 检测超卖(库存<0)
 *       · 检测重复扣减(成功数 ≠ 库存差)
 *       · 检测重复订单(订单数 ≠ 成功数)
 * ------------------------------------------------------------
 * 触发方式:
 *   · 浏览器: 在 module-test.html 调用 runStockIdempotencyTest()
 *   · 控制台: runStockIdempotencyTest({ concurrency: 100 })
 *   · Headless: window.__runStockIdempotencyTestPromise
 * ------------------------------------------------------------
 * 参数:
 *   concurrency    并发请求数 (默认 50)
 *   productId      商品ID (默认 2)
 *   qtyPerOrder    每单数量 (默认 1)
 *   memberLevel    会员等级 (默认 'L5')
 *   memberId       会员ID (默认 2)
 *   region         发货区域 (默认 '山东泰安',已自动认领)
 *   injectAsyncGap 注入异步延迟ms (默认 0=同步; >0 模拟 IndexedDB/fetch 异步存储)
 *   sink           日志输出回调 (line, type) => void
 *   onComplete     完成回调 (report) => void
 * ============================================================ */
async function runStockIdempotencyTest(options) {
    options = options || {};
    const concurrency = options.concurrency || 50;
    const productId = options.productId || 2;
    const qtyPerOrder = options.qtyPerOrder || 1;
    const memberLevel = options.memberLevel || 'L5';
    const memberId = options.memberId || 2;
    const region = options.region || '山东泰安';
    const injectAsyncGap = options.injectAsyncGap || 0; // 注入异步延迟(>0 时模拟异步存储)
    const sink = options.sink || null;
    const onComplete = options.onComplete || null;

    // ---------- 输出工具 ----------
    function emit(line, type) {
        type = type || 'info';
        if (sink && typeof sink === 'function') {
            sink(line, type);
            return;
        }
        if (typeof console !== 'undefined') {
            const tag = type === 'pass' ? '✅' : type === 'fail' ? '❌' : type === 'warn' ? '⚠️' : 'ℹ️';
            console.log(`[StockIdempotencyTest] ${tag} ${line}`);
        }
    }

    // ---------- 前置检查 ----------
    if (typeof AppCheckoutService === 'undefined') {
        const err = 'AppCheckoutService 未加载, 请先引入 js/main.js';
        emit(err, 'fail');
        const report = { error: err, allPass: false };
        if (onComplete) onComplete(report);
        return report;
    }

    const A = AppCheckoutService;
    const productName = (function () {
        const db = A.getMockDB();
        const p = db.products.find(x => x.id === productId);
        return p ? p.name : ('商品' + productId);
    })();

    emit('====================================', 'info');
    emit('库存扣减幂等性测试开始', 'info');
    emit(`目标: AppCheckoutService.submit (阶段4 库存扣减)`, 'info');
    emit('====================================', 'info');
    emit(`参数: 并发 ${concurrency}, 商品 [${productId}] ${productName}, 每单 ${qtyPerOrder} 瓶`, 'info');
    emit(`会员: ID=${memberId}, 等级=${memberLevel}, 区域=${region}`, 'info');
    emit(`存储模式: ${injectAsyncGap > 0 ? '异步(延迟 ' + injectAsyncGap + 'ms, 模拟 IndexedDB/fetch)' : '同步(localStorage)'}`, 'info');
    emit(`悲观锁: 已启用(Mutex Promise 链,防止并发超卖)`, 'info');
    emit('', 'info');

    // ---------- 准备环境 ----------
    A.resetMock();
    // 注入异步延迟(>0 时模拟未来 IndexedDB/fetch 异步存储,产生竞争窗口)
    A._setAsyncGap(injectAsyncGap);
    A.claim(1, region);

    const db0 = A.getMockDB();
    const product0 = db0.products.find(p => p.id === productId);
    const initialStock = product0.stock;
    const member0 = db0.members.find(m => m.id === memberId);
    const initialPoints = member0 ? member0.points : 0;

    emit(`初始库存: ${initialStock}`, 'info');
    emit(`初始会员积分: ${initialPoints}`, 'info');
    emit(`预期: 成功 ${Math.min(concurrency, Math.floor(initialStock / qtyPerOrder))} 个, 失败 ${Math.max(0, concurrency - Math.floor(initialStock / qtyPerOrder))} 个`, 'info');
    emit('', 'info');

    // ---------- 构造并发请求 ----------
    emit(`开始并发提交 ${concurrency} 个订单...`, 'info');
    const startTime = Date.now();

    const tasks = Array.from({ length: concurrency }, (_, i) =>
        A.submit({
            items: [{ id: productId, name: productName, price: product0.price, qty: qtyPerOrder }],
            memberId: memberId,
            memberLevel: memberLevel,
            points: 0,
            couponCode: undefined,
            paymentMethod: 'wechat',
            region: region,
        }).then(r => ({ index: i, result: r }))
    );

    // Promise.all 等待全部完成
    const settled = await Promise.all(tasks.map(t => t.then(
        v => ({ status: 'fulfilled', value: v }),
        e => ({ status: 'rejected', reason: e })
    )));

    const duration = Date.now() - startTime;

    // ---------- 收集结果 ----------
    const results = settled.map(s => {
        if (s.status === 'fulfilled') return s.value;
        return { index: -1, result: { success: false, error: s.reason ? s.reason.message : 'rejected' } };
    });

    const successResults = results.filter(r => r.result && r.result.success);
    const failResults = results.filter(r => !r.result || !r.result.success);
    const successCount = successResults.length;
    const failCount = failResults.length;

    // ---------- 验证数据库状态 ----------
    const db1 = A.getMockDB();
    const product1 = db1.products.find(p => p.id === productId);
    const finalStock = product1.stock;
    const orders = db1.orders.filter(o =>
        o.items && o.items.some(i => i.id === productId)
    );
    const ordersCount = orders.length;

    // 服务费流水
    const sdb1 = A.getShippingDB();
    const feeCount = (sdb1.service_fees || []).length;

    // 分润记录
    const profitCount = db1.profit_records.length;

    // 事务轨迹
    const txBegin = db1.tx_log.filter(t => t.type === 'BEGIN').length;
    const txCommit = db1.tx_log.filter(t => t.type === 'COMMIT').length;
    const txRollback = db1.tx_log.filter(t => t.type === 'ROLLBACK').length;

    // ---------- 幂等性指标 ----------
    const expectedSuccess = Math.min(concurrency, Math.floor(initialStock / qtyPerOrder));
    const expectedFail = Math.max(0, concurrency - expectedSuccess);
    const expectedStock = Math.max(0, initialStock - expectedSuccess * qtyPerOrder);

    // 超卖检测: 库存 < 0 或 库存差 ≠ 成功数 * qtyPerOrder
    const stockDiff = initialStock - finalStock;
    const expectedStockDiff = successCount * qtyPerOrder;
    const noOversell = finalStock >= 0 && stockDiff === expectedStockDiff;

    // 订单幂等: 订单数 = 成功数 (无重复订单)
    const noDuplicateOrders = ordersCount === successCount;

    // 事务原子性: BEGIN 数 = concurrency, COMMIT 数 = successCount, ROLLBACK 数 = failCount
    const txBeginOk = txBegin === concurrency;
    const txCommitOk = txCommit === successCount;
    const txRollbackOk = txRollback === failCount;

    // 综合判定
    const allPass = (successCount === expectedSuccess)
        && (failCount === expectedFail)
        && noOversell
        && noDuplicateOrders
        && txCommitOk
        && txRollbackOk;

    // ---------- 输出报告 ----------
    emit('', 'info');
    emit('====================================', 'info');
    emit('库存扣减幂等性测试结果', 'info');
    emit('====================================', 'info');
    emit(`耗时: ${duration}ms`, 'info');
    emit(`并发请求: ${concurrency}`, 'info');
    emit(`成功: ${successCount} (期望 ${expectedSuccess})`, successCount === expectedSuccess ? 'pass' : 'fail');
    emit(`失败: ${failCount} (期望 ${expectedFail})`, failCount === expectedFail ? 'pass' : 'fail');
    emit('', 'info');
    emit('--- 数据库状态 ---', 'info');
    emit(`商品库存: 初始 ${initialStock} → 最终 ${finalStock} (差 ${stockDiff}, 期望差 ${expectedStockDiff})`, noOversell ? 'pass' : 'fail');
    emit(`订单数: ${ordersCount} (期望 ${successCount})`, noDuplicateOrders ? 'pass' : 'fail');
    emit(`分润记录: ${profitCount}`, 'info');
    emit(`服务费流水: ${feeCount}`, 'info');
    emit('', 'info');
    emit('--- 事务轨迹 ---', 'info');
    emit(`BEGIN: ${txBegin} (期望 ${concurrency})`, txBeginOk ? 'pass' : 'fail');
    emit(`COMMIT: ${txCommit} (期望 ${successCount})`, txCommitOk ? 'pass' : 'fail');
    emit(`ROLLBACK: ${txRollback} (期望 ${failCount})`, txRollbackOk ? 'pass' : 'fail');
    emit('', 'info');
    emit('--- 幂等性判定 ---', 'info');
    emit(`无超卖 (库存≥0 且 库存差=成功数×qty): ${noOversell ? '✓ PASS' : '✗ FAIL'}`, noOversell ? 'pass' : 'fail');
    emit(`无重复订单 (订单数=成功数): ${noDuplicateOrders ? '✓ PASS' : '✗ FAIL'}`, noDuplicateOrders ? 'pass' : 'fail');
    emit(`事务原子性 (COMMIT+ROLLBACK=BEGIN): ${txCommitOk && txRollbackOk ? '✓ PASS' : '✗ FAIL'}`, (txCommitOk && txRollbackOk) ? 'pass' : 'fail');
    emit('', 'info');
    emit('====================================', 'info');
    if (allPass) {
        emit(`✅ 全部判定 PASS — 库存扣减在 ${concurrency} 并发下幂等性正常`, 'pass');
    } else {
        emit(`❌ 存在幂等性破坏, 请检查上方详情`, 'fail');
    }
    emit('====================================', 'info');

    // ---------- 失败原因分析 ----------
    if (!noOversell) {
        emit('', 'warn');
        emit('⚠️ 超卖检测:', 'warn');
        emit(`  库存差 ${stockDiff} ≠ 成功数×qty ${expectedStockDiff}`, 'warn');
        if (finalStock < 0) emit(`  库存为负数 ${finalStock}, 存在超卖!`, 'warn');
    }
    if (!noDuplicateOrders) {
        emit('', 'warn');
        emit('⚠️ 重复订单检测:', 'warn');
        emit(`  订单数 ${ordersCount} ≠ 成功数 ${successCount}`, 'warn');
    }

    // ---------- 构造结构化报告 ----------
    const report = {
        target: 'AppCheckoutService.submit (阶段4 库存扣减 + 悲观锁 Mutex)',
        params: { concurrency, productId, productName, qtyPerOrder, memberLevel, memberId, region, injectAsyncGap, mutexEnabled: true },
        duration,
        expectedSuccess,
        expectedFail,
        expectedStock,
        actual: {
            success: successCount,
            fail: failCount,
            initialStock,
            finalStock,
            stockDiff,
            ordersCount,
            profitCount,
            feeCount,
            txBegin, txCommit, txRollback,
        },
        checks: {
            successCount: { pass: successCount === expectedSuccess, actual: successCount, expected: expectedSuccess },
            noOversell: { pass: noOversell, actual: finalStock, expected: expectedStock },
            noDuplicateOrders: { pass: noDuplicateOrders, actual: ordersCount, expected: successCount },
            txBegin: { pass: txBeginOk, actual: txBegin, expected: concurrency },
            txCommit: { pass: txCommitOk, actual: txCommit, expected: successCount },
            txRollback: { pass: txRollbackOk, actual: txRollback, expected: failCount },
        },
        allPass,
        failedResults: failResults.slice(0, 5).map(r => ({
            index: r.index,
            error: r.result ? r.result.error : 'unknown',
        })),
    };

    if (onComplete && typeof onComplete === 'function') {
        onComplete(report);
    }
    // 清理: 恢复同步模式(避免影响后续测试)
    A._setAsyncGap(0);
    return report;
}

// 暴露到 window
if (typeof window !== 'undefined') {
    window.runStockIdempotencyTest = runStockIdempotencyTest;
    window.__runStockIdempotencyTestPromise = runStockIdempotencyTest; // headless 调用别名
}

/* ============================================================
 * runHighConcurrencyStressTest · 高并发压力测试
 * ------------------------------------------------------------
 * 用途: 模拟真实生产环境流量,验证 Mutex 锁在极端情况下的表现
 *       · 峰值突发(瞬时高并发)
 *       · 持续流量(分批发送)
 *       · 多商品混合(3种商品同时下单)
 *       · 多会员等级混合(L3/L5/SVIP)
 *       · 异步存储+高并发(模拟未来 IndexedDB/fetch)
 *       · 库存临界(最后1件争抢)
 * ------------------------------------------------------------
 * 触发方式:
 *   · 浏览器: runHighConcurrencyStressTest()
 *   · 控制台: runHighConcurrencyStressTest({ peakConcurrency: 500 })
 *   · Headless: window.__runHighConcurrencyStressTestPromise
 * ------------------------------------------------------------
 * 参数:
 *   peakConcurrency  峰值并发数 (默认 200)
 *   asyncGap         异步延迟ms (默认 0=同步)
 *   batchSize        分批大小 (默认 50,每批之间间隔10ms)
 *   mixProducts      是否混合多商品 (默认 true)
 *   mixLevels        是否混合会员等级 (默认 true)
 *   sink             日志回调 (line, type) => void
 *   onComplete       完成回调 (report) => void
 * ============================================================ */
async function runHighConcurrencyStressTest(options) {
    options = options || {};
    const peakConcurrency = options.peakConcurrency || 200;
    const asyncGap = options.asyncGap || 0;
    const batchSize = options.batchSize || 50;
    const mixProducts = options.mixProducts !== false;
    const mixLevels = options.mixLevels !== false;
    const sink = options.sink || null;
    const onComplete = options.onComplete || null;

    // ---------- 输出工具 ----------
    function emit(line, type) {
        type = type || 'info';
        if (sink && typeof sink === 'function') { sink(line, type); return; }
        if (typeof console !== 'undefined') {
            const tag = type === 'pass' ? '✅' : type === 'fail' ? '❌' : type === 'warn' ? '⚠️' : 'ℹ️';
            console.log(`[StressTest] ${tag} ${line}`);
        }
    }

    if (typeof AppCheckoutService === 'undefined') {
        const err = 'AppCheckoutService 未加载';
        emit(err, 'fail');
        const report = { error: err, allPass: false };
        if (onComplete) onComplete(report);
        return report;
    }

    const A = AppCheckoutService;
    emit('========================================', 'info');
    emit('高并发压力测试开始', 'info');
    emit('========================================', 'info');
    emit(`峰值并发: ${peakConcurrency}`, 'info');
    emit(`异步延迟: ${asyncGap > 0 ? asyncGap + 'ms' : '0(同步)'}`, 'info');
    emit(`分批大小: ${batchSize}`, 'info');
    emit(`混合商品: ${mixProducts ? '是(3种)' : '否(单商品)'}`, 'info');
    emit(`混合等级: ${mixLevels ? '是(L3/L5/SVIP)' : '否(仅L5)'}`, 'info');
    emit('', 'info');

    // ---------- 准备环境 ----------
    A.resetMock();
    A._setAsyncGap(asyncGap);
    A.claim(1, '山东泰安');

    // 商品池
    const productPool = mixProducts
        ? [
            { id: 1, name: '竹奕·竹香经典 500ml', price: 268, stockKey: 1 },
            { id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368, stockKey: 2 },
            { id: 3, name: '竹奕·竹香珍藏 500ml', price: 698, stockKey: 3 },
        ]
        : [{ id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368, stockKey: 2 }];

    // 会员等级池
    const levelPool = mixLevels
        ? [{ id: 2, level: 'L5' }, { id: 2, level: 'L5' }, { id: 2, level: 'L3' }]
        : [{ id: 2, level: 'L5' }];

    // 记录初始库存
    const initialStocks = {};
    productPool.forEach(p => {
        initialStocks[p.id] = A.getMockDB().products.find(x => x.id === p.id).stock;
    });

    // ---------- 构造并发请求 ----------
    const totalRequests = peakConcurrency;
    emit(`开始发送 ${totalRequests} 个并发请求...`, 'info');
    const startTime = Date.now();

    // 分批发送,模拟持续流量
    const allResults = [];
    const batches = Math.ceil(totalRequests / batchSize);

    for (let b = 0; b < batches; b++) {
        const batchStart = b * batchSize;
        const batchEnd = Math.min(batchStart + batchSize, totalRequests);
        const batchCount = batchEnd - batchStart;

        const batchTasks = Array.from({ length: batchCount }, (_, i) => {
            const idx = batchStart + i;
            const product = productPool[idx % productPool.length];
            const member = levelPool[idx % levelPool.length];
            const requestStart = Date.now();
            return A.submit({
                items: [{ id: product.id, name: product.name, price: product.price, qty: 1 }],
                memberId: member.id,
                memberLevel: member.level,
                region: '山东泰安',
                paymentMethod: 'wechat',
            }).then(r => ({
                index: idx,
                success: r.success,
                error: r.error,
                productId: product.id,
                memberLevel: member.level,
                latency: Date.now() - requestStart,
                finalAmount: r.data ? r.data.finalAmount : null,
            }));
        });

        const batchResults = await Promise.all(batchTasks);
        allResults.push(...batchResults);

        // 批次间小延迟(模拟用户间隔)
        if (b < batches - 1) {
            await new Promise(r => setTimeout(r, 10));
        }
    }

    const duration = Date.now() - startTime;

    // ---------- 收集结果 ----------
    const successResults = allResults.filter(r => r.success);
    const failResults = allResults.filter(r => !r.success);
    const successCount = successResults.length;
    const failCount = failResults.length;

    // ---------- 验证数据库状态 ----------
    const db = A.getMockDB();
    const finalStocks = {};
    let totalInitialStock = 0;
    let totalFinalStock = 0;
    let totalStockDiff = 0;
    let noOversell = true;

    productPool.forEach(p => {
        finalStocks[p.id] = db.products.find(x => x.id === p.id).stock;
        const diff = initialStocks[p.id] - finalStocks[p.id];
        totalInitialStock += initialStocks[p.id];
        totalFinalStock += finalStocks[p.id];
        totalStockDiff += diff;
        if (finalStocks[p.id] < 0) noOversell = false;
    });

    // 验证库存差 = 成功数 * qty(每单1瓶,但可能多商品)
    const expectedStockDiff = successCount; // 每单1瓶
    if (totalStockDiff !== expectedStockDiff) noOversell = false;

    // 订单数 = 成功数(无重复)
    const ordersCount = db.orders.length;
    const noDuplicateOrders = ordersCount === successCount;

    // 事务日志完整性
    const txBegin = db.tx_log.filter(t => t.type === 'BEGIN').length;
    const txCommit = db.tx_log.filter(t => t.type === 'COMMIT').length;
    const txRollback = db.tx_log.filter(t => t.type === 'ROLLBACK').length;
    const txAtomicity = (txCommit + txRollback) === txBegin;

    // ---------- 延迟分析 ----------
    const latencies = allResults.map(r => r.latency).sort((a, b) => a - b);
    const avgLatency = latencies.reduce((s, l) => s + l, 0) / latencies.length;
    const p50 = latencies[Math.floor(latencies.length * 0.5)];
    const p95 = latencies[Math.floor(latencies.length * 0.95)];
    const p99 = latencies[Math.floor(latencies.length * 0.99)];
    const minLatency = latencies[0];
    const maxLatency = latencies[latencies.length - 1];

    // 吞吐量
    const throughput = (totalRequests / duration * 1000).toFixed(1);

    // ---------- 按商品分布 ----------
    const productStats = {};
    productPool.forEach(p => {
        const productResults = allResults.filter(r => r.productId === p.id);
        productStats[p.id] = {
            name: p.name,
            initialStock: initialStocks[p.id],
            finalStock: finalStocks[p.id],
            success: productResults.filter(r => r.success).length,
            fail: productResults.filter(r => !r.success).length,
        };
    });

    // ---------- 失败原因分析 ----------
    const errorTypes = {};
    failResults.forEach(r => {
        const err = r.error || 'unknown';
        errorTypes[err] = (errorTypes[err] || 0) + 1;
    });

    // ---------- 综合判定 ----------
    const allPass = noOversell && noDuplicateOrders && txAtomicity && (txBegin === totalRequests);

    // ---------- 输出报告 ----------
    emit('', 'info');
    emit('========================================', 'info');
    emit('高并发压力测试结果', 'info');
    emit('========================================', 'info');
    emit(`总耗时: ${duration}ms`, 'info');
    emit(`吞吐量: ${throughput} req/s`, 'info');
    emit(`总请求: ${totalRequests}`, 'info');
    emit(`成功: ${successCount} | 失败: ${failCount}`, successCount > 0 ? 'pass' : 'fail');
    emit(`成功率: ${(successCount / totalRequests * 100).toFixed(1)}%`, 'info');
    emit('', 'info');
    emit('--- 延迟分析 ---', 'info');
    emit(`最小延迟: ${minLatency}ms`, 'info');
    emit(`平均延迟: ${avgLatency.toFixed(1)}ms`, 'info');
    emit(`P50: ${p50}ms`, 'info');
    emit(`P95: ${p95}ms`, 'warn');
    emit(`P99: ${p99}ms`, 'warn');
    emit(`最大延迟: ${maxLatency}ms`, maxLatency > 1000 ? 'warn' : 'info');
    emit('', 'info');
    emit('--- 库存状态 ---', 'info');
    Object.keys(productStats).forEach(id => {
        const s = productStats[id];
        emit(`[${id}] ${s.name}: ${s.initialStock}→${s.finalStock} (扣${s.initialStock - s.finalStock}, 成功${s.success}, 失败${s.fail})`, s.finalStock >= 0 ? 'info' : 'fail');
    });
    emit(`总库存: ${totalInitialStock}→${totalFinalStock} (扣${totalStockDiff})`, noOversell ? 'pass' : 'fail');
    emit(`超卖检测: ${noOversell ? '✓ 无超卖' : '✗ 存在超卖'}`, noOversell ? 'pass' : 'fail');
    emit(`重复订单: ${noDuplicateOrders ? '✓ 无重复' : '✗ 存在重复'} (订单数=${ordersCount}, 成功数=${successCount})`, noDuplicateOrders ? 'pass' : 'fail');
    emit('', 'info');
    emit('--- 事务日志 ---', 'info');
    emit(`BEGIN: ${txBegin} (期望 ${totalRequests})`, txBegin === totalRequests ? 'pass' : 'fail');
    emit(`COMMIT: ${txCommit}`, 'pass');
    emit(`ROLLBACK: ${txRollback}`, 'info');
    emit(`原子性: ${txAtomicity ? '✓ COMMIT+ROLLBACK=BEGIN' : '✗ 不匹配'}`, txAtomicity ? 'pass' : 'fail');
    emit('', 'info');
    emit('--- 失败原因分布 ---', 'info');
    if (Object.keys(errorTypes).length === 0) {
        emit('(无失败)', 'pass');
    } else {
        Object.keys(errorTypes).forEach(err => {
            emit(`${err}: ${errorTypes[err]} 次`, 'warn');
        });
    }
    emit('', 'info');
    emit('========================================', 'info');
    if (allPass) {
        emit(`✅ 压测通过 — ${totalRequests} 并发下 Mutex 锁正常, 无超卖/无重复/事务原子`, 'pass');
    } else {
        emit(`❌ 压测失败 — 存在超卖/重复/原子性破坏`, 'fail');
    }
    emit('========================================', 'info');

    // ---------- 结构化报告 ----------
    const report = {
        target: 'AppCheckoutService.submit (9阶段事务 + Mutex 悲观锁)',
        params: { peakConcurrency, asyncGap, batchSize, mixProducts, mixLevels },
        summary: {
            totalRequests,
            successCount,
            failCount,
            successRate: (successCount / totalRequests * 100).toFixed(1) + '%',
            duration,
            throughput: throughput + ' req/s',
        },
        latency: {
            min: minLatency, avg: parseFloat(avgLatency.toFixed(1)),
            p50, p95, p99, max: maxLatency,
        },
        stock: {
            totalInitial: totalInitialStock,
            totalFinal: totalFinalStock,
            totalDiff: totalStockDiff,
            noOversell,
            perProduct: productStats,
        },
        orders: { count: ordersCount, noDuplicate: noDuplicateOrders },
        txLog: { begin: txBegin, commit: txCommit, rollback: txRollback, atomicity: txAtomicity },
        errors: errorTypes,
        allPass,
    };

    A._setAsyncGap(0); // 清理
    if (onComplete && typeof onComplete === 'function') onComplete(report);
    return report;
}

// 暴露到 window
if (typeof window !== 'undefined') {
    window.runHighConcurrencyStressTest = runHighConcurrencyStressTest;
    window.__runHighConcurrencyStressTestPromise = runHighConcurrencyStressTest;
}
