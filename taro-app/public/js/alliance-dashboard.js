/**
 * 37号·AI智能网站同盟模块 v1.0 · 同盟管理看板脚本
 *
 * 页面: ai-alliance-dashboard.html
 * 职责(按页面区块):
 *   1. 全景统计: GET /api/alliance/report/overview(admin)
 *      商户(五态)/商品/订单GMV/结算分润 四组卡片
 *   2. 入盟申请审核: GET /api/alliance/applications?status=manual_reviewing
 *      表格(申请人/类目/店铺/AI预审分+level徽章/资质数) + 每行「通过」「拒绝」
 *      → POST /api/alliance/applications/{id}/audit
 *        {"approved": true/false, "reviewer": "admin", "note": ""}
 *   3. 商户管理: GET /api/alliance/merchants 九态徽章 + 状态操作按钮
 *      signed→「激活试用」POST .../{id}/activate; probation→「转正」POST .../{id}/confirm;
 *      active/probation→「暂停」「终止」POST .../{id}/suspend?reason= / .../{id}/terminate?reason=(confirm 确认);
 *      操作成功后局部刷新(商户/商品/全景)
 *   4. 类目字典: GET /api/alliance/categories(公开, 折叠默认收起)
 *      code/名称/溯源级别(full红·lite灰)/必备资质/密度上限gridCap
 *   5. 就近推荐: GET /api/alliance/geo/nearby?lat=&lng=&category=(公开, P1 GeoGrid)
 *   6. 商品浏览: GET /api/alliance/products?category=(公开)
 *      行展开显示溯源信息 trace(batchNo/credentials/evidenceHash)
 *   7. 分润与结算: GET share-settings(抽佣率+五方比例) / GET share-preview?amount=(结算预览)
 *      / GET settlements?status=settled(结算单列表) 均为 admin
 *   8. 评价: GET /api/alliance/reviews?merchantId=(公开)
 *      评分星级★/内容/AI审评分/折叠状态, 折叠评价灰显
 *   9. 月度考核: POST /api/alliance/assessment/run?month=YYYY-MM(执行考核)
 *      + GET /api/alliance/merchants/{id}/assessment(考核历史, S金A绿B蓝C红徽章)
 *  10. 场景与定制: GET /api/alliance/scenes(会员端 X-Member-Id, 三子单/核销码/状态)
 *      + GET /api/alliance/custom-demands(admin 头, 定制六态徽章)
 *
 * 鉴权: 管理端 X-Role: admin / 会员端 X-Member-Id / 公开端点无特殊头
 * 响应约定: 统一解包 {"success": true, "data": ...}; 失败时红色横幅展示后端 detail
 * 自动刷新: 30s 仅重渲染数据区(沿用当前输入值), 不清任何表单
 */
'use strict';

/* ========= 全局状态 ========= */
var API_BASE_KEY = 'allianceDash.apiBase';   // 后端地址持久化键(本页独立)
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    autoTimer: null,       // 30s 自动刷新句柄
    AUTO_MS: 30000,
    categories: [],        // 类目字典(下拉数据源)
    categoriesSig: null,   // 类目集合签名(无变化不重绘下拉, 保持选择)
    applications: [],     // 待人工审核申请
    merchants: [],         // 商户列表
    products: [],         // 商品列表
    productExpanded: {},  // 商品行展开: {productId: true}
    settlements: [],       // 已结算结算单
    reviews: [],          // 评价列表
    assessments: [],      // 商户考核历史
    scenes: [],           // 我的场景单
    demands: [],          // 定制需求列表
    nearby: [],           // 就近推荐结果
    nearbyLoaded: false,  // 就近推荐已查询(自动刷新仅重查已加载区, 沿用当前输入)
    reviewsLoaded: false, // 评价区已查询
    assessLoaded: false,  // 考核历史已查询
    scenesLoaded: false,  // 场景单已查询
    demandsLoaded: false, // 定制需求已查询
};

/* ========= 中文标签与徽章映射 ========= */
/* 类目中文名映射(硬性约定: 酒水不分家, 水茶酒菜肉鱼器境) */
var CATEGORY_LABEL = {
    water: '好水', tea: '好茶', wine: '好酒', dish: '好菜',
    meat: '肉类', fish: '鱼类', vessel: '酒具', venue: '好境',
};
/* 溯源级别: full 红(全量) / lite 灰(简化) */
var TRACE_LEVEL_BADGE = { full: 'red', lite: 'weak' };
/* 商户九态状态: 中文 + 徽章色(pending灰/ai_reviewing蓝/manual_reviewing黄/signed蓝/probation紫/active绿/suspended橙/terminated红/rejected灰) */
var MERCHANT_STATUS = {
    pending: ['待预审', 'weak'],
    ai_reviewing: ['AI预审中', 'blue'],
    manual_reviewing: ['人工审核中', 'yellow'],
    signed: ['已签约', 'blue'],
    probation: ['试用期', 'purple'],
    active: ['在营', 'green'],
    suspended: ['已暂停', 'orange'],
    terminated: ['已终止', 'red'],
    rejected: ['已拒绝', 'weak'],
};
/* 入盟 AI 预审三档: 优质绿 / 待核黄 / 不足红 */
var AI_LEVEL = { high: ['优质', 'green'], medium: ['待核', 'yellow'], low: ['不足', 'red'] };
/* 商户/考核等级徽章: S金 A绿 B蓝 C红(D 兜底灰) */
var GRADE_BADGE = { S: 'gold', A: 'green', B: 'blue', C: 'red', D: 'weak' };
/* 分润五方中文名 */
var SHARE_ROLE_LABEL = {
    platform: '平台', category_service: '类目服务商', referrer: '推荐人',
    city_store: '就近市店', development_fund: '同盟发展基金',
};
/* 评价 AI 审评三档(违规分, 越高越可疑): low正常 / medium观察 / high高危 */
var REVIEW_AI_LEVEL = { low: ['正常', 'green'], medium: ['观察', 'yellow'], high: ['高危', 'red'] };
/* 定制需求状态机六态徽章 */
var CUSTOM_STATUS = {
    demand: ['需求提交', 'weak'], quoted: ['已报价', 'blue'],
    confirmed: ['已确认', 'green'], producing: ['生产中', 'yellow'],
    delivered: ['已交付', 'gold'], cancelled: ['已取消', 'red'],
};
/* 定制类型中文名 */
var CUSTOM_TYPE_LABEL = { engraving: '酒具刻字', private_feast: '私宴定制', sealing: '封坛定制' };
/* 场景单状态: created 待核销 / redeemed 已核销 */
var SCENE_STATUS = { created: ['待核销', 'blue'], redeemed: ['已核销', 'green'] };
/* 考核处置动作 */
var ASSESS_ACTION = { none: ['无处置', 'weak'], suspend: ['暂停', 'orange'], terminate: ['清退', 'red'] };

