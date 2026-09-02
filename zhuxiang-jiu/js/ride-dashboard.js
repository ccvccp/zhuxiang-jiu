/* 41号·AI智能代驾看板 js/ride-dashboard.js
 * 连接配置(localStorage 持久化) + 7 区块渲染(对齐 blogger-dashboard.js 模式)
 */
var API_BASE_KEY = 'rideDash.apiBase';
var MEMBER_KEY = 'rideDash.memberId';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    memberId: localStorage.getItem(MEMBER_KEY) || '1',
    timer: null,
};

function headers(admin) {
    if (admin) return { 'X-Role': 'admin' };
    return { 'X-Member-Id': state.memberId };
}

async function fetchJson(url, options, label, admin) {
    var opts = Object.assign({ headers: headers(admin) }, options || {});
    if (opts.body && typeof opts.body === 'object') {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
    }
    var resp = await fetch(url, opts);
    var body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    if (!resp.ok) {
        var msg = (body && (body.detail || body.error)) || resp.status;
        throw new Error(label + ': ' + msg);
    }
    return body;
}
function api(path) { return state.apiBase + path; }
function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}
function showError(msg) {
    var el = document.getElementById('errBar');
    el.textContent = msg; el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}
function showInfo(msg) {
    var el = document.getElementById('infoBar');
    el.textContent = msg; el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}
function markUpdate() {
    document.getElementById('lastUpdate').textContent =
        '更新于 ' + new Date().toLocaleTimeString();
}
function badge(map, st) {
    var cls = map[st] || 'weak';
    return '<span class="badge ' + cls + '">' + esc(st || '-') + '</span>';
}
var RIDE_ST = {
    settled: 'green', trip_started: 'blue', dispatched: 'blue',
    driver_arriving: 'blue', requested: 'weak', settling: 'yellow',
    cancelled: 'weak', no_driver: 'red'};
var COUPON_ST = {
    granted: 'green', used: 'weak', expired: 'red', revoked: 'red'};
var RECON_ST = {
    reconciling: 'yellow', confirmed: 'blue', paid: 'green',
    diff: 'red', investigating: 'red', resolved: 'yellow'};
var RISK_ST = { resolved: true };

/* ---------- 连接 ---------- */
function saveConn() {
    state.apiBase = document.getElementById('apiBase').value.trim()
        || 'http://localhost:8000';
    state.memberId = document.getElementById('memberId').value.trim() || '1';
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    localStorage.setItem(MEMBER_KEY, state.memberId);
    refreshData();
}

/* ---------- ① 全景 ---------- */
async function loadOverview() {
    var b = await fetchJson(api('/api/ride/admin/overview'), {}, '全景统计', true);
    var apps = b.applications || {};
    var cells = [
        ['司机池', b.poolTotal], ['在线', b.onlineCount],
        ['自营/加盟/直发',
            (b.byTrack ? (b.byTrack.self || 0) + '/' + (b.byTrack.partner || 0)
             + '/' + (b.byTrack.platform || 0) : '-')],
        ['审查申请', apps.total || 0],
        ['已通过', apps.approved || 0],
        ['待复核', apps.manualReview || 0],
        ['已拒绝', apps.rejected || 0],
    ];
    document.getElementById('ovCells').innerHTML = cells.map(function (c) {
        return '<div class="ov-cell"><div class="k">' + esc(c[0])
            + '</div><div class="v">' + esc(c[1]) + '</div></div>';
    }).join('');
}

/* ---------- ② 券包 ---------- */
async function loadCoupons() {
    var b = await fetchJson(api('/api/ride/coupons'), {}, '券包', false);
    var rows = (b.coupons || []).map(function (c) {
        return '<tr><td>' + esc(c.code) + '</td><td>¥'
            + esc(c.value) + '</td><td>'
            + badge(COUPON_ST, c.status) + '</td><td>'
            + esc(c.orderId || '-') + '</td><td>'
            + esc((c.expiresAt || '').slice(0, 10)) + '</td></tr>';
    }).join('');
    document.querySelector('#couponTable tbody').innerHTML = rows;
    document.getElementById('couponEmpty').style.display =
        rows ? 'none' : 'block';
}

/* ---------- ③ 行程 ---------- */
async function loadRides() {
    var st = document.getElementById('rideStatusSel').value;
    var q = st ? ('?status=' + st) : '';
    var b = await fetchJson(api('/api/ride/orders' + q), {}, '我的行程', false);
    var rows = (b.rides || []).map(function (r) {
        var snap = r.driverSnapshot || {};
        var p = r.pricing || {};
        return '<tr><td>' + esc(r.rideId) + '</td><td>'
            + badge(RIDE_ST, r.status) + '</td><td>'
            + esc(snap.trackName || '-') + '</td><td>'
            + esc(snap.name || '-') + '</td><td>'
            + esc(r.distanceKm) + 'km</td><td>¥'
            + esc(p.totalAmount != null ? p.totalAmount : '-')
            + '</td><td>¥' + esc(p.couponDeduction != null
                ? p.couponDeduction : '-') + '</td><td>¥'
            + esc(p.extraCharge != null ? p.extraCharge : '-')
            + '</td></tr>';
    }).join('');
    document.querySelector('#rideTable tbody').innerHTML = rows;
    document.getElementById('rideEmpty').style.display =
        rows ? 'none' : 'block';
}

