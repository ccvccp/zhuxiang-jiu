/**
 * 智运·AI智能物流大模型看板(六区块)
 * 范式: js/legal-dashboard.js 平移——ES5、同源默认、401 汉化。
 * 依赖后端: /api/logistics-ai/*(zw_routes; X-Role admin)
 * 区块: ①模式+总览 ②承运商评分/健康 ③路由留痕 ④异常/延误
 *       ⑤理赔 ⑥成本
 */
'use strict';

var API_BASE_KEY = 'zhiyunDash.apiBase';
/* 默认同源(生产 nginx 已反代 /api/) */
var state = { apiBase: localStorage.getItem(API_BASE_KEY) || '' };

function adminHeaders() {
    var h = { 'X-Role': 'admin', 'Content-Type': 'application/json' };
    var auth = (typeof Auth !== 'undefined') ? Auth.apiHeaders() : null;
    return auth ? Object.assign(h, auth) : h;
}

function api(path) { return state.apiBase + path; }

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}

async function fetchJson(url, options, label) {
    try {
        var resp = await fetch(url, options);
        var text = await resp.text();
        var body = {};
        try { body = JSON.parse(text); } catch (e) { body = { raw: text }; }
        if (!resp.ok) {
            if (resp.status === 401) {
                throw new Error(label + '：请先以管理员账号登录'
                    + '（登录页登录后回到本页刷新）');
            }
            var detail = (body && (body.detail || body.error)) || resp.status;
            throw new Error(label + ' HTTP ' + resp.status + ': ' + detail);
        }
        return body;
    } catch (e) {
        if (e instanceof TypeError) {
            throw new Error(label + ' 无法连接后端(检查地址/跨域)');
        }
        throw e;
    }
}

function showError(msg) {
    var el = document.getElementById('errBar');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}

function markUpdate() {
    document.getElementById('lastUpdate').textContent =
        '更新于 ' + new Date().toLocaleTimeString();
}

function saveConn() {
    var el = document.getElementById('apiBase');
    state.apiBase = el.value.trim().replace(/\/+$/, '');
    el.value = state.apiBase;
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    loadAll();
}

function cells(target, items) {
    document.getElementById(target).innerHTML =
        items.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                '</div><div class="v ' + (c.cls || '') + '">' + esc(c.v) +
                '</div></div>';
        }).join('');
}

function pct(x) { return (x == null) ? '-' : (x * 100).toFixed(0) + '%'; }
function when(s) {
    return String(s || '').replace('T', ' ').slice(5, 16);
}

/* ① 模式态 + 总览 */
async function loadStatus() {
    var mode = null;
    try {
        var mb = await fetchJson(api('/api/logistics-ai/mode'),
                                 { headers: adminHeaders() }, '模式态');
        mode = mb.data || mb;
    } catch (e) { showError(String(e.message || e)); }
    try {
        var body = await fetchJson(api('/api/logistics-ai/status'),
                                   { headers: adminHeaders() }, '总览');
        var d = body.data || {};
        var m = mode || {};
        var cells6 = [
            { k: '模式', v: m.mode || '-',
              cls: m.mode === 'assist' ? 'green' : 'yellow' },
            { k: '订单量', v: d.orders != null ? d.orders : '-' },
            { k: '签收率', v: pct(d.signRate), cls: 'blue' },
            { k: '均签收时效', v: d.avgSignHours != null
                                   ? d.avgSignHours + 'h' : '-' },
        ];
        /* 实测 status_view 字段为 paused(非 guardPaused); 暂停须醒目 */
        if (m.paused) {
            cells6.push({ k: '护栏', v: '已暂停', cls: 'red' });
            cells6.push({ k: '暂停原因', v: '见下', cls: 'red' });
        }
        cells('ovStatus', cells6);
        var noteText = (d.note || '');
        if (m.paused && m.pausedReason) {
            noteText = '⚠️ 护栏暂停: ' + m.pausedReason + ' | ' + noteText;
        }
        document.getElementById('statusNote').textContent =
            noteText + (d.updatedAt ? ' · ' + d.updatedAt : '');
    } catch (e) { showError(String(e.message || e)); }
}

/* ② 承运商评分 + 健康度 */
async function loadCarriers() {
    var scores = {}, healths = [];
    try {
        var sb = await fetchJson(api('/api/logistics-ai/route/carrier-scores'),
                                 { headers: adminHeaders() }, '承运商评分');
        scores = sb.data || {};
    } catch (e) { showError(String(e.message || e)); }
    try {
        var hb = await fetchJson(api('/api/logistics-ai/route/health'),
                                 { headers: adminHeaders() }, '健康度');
        var hd = hb.data || {};
        /* 实测结构: 顶层 {carriers:[...], thresholds, note, monitoredAt} */
        healths = hd.carriers || hd.reports
                  || (Array.isArray(hd) ? hd : []);
    } catch (e) { showError(String(e.message || e)); }
    var hmap = {};
    healths.forEach(function (h) { hmap[h.carrier] = h; });

    var keys = Object.keys(scores);
    var el = document.getElementById('carrierList');
    if (!keys.length) {
        el.innerHTML = '<tr><td colspan="8" class="dash-empty">' +
            '暂无评分数据</td></tr>';
        return;
    }
    el.innerHTML = keys.map(function (k) {
        var s = scores[k] || {};
        var h = hmap[k] || {};
        var hp = h.health === 'healthy'
            ? '<span class="ok-pill">健康</span>'
            : (h.health === 'degraded'
               ? '<span class="risk-pill">降级</span>'
               : '<span class="gray-pill">' + esc(h.health || '-') + '</span>');
        var scoreCls = s.score >= 85 ? 'green'
            : (s.score >= 70 ? 'blue' : 'red');
        return '<tr><td><b>' + esc(s.carrierName || k) + '</b></td>' +
            '<td class="' + scoreCls + '"><b>' + esc(s.score) + '</b></td>' +
            '<td>' + esc(s.sample) + (s.coldStart ? '(冷启动)' : '') + '</td>' +
            '<td>' + pct(s.signRate) + '</td>' +
            '<td>' + esc(s.avgSignHours) + 'h</td>' +
            '<td>¥' + esc(s.avgFee) + '</td>' +
            '<td>' + hp + '</td>' +
            '<td>' + esc(h.explain || s.explain || '') + '</td></tr>';
    }).join('');
}