/* ========= 通用工具 ========= */
/* HTML 转义(防注入, 统一出口) */
function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

/* ISO 时间显示(UTC, 截断毫秒与时区后缀) */
function fmtTime(iso) {
    if (!iso) { return '--'; }
    return String(iso).replace('T', ' ').replace(/\.\d+/, '').replace(/(\+00:00|Z)$/, '') + ' UTC';
}

/* 金额显示(保留两位小数, 空值兜底) */
function money(v) {
    return v != null && !isNaN(Number(v)) ? Number(v).toFixed(2) : '--';
}

/* 百分比显示(避免 0.15*100 浮点尾差) */
function pct(v) {
    return v != null ? Math.round(Number(v) * 100) + '%' : '--';
}

/* 文本截断(超长加省略号, 配合 title 属性悬浮全文) */
function truncate(text, max) {
    var s = String(text || '');
    return s.length > max ? s.slice(0, max) + '…' : s;
}

/* 类目中文名(code → 中文, 未知原样返回) */
function categoryName(code) {
    return CATEGORY_LABEL[code] || code || '--';
}

/* 星级字符串(1-5 星, 实心★+空心☆) */
function stars(score) {
    var n = Math.max(0, Math.min(5, Number(score) || 0));
    return '★'.repeat(n) + '☆'.repeat(5 - n);
}

/* 星级单元格(均分 + 条数, 无评价显示占位) */
function ratingHtml(avg, count) {
    if (!count) { return '<span class="cap-id">暂无评价</span>'; }
    return '<b>' + Number(avg || 0).toFixed(2) + '</b> <span class="cap-id">(' + count + '条)</span>';
}

/* 表格区块错误占位(单区块失败不影响其他区块渲染, 错误同时上抛至工具栏横幅) */
function sectionError(tbodyId, colspan, err) {
    var tbody = document.getElementById(tbodyId);
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="' + colspan + '" class="dash-empty" style="color:var(--color-danger);">加载失败：'
            + esc(err.message) + '</td></tr>';
    }
}

/* ========= API ========= */
/* 鉴权头: admin → X-Role: admin; member → X-Member-Id(取会员输入框当前值); 公开不附加 */
function apiHeaders(role) {
    var h = { 'Content-Type': 'application/json' };
    if (role === 'admin') { h['X-Role'] = 'admin'; }
    if (role === 'member') {
        h['X-Member-Id'] = String(document.getElementById('memberId').value || '').trim();
    }
    var auth = (typeof Auth !== 'undefined') ? Auth.apiHeaders() : null;
    return auth ? Object.assign(h, auth) : h;
}

/* 统一请求: 解包 {"success": true, "data": ...}; 失败抛出含后端 detail 的错误
   (401 不自动跳登录: 会员端 401 多为 X-Member-Id 缺失/非法, 直接展示 detail 即可) */
async function fetchJson(url, options, role) {
    var res = await fetch(url, Object.assign({ headers: apiHeaders(role) }, options || {}));
    var body = null;
    try { body = await res.json(); } catch (e) { /* 非 JSON 错误体 */ }
    if (!res.ok) {
        var msg = body && (body.detail || body.error || body.message)
            ? (body.detail || body.error || body.message) : ('HTTP ' + res.status);
        throw new Error(msg);
    }
    if (!body || body.success !== true) {
        throw new Error((body && (body.detail || body.error)) || '响应格式异常(缺少 success/data 包裹)');
    }
    return body.data;
}

/* ========= 数据加载 ========= */
/* 全量刷新: 常驻数据区并行加载; 工具区(评价/考核历史)与会员演示区仅在已查询过后
   沿用当前输入值重查 —— 任何情况下都不重置/清空表单输入。
   区块守卫: 单个区块失败仅记录(该区块表格内已内联展示后端 detail), 不影响其他区块;
   全部失败才提示"后端未启动", 部分失败则逐区块点名。 */
