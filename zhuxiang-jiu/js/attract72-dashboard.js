/**
 * 智流·AI智能自动引流大模型看板(72号, 六区块)
 * 范式: js/qr70-dashboard.js 平移——ES5、同源默认、401 汉化、
 * 宽松取值降级渲染。
 * 依赖后端: /api/attract72/*(49 端点; X-Role admin)
 *          + /api/attract/*(v1.0 归因底座)
 */
'use strict';

var API_BASE_KEY = 'attract72Dash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY) || '' };

var PERSONA_CN = {
    taster: '品鉴型', sharer: '分享型',
    bargain: '优惠型', newcomer: '新晋型'
};
var VERDICT_CN = {
    chase: '追击', observe: '观察', reject: '放弃'
};
var VARIANT_CN = {
    trust_first: '信任优先', benefit_first: '利益优先',
    default: '默认'
};

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

function when(s) {
    return String(s || '').replace('T', ' ').slice(5, 16);
}

function renderTable(target, columns, rows, emptyTip) {
    if (!rows || !rows.length) {
        document.getElementById(target).innerHTML =
            '<div class="dash-empty">' + esc(emptyTip || '暂无数据') + '</div>';
        return;
    }
    var html = '<table class="dash-table"><tr>' +
        columns.map(function (c) {
            return '<th>' + esc(c.t) + '</th>';
        }).join('') + '</tr>';
    rows.slice(0, 10).forEach(function (r) {
        html += '<tr>' + columns.map(function (c) {
            return '<td>' + esc(c.f(r)) + '</td>';
        }).join('') + '</tr>';
    });
    document.getElementById(target).innerHTML = html + '</table>';
}

/* ① 模式 + 模型状态 */
async function loadStatus() {
    try {
        var b = await fetchJson(api('/api/attract72/model/status'),
            { headers: adminHeaders() }, '模型状态');
        var d = b.data || {};
        var mode = d.mode || '-';
        var pill = document.getElementById('modePill');
        pill.textContent = 'ATTRACT72_MODE=' + mode;
        pill.className = 'mode-pill ' + (mode === 'assist' ? 'ok'
            : (mode === 'shadow' ? 'warn' : (mode === 'full' ? 'ok' : 'risk')));
        var h = d.health || {};
        cells('ovStatus', [
            { k: '模式', v: mode, cls: mode === 'off' ? 'red' : 'green' },
            { k: 'KILL 制动', v: d.kill ? '已制动' : '正常',
              cls: d.kill ? 'red' : 'green' },
            { k: '冻结态', v: d.frozen ? 'frozen' : '未冻结',
              cls: d.frozen ? 'red' : 'green' },
            { k: '健康度', v: h.verdict || '无台账',
              cls: h.verdict === 'healthy' ? 'green'
                : (h.verdict === 'frozen' ? 'red' : 'yellow') },
            { k: '模型版本', v: d.modelVersion || '-' },
            { k: '红队最近', v: d.redteamLastRun
                ? when(d.redteamLastRun.runAt
                    || d.redteamLastRun.createdAt) : '未运行',
              cls: d.redteamLastRun ? 'blue' : 'yellow' }
        ]);
    } catch (e) {
        showError(e.message);
        cells('ovStatus', [{ k: '模型状态', v: '降级', cls: 'red' }]);
    }
}

