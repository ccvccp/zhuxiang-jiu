/* 42号·无感开票拦截面板 js/invoice-dashboard.js
 * 四区块渲染(对齐 ride-dashboard.js 模式) + 四步处置法操作
 */
var API_BASE_KEY = 'invoiceDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    timer: null,
};

function headers() { return { 'X-Role': 'admin' }; }

async function fetchJson(url, options, label) {
    var opts = Object.assign({ headers: headers() }, options || {});
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
function pct(v) { return ((v || 0) * 100).toFixed(1) + '%'; }
var ACTION_ST = {
    auto_issue: 'green', manual_queue: 'yellow',
    reject: 'red', collect: 'blue'};
var APPEAL_ST = {
    pending: 'yellow', approved: 'green', rejected: 'red'};

function saveConn() {
    state.apiBase = document.getElementById('apiBase').value.trim()
        || 'http://localhost:8000';
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    refreshData();
}

/* ---------- ① 统计 ---------- */
async function loadStats() {
    var b = await fetchJson(api('/api/invoice/admin/stats'), {},
                            '统计');
    var by = b.byAction || {};
    var ap = b.appeals || {};
    var cells = [
        ['决策总数', b.total],
        ['自动开具', by.auto_issue || 0],
        ['待确认', by.manual_queue || 0],
        ['拦截', by.reject || 0],
        ['引导收集', by.collect || 0],
        ['自动化率', pct(b.automationRate)],
        ['申诉总数', ap.total || 0],
        ['待裁决', ap.pending || 0],
        ['误拦截率', pct(b.falsePositiveRate)],
    ];
    document.getElementById('ovCells').innerHTML =
        cells.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c[0])
                + '</div><div class="v">' + esc(c[1]) + '</div></div>';
        }).join('');
}

/* ---------- ② 申诉队列 ---------- */
async function loadAppeals() {
    var st = document.getElementById('appealStatusSel').value;
    var q = st ? ('?status=' + st) : '';
    var b = await fetchJson(api('/api/invoice/admin/appeals' + q), {},
                            '申诉队列');
    var rows = (b.appeals || []).map(function (a) {
        var act = a.status === 'pending'
            ? '<button class="btn-mini" onclick="decideAppeal('
              + a.appealId + ', true)">恢复</button>'
              + '<button class="btn-mini danger" onclick="decideAppeal('
              + a.appealId + ', false)">维持</button>'
            : '<span class="dash-empty">'
              + esc(a.reviewNote || '-') + '</span>';
        return '<tr><td>' + esc(a.appealId) + '</td><td>'
            + esc(a.orderId) + '</td><td>' + esc(a.memberId)
            + '</td><td>' + esc(a.reason) + '</td><td>'
            + esc(a.scoreAtDecision) + '</td><td>'
            + '<span class="badge ' + (APPEAL_ST[a.status] || 'weak')
            + '">' + esc(a.status) + '</span></td><td>' + act
            + '</td></tr>';
    }).join('');
    document.querySelector('#appealTable tbody').innerHTML = rows;
    document.getElementById('appealEmpty').style.display =
        rows ? 'none' : 'block';
}
async function decideAppeal(id, approve) {
    var note = approve ? '误拦确认, 会员可补开' : '维持拦截';
    try {
        await fetchJson(api('/api/invoice/admin/appeals/' + id
            + '/decide'), { method: 'POST',
                            body: { approve: approve, note: note } },
            '申诉裁决');
        showInfo('申诉 #' + id + (approve ? ' 已恢复(通知会员手动补开)'
                                       : ' 已维持拦截归档'));
        loadAppeals(); loadStats(); loadRejects();
    } catch (e) { showError(e.message); }
}

/* ---------- ③ 拦截流水(因子判读) ---------- */
async function loadRejects() {
    var b = await fetchJson(api('/api/invoice/admin/decisions'
        + '?action=reject'), {}, '拦截流水');
    var rows = (b.decisions || []).slice(0, 50).map(function (d) {
        return '<tr><td>' + esc(d.orderId) + '</td><td>'
            + esc(d.memberId) + '</td><td>' + esc(d.score)
            + '</td><td>' + esc(d.detail || '-') + '</td><td>'
            + esc((d.decidedAt || '').slice(0, 19)) + '</td><td>'
            + '<button class="btn-mini ghost" onclick="showFactors(\''
            + esc(d.orderId) + '\')">因子</button></td></tr>';
    }).join('');
    document.querySelector('#rejectTable tbody').innerHTML = rows;
    document.getElementById('rejectEmpty').style.display =
        rows ? 'none' : 'block';
}
async function showFactors(orderId) {
    var b = await fetchJson(api('/api/invoice/admin/decisions'
        + '?action=reject'), {}, '拦截流水');
    var d = (b.decisions || []).find(function (x) {
        return x.orderId === orderId;
    }) || {};
    var factors = (d.scoreSnapshot || {}).factors || d.factors || [];
    var d2 = document.getElementById('factorDetail');
    d2.style.display = 'block';
    d2.textContent = '订单 ' + orderId + ' 因子明细:\n'
        + JSON.stringify(factors, null, 2);
}

/* ---------- ④ 全量决策流水 ---------- */
async function loadDecisions() {
    var act = document.getElementById('actionSel').value;
    var q = act ? ('?action=' + act) : '';
    var b = await fetchJson(api('/api/invoice/admin/decisions' + q), {},
                            '决策流水');
    var rows = (b.decisions || []).slice(0, 100).map(function (d) {
        return '<tr><td>' + esc(d.orderId) + '</td><td>'
            + esc(d.memberId) + '</td><td><span class="badge '
            + (ACTION_ST[d.action] || 'weak') + '">' + esc(d.action)
            + '</span></td><td>' + esc(d.score) + '</td><td>'
            + esc(d.invoiceNo || '-') + '</td><td>'
            + (d.evidenceHash ? '有' : '-') + '</td><td>'
            + esc(d.detail || '-') + '</td><td>'
            + esc((d.decidedAt || '').slice(0, 19)) + '</td></tr>';
    }).join('');
    document.querySelector('#decisionTable tbody').innerHTML = rows;
    document.getElementById('decisionEmpty').style.display =
        rows ? 'none' : 'block';
}

/* ---------- 总刷新 ---------- */
async function refreshData() {
    try {
        await Promise.all([loadStats(), loadAppeals(),
                           loadRejects(), loadDecisions()]);
        markUpdate();
    } catch (e) { showError(e.message); }
}
(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    refreshData();
    setInterval(function () { refreshData(); }, 30000);
})();