/* ---------- ④ 司机池 ---------- */
async function loadPool() {
    var track = document.getElementById('trackSel').value;
    var st = document.getElementById('poolStatusSel').value;
    var q = [];
    if (track) q.push('track=' + track);
    if (st) q.push('status=' + st);
    var b = await fetchJson(api('/api/ride/admin/pool'
        + (q.length ? '?' + q.join('&') : '')), {}, '司机池', true);
    var rows = (b.drivers || []).map(function (d) {
        return '<tr><td>' + esc(d.driverId) + '</td><td>'
            + esc(d.name) + '</td><td>' + esc(d.trackName)
            + '</td><td>' + esc(d.status) + '</td><td>'
            + esc(d.rating) + '</td><td>' + esc(d.completedOrders)
            + '</td><td>' + esc(d.todayOrders) + '</td><td>'
            + esc(d.plateNo || '-') + '</td></tr>';
    }).join('');
    document.querySelector('#poolTable tbody').innerHTML = rows
        || '<tr><td colspan="8" class="dash-empty">无匹配司机</td></tr>';
}

/* ---------- ⑤ 风险面板 ---------- */
async function loadRisk() {
    var b = await fetchJson(api('/api/ride/admin/risk-panel'), {},
                            '风险面板', true);
    var by = b.byType || {};
    var cells = [
        ['事件总数', b.total], ['未处置', b.unresolved],
        ['POI高频', by.poi_high_frequency || 0],
        ['行程超时', by.trip_timeout || 0],
        ['里程异常', by.mileage_anomaly || 0],
    ];
    document.getElementById('riskSummary').innerHTML =
        cells.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c[0])
                + '</div><div class="v">' + esc(c[1]) + '</div></div>';
        }).join('');
    var rows = (b.events || []).slice(0, 20).map(function (e) {
        var st = e.resolved
            ? '<span class="badge green">已处置</span>'
            : '<span class="badge red">未处置</span>';
        var act = e.resolved ? '' : '<button class="btn-mini" onclick="'
            + 'resolveRisk(' + e.riskId + ')">处置</button>';
        return '<tr><td>' + esc(e.riskId) + '</td><td>'
            + esc(e.type) + '</td><td>' + esc(e.rideId || '-')
            + '</td><td>' + esc(e.detail) + '</td><td>' + st
            + '</td><td>' + act + '</td></tr>';
    }).join('');
    document.querySelector('#riskTable tbody').innerHTML = rows
        || '<tr><td colspan="6" class="dash-empty">无风险事件</td></tr>';
}
async function resolveRisk(id) {
    try {
        await fetchJson(api('/api/ride/admin/risk-events/' + id
            + '/resolve'), { method: 'POST', body: { note: '看板处置' } },
            '处置风险', true);
        showInfo('风险事件 #' + id + ' 已处置');
        loadRisk();
    } catch (e) { showError(e.message); }
}
async function runSafetyScan() {
    try {
        var b = await fetchJson(api('/api/ride/admin/safety/scan'),
                                { method: 'POST' }, '超时扫描', true);
        showInfo('扫描 ' + (b.scanned || 0) + ' 个进行中行程, 新增预警 '
            + (b.warnings || []).length + ' 条');
        loadRisk();
    } catch (e) { showError(e.message); }
}