/* ② v1.0 归因底座 */
async function loadFunnel() {
    var fv = {};
    try {
        var b = await fetchJson(api('/api/attract/report/funnel'),
            { headers: adminHeaders() }, '漏斗');
        fv = b.data || {};
    } catch (e) { /* 宽松降级 */ }
    var f = fv.funnel || fv;
    cells('ovFunnel', [
        { k: '短链点击', v: f.clicks || f.clicked || fv.totalClicks || '-',
          cls: 'blue' },
        { k: '注册归因', v: f.registered || fv.totalRegistered || '-',
          cls: 'green' },
        { k: '下单转化', v: f.ordered || fv.totalOrdered || '-',
          cls: 'green' },
        { k: 'GMV', v: f.gmv || fv.totalGmv || '-', cls: 'yellow' },
        { k: '注册率', v: f.regRate || fv.registerRate || '-',
          cls: 'blue' },
        { k: '下单率', v: f.orderRate || fv.orderRate || '-', cls: 'blue' }
    ]);
    try {
        var cb = await fetchJson(api('/api/attract/clicks?limit=10'),
            { headers: adminHeaders() }, '最近点击');
        var clicks = cb.data || {};
        var rows = clicks.items || clicks.list || clicks
            || (clicks.clicks) || [];
        renderTable('tblClicks', [
            { t: 'ID', f: function (r) { return r.clickId || r.id || '-'; } },
            { t: '短码', f: function (r) { return r.code || '-'; } },
            { t: '渠道', f: function (r) { return r.channel || '-'; } },
            { t: 'IP', f: function (r) { return r.ip || '-'; } },
            { t: '时间', f: function (r) { return when(r.at || r.createdAt); } }
        ], rows, '暂无点击(短链分发待运营)');
    } catch (e) {
        renderTable('tblClicks', [], '读取失败: ' + e.message);
    }
    try {
        var ab = await fetchJson(api('/api/attract/attributions?limit=10'),
            { headers: adminHeaders() }, '最近归因');
        var attrs = ab.data || {};
        var arows = attrs.items || attrs.list || attrs || [];
        renderTable('tblAttrs', [
            { t: 'clickId', f: function (r) {
                return r.clickId || r.click_id || '-'; } },
            { t: '会员', f: function (r) {
                return r.memberId || r.member_id || '-'; } },
            { t: '事件', f: function (r) {
                return r.event || r.eventType || '-'; } },
            { t: '时间', f: function (r) {
                return when(r.at || r.createdAt); } }
        ], arows, '暂无归因(真实转化待积累)');
    } catch (e) {
        renderTable('tblAttrs', [], '读取失败: ' + e.message);
    }
}

/* ③ P1 渠道画像 */
async function loadPersonas() {
    try {
        var b = await fetchJson(api('/api/attract72/personas?limit=10'),
            { headers: adminHeaders() }, '渠道画像');
        var rows = b.data || [];
        renderTable('tblPersonas', [
            { t: '人格', f: function (r) {
                return PERSONA_CN[r.personaType] || r.personaType || '-'; } },
            { t: '主体', f: function (r) {
                return (r.subjectType || '-') + '/' +
                    (r.subjectId || r.subject || '-'); } },
            { t: '画像摘要', f: function (r) {
                var t = r.traits || r.profile || r.summary || '';
                return (typeof t === 'string') ? t.slice(0, 40)
                    : JSON.stringify(t).slice(0, 40); } },
            { t: '更新', f: function (r) {
                return when(r.updatedAt || r.createdAt); } }
        ], rows, '暂无画像(personas/sync 或真实流量触发)');
    } catch (e) {
        renderTable('tblPersonas', [], '读取失败: ' + e.message);
    }
}

/* ④ P4 落地变体 */
async function loadVariants() {
    try {
        var b = await fetchJson(api('/api/attract72/landing/variants?limit=10'),
            { headers: adminHeaders() }, '落地变体');
        var rows = b.data || [];
        if (rows && !rows.length) {
            rows = b.data && (b.data.items || b.data.variants) || [];
        }
        renderTable('tblVariants', [
            { t: '变体', f: function (r) {
                return VARIANT_CN[r.variant] || r.variant
                    || r.name || '-'; } },
            { t: '短码/页', f: function (r) {
                return r.code || r.landing || r.page || '-'; } },
            { t: '曝光', f: function (r) {
                return r.impressions || r.count || 0; } },
            { t: '转化', f: function (r) {
                return r.conversions || r.converted || 0; } },
            { t: '更新', f: function (r) {
                return when(r.updatedAt || r.createdAt); } }
        ], rows, '暂无变体记录(真实点击触发 decide_landing)');
    } catch (e) {
        renderTable('tblVariants', [], '读取失败: ' + e.message);
    }
}