async function refreshData() {
    var btn = document.getElementById('btnRefresh');
    btn.disabled = true; btn.textContent = '刷新中…';
    var failures = [];
    var guard = function (name, task) {
        return task.catch(function (err) { failures.push(name + '：' + err.message); });
    };
    try {
        var tasks = [
            guard('全景统计', loadOverview()),
            guard('入盟申请', loadApplications()),
            guard('商户管理', loadMerchants()),
            guard('类目字典', loadCategories()),
            guard('商品浏览', loadProducts()),
            guard('分润配置', loadShareSettings()),
            guard('结算单列表', loadSettlements()),
        ];
        if (state.nearbyLoaded) { tasks.push(guard('就近推荐', loadNearby())); }
        if (state.scenesLoaded) { tasks.push(guard('我的场景单', loadScenes())); }
        if (state.demandsLoaded) { tasks.push(guard('我的定制', loadCustomDemands())); }
        if (state.reviewsLoaded) { tasks.push(guard('评价区', loadReviews())); }
        if (state.assessLoaded) { tasks.push(guard('考核历史', loadAssessmentHistory())); }
        await Promise.all(tasks);
        if (failures.length === tasks.length) {
            showBanner('errorBanner', '数据加载失败：' + failures[0] + '（请确认后端已启动且地址正确）');
        } else if (failures.length) {
            showBanner('errorBanner', '部分区块加载失败 → ' + failures.join('；'));
        } else {
            hideBanner('errorBanner');
        }
    } finally {
        btn.disabled = false; btn.textContent = '刷新';
        document.getElementById('lastUpdate').textContent = '最后刷新 ' + new Date().toLocaleTimeString('zh-CN');
    }
}

/* ① 全景统计卡: 商户/商品/订单/结算 四组 */
async function loadOverview() {
    var d;
    try {
        d = await fetchJson(state.apiBase + '/api/alliance/report/overview', null, 'admin');
    } catch (err) {
        document.getElementById('ovWrap').innerHTML =
            '<div class="dash-empty" style="color:var(--color-danger);">加载失败：' + esc(err.message) + '</div>';
        throw err;
    }
    var m = d.merchants || {}, p = d.products || {}, o = d.orders || {}, s = d.settlements || {};
    var cell = function (v, label) {
        return '<div class="ov-cell"><b>' + (v != null ? v : '--') + '</b><span>' + label + '</span></div>';
    };
    document.getElementById('ovWrap').innerHTML =
        '<div class="ov-group"><div class="ov-gtitle">商户</div><div class="ov-cells">'
            + cell(m.total, '总数') + cell(m.active, '在营') + cell(m.probation, '试用期')
            + cell(m.suspended, '已暂停') + cell(m.terminated, '已终止') +
        '</div></div>' +
        '<div class="ov-group"><div class="ov-gtitle">商品</div><div class="ov-cells">'
            + cell(p.total, '总数') + cell(p.active, '在售') +
        '</div></div>' +
        '<div class="ov-group"><div class="ov-gtitle">订单</div><div class="ov-cells">'
            + cell(o.total, '总数') + cell(o.paid, '已支付') + cell(o.settled, '已结算')
            + cell('¥' + money(o.gmv), 'GMV') +
        '</div></div>' +
        '<div class="ov-group"><div class="ov-gtitle">结算</div><div class="ov-cells">'
            + cell(s.total, '结算单') + cell(s.settled, '已结算')
            + cell('¥' + money(s.commissionTotal), '佣金总额')
            + cell('¥' + money(s.proceedsTotal), '货款总额') +
        '</div></div>';
}

/* ② 入盟申请列表(仅人工审核中) */
async function loadApplications() {
    try {
        state.applications = await fetchJson(
            state.apiBase + '/api/alliance/applications?status=manual_reviewing', null, 'admin');
    } catch (err) { sectionError('appBody', 7, err); throw err; }
    renderApplications();
}

/* 入盟申请表格: 申请ID/申请人/类目/店铺/AI预审分(徽章)/资质数/操作 */
function renderApplications() {
    var list = state.applications;
    document.getElementById('appCount').textContent = '待人工审核 ' + list.length + ' 件';
    var tbody = document.getElementById('appBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="dash-empty">暂无待人工审核的申请(AI 预审不足 60 分的申请已在提交时直接拒绝)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (a) {
        var ai = a.aiReview || {};
        var lv = AI_LEVEL[ai.level] || null;
        var creds = a.credentials || [];
        return '<tr>' +
            '<td class="cap-id">#' + a.applicationId + '</td>' +
            '<td>会员 ' + esc(a.memberId) + '</td>' +
            '<td>' + esc(categoryName(a.category)) + '</td>' +
            '<td style="white-space:normal;max-width:180px;" title="' + esc(a.shopName) + '">' + esc(a.shopName || '--') + '</td>' +
            '<td><b>' + (ai.score != null ? ai.score : '--') + '</b>'
                + (lv ? ' <span class="badge ' + lv[1] + '">' + esc(lv[0]) + '</span>' : '') + '</td>' +
            '<td class="num" title="' + esc(creds.join('、')) + '">' + creds.length + ' 项</td>' +
            '<td><button class="btn-mini" onclick="auditApplication(' + a.applicationId + ', true)">通过</button> '
                + '<button class="btn-mini danger" onclick="auditApplication(' + a.applicationId + ', false)">拒绝</button></td>' +
        '</tr>';
    }).join('');
}

/* 入盟人工终审: 通过→签约建档(signed); 拒绝→rejected(90 天冷却) */
async function auditApplication(applicationId, approved) {
    try {
        await fetchJson(
            state.apiBase + '/api/alliance/applications/' + encodeURIComponent(applicationId) + '/audit',
            { method: 'POST', body: JSON.stringify({ approved: approved, reviewer: 'admin', note: '' }) },
            'admin');
        showBanner('infoBanner', '申请 #' + applicationId
            + (approved ? ' 已通过审核(已签约建档, 可在商户管理区「激活试用」)' : ' 已拒绝(90 天冷却期内不可重新申请)'));
        /* 局部刷新: 申请列表/商户列表/全景统计 */
        await Promise.all([loadApplications(), loadMerchants(), loadOverview()]);
    } catch (err) {
        showBanner('errorBanner', '审核失败：' + err.message);
    }
}

