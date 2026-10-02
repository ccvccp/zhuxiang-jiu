/**
 * 智法·AI智能法务大模型看板(五区块)
 * 范式: js/trust-risk-dashboard.js 平移——ES5、localStorage
 * 连接、区块化加载(手动刷新, 不进自动刷新)。
 * 依赖后端: /api/legal/*(zf_routes) + /api/agreements/*(admin)
 * 区块: ①总览 ②判例库 ③价格审计 ④协议签署 ⑤数字孪生
 */
'use strict';

var API_BASE_KEY = 'legalDash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY)
              || 'http://localhost:8000' };
/* 鉴权: 登录后叠加 Authorization Bearer, 未登录保留 compat 头 */
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
    if (!state.apiBase) { state.apiBase = 'http://localhost:8000'; }
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

/* ① 总览 */
async function loadStatus() {
    try {
        var body = await fetchJson(api('/api/legal/status'),
                                   { headers: adminHeaders() }, '总览');
        var d = body.data || {};
        var prod = d.production || {};
        var ev = d.evolution || {};
        cells('ovStatus', [
            { k: '工艺校验', v: prod.checks != null ? prod.checks : '-' },
            { k: '违规检出', v: prod.violations != null ? prod.violations : '-',
              cls: prod.violations > 0 ? 'red' : 'green' },
            { k: '进化反馈', v: ev.feedbacks != null ? ev.feedbacks : '-' },
            { k: '严格度', v: ev.strictness != null ? ev.strictness : '-',
              cls: 'blue' },
            { k: '判例数', v: d.precedents != null ? d.precedents : '-' },
        ]);
        document.getElementById('statusNote').textContent =
            esc(d.note || '') + (d.updatedAt ? ' · ' + d.updatedAt : '');
    } catch (e) { showError(String(e.message || e)); }
}

/* ② 判例库 */
async function loadPrecedents() {
    try {
        var body = await fetchJson(api('/api/legal/evolution/precedents'),
                                   { headers: adminHeaders() }, '判例库');
        var rows = body.data || [];
        var el = document.getElementById('precedentList');
        if (!rows.length) {
            el.innerHTML = '<tr><td colspan="5" class="dash-empty">' +
                '暂无判例</td></tr>';
            return;
        }
        el.innerHTML = rows.map(function (r) {
            var loss = r.outcome === '败诉' ?
                '<span class="risk-pill">败诉</span>' :
                '<span class="ok-pill">' + esc(r.outcome) + '</span>';
            return '<tr><td>' + esc(r.caseName) + '</td><td>' + loss +
                '</td><td>' + esc(r.lossPoint) + '</td><td>' +
                esc(r.ruleSuggestion) + '</td><td>' +
                esc(r.relatedScene) + '</td></tr>';
        }).join('');
    } catch (e) { showError(String(e.message || e)); }
}

/* ⑤ 数字孪生 */
async function loadTwin() {
    try {
        var body = await fetchJson(api('/api/legal/evolution/twin'),
                                   { headers: adminHeaders() }, '数字孪生');
        var d = body.data || {};
        var h = d.twinHealth != null ? d.twinHealth : '-';
        cells('ovTwin', [{ k: '孪生健康分', v: h,
                           cls: h >= 80 ? 'green' : (h >= 60 ? 'yellow' : 'red') }]);
        document.getElementById('twinNote').textContent =
            esc(d.note || '');
    } catch (e) { showError(String(e.message || e)); }
}

/* ③ 价格审计留痕 */
async function loadAudits() {
    try {
        var body = await fetchJson(api('/api/legal/commerce/price-audits'),
                                   { headers: adminHeaders() }, '价格审计');
        var rows = body.data || [];
        var el = document.getElementById('auditList');
        if (!rows.length) {
            el.innerHTML = '<tr><td colspan="7" class="dash-empty">' +
                '暂无审计记录(秒杀加品自动留痕)</td></tr>';
            return;
        }
        el.innerHTML = rows.map(function (r) {
            var ok = r.compliant ?
                '<span class="ok-pill">合规</span>' :
                '<span class="risk-pill">检出' +
                (r.findings || []).length + '项</span>';
            var names = (r.findings || []).map(function (f) {
                return esc(f.name);
            }).join('、') || '-';
            return '<tr><td>#' + esc(r.auditId) + '</td><td>' +
                esc(r.productId) + '</td><td>' + esc(r.windowLow) +
                '</td><td>' + esc(r.windowHigh) + '</td><td>' + ok +
                '</td><td>' + names + '</td><td>' +
                esc((r.auditedAt || '').replace('T', ' ').slice(0, 19)) +
                '</td></tr>';
        }).join('');
    } catch (e) { showError(String(e.message || e)); }
}

/* ④ 协议签署统计 */
async function loadAgreements() {
    try {
        var statBody = await fetchJson(
            api('/api/agreements/stats/overview'),
            { headers: adminHeaders() }, '协议统计');
        var st = statBody.data || statBody || {};
        var listBody = await fetchJson(
            api('/api/agreements'),
            { headers: adminHeaders() }, '协议列表');
        var rows = listBody.data || [];
        cells('ovAgreements', [
            { k: '协议总数', v: st.totalAgreements != null ?
                               st.totalAgreements : rows.length },
            { k: '已发布', v: st.publishedAgreements != null ?
                              st.publishedAgreements : '-', cls: 'green' },
            { k: '角色协议', v: st.totalProtocols != null ?
                               st.totalProtocols : '-', cls: 'blue' },
            { k: '生效中', v: st.activeProtocols != null ?
                              st.activeProtocols : '-', cls: 'green' },
        ]);
        var el = document.getElementById('agreementList');
        if (!rows.length) {
            el.innerHTML = '<tr><td colspan="6" class="dash-empty">' +
                '暂无协议</td></tr>';
            return;
        }
        el.innerHTML = rows.map(function (a) {
            var pub = a.status === 'published' ?
                '<span class="ok-pill">已发布</span>' :
                '<span class="risk-pill">' + esc(a.status) + '</span>';
            return '<tr><td>' + esc(a.agreementNo) + '</td><td>' +
                esc(a.name) + '</td><td>' + esc(a.type) + '</td><td>' +
                esc(a.applicableRole) + '</td><td>' +
                esc(a.currentVersion) + '</td><td>' + pub + '</td></tr>';
        }).join('');
    } catch (e) { showError(String(e.message || e)); }
}

async function loadAll() {
    document.getElementById('apiBase').value = state.apiBase;
    await Promise.all([
        loadStatus(), loadPrecedents(), loadTwin(),
        loadAudits(), loadAgreements(),
    ]);
    markUpdate();
}

/* 入口: DOM 就绪后首载 */
document.addEventListener('DOMContentLoaded', loadAll);
