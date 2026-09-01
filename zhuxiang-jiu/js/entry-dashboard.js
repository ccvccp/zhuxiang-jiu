/* 39号·AI智能网站入口管理模块 v1.0 · 入口运营看板脚本(P2)
 * 页面: ai-entry-dashboard.html
 * 鉴权: X-Role: admin(compat 头直传; strict 模式走 JWT)
 * localStorage 键: entryDash.apiBase
 */
'use strict';

var API_BASE_KEY = 'entryDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    timer: null,
};

function $(id) { return document.getElementById(id); }

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

function showError(msg) {
    var el = $('errorBanner');
    el.textContent = '数据加载失败：' + msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}

function showInfo(msg) {
    var el = $('infoBanner');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}

var MODE_NAMES = {
    password: '密码', sms: '短信', qr: '扫码',
    fingerprint: '指纹', face: '刷脸', oauth: '三方',
};
var ACTION_BADGE = {
    allow: ['放行', 'green'], step_up: ['二次核验', 'yellow'],
    challenge: ['强核验', 'orange'], block: ['拦截', 'red'],
};
var REVIEW_BADGE = {
    none: ['未复核', 'weak'], confirm: ['正确', 'green'],
    false_block: ['误拦', 'red'], false_allow: ['漏放', 'orange'],
};

function modeName(m) { return MODE_NAMES[m] || m || '-'; }

/* ========= 工具栏 ========= */

function saveApiBase() {
    state.apiBase = $('apiBase').value.trim().replace(/\/+$/, '');
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    showInfo('后端地址已保存: ' + state.apiBase);
    refreshData();
}

function toggleAutoRefresh() {
    if ($('autoRefresh').checked) { startTimer(); }
    else { clearInterval(state.timer); state.timer = null; }
}

function startTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(refreshData, 30000);
}

/* ========= ① 全景统计 ========= */

async function loadOverview() {
    var d = await fetchJson(state.apiBase + '/api/entry/report/overview',
                            null, '全景统计');
    var o = d.data || {};
    var modes = o.modeStats || {};
    var actions = o.actionStats || {};
    var html = '<div class="ov-group"><div class="ov-gtitle">总量</div>'
        + '<div class="ov-cells">'
        + '<div class="ov-cell"><b>' + (o.totalEvents || 0) + '</b><span>登录事件</span></div>'
        + '<div class="ov-cell"><b>' + (o.totalDecisions || 0) + '</b><span>风控决策</span></div>'
        + '<div class="ov-cell"><b>' + (o.degradedDecisions || 0) + '</b><span>评分降级</span></div>'
        + '</div></div>';
    html += '<div class="ov-group"><div class="ov-gtitle">风险动作分布</div>'
        + '<div class="ov-cells">'
        + Object.keys(ACTION_BADGE).map(function (a) {
            return '<div class="ov-cell"><b>' + (actions[a] || 0) + '</b><span>'
                + ACTION_BADGE[a][0] + '</span></div>';
        }).join('') + '</div></div>';
    html += '<div class="ov-group"><div class="ov-gtitle">通道尝试</div>'
        + '<div class="ov-cells">'
        + Object.keys(modes).map(function (m) {
            return '<div class="ov-cell"><b>' + (modes[m].attempts || 0)
                + '</b><span>' + modeName(m) + '</span></div>';
        }).join('') + '</div></div>';
    $('ovWrap').innerHTML = html;
}

/* ========= ② 通道漏斗 ========= */

async function loadModes() {
    var d = await fetchJson(state.apiBase + '/api/entry/report/overview',
                            null, '通道统计');
    var modes = (d.data || {}).modeStats || {};
    var entries = Object.keys(modes).map(function (m) {
        return { mode: m, s: modes[m] };
    });
    $('modeCount').textContent = entries.length + ' 个通道';
    var hints = {
        password: '传统通道(兜底)', sms: '60s 冷却+日限 10 次',
        qr: 'PC 展码手机确认', fingerprint: '设备端本地验证(Mock 轨)',
        face: '设备本地为主(Mock 轨)', oauth: '微信/支付宝/QQ',
    };
    var rows = entries.map(function (e) {
        var rate = (e.s.rate != null ? e.s.rate : 0);
        var badge = rate >= 90 ? 'green' : (rate >= 60 ? 'yellow' : 'red');
        return '<tr><td><b>' + modeName(e.mode) + '</b></td>'
            + '<td class="num">' + (e.s.attempts || 0) + '</td>'
            + '<td class="num">' + (e.s.success || 0) + '</td>'
            + '<td class="num"><span class="badge ' + badge + '">'
            + rate + '%</span></td>'
            + '<td style="white-space:normal;">' + (hints[e.mode] || '') + '</td></tr>';
    }).join('');
    $('modeBody').innerHTML = rows ||
        '<tr><td colspan="5" class="dash-empty">暂无登录数据</td></tr>';
}