/* ③ 商户列表(全部状态) */
async function loadMerchants() {
    try {
        state.merchants = await fetchJson(state.apiBase + '/api/alliance/merchants', null, 'admin');
    } catch (err) { sectionError('merchantBody', 8, err); throw err; }
    renderMerchants();
}

/* 商户表格: ID/店铺/类目中文/九态徽章+status/等级/星级/推荐人/状态操作 */
function renderMerchants() {
    var list = state.merchants;
    document.getElementById('merchantCount').textContent = '共 ' + list.length + ' 家';
    var tbody = document.getElementById('merchantBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="dash-empty">暂无商户(入盟申请审核通过后签约建档)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (m) {
        var st = MERCHANT_STATUS[m.status] || null;
        var grade = m.grade || 'C';
        return '<tr>' +
            '<td class="cap-id">#' + m.merchantId + '</td>' +
            '<td style="white-space:normal;max-width:170px;" title="' + esc(m.shopName) + '"><b>' + esc(m.shopName || '--') + '</b></td>' +
            '<td>' + esc(categoryName(m.category)) + '</td>' +
            '<td>' + (st ? '<span class="badge ' + st[1] + '">' + esc(st[0]) + '</span> ' : '')
                + '<span class="cap-id">' + esc(m.status) + '</span></td>' +
            '<td><span class="badge ' + (GRADE_BADGE[grade] || 'weak') + '">' + esc(grade) + '</span></td>' +
            '<td>' + ratingHtml(m.ratingAvg, m.ratingCount) + '</td>' +
            '<td class="cap-id">' + (m.referrerMemberId != null ? '会员 ' + esc(m.referrerMemberId) : '—') + '</td>' +
            '<td>' + merchantActions(m) + '</td>' +
        '</tr>';
    }).join('');
}

/* 商户状态操作按钮(按状态显示): signed→激活试用; probation→转正;
   active/probation→暂停/终止 */
function merchantActions(m) {
    var id = m.merchantId;
    var btns = [];
    if (m.status === 'signed') {
        btns.push('<button class="btn-mini" onclick="merchantAction(' + id + ', \'activate\')">激活试用</button>');
    }
    if (m.status === 'probation') {
        btns.push('<button class="btn-mini" onclick="merchantAction(' + id + ', \'confirm\')">转正</button>');
    }
    if (m.status === 'active' || m.status === 'probation') {
        btns.push('<button class="btn-mini" onclick="merchantAction(' + id + ', \'suspend\')">暂停</button>');
        btns.push('<button class="btn-mini danger" onclick="merchantAction(' + id + ', \'terminate\')">终止</button>');
    }
    return btns.length ? btns.join(' ') : '—';
}

/* 商户状态操作: activate/confirm 无参; suspend/terminate 携带 reason 查询参数(终止前 confirm 确认) */
async function merchantAction(merchantId, action) {
    var labels = { activate: '激活试用', confirm: '转正', suspend: '暂停', terminate: '终止' };
    var label = labels[action] || action;
    /* 终止为高危操作, 需二次确认 */
    if (action === 'terminate' &&
        !confirm('确认终止商户 #' + merchantId + '?\n终止后在售商品将全量下架, 且 90 天冷却期内不可重新入盟。')) {
        return;
    }
    var reason = '';
    if (action === 'suspend' || action === 'terminate') {
        reason = prompt(label + '原因(可留空):', '');
        if (reason === null) { return; }   /* 用户取消输入 */
        reason = reason.trim();
    }
    var url = state.apiBase + '/api/alliance/merchants/' + encodeURIComponent(merchantId)
        + '/' + action + (reason ? '?reason=' + encodeURIComponent(reason) : '');
    try {
        var m = await fetchJson(url, { method: 'POST' }, 'admin');
        var newStatus = m && m.status;
        var statusText = (MERCHANT_STATUS[newStatus] || [])[0] || newStatus || '--';
        showBanner('infoBanner', '商户 #' + merchantId + ' ' + label + '成功(当前状态: ' + statusText + ')');
        /* 局部刷新: 商户状态变化 + 全景统计 + 商品(暂停/终止会全量下架在售商品) */
        await Promise.all([loadMerchants(), loadOverview(), loadProducts()]);
    } catch (err) {
        showBanner('errorBanner', label + '失败：' + err.message);
    }
}

/* ④ 类目字典(公开; 数据常载, 面板折叠仅控制展示) */
async function loadCategories() {
    try {
        state.categories = await fetchJson(state.apiBase + '/api/alliance/categories');
    } catch (err) { sectionError('catBody', 5, err); throw err; }
    renderCategories();
}

/* 类目表格: code/名称/溯源级别徽章(full红·lite灰)/必备资质/密度上限 */
function renderCategories() {
    var list = state.categories;
    document.getElementById('catCount').textContent = '共 ' + list.length + ' 类(水茶酒菜肉鱼器境)';
    var tbody = document.getElementById('catBody');
    tbody.innerHTML = list.length ? list.map(function (c) {
        var creds = c.requiredCredentials || [];
        return '<tr>' +
            '<td class="cap-id">' + esc(c.code) + '</td>' +
            '<td><b>' + esc(c.name || categoryName(c.code)) + '</b></td>' +
            '<td><span class="badge ' + (TRACE_LEVEL_BADGE[c.traceLevel] || 'weak') + '">'
                + esc(c.traceLevel || '--') + '</span></td>' +
            '<td style="white-space:normal;max-width:220px;">' + (creds.length ? esc(creds.join('、')) : '—') + '</td>' +
            '<td class="num">' + (c.gridCap != null ? c.gridCap : '--') + '</td>' +
        '</tr>';
    }).join('') : '<tr><td colspan="5" class="dash-empty">暂无类目数据</td></tr>';
    renderCategorySelects();
}