/* ③ 路由决策留痕 */
async function loadDecisions() {
    try {
        var body = await fetchJson(api('/api/logistics-ai/route/decisions'),
                                   { headers: adminHeaders() }, '路由留痕');
        var rows = body.data || [];
        var el = document.getElementById('decisionList');
        if (!rows.length) {
            el.innerHTML = '<tr><td colspan="5" class="dash-empty">' +
                '暂无决策留痕(发货时自动生成路由建议书)</td></tr>';
            return;
        }
        el.innerHTML = rows.slice().reverse().map(function (r) {
            var d = r.decision || {};
            return '<tr><td>#' + esc(r.decisionId) + '</td>' +
                '<td><b>' + esc(d.carrierName || d.carrier) + '</b></td>' +
                '<td>' + esc(d.combinedScore) + '</td>' +
                '<td>' + esc(d.ruleReason) + '</td>' +
                '<td>' + when(r.decidedAt) + '</td></tr>';
        }).join('');
    } catch (e) { showError(String(e.message || e)); }
}

/* ④ 异常/延误 */
async function loadAnomalies() {
    try {
        var body = await fetchJson(api('/api/logistics-ai/track/anomalies'),
                                   { headers: adminHeaders() }, '异常检测');
        var rows = body.data || [];
        var el = document.getElementById('anomalyList');
        if (!rows.length) {
            el.innerHTML = '<tr><td colspan="6" class="dash-empty">' +
                '暂无异常(四检测器: 揽收超时/停滞/派送失败/签收超时)</td></tr>';
            return;
        }
        var typeNames = { pickup_timeout: '揽收超时',
                          stagnation: '运输停滞',
                          deliver_failed: '派送失败',
                          sign_timeout: '签收超时' };
        el.innerHTML = rows.map(function (a) {
            var sev = a.severity === 'high'
                ? '<span class="risk-pill">高</span>'
                : '<span class="warn-pill">中</span>';
            return '<tr><td>' + esc(a.waybillNo) + '</td>' +
                '<td>' + esc(a.carrier) + '</td>' +
                '<td>' + esc(typeNames[a.type] || a.type) + '</td>' +
                '<td>' + sev + '</td>' +
                '<td>' + esc(a.detail) + '</td>' +
                '<td>' + esc(a.action) + '</td></tr>';
        }).join('');
    } catch (e) { showError(String(e.message || e)); }
}

/* ⑤ 理赔工单 */
async function loadClaims() {
    try {
        var body = await fetchJson(api('/api/logistics-ai/risk/claims'),
                                   { headers: adminHeaders() }, '理赔工单');
        var rows = body.data || [];
        var el = document.getElementById('claimList');
        if (!rows.length) {
            el.innerHTML = '<tr><td colspan="4" class="dash-empty">' +
                '暂无理赔工单</td></tr>';
            return;
        }
        /* 实测字段: claimTypeName(中文名)/status/description(无 suggestion) */
        el.innerHTML = rows.map(function (c) {
            var no = c.claimNo || ('#' + (c.claimId != null ? c.claimId
                                                          : c.id));
            return '<tr><td>' + esc(no) + '</td>' +
                '<td>' + esc(c.claimTypeName || c.claimType || c.type)
                + '</td>' +
                '<td>¥' + esc(c.claimAmount != null ? c.claimAmount
                                                    : c.amount) + '</td>' +
                '<td>' + esc((c.status || '') + (c.description
                             ? ' · ' + c.description : '')) + '</td></tr>';
        }).join('');
    } catch (e) { showError(String(e.message || e)); }
}

/* ⑥ 成本分析(实测: {totalFee, byCarrier, byMonth, suggestions, note}) */
async function loadCost() {
    try {
        var body = await fetchJson(api('/api/logistics-ai/analysis/cost'),
                                   { headers: adminHeaders() }, '成本分析');
        var d = body.data || {};
        cells('ovCost', [
            { k: '物流总成本', v: '¥' + (d.totalFee != null ? d.totalFee
                                                           : '-') },
            { k: '承运商数', v: d.byCarrier
                                ? Object.keys(d.byCarrier).length : '-' },
            { k: '议价建议', v: (d.suggestions || []).length + ' 条',
              cls: 'blue' },
        ]);
        var sug = d.suggestions || [];
        var sugText = sug.length
            ? sug.map(function (s) {
                return typeof s === 'string' ? s : (s.suggestion || s.text
                                                     || JSON.stringify(s));
            }).join('；')
            : (d.note || '');
        document.getElementById('costNote').textContent =
            String(sugText).slice(0, 200);
    } catch (e) { showError(String(e.message || e)); }
}

async function loadAll() {
    document.getElementById('apiBase').value = state.apiBase;
    await Promise.all([
        loadStatus(), loadCarriers(), loadDecisions(),
        loadAnomalies(), loadClaims(), loadCost(),
    ]);
    markUpdate();
}

document.addEventListener('DOMContentLoaded', loadAll);
