/**
 * 智搜·AI智能搜索引擎大模型看板(五区块)
 * 范式: js/zhiyun-dashboard.js 平移——ES5、同源默认、401 汉化。
 * 依赖后端: /api/search-ai/*(zs_routes; X-Role admin)
 * 区块: ①模式+总览 ②意图分布 ③进化参数 ④反馈流 ⑤决策留痕
 */
'use strict';

var API_BASE_KEY = 'zhisouDash.apiBase';
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

function pct(x) { return (x == null) ? '-' : (x * 100).toFixed(1) + '%'; }
function when(s) {
    return String(s || '').replace('T', ' ').slice(5, 16);
}

function modePillCls(m) {
    return m === 'assist' ? 'ok-pill' : (m === 'shadow' ? 'warn-pill'
        : 'risk-pill');
}

/* ① 模式态 + 总览 */
async function loadStatus() {
    var mode = null;
    try {
        var mb = await fetchJson(api('/api/search-ai/mode'),
            { headers: adminHeaders() }, '模式读取');
        mode = (mb.data || {}).mode || '-';
        var pill = document.getElementById('modePill');
        pill.textContent = 'ZS_MODE=' + mode;
        pill.className = modePillCls(mode);
    } catch (e) { showError(e.message); }
    try {
        var b = await fetchJson(api('/api/search-ai/status'),
            { headers: adminHeaders() }, '总览');
        var d = b.data || {};
        cells('ovStatus', [
            { k: '模式', v: mode || d.mode || '-' },
            { k: '查询总量', v: d.queries || 0, cls: 'blue' },
            { k: '合规拦截率', v: pct(d.blockedRate), cls: 'red' },
            { k: '命中意图数', v: d.intents || 0 },
            { k: '决策留痕', v: d.decisions || 0 },
            { k: '反馈数', v: d.feedbacks || 0, cls: 'green' },
            { k: '反馈有用率', v: pct(d.feedbackUsefulRate) },
            { k: '模型版本', v: d.modelVersion || '-' },
        ]);
    } catch (e) {
        cells('ovStatus', [{ k: '总览', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

/* ② 意图分布(横向条形) */
async function loadIntents() {
    var el = document.getElementById('intentDist');
    try {
        var b = await fetchJson(api('/api/search-ai/intent-stats'),
            { headers: adminHeaders() }, '意图分布');
        var d = b.data || {};
        var rows = Object.entries(d.byIntent || {})
            .sort(function (a, c) { return c[1] - a[1]; });
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无查询数据</div>';
            return;
        }
        var max = rows[0][1] || 1;
        var total = d.total || 1;
        el.innerHTML = rows.map(function (r) {
            var w = Math.max(2, Math.round(r[1] / max * 100));
            var cls = r[0] === '合规拦截' ? 'risk-pill' : 'gray-pill';
            return '<div class="bar-row"><span class="bar-label">'
                + esc(r[0]) + '</span><div class="bar-track"><div'
                + ' class="bar-fill" style="width:' + w + '%"></div></div>'
                + '<span class="bar-val">' + r[1] + ' 次 ('
                + (r[1] / total * 100).toFixed(0) + '%)</span>'
                + '<span class="' + cls + '">' + r[0] + '</span></div>';
        }).join('');
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ③ 进化参数(routeBoost + intentWeight + LLM 兜底) */
async function loadEvolution() {
    var el = document.getElementById('evoParams');
    try {
        var sb = await fetchJson(api('/api/search-ai/status'),
            { headers: adminHeaders() }, '进化参数');
        var s = sb.data || {};
        var fb = await fetchJson(api('/api/search-ai/feedbacks?limit=1'),
            { headers: adminHeaders() }, '反馈面');
        var boosts = (fb.data || {}).routeBoost || s.routeBoost || {};
        var html = '';

        html += '<div style="font-size:12px;color:#555;margin-bottom:6px">'
            + '<b>routeBoost</b>(各意图检索路加成, clamp [0.8, 1.2]; '
            + '显式反馈 ±0.05 / 动作卡点击 +0.03)</div>';
        var keys = Object.keys(boosts).sort();
        if (!keys.length) {
            html += '<div class="dash-empty">暂无进化调整(全 1.0 基线)</div>';
        } else {
            keys.forEach(function (k) {
                var v = boosts[k];
                /* 0.8-1.2 映射到 0-100% 条宽 */
                var w = Math.max(2, Math.round((v - 0.8) / 0.4 * 100));
                var cls = v > 1 ? 'ok-pill' : (v < 1 ? 'risk-pill'
                    : 'gray-pill');
                html += '<div class="bar-row"><span class="bar-label">'
                    + esc(k) + '</span><div class="bar-track"><div'
                    + ' class="bar-fill" style="width:' + w + '%"></div>'
                    + '</div><span class="bar-val">' + v.toFixed(3)
                    + '</span><span class="' + cls + '">'
                    + (v > 1 ? '上调' : (v < 1 ? '下调' : '基线'))
                    + '</span></div>';
            });
        }

        var iw = s.intentWeight == null ? 0.6 : s.intentWeight;
        var llm = s.llmAssist || {};
        html += '<div style="font-size:12px;color:#555;margin:12px 0 6px">'
            + '<b>intentWeight</b>(LLM 兜底采纳权重, clamp [0.4, 0.8]; '
            + '≥0.6 采纳 LLM 兜底意图): <span class="'
            + (iw >= 0.6 ? 'ok-pill' : 'warn-pill') + '">' + iw.toFixed(3)
            + '</span></div>';
        html += '<div style="font-size:12px;color:#555">'
            + '<b>LLM 兜底统计</b>: 触发 ' + (llm.triggers || 0)
            + ' 次 · 采纳 ' + (llm.adopted || 0) + ' 次 · 采纳率 '
            + pct(llm.adoptRate) + '(规则低置信才触发, fail-soft)</div>';
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ④ 反馈流 */
async function loadFeedbacks() {
    var el = document.getElementById('fbTable');
    try {
        var b = await fetchJson(api('/api/search-ai/feedbacks?limit=15'),
            { headers: adminHeaders() }, '反馈流');
        var rows = (b.data || {}).feedbacks || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无反馈'
                + '(用户在搜索页点「有用/没用」或动作卡即产生)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>意图</th><th>判定</th><th>来源</th><th>动作卡</th>'
            + '<th>routeBoost</th><th>intentWeight</th></tr>';
        rows.forEach(function (f) {
            var v = f.verdict === 'useful'
                ? '<span class="ok-pill">有用</span>'
                : '<span class="risk-pill">没用</span>';
            var src = f.source === 'action' ? '隐式点击' : '显式反馈';
            var rb = f.routeBoostAfter == null ? '-'
                : (f.routeBoostBefore + '→' + f.routeBoostAfter);
            var iw = f.intentWeightAfter == null ? '-'
                : (f.intentWeightBefore + '→' + f.intentWeightAfter);
            var ev = f.evolved === false
                ? '<span class="warn-pill">不进化(红线)</span>' : '';
            html += '<tr><td>' + esc(when(f.createdAt)) + '</td><td>'
                + esc(f.intent) + '</td><td>' + v + '</td><td>' + esc(src)
                + '</td><td>' + esc(f.actionLabel || '-') + '</td><td>'
                + esc(rb) + ' ' + ev + '</td><td>' + esc(iw) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑤ 决策留痕 */
async function loadDecisions() {
    var el = document.getElementById('dsTable');
    try {
        var b = await fetchJson(api('/api/search-ai/decisions?limit=15'),
            { headers: adminHeaders() }, '决策留痕');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无决策</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>查询</th><th>意图</th><th>置信</th><th>结果</th>'
            + '<th>LLM兜底</th></tr>';
        rows.forEach(function (d) {
            var out = d.outcome === 'blocked'
                ? '<span class="risk-pill">合规拦截</span>'
                : (d.outcome === 'answered' ? d.resultCount + ' 条'
                   : esc(d.outcome || '-'));
            var llm = d.llmAssist
                ? ('<span class="' + (d.llmAssist.adopted
                    ? 'ok-pill' : 'warn-pill') + '">'
                    + esc(d.llmAssist.intent)
                    + (d.llmAssist.adopted ? ' 采纳' : ' 仅留痕')
                    + '</span>') : '-';
            html += '<tr><td>' + esc(when(d.queriedAt)) + '</td><td>'
                + esc(String(d.query || '').slice(0, 24)) + '</td><td>'
                + esc(d.intent) + '</td><td>'
                + pct(d.confidence) + '</td><td>' + out + '</td><td>'
                + llm + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

async function loadAll() {
    markUpdate();
    await Promise.all([
        loadStatus(), loadIntents(), loadEvolution(),
        loadFeedbacks(), loadDecisions(),
    ]);
}

/* 入口: 填地址 + 首载 + 30s 自动刷新 */
(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
    setInterval(loadAll, 30000);
})();