/* 类目下拉填充(就近推荐/商品浏览共用): 签名无变化不重绘, 保持刷新前的选择 */
function renderCategorySelects() {
    var sig = state.categories.map(function (c) { return c.code; }).join(',');
    if (state.categoriesSig !== null && sig === state.categoriesSig) { return; }
    state.categoriesSig = sig;
    var options = '<option value="">全部类目</option>' + state.categories.map(function (c) {
        return '<option value="' + esc(c.code) + '">' + esc(categoryName(c.code)) + '</option>';
    }).join('');
    ['nearbyCategory', 'productCategory'].forEach(function (id) {
        var sel = document.getElementById(id);
        var prev = sel.value;
        sel.innerHTML = options;
        if (prev && state.categories.some(function (c) { return c.code === prev; })) { sel.value = prev; }
    });
}

/* 类目字典折叠/展开(默认收起) */
function toggleCategories() {
    var panel = document.getElementById('catPanel');
    var btn = document.getElementById('catToggle');
    var collapsed = panel.style.display === 'none';
    panel.style.display = collapsed ? 'block' : 'none';
    btn.textContent = collapsed ? '收起 ▾' : '展开 ▸';
}

/* ⑤ 就近推荐(公开): 纬度/经度 + 类目 → 网格商户, 距离升序 */
async function loadNearby() {
    var latRaw = String(document.getElementById('nearbyLat').value || '').trim();
    var lngRaw = String(document.getElementById('nearbyLng').value || '').trim();
    var lat = Number(latRaw);
    var lng = Number(lngRaw);
    if (!latRaw || !lngRaw || isNaN(lat) || isNaN(lng)) {
        /* 输入校验失败: 区块内提示(不动全局横幅, 避免与自动刷新互相覆盖) */
        document.getElementById('nearbyBody').innerHTML =
            '<tr><td colspan="5" class="dash-empty">请输入有效的纬度/经度(如 36.06 / 120.38)</td></tr>';
        return;
    }
    state.nearbyLoaded = true;
    var category = document.getElementById('nearbyCategory').value;
    var url = state.apiBase + '/api/alliance/geo/nearby?lat=' + encodeURIComponent(lat)
        + '&lng=' + encodeURIComponent(lng)
        + (category ? '&category=' + encodeURIComponent(category) : '');
    try {
        state.nearby = await fetchJson(url);
    } catch (err) { sectionError('nearbyBody', 5, err); throw err; }
    renderNearby();
}

/* 就近推荐表格: 店铺/类目/等级/星级/距离km */
function renderNearby() {
    var list = state.nearby || [];
    document.getElementById('nearbyCount').textContent = '推荐 ' + list.length + ' 家(3×3 邻近网格)';
    var tbody = document.getElementById('nearbyBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="dash-empty">该位置邻近网格暂无在营覆盖商户</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (n) {
        var grade = n.grade || 'C';
        return '<tr>' +
            '<td style="white-space:normal;max-width:180px;"><b>' + esc(n.shopName || '--') + '</b></td>' +
            '<td>' + esc(categoryName(n.category)) + '</td>' +
            '<td><span class="badge ' + (GRADE_BADGE[grade] || 'weak') + '">' + esc(grade) + '</span></td>' +
            '<td>' + ratingHtml(n.ratingAvg, n.ratingCount) + '</td>' +
            '<td class="num"><b>' + (n.distanceKm != null ? n.distanceKm : '--') + '</b> km</td>' +
        '</tr>';
    }).join('');
}

/* ⑥ 商品列表(公开, 按类目筛选, 默认在售) */
async function loadProducts() {
    var category = document.getElementById('productCategory').value;
    var url = state.apiBase + '/api/alliance/products'
        + (category ? '?category=' + encodeURIComponent(category) : '');
    try {
        state.products = await fetchJson(url);
    } catch (err) { sectionError('productBody', 7, err); throw err; }
    renderProducts();
}

/* 商品表格: SKU/名称/类目/商户ID/价格/库存/溯源级别徽章; 行展开溯源信息 */
function renderProducts() {
    var list = state.products;
    document.getElementById('productCount').textContent = '共 ' + list.length + ' 件在售';
    var tbody = document.getElementById('productBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="dash-empty">暂无在售商品(可切换类目)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (p) {
        return renderProductRow(p) + (state.productExpanded[p.productId] ? renderProductDetail(p) : '');
    }).join('');
}

/* 商品行(点击展开/收起溯源) */
function renderProductRow(p) {
    var trace = p.trace || {};
    return '<tr class="row-toggle" data-pid="' + p.productId + '">' +
        '<td class="cap-id">' + esc(p.sku || ('#' + p.productId)) + '</td>' +
        '<td style="white-space:normal;max-width:240px;"><span class="exp-arrow">'
            + (state.productExpanded[p.productId] ? '▾' : '▸') + '</span>' + esc(p.name || '--') + '</td>' +
        '<td>' + esc(categoryName(p.category)) + '</td>' +
        '<td class="cap-id">#' + esc(p.merchantId) + '</td>' +
        '<td class="num">¥' + money(p.price) + '</td>' +
        '<td class="num">' + (p.stock != null ? p.stock : '--') + '</td>' +
        '<td><span class="badge ' + (TRACE_LEVEL_BADGE[trace.level] || 'weak') + '">'
            + esc(trace.level || '--') + '</span></td>' +
    '</tr>';
}