/* ========= ③ 决策复核 ========= */

async function loadDecisions() {
    var action = $('actionFilter').value;
    var url = state.apiBase + '/api/entry/decisions?limit=100'
        + (action ? '&action=' + action : '');
    var d = await fetchJson(url, null, '决策列表');
    var list = d.data || [];
    $('decisionCount').textContent = list.length + ' 条';
    var rows = list.map(function (x) {
        var a = ACTION_BADGE[x.action] || [x.action, 'weak'];
        var rv = REVIEW_BADGE[x.reviewStatus]
            || [x.reviewStatus || '未复核', 'weak'];
        var ops = '';
        if (!x.reviewStatus || x.reviewStatus === 'none') {
            ops = '<button class="btn-mini" onclick="reviewDecision('
                + x.decisionId + ', \'confirm\')">正确</button> '
                + '<button class="btn-mini danger" onclick="reviewDecision('
                + x.decisionId + ', \'false_block\')">误拦</button> '
                + '<button class="btn-mini danger" onclick="reviewDecision('
                + x.decisionId + ', \'false_allow\')">漏放</button>';
        } else {
            ops = '<span style="font-size:11px;color:var(--color-text-light);">'
                + esc(String(x.reviewedAt || '').slice(0, 19)) + '</span>';
        }
        return '<tr>'
            + '<td class="num">' + x.decisionId + '</td>'
            + '<td class="num">' + (x.memberId || '-') + '</td>'
            + '<td>' + modeName(x.mode) + '</td>'
            + '<td class="num"><b>' + (x.riskScore != null ? x.riskScore : '-')
            + '</b></td>'
            + '<td><span class="badge ' + a[1] + '">' + a[0] + '</span>'
            + (x.degraded ? ' <span class="badge purple">降级</span>' : '')
            + '</td>'
            + '<td><span class="badge ' + rv[1] + '">' + rv[0] + '</span></td>'
            + '<td>' + ops + '</td></tr>';
    }).join('');
    $('decisionBody').innerHTML = rows ||
        '<tr><td colspan="7" class="dash-empty">暂无决策记录</td></tr>';
}

async function reviewDecision(id, verdict) {
    try {
        await fetchJson(
            state.apiBase + '/api/entry/decisions/' + id + '/review',
            { method: 'POST', body: { verdict: verdict } }, '决策复核');
        showInfo('已复核 #' + id + '(' + verdict + '), 反馈回流 AI 自学习');
        loadDecisions(); loadOverview();
    } catch (e) { showError(e.message); }
}

/* ========= ④ 事件流水 ========= */

async function loadEvents() {
    var mode = $('modeFilter').value;
    var url = state.apiBase + '/api/entry/events?limit=100'
        + (mode ? '&mode=' + mode : '');
    var d = await fetchJson(url, null, '事件流水');
    var list = d.data || [];
    $('eventCount').textContent = list.length + ' 条';
    var rows = list.map(function (e) {
        return '<tr>'
            + '<td class="num">' + e.eventId + '</td>'
            + '<td class="num">' + (e.memberId || '-') + '</td>'
            + '<td>' + modeName(e.mode) + '</td>'
            + '<td class="num">' + (e.riskScore != null ? e.riskScore : '-') + '</td>'
            + '<td>' + (e.success
                ? '<span class="badge green">成功</span>'
                : '<span class="badge red">失败</span>')
            + (e.note ? ' <span style="font-size:11px;color:#999;">'
                + esc(e.note) + '</span>' : '') + '</td>'
            + '<td class="cap-id">' + esc(String(e.deviceId || '-').slice(-6)) + '</td>'
            + '<td class="cap-id">' + esc(String(e.createdAt || '').slice(0, 19)) + '</td>'
            + '</tr>';
    }).join('');
    $('eventBody').innerHTML = rows ||
        '<tr><td colspan="7" class="dash-empty">暂无登录事件</td></tr>';
}

/* ========= 主流程 ========= */

async function refreshData() {
    try {
        await Promise.all([loadOverview(), loadModes(),
                           loadDecisions(), loadEvents()]);
        $('lastUpdate').textContent = '最后更新 '
            + new Date().toLocaleTimeString();
    } catch (e) {
        showError(e.message);
        $('lastUpdate').textContent = '刷新失败 '
            + new Date().toLocaleTimeString();
    }
}

(function init() {
    $('apiBase').value = state.apiBase;
    refreshData();
    startTimer();
})();