/* ---------- ⑥ 对账单 ---------- */
async function loadRecons() {
    var b = await fetchJson(api('/api/ride/admin/reconciliations'), {},
                            '对账单列表', true);
    var rows = (b.reconciliations || []).map(function (r) {
        var act = [];
        if (r.status === 'diff') act.push(['investigate', '调查']);
        if (r.status === 'investigating') act.push(['resolve', '处理完']);
        if (r.status === 'reconciling' || r.status === 'resolved')
            act.push(['confirm', '确认']);
        if (r.status === 'confirmed') act.push(['pay', '付款']);
        act.push(['detail', '差异']);
        var btns = act.map(function (a) {
            return '<button class="btn-mini' + (a[0] === 'detail'
                ? ' ghost' : '') + '" onclick="reconAction(\''
                + a[0] + '\', \'' + esc(r.reconNo) + '\')">'
                + a[1] + '</button>';
        }).join('');
        return '<tr><td>' + esc(r.reconNo) + '</td><td>'
            + esc(r.period) + '</td><td>' + esc(r.track) + '</td><td>'
            + esc(r.totalOrders) + '</td><td>¥' + esc(r.siteTotal)
            + '</td><td>¥' + esc(r.channelTotal) + '</td><td>'
            + esc(r.diffCount) + '</td><td>'
            + badge(RECON_ST, r.status) + '</td><td>' + btns
            + '</td></tr>';
    }).join('');
    document.querySelector('#reconTable tbody').innerHTML = rows
        || '<tr><td colspan="9" class="dash-empty">暂无对账单</td></tr>';
}
async function startRecon() {
    var period = document.getElementById('reconPeriod').value.trim();
    var track = document.getElementById('reconTrackSel').value;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(period)) {
        showError('账期格式须为 YYYY-MM-DD'); return;
    }
    try {
        var b = await fetchJson(api('/api/ride/admin/reconciliation/start'),
            { method: 'POST', body: { period: period, track: track } },
            '生成对账单', true);
        showInfo('对账单 ' + b.reconNo + ' 已生成(状态 ' + b.status
            + ', 差异 ' + b.diffCount + ')');
        loadRecons();
    } catch (e) { showError(e.message); }
}
async function reconAction(action, reconNo) {
    try {
        if (action === 'detail') {
            var b = await fetchJson(api('/api/ride/admin/reconciliation/'
                + reconNo), {}, '对账详情', true);
            var d = document.getElementById('reconDetail');
            d.style.display = 'block';
            d.textContent = JSON.stringify(
                (b.reconciliation || {}).diffDetails || [], null, 2)
                || '无差异明细';
            return;
        }
        await fetchJson(api('/api/ride/admin/reconciliation/' + reconNo
            + '/' + action), { method: 'POST', body: {} },
            '对账操作', true);
        showInfo('对账单 ' + reconNo + ' ' + action + ' 完成');
        loadRecons();
    } catch (e) { showError(e.message); }
}

/* ---------- ⑦ 学习闭环 ---------- */
async function loadLearning() {
    var b = await fetchJson(api('/api/ride/admin/learning/status'), {},
                            '学习状态', true);
    var cells = [
        ['已结算行程', (b.dispatch || {}).settled],
        ['派单已回流', (b.dispatch || {}).fed],
        ['审查已通过', (b.gate || {}).approved],
        ['审查已回流', (b.gate || {}).fed],
        ['评价已标注', (b.review || {}).annotated],
        ['评价已回流', (b.review || {}).fed],
    ];
    document.getElementById('learnCells').innerHTML =
        cells.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c[0])
                + '</div><div class="v">' + esc(c[1]) + '</div></div>';
        }).join('');
}
async function collectLearning() {
    try {
        var b = await fetchJson(api('/api/ride/admin/learning/collect'),
                                { method: 'POST' }, '批量回流', true);
        showInfo('回流完成: 派单 ' + (b.dispatch || {}).submitted
            + ' 条, 审查 ' + (b.gate || {}).submitted + ' 条');
        loadLearning();
    } catch (e) { showError(e.message); }
}
async function runLearning() {
    try {
        var b = await fetchJson(api('/api/ride/admin/learning/run'),
                                { method: 'POST' }, '触发学习', true);
        var d = document.getElementById('learnDetail');
        d.style.display = 'block';
        d.textContent = JSON.stringify(b.results || {}, null, 2);
        showInfo('学习触发完成(明细见下方)');
    } catch (e) { showError(e.message); }
}

/* ---------- ⑧ 营销 ROI ---------- */
async function loadRoi() {
    var b = await fetchJson(api('/api/ride/admin/roi'), {}, '营销ROI', true);
    var st = b.byStatus || {};
    var cells = [
        ['总发放', b.totalGranted],
        ['已核销', st.used || 0],
        ['核销率', ((b.usedRate || 0) * 100).toFixed(1) + '%'],
        ['未使用', st.granted || 0],
        ['已过期', st.expired || 0],
        ['已作废', st.revoked || 0],
        ['覆盖会员', b.distinctMembers],
        ['复购会员', b.repeatMembers],
        ['复购率', ((b.repeatRate || 0) * 100).toFixed(1) + '%'],
        ['营销成本(¥)', b.marketingCost],
        ['券均拉动(¥)', b.avgRideAmountPerUsedCoupon],
    ];
    document.getElementById('roiCells').innerHTML =
        cells.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c[0])
                + '</div><div class="v">' + esc(c[1]) + '</div></div>';
        }).join('');
}

/* ---------- 总刷新 ---------- */
async function refreshData() {
    try {
        await Promise.all([
            loadOverview(), loadCoupons(), loadRides(),
            loadPool(), loadRisk(), loadRecons(), loadLearning(),
            loadRoi(),
        ]);
        markUpdate();
    } catch (e) { showError(e.message); }
}
function startTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(function () {
        if (document.getElementById('autoRef').checked) refreshData();
    }, 30000);
}
(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    document.getElementById('memberId').value = state.memberId;
    refreshData();
    startTimer();
})();