/* 商品展开行: 溯源信息 trace(batchNo/credentials/evidenceHash) + 核验状态 */
function renderProductDetail(p) {
    var trace = p.trace || {};
    var creds = trace.credentials || [];
    var meta = [
        ['溯源级别', trace.level || '--'],
        ['溯源批次号', trace.batchNo || '—'],
        ['溯源凭证', creds.length ? creds.join('、') : '—'],
        ['存证哈希', trace.evidenceHash || '—(未上链)'],
        ['溯源核验', trace.traceVerified ? '已核验' : '未核验'],
    ];
    return '<tr class="detail-row"><td colspan="7"><div class="detail-box">' +
        '<div class="detail-body">' + (p.description ? esc(p.description) : '(无商品描述)') + '</div>' +
        '<div class="detail-meta">' + meta.map(function (m) {
            return '<span>' + esc(m[0]) + ': <b>' + esc(m[1]) + '</b></span>';
        }).join('') + '</div>' +
    '</div></td></tr>';
}

/* ⑦ 分润配置(admin): 抽佣率 + 五方比例 */
async function loadShareSettings() {
    var d;
    try {
        d = await fetchJson(state.apiBase + '/api/alliance/share-settings', null, 'admin');
    } catch (err) {
        document.getElementById('shareWrap').innerHTML =
            '<div class="dash-empty" style="color:var(--color-danger);">加载失败：' + esc(err.message) + '</div>';
        throw err;
    }
    var rates = d.shareRates || {};
    var cell = function (v, label) {
        return '<div class="ov-cell"><b>' + v + '</b><span>' + label + '</span></div>';
    };
    var html = '<div class="ov-group"><div class="ov-gtitle">抽佣与五方比例</div><div class="ov-cells">'
        + cell(pct(d.commissionRate), '平台抽佣率');
    Object.keys(rates).forEach(function (role) {
        html += cell(pct(rates[role]), SHARE_ROLE_LABEL[role] || role);
    });
    html += '</div></div>';
    document.getElementById('shareWrap').innerHTML = html;
}

/* 结算预览(admin): 金额 → 佣金/货款/五方拆账明细 */
async function previewShares() {
    var raw = String(document.getElementById('previewAmount').value || '').trim();
    var amount = Number(raw);
    if (!raw || isNaN(amount) || amount <= 0) {
        showBanner('errorBanner', '请输入有效的订单金额(大于 0)');
        return;
    }
    var btn = document.getElementById('btnPreview');
    btn.disabled = true; btn.textContent = '预览中…';
    try {
        var r = await fetchJson(
            state.apiBase + '/api/alliance/share-preview?amount=' + encodeURIComponent(amount), null, 'admin');
        renderPreview(r);
    } catch (err) {
        showBanner('errorBanner', '结算预览失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '结算预览';
    }
}

/* 预览结果: 金额/抽佣率/佣金/货款 + 五方金额格子 */
function renderPreview(r) {
    var shares = r.shares || {};
    var cell = function (v, label) {
        return '<div class="ov-cell"><b>' + v + '</b><span>' + label + '</span></div>';
    };
    document.getElementById('previewResult').innerHTML =
        '<div class="tool-lines">订单金额 <b>¥' + money(r.orderAmount) + '</b> · 抽佣率 <b>'
            + pct(r.commissionRate) + '</b> · 佣金 <b>¥' + money(r.commission)
            + '</b> · 商户货款 <b>¥' + money(r.merchantProceeds) + '</b></div>' +
        '<div class="ov-cells" style="padding:8px 0 2px;">'
            + Object.keys(shares).map(function (role) {
                return cell('¥' + money(shares[role]), SHARE_ROLE_LABEL[role] || role);
            }).join('') +
        '</div>';
}

/* 结算单列表(admin): 已结算结算单 */
async function loadSettlements() {
    try {
        state.settlements = await fetchJson(
            state.apiBase + '/api/alliance/settlements?status=settled', null, 'admin');
    } catch (err) { sectionError('settleBody', 4, err); throw err; }
    renderSettlements();
}

/* 结算单表格: 订单号/佣金/货款/结算时间 */
function renderSettlements() {
    var list = state.settlements;
    document.getElementById('settleCount').textContent = '共 ' + list.length + ' 单';
    var tbody = document.getElementById('settleBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="dash-empty">暂无已结算结算单</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (s) {
        return '<tr>' +
            '<td class="cap-id">' + esc(s.orderId) + '</td>' +
            '<td class="num">' + money(s.commission) + '</td>' +
            '<td class="num">' + money(s.merchantProceeds) + '</td>' +
            '<td style="white-space:nowrap;">' + esc(fmtTime(s.settledAt)) + '</td>' +
        '</tr>';
    }).join('');
}

/* ⑧ 评价列表(公开): 商户ID筛选(留空查全部) */
async function loadReviews() {
    var mid = String(document.getElementById('reviewMerchantId').value || '').trim();
    state.reviewsLoaded = true;
    var url = state.apiBase + '/api/alliance/reviews'
        + (mid ? '?merchantId=' + encodeURIComponent(mid) : '');
    try {
        state.reviews = await fetchJson(url);
    } catch (err) { sectionError('reviewBody', 5, err); throw err; }
    renderReviews();
}