/* ⑤ P5 热点决策 */
async function loadHotspots() {
    try {
        var b = await fetchJson(api('/api/attract72/hotspot/decisions?limit=10'),
            { headers: adminHeaders() }, '热点决策');
        var rows = b.data || [];
        renderTable('tblHotspots', [
            { t: '裁决', f: function (r) {
                var v = r.verdict || r.decision || '-';
                return (VERDICT_CN[v] || v); } },
            { t: '热点', f: function (r) {
                return (r.hotspot || r.topic || r.title
                    || r.hotspotId || '-'); } },
            { t: '势能', f: function (r) {
                return r.potential != null ? r.potential
                    : (r.score != null ? r.score : '-'); } },
            { t: '状态', f: function (r) {
                return r.status || r.state || '-'; } },
            { t: '时间', f: function (r) {
                return when(r.decidedAt || r.createdAt); } }
        ], rows, '暂无决策(热点卡位待真实流量)');
    } catch (e) {
        renderTable('tblHotspots', [], '读取失败: ' + e.message);
    }
}

/* ⑥ P6 元认知 */
async function loadHealth(refresh) {
    try {
        var q = refresh ? '?refresh=1' : '';
        var b = await fetchJson(api('/api/attract72/meta/health' + q),
            { headers: adminHeaders() }, '健康度');
        var d = b.data || {};
        cells('ovHealth', [
            { k: 'checkId', v: d.checkId || '-' },
            { k: 'verdict', v: d.verdict || '-',
              cls: d.verdict === 'healthy' ? 'green'
                : (d.verdict === 'frozen' ? 'red' : 'yellow') },
            { k: '多样性指数', v: d.diversityIndex != null
                ? d.diversityIndex : '-' },
            { k: '匹配准确率', v: d.matchAccuracy != null
                ? d.matchAccuracy : '-' },
            { k: '预测偏差MAPE', v: d.forecastMape != null
                ? d.forecastMape : '-' },
            { k: 'insufficient', v: d.insufficient ? '是' : '否',
              cls: d.insufficient ? 'yellow' : 'green' },
            { k: '检查时间', v: when(d.at || d.checkedAt) }
        ]);
        if (refresh) { markUpdate(); }
    } catch (e) {
        showError(e.message);
        cells('ovHealth', [{ k: '健康度', v: '降级', cls: 'red' }]);
    }
    try {
        var rb = await fetchJson(api('/api/attract72/redteam?limit=10'),
            { headers: adminHeaders() }, '红队');
        var rrows = rb.data || [];
        renderTable('tblRedteam', [
            { t: '向量', f: function (r) {
                return r.vectorId || r.vector || '-'; } },
            { t: '防御', f: function (r) {
                var d2 = r.defended;
                return d2 === true ? '已防御' : (d2 === false
                    ? '击穿' : (d2 != null ? String(d2) : '-')); } },
            { t: '说明', f: function (r) {
                return (r.note || r.detail || '').slice(0, 30); } },
            { t: '时间', f: function (r) {
                return when(r.runAt || r.createdAt); } }
        ], rrows, '红队未运行(手动 SOP)');
    } catch (e) {
        renderTable('tblRedteam', [], '读取失败: ' + e.message);
    }
    try {
        var eb = await fetchJson(api('/api/attract72/evolution/log?limit=10'),
            { headers: adminHeaders() }, '进化日志');
        var erows = eb.data || [];
        if (erows && !Array.isArray(erows)) {
            erows = eb.data && eb.data.entries || [];
        }
        renderTable('tblEvo', [
            { t: '事件', f: function (r) {
                return r.kind || r.event || r.type || '-'; } },
            { t: '摘要', f: function (r) {
                return (r.summary || r.detail
                    || JSON.stringify(r)).slice(0, 44); } },
            { t: '时间', f: function (r) {
                return when(r.at || r.createdAt); } }
        ], erows, '暂无进化事件');
    } catch (e) {
        renderTable('tblEvo', [], '读取失败: ' + e.message);
    }
}

async function loadAll() {
    markUpdate();
    await Promise.all([
        loadStatus(),
        loadFunnel(),
        loadPersonas(),
        loadVariants(),
        loadHotspots(),
        loadHealth(false)
    ]);
}

document.getElementById('apiBase').value = state.apiBase;
loadAll();
setInterval(loadAll, 60000);
