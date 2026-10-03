/**
 * 智单·AI智能订单大模型看板(八区块)
 * 范式: js/zy-dashboard.js 平移——ES5、同源默认、401 汉化、
 * 宽松取值降级渲染。
 * 依赖后端: /api/order-ai/*(zd_routes; X-Role admin)
 */
'use strict';

var API_BASE_KEY = 'zhidanDash.apiBase';
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

function money(x) {
    if (x == null) { return '-'; }
    var n = Number(x);
    if (isNaN(n)) { return String(x); }
    if (Math.abs(n) >= 10000) {
        return (n / 10000).toFixed(1) + ' 万';
    }
    return n.toFixed(0);
}

/* ① 模式 + 总览 */
async function loadStatus() {
    var mode = '-';
    try {
        var mb = await fetchJson(api('/api/order-ai/mode'),
            { headers: adminHeaders() }, '模式读取');
        mode = (mb.data || {}).mode || '-';
        var pill = document.getElementById('modePill');
        pill.textContent = 'ZD_MODE=' + mode;
        pill.className = mode === 'assist' ? 'ok-pill'
            : (mode === 'shadow' ? 'warn-pill' : 'risk-pill');
    } catch (e) { showError(e.message); }
    try {
        var b = await fetchJson(api('/api/order-ai/overview'),
            { headers: adminHeaders() }, '总览');
        var d = b.data || {};
        cells('ovStatus', [
            { k: '模式', v: mode },
            { k: '订单总量', v: d.totalOrders || 0, cls: 'blue' },
            { k: 'GMV', v: '¥' + money(d.gmv), cls: 'green' },
            { k: '退款率', v: d.refundRate == null ? '-'
                : (d.refundRate + ''),
              cls: Number(d.refundRate) > 0.1 ? 'red' : '' },
            { k: '客单价', v: '¥' + money(d.avgOrderValue) },
            { k: '平均履约(h)', v: d.avgFulfillmentHours == null ? '-'
                : d.avgFulfillmentHours },
        ]);
    } catch (e) {
        cells('ovStatus', [{ k: '总览', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

/* ② 九态分布 */
async function loadOverview() {
    var el = document.getElementById('overviewBox');
    try {
        var b = await fetchJson(api('/api/order-ai/overview'),
            { headers: adminHeaders() }, '九态分布');
        var d = b.data || {};
        var dist = d.statusDistribution || d.statuses || {};
        var keys = Object.keys(dist);
        if (!keys.length) {
            el.innerHTML = '<div class="dash-empty">暂无订单数据</div>';
            return;
        }
        var max = 1;
        keys.forEach(function (k) {
            max = Math.max(max, Number(dist[k]) || 0);
        });
        var html = keys.map(function (k) {
            var v = Number(dist[k]) || 0;
            var w = Math.max(2, Math.round(v / max * 100));
            return '<div class="bar-row"><span class="bar-label">'
                + esc(k) + '</span><div class="bar-track"><div'
                + ' class="bar-fill" style="width:' + w + '%"></div>'
                + '</div><span class="bar-val">' + v + ' 单</span>'
                + '</div>';
        }).join('');
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ③ 健康体检 */
async function loadCheckup() {
    var el = document.getElementById('checkupBox');
    try {
        var b = await fetchJson(api('/api/order-ai/checkup'),
            { headers: adminHeaders() }, '健康体检');
        var d = b.data || {};
        var score = d.score == null ? '-' : d.score;
        var grade = d.grade || '-';
        var cls = String(grade).toUpperCase() === 'A' ? 'ok-pill'
            : (String(grade).toUpperCase() === 'B' ? 'warn-pill'
               : 'risk-pill');
        var html = '<div style="margin-bottom:8px"><span class="' + cls
            + '">综合 ' + esc(score) + ' 分 · ' + esc(grade)
            + ' 级</span></div>';
        var dims = d.dimensions || d.dims || {};
        var dkeys = Object.keys(dims);
        if (dkeys.length) {
            dkeys.forEach(function (k) {
                var v = Number(dims[k] == null ? 0
                    : (dims[k].score == null ? dims[k] : dims[k].score));
                var w = Math.max(2, Math.min(100, v));
                html += '<div class="bar-row"><span class="bar-label">'
                    + esc(k) + '</span><div class="bar-track"><div'
                    + ' class="bar-fill" style="width:' + w + '%"></div>'
                    + '</div><span class="bar-val">' + v.toFixed(0)
                    + '</span></div>';
            });
        }
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ④ 预测 */
async function loadForecast() {
    var el = document.getElementById('forecastBox');
    var html = '';
    try {
        var b = await fetchJson(api('/api/order-ai/eta'),
            { headers: adminHeaders() }, 'ETA');
        var d = b.data || {};
        var eta = d.etaHours == null ? d.eta : d.etaHours;
        html += '<div style="margin-bottom:8px"><b style="font-size:13px">'
            + '履约 ETA</b> <span class="'
            + (eta == null ? 'gray-pill' : 'ok-pill') + '">'
            + (eta == null ? '样本不足(诚实返回)' : eta + ' 小时')
            + '</span></div>';
    } catch (e) { /* ETA 降级 */ }
    try {
        var b2 = await fetchJson(api('/api/order-ai/forecast?periods=6'),
            { headers: adminHeaders() }, '单量预测');
        var d2 = b2.data || {};
        var rows = d2.forecast || d2.series || [];
        if (rows.length) {
            html += '<b style="font-size:13px">单量滚动预测(近 6 期)'
                + '</b><table class="dash-table"><tr><th>期</th>'
                + '<th>预测单量</th></tr>';
            rows.slice(0, 6).forEach(function (r) {
                html += '<tr><td>' + esc(r.date || r.period || '-')
                    + '</td><td>' + esc(r.forecast == null
                        ? r.value || '-' : r.forecast) + '</td></tr>';
            });
            html += '</table>';
        }
    } catch (e) { /* 预测降级 */ }
    el.innerHTML = html
        || '<div class="dash-empty">暂无预测数据</div>';
}

/* ⑤ 三检测器 */
async function loadDetect() {
    var el = document.getElementById('detectBox');
    try {
        var b = await fetchJson(api('/api/order-ai/detect'),
            { headers: adminHeaders() }, '三检测器');
        var d = b.data || {};
        var alerts = d.alerts || [];
        if (!alerts.length) {
            el.innerHTML = '<div class="dash-empty">三检测器无告警'
                + '(spike/drop/surge)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>类型</th>'
            + '<th>当期</th><th>说明</th></tr>';
        alerts.forEach(function (a) {
            html += '<tr><td><span class="risk-pill">' + esc(a.type)
                + '</span></td><td>' + esc(a.current == null ? '-'
                    : a.current) + '</td><td>' + esc(String(
                    a.detail || a.message || '-').slice(0, 46))
                + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑥ 异常订单 */
async function loadAnomalies() {
    var el = document.getElementById('anomalyBox');
    try {
        var b = await fetchJson(api('/api/order-ai/anomalies?limit=10'),
            { headers: adminHeaders() }, '异常订单');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无异常订单'
                + '(高频下单/大额囤货/秒退款)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>类型</th>'
            + '<th>订单</th><th>会员</th><th>发现</th></tr>';
        rows.forEach(function (r) {
            html += '<tr><td><span class="warn-pill">' + esc(r.type
                || r.kind || '-') + '</span></td><td>'
                + esc(r.orderId || '-') + '</td><td>'
                + esc(r.memberId == null ? '-' : r.memberId)
                + '</td><td>' + esc(String(r.detail || r.reason
                    || '-').slice(0, 40)) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑦ 进化 */
async function loadEvolution() {
    var el = document.getElementById('evoBox');
    var html = '';
    try {
        var b = await fetchJson(api('/api/order-ai/params'),
            { headers: adminHeaders() }, '进化参数');
        var d = b.data || {};
        html += '<div style="margin-bottom:8px"><b style="font-size:13px">'
            + 'etaRecentWeight</b> = <span class="ok-pill">'
            + esc(d.etaRecentWeight == null ? '0.6' : d.etaRecentWeight)
            + '</span> <span class="gray-pill">clamp ['
            + ((d.clamp || [0.4, 0.8]).join(', ')) + ']</span></div>';
    } catch (e) { /* 参数降级 */ }
    try {
        var b2 = await fetchJson(api('/api/order-ai/feedbacks?limit=10'),
            { headers: adminHeaders() }, '反馈流');
        var rows = b2.data || [];
        if (rows.length) {
            html += '<table class="dash-table"><tr><th>时间</th>'
                + '<th>目标</th><th>裁决</th><th>权重</th></tr>';
            rows.forEach(function (r) {
                var v = r.verdict === 'adopted'
                    ? '<span class="ok-pill">采纳</span>'
                    : (r.verdict === 'rejected'
                       ? '<span class="risk-pill">拒绝</span>'
                       : '<span class="warn-pill">修正</span>');
                html += '<tr><td>' + esc(String(r.feedbackAt || '')
                    .replace('T', ' ').slice(5, 16)) + '</td><td>'
                    + esc(r.targetType) + '</td><td>' + v + '</td><td>'
                    + esc(r.etaRecentWeightAfter == null ? '-'
                        : r.etaRecentWeightAfter)
                    + (r.evolved === false ? ' (冻结)' : '')
                    + '</td></tr>';
            });
            html += '</table>';
        } else {
            html += '<div class="dash-empty">暂无反馈(对预测标记采纳/'
                + '修正/拒绝即产生)</div>';
        }
    } catch (e) { /* 反馈降级 */ }
    el.innerHTML = html || '<div class="dash-empty">暂无数据</div>';
}

/* ⑧ 近期订单 */
async function loadOrders() {
    var el = document.getElementById('ordersBox');
    try {
        var b = await fetchJson(api('/api/order-ai/orders?limit=10'),
            { headers: adminHeaders() }, '近期订单');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无订单</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>订单</th>'
            + '<th>状态</th><th>金额</th><th>创建</th></tr>';
        rows.forEach(function (r) {
            html += '<tr><td>' + esc(r.orderId || '-') + '</td><td>'
                + esc(r.statusName || r.status || '-') + '</td><td>¥'
                + money(r.totalAmount == null ? r.amount : r.totalAmount)
                + '</td><td>' + esc(String(r.createdAt || '')
                    .replace('T', ' ').slice(5, 16)) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* 手动扫描 */
async function runScan() {
    try {
        var b = await fetchJson(api('/api/order-ai/scan/run'),
            { method: 'POST', headers: adminHeaders() }, '立即扫描');
        var d = b.data || {};
        showError('扫描完成: 体检 ' + esc(d.checkupGrade || '-')
            + ' 级(' + esc(d.checkupScore == null ? '-'
                : d.checkupScore) + ' 分) · 异常订单 '
            + (d.anomalyOrders == null ? 0 : d.anomalyOrders)
            + ' · 检测器告警 '
            + (d.detectorAlerts || []).length + ' 项');
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function loadAll() {
    markUpdate();
    await Promise.all([
        loadStatus(), loadOverview(), loadCheckup(), loadForecast(),
        loadDetect(), loadAnomalies(), loadEvolution(), loadOrders(),
    ]);
}

(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
    setInterval(loadAll, 30000);
})();