/* 评价表格: 订单/评分星级★/内容/AI审评分/折叠状态; 折叠评价整行灰显 */
function renderReviews() {
    var list = state.reviews;
    document.getElementById('reviewCount').textContent = '共 ' + list.length + ' 条';
    var tbody = document.getElementById('reviewBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="dash-empty">暂无评价(订单结算后方可评价, 一单一评)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (r) {
        var ai = r.aiReview || {};
        var lv = REVIEW_AI_LEVEL[ai.level] || null;
        var content = String(r.content || '');
        return '<tr' + (r.folded ? ' class="folded-row"' : '') + '>' +
            '<td class="cap-id">' + esc(r.orderId) + '</td>' +
            '<td><span class="stars">' + stars(r.score) + '</span> <b>' + (r.score != null ? r.score : '--') + '</b></td>' +
            '<td style="white-space:normal;max-width:300px;" title="' + esc(content) + '">'
                + esc(truncate(content, 40)) + '</td>' +
            '<td>' + (ai.score != null
                ? '<b>' + ai.score + '</b>' + (lv ? ' <span class="badge ' + lv[1] + '">' + esc(lv[0]) + '</span>' : '')
                : '<span class="cap-id">未审评</span>') + '</td>' +
            '<td>' + (r.folded
                ? '<span class="badge weak" title="' + esc(r.foldReason || '') + '">已折叠</span>'
                : '<span class="badge green">展示中</span>') + '</td>' +
        '</tr>';
    }).join('');
}

/* ⑨ 执行月度考核(admin): GMV/星级 → S/A/B/C → 连续C处置 */
async function runAssessment() {
    var month = String(document.getElementById('assessMonth').value || '').trim();
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) {
        showBanner('errorBanner', '考核月份格式须为 YYYY-MM(如 2026-09)');
        return;
    }
    var btn = document.getElementById('btnRunAssess');
    btn.disabled = true; btn.textContent = '考核中…';
    try {
        var r = await fetchJson(
            state.apiBase + '/api/alliance/assessment/run?month=' + encodeURIComponent(month),
            { method: 'POST' }, 'admin');
        renderAssessResult(r);
        showBanner('infoBanner', '月度考核完成(' + esc(r.month) + '): 考核 ' + r.assessed
            + ' 家, 暂停 ' + (r.suspended || []).length + ' 家, 清退 ' + (r.terminated || []).length + ' 家');
        /* 局部刷新: 考核处置会变更商户状态与全景统计; 历史表已查询过则一并刷新 */
        var tasks = [loadMerchants(), loadOverview()];
        if (state.assessLoaded) { tasks.push(loadAssessmentHistory()); }
        await Promise.all(tasks);
    } catch (err) {
        showBanner('errorBanner', '执行考核失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '执行考核';
    }
}

/* 考核结果摘要: 月份/考核数/暂停数(含ID)/清退数(含ID) */
function renderAssessResult(r) {
    var ids = function (arr) { return (arr && arr.length) ? arr.join('、') : '无'; };
    document.getElementById('assessResult').innerHTML =
        '<div class="tool-lines">考核月份: <b>' + esc(r.month) + '</b> · 考核商户: <b>'
            + (r.assessed != null ? r.assessed : '--') + ' 家</b> · 暂停: <b>'
            + (r.suspended || []).length + ' 家</b>(' + esc(ids(r.suspended)) + ') · 清退: <b>'
            + (r.terminated || []).length + ' 家</b>(' + esc(ids(r.terminated)) + ')</div>';
}

/* 商户考核历史(admin): 月份/GMV/星级/等级徽章/连续C/动作 */
async function loadAssessmentHistory() {
    var mid = String(document.getElementById('assessMerchantId').value || '').trim();
    if (!mid) {
        document.getElementById('assessBody').innerHTML =
            '<tr><td colspan="6" class="dash-empty">输入商户ID查询考核历史</td></tr>';
        return;
    }
    state.assessLoaded = true;
    try {
        state.assessments = await fetchJson(
            state.apiBase + '/api/alliance/merchants/' + encodeURIComponent(mid) + '/assessment',
            null, 'admin');
    } catch (err) { sectionError('assessBody', 6, err); throw err; }
    renderAssessments();
}

/* 考核历史表格: 月份/GMV/星级/等级(S金A绿B蓝C红)/连续C/动作徽章 */
function renderAssessments() {
    var list = state.assessments || [];
    var tbody = document.getElementById('assessBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="dash-empty">该商户暂无考核记录(执行考核后生成)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (a) {
        var grade = a.grade || 'C';
        var act = ASSESS_ACTION[a.action] || null;
        return '<tr>' +
            '<td><b>' + esc(a.month) + '</b></td>' +
            '<td class="num">' + money(a.gmv) + '</td>' +
            '<td>' + (a.ratingAvg != null ? Number(a.ratingAvg).toFixed(2) : '--') + '</td>' +
            '<td><span class="badge ' + (GRADE_BADGE[grade] || 'weak') + '">' + esc(grade) + '</span></td>' +
            '<td class="num">' + (a.consecutiveC != null ? a.consecutiveC : '--') + '</td>' +
            '<td>' + (act ? '<span class="badge ' + act[1] + '">' + esc(act[0]) + '</span>' : '—') + '</td>' +
        '</tr>';
    }).join('');
}

/* ⑩ 我的场景单(会员端 X-Member-Id): 一单三子单 + 核销码 + 状态 */
async function loadScenes() {
    var mid = String(document.getElementById('memberId').value || '').trim();
    if (!mid) {
        document.getElementById('sceneCount').textContent = '';
        document.getElementById('sceneBody').innerHTML =
            '<tr><td colspan="7" class="dash-empty">请输入 X-Member-Id 后查询</td></tr>';
        return;
    }
    state.scenesLoaded = true;
    try {
        state.scenes = await fetchJson(state.apiBase + '/api/alliance/scenes', null, 'member');
    } catch (err) { sectionError('sceneBody', 7, err); throw err; }
    renderScenes();
}

