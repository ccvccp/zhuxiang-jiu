/**
 * 智启元·AI智能财务大模型看板(八区块)
 * 范式: js/zhisou-dashboard.js 平移——ES5、同源默认、401 汉化、
 * 宽松取值降级渲染(字段名跨版本兼容)。
 * 依赖后端: /api/zy/*(zy_routes; X-Role admin)
 */
'use strict';

var API_BASE_KEY = 'zhiyuanDash.apiBase';
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
        var mb = await fetchJson(api('/api/zy/mode'),
            { headers: adminHeaders() }, '模式读取');
        mode = (mb.data || {}).mode || '-';
        var pill = document.getElementById('modePill');
        pill.textContent = 'ZY_MODE=' + mode;
        pill.className = mode === 'assist' ? 'ok-pill'
            : (mode === 'shadow' ? 'warn-pill' : 'risk-pill');
    } catch (e) { showError(e.message); }
    try {
        var b = await fetchJson(api('/api/zy/status'),
            { headers: adminHeaders() }, '总览');
        var d = b.data || {};
        var fb = d.feedbacks || {};
        var anomalies = d.anomalies || [];
        cells('ovStatus', [
            { k: '模式', v: mode },
            { k: '反馈总数', v: fb.total || 0, cls: 'blue' },
            { k: '采纳数', v: fb.adopted || 0, cls: 'green' },
            { k: 'trendWeight', v: fb.trendWeight == null
                ? '0.5' : fb.trendWeight },
            { k: '当前异常', v: anomalies.length,
              cls: anomalies.length ? 'red' : 'green' },
        ]);
    } catch (e) {
        cells('ovStatus', [{ k: '总览', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

/* ② 月度时序 */
async function loadSeries() {
    var el = document.getElementById('seriesBox');
    try {
        var b = await fetchJson(api('/api/zy/series?months=12'),
            { headers: adminHeaders() }, '月度时序');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无订单数据'
                + '(已支付订单计入收入)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>月份</th>'
            + '<th>收入</th><th>退款</th><th>成本</th><th>税负</th>'
            + '<th>净利</th></tr>';
        rows.slice().reverse().forEach(function (r) {
            var net = Number(r.netAmount || 0);
            html += '<tr><td>' + esc(r.month || r.period || '-') + '</td>'
                + '<td>¥' + money(r.revenue) + '</td>'
                + '<td>¥' + money(r.refund) + '</td>'
                + '<td>¥' + money(r.cost) + '</td>'
                + '<td>¥' + money(r.tax) + '</td>'
                + '<td style="color:' + (net >= 0 ? '#2f9e44' : '#c0392b')
                + '">¥' + money(net) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ③ 杜邦 + 健康度 */
async function loadDupont() {
    var el = document.getElementById('dupontBox');
    var html = '<div style="display:grid;grid-template-columns:1fr 1fr;'
        + 'gap:12px">';
    try {
        var b = await fetchJson(api('/api/zy/dupont'),
            { headers: adminHeaders() }, '杜邦');
        var d = b.data || {};
        html += '<div><b style="font-size:13px">杜邦(ROE 三因素)</b>'
            + '<table class="dash-table"><tr><th>指标</th><th>值</th></tr>'
            + '<tr><td>ROE</td><td>' + esc(d.roe == null ? '-' :
                (d.roe + (d.roeUnit || ''))) + '</td></tr>'
            + '<tr><td>净利率</td><td>' + esc(d.netMargin == null ? '-'
                : d.netMargin) + '</td></tr>'
            + '<tr><td>周转率</td><td>' + esc(d.turnover == null ? '-'
                : d.turnover) + '</td></tr>'
            + '<tr><td>权益乘数</td><td>' + esc(d.equityMultiplier == null
                ? '-' : d.equityMultiplier) + '</td></tr>'
            + '</table></div>';
    } catch (e) {
        html += '<div class="dash-empty">杜邦降级</div>';
    }
    try {
        var b2 = await fetchJson(api('/api/zy/health'),
            { headers: adminHeaders() }, '健康度');
        var h = b2.data || {};
        var score = h.score == null ? '-' : h.score;
        var grade = h.grade || h.level || '-';
        var cls = Number(score) >= 70 ? 'ok-pill'
            : (Number(score) >= 50 ? 'warn-pill' : 'risk-pill');
        html += '<div><b style="font-size:13px">健康度五维</b>'
            + '<div style="margin:6px 0"><span class="' + cls + '">'
            + esc(score) + ' 分 · ' + esc(grade) + '</span></div>';
        var dims = h.dimensions || h.dims || {};
        Object.keys(dims).forEach(function (k) {
            var v = Number(dims[k] == null ? 0 : dims[k]);
            var w = Math.max(2, Math.min(100, v));
            html += '<div class="bar-row"><span class="bar-label">'
                + esc(k) + '</span><div class="bar-track"><div'
                + ' class="bar-fill" style="width:' + w + '%"></div>'
                + '</div><span class="bar-val">' + v.toFixed(0)
                + '</span></div>';
        });
        html += '</div>';
    } catch (e) {
        html += '<div class="dash-empty">健康度降级</div>';
    }
    el.innerHTML = html + '</div>';
}

/* ④ 预测 + 驱动 */
async function loadForecast() {
    var el = document.getElementById('forecastBox');
    var html = '';
    try {
        var b = await fetchJson(api('/api/zy/forecast?horizon=6'),
            { headers: adminHeaders() }, '预测');
        var d = b.data || {};
        var rows = d.forecast || d.series || [];
        html += '<b style="font-size:13px">滚动预测(近 6 期)</b>'
            + '<table class="dash-table"><tr><th>期</th>'
            + '<th>预测净利</th></tr>';
        rows.slice(0, 6).forEach(function (r) {
            html += '<tr><td>' + esc(r.month || r.period || '-') + '</td>'
                + '<td>¥' + money(r.netAmount == null
                    ? r.net || r.value : r.netAmount) + '</td></tr>';
        });
        html += '</table>';
    } catch (e) {
        html += '<div class="dash-empty">预测降级</div>';
    }
    try {
        var b2 = await fetchJson(api('/api/zy/drivers'),
            { headers: adminHeaders() }, '驱动因素');
        var drs = (b2.data || {}).drivers || b2.data || [];
        if (Array.isArray(drs) && drs.length) {
            html += '<div style="margin-top:10px"><b style="font-size:13px">'
                + '收入驱动(相关性排序)</b>';
            drs.slice(0, 4).forEach(function (d0) {
                var name = d0.factor || d0.name || '-';
                var corr = d0.correlation == null ? '-' : d0.correlation;
                html += '<span class="gray-pill" style="margin:2px 4px">'
                    + esc(name) + ' r=' + esc(corr) + '</span>';
            });
            html += '</div>';
        }
    } catch (e) { /* 驱动降级静默 */ }
    el.innerHTML = html || '<div class="dash-empty">无数据</div>';
}

/* ⑤ 税务风险 */
async function loadTax() {
    var el = document.getElementById('taxBox');
    try {
        var b = await fetchJson(api('/api/zy/tax/risk-heatmap'),
            { headers: adminHeaders() }, '税务风险');
        var d = b.data || {};
        var level = d.overallLevel || d.level || '-';
        var cls = String(level).indexOf('高') >= 0 ? 'risk-pill'
            : (String(level).indexOf('中') >= 0 ? 'warn-pill'
               : 'ok-pill');
        var html = '<div style="margin-bottom:8px"><span class="' + cls
            + '">综合风险: ' + esc(level) + '</span></div>';
        var dims = d.dimensions || d.risks || [];
        if (Array.isArray(dims) && dims.length) {
            html += '<table class="dash-table"><tr><th>维度</th>'
                + '<th>等级</th><th>发现</th></tr>';
            dims.slice(0, 5).forEach(function (r) {
                html += '<tr><td>' + esc(r.dimension || r.name || '-')
                    + '</td><td>' + esc(r.level || r.heat || '-')
                    + '</td><td>' + esc(String(r.finding
                        || r.detail || '-').slice(0, 40)) + '</td></tr>';
            });
            html += '</table>';
        } else {
            html += '<div class="dash-empty">五维扫描无异常</div>';
        }
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑥ 异常 */
async function loadAnomalies() {
    var el = document.getElementById('anomalyBox');
    try {
        var b = await fetchJson(api('/api/zy/evolution/anomalies'),
            { headers: adminHeaders() }, '异常检测');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">三检测器无异常'
                + '(spike/drop/surge)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>类型</th>'
            + '<th>期</th><th>说明</th></tr>';
        rows.forEach(function (r) {
            html += '<tr><td><span class="risk-pill">' + esc(r.type)
                + '</span></td><td>' + esc(r.month || r.period || '-')
                + '</td><td>' + esc(String(r.detail || r.message
                    || '-').slice(0, 50)) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑦ 资金排程 */
async function loadCash() {
    var el = document.getElementById('cashBox');
    try {
        var b = await fetchJson(api('/api/zy/evolution/cash-schedule?days=30'),
            { headers: adminHeaders() }, '资金排程');
        var d = b.data || {};
        var gapDays = d.gapDays == null ? (d.summary || {}).gapDays : d.gapDays;
        var worst = d.worstGap == null ? (d.summary || {}).worstGap
            : d.worstGap;
        var advice = d.financingAdvice || d.advice
            || (d.summary || {}).advice || '';
        cells('cashBox', [
            { k: '推演窗口', v: '30 日' },
            { k: '缺口天数', v: gapDays == null ? '-' : gapDays,
              cls: Number(gapDays) > 0 ? 'red' : 'green' },
            { k: '最大缺口', v: '¥' + money(worst),
              cls: Number(worst) < 0 ? 'red' : '' },
            { k: '建议', v: String(advice).slice(0, 12) || '暂无',
              cls: 'blue' },
        ]);
    } catch (e) {
        cells('cashBox', [{ k: '资金排程', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

/* ⑧ 进化流 */
async function loadEvolution() {
    var el = document.getElementById('evoBox');
    try {
        var b = await fetchJson(api('/api/zy/evolution/feedbacks?limit=10'),
            { headers: adminHeaders() }, '反馈流');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无反馈(对预测/分析'
                + '标记采纳/修正/拒绝即产生)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>目标</th><th>裁决</th><th>trendWeight</th></tr>';
        rows.forEach(function (r) {
            var v = r.verdict === 'adopted'
                ? '<span class="ok-pill">采纳</span>'
                : (r.verdict === 'rejected'
                   ? '<span class="risk-pill">拒绝</span>'
                   : '<span class="warn-pill">修正</span>');
            html += '<tr><td>' + esc(String(r.createdAt || '')
                .replace('T', ' ').slice(5, 16)) + '</td><td>'
                + esc(r.targetType) + '</td><td>' + v + '</td><td>'
                + esc(r.trendWeightAfter == null ? '-'
                    : r.trendWeightAfter)
                + (r.evolved === false ? ' (冻结)' : '') + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* 手动扫描(调度器单轮) */
async function runScan() {
    try {
        var b = await fetchJson(api('/api/zy/scan/run'),
            { method: 'POST', headers: adminHeaders() }, '立即扫描');
        var d = b.data || {};
        showError('扫描完成: 异常 ' + (d.anomalyCount == null ? 0
            : d.anomalyCount) + ' 项 · 税务风险 '
            + (d.taxRiskLevel || '-') + ' · 最大资金缺口 ¥'
            + money(d.worstCashGap));
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function loadAll() {
    markUpdate();
    await Promise.all([
        loadStatus(), loadSeries(), loadDupont(), loadForecast(),
        loadTax(), loadAnomalies(), loadCash(), loadEvolution(),
    ]);
}

(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
    setInterval(loadAll, 30000);
})();