/* 场景单表格: 场景ID/类型/人数/三子单(酒·菜·境)/总额/核销码/状态 */
function renderScenes() {
    var list = state.scenes || [];
    document.getElementById('sceneCount').textContent = '共 ' + list.length + ' 单';
    var tbody = document.getElementById('sceneBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="dash-empty">该会员暂无场景单(酒友小聚「选酒→配菜→订境」后生成)</td></tr>';
        return;
    }
    /* 三子单徽章配色: 酒金/菜绿/境蓝 */
    var itemBadge = { wine: 'gold', dish: 'green', venue: 'blue' };
    var itemLabel = { wine: '酒', dish: '菜', venue: '境' };
    tbody.innerHTML = list.map(function (s) {
        var st = SCENE_STATUS[s.status] || null;
        var items = (s.items || []).map(function (it) {
            return '<span class="badge ' + (itemBadge[it.type] || 'weak') + '">'
                + esc(itemLabel[it.type] || it.type) + ' ' + esc(it.orderId) + ' ¥' + money(it.amount) + '</span>';
        }).join('');
        return '<tr>' +
            '<td class="cap-id">#' + s.sceneId + '</td>' +
            '<td>' + (s.type === 'gathering' ? '酒友小聚' : esc(s.type || '--')) + '</td>' +
            '<td class="num">' + (s.partySize != null ? s.partySize : '--') + '</td>' +
            '<td><div class="trace-chips">' + items + '</div></td>' +
            '<td class="num">' + money(s.totalAmount) + '</td>' +
            '<td class="cap-id">' + esc(s.redeemCode || '—') + '</td>' +
            '<td>' + (st ? '<span class="badge ' + st[1] + '">' + esc(st[0]) + '</span>' : esc(s.status)) + '</td>' +
        '</tr>';
    }).join('');
}

/* ⑩ 定制需求列表(admin 头): 状态机六态徽章 */
async function loadCustomDemands() {
    state.demandsLoaded = true;
    try {
        state.demands = await fetchJson(state.apiBase + '/api/alliance/custom-demands', null, 'admin');
    } catch (err) { sectionError('demandBody', 7, err); throw err; }
    renderDemands();
}

/* 定制需求表格: 需求ID/商户/类型/描述/预算/报价/状态徽章 */
function renderDemands() {
    var list = state.demands || [];
    var tbody = document.getElementById('demandBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="dash-empty">暂无定制需求</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (d) {
        var st = CUSTOM_STATUS[d.status] || null;
        var desc = String(d.description || '');
        return '<tr>' +
            '<td class="cap-id">#' + d.demandId + '</td>' +
            '<td class="cap-id">#' + esc(d.merchantId) + '</td>' +
            '<td>' + esc(CUSTOM_TYPE_LABEL[d.demandType] || d.demandType || '--') + '</td>' +
            '<td style="white-space:normal;max-width:200px;" title="' + esc(desc) + '">'
                + esc(truncate(desc, 24)) + '</td>' +
            '<td class="num">' + (d.budget ? money(d.budget) : '—') + '</td>' +
            '<td class="num">' + (d.quotedPrice ? money(d.quotedPrice) : '—') + '</td>' +
            '<td>' + (st ? '<span class="badge ' + st[1] + '">' + esc(st[0]) + '</span>' : esc(d.status)) + '</td>' +
        '</tr>';
    }).join('');
}

/* ========= 交互: 商品行展开/收起(事件委托, 按钮点击不触发) ========= */
document.getElementById('productBody').addEventListener('click', function (evt) {
    if (evt.target.closest('button')) { return; }
    var tr = evt.target.closest('tr[data-pid]');
    if (!tr) { return; }
    var pid = Number(tr.getAttribute('data-pid'));
    state.productExpanded[pid] = !state.productExpanded[pid];
    renderProducts();
});

/* ========= 横幅/工具栏 ========= */
function showBanner(id, msg) {
    var el = document.getElementById(id);
    el.textContent = msg;
    el.style.display = 'block';
}
function hideBanner(id) {
    document.getElementById(id).style.display = 'none';
}

function saveApiBase() {
    var v = document.getElementById('apiBase').value.trim().replace(/\/+$/, '');
    if (!v) { return; }
    state.apiBase = v;
    localStorage.setItem(API_BASE_KEY, v);
    showBanner('infoBanner', '后端地址已保存: ' + v);
    refreshData();
}

function toggleAutoRefresh() {
    if (state.autoTimer) { clearInterval(state.autoTimer); state.autoTimer = null; }
    if (document.getElementById('autoRefresh').checked) {
        state.autoTimer = setInterval(refreshData, state.AUTO_MS);
    }
}

/* ========= 初始化 ========= */
(function init() {
    if (typeof Auth !== 'undefined') {
        Auth.mountBadge(document.getElementById('authBadge'));
    }
    document.getElementById('apiBase').value = state.apiBase;
    /* 考核月份默认当月(本地时区 YYYY-MM) */
    var now = new Date();
    document.getElementById('assessMonth').value =
        now.getFullYear() + '-' + ('0' + (now.getMonth() + 1)).slice(-2);
    /* 就近推荐/场景单/定制需求随默认输入(36.06/120.38 · 60001)首次加载演示;
       此后 30s 自动刷新沿用输入框当前值, 不清表单 */
    state.nearbyLoaded = true;
    state.scenesLoaded = true;
    state.demandsLoaded = true;
    toggleAutoRefresh();
    refreshData();
})();
