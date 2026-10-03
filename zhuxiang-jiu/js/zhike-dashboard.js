/**
 * 智客·AI智能会员大模型看板(八区块)
 * 范式: js/zhidan-dashboard.js 平移——ES5、同源默认、401 汉化、
 * 宽松取值降级渲染。
 * 依赖后端: /api/member-ai/*(zk_routes; X-Role admin)
 */
'use strict';

var API_BASE_KEY = 'zhikeDash.apiBase';
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

function when(s) {
    return String(s || '').replace('T', ' ').slice(5, 16);
}

/* ① 模式 + 总览 */
async function loadStatus() {
    var mode = '-';
    try {
        var mb = await fetchJson(api('/api/member-ai/mode'),
            { headers: adminHeaders() }, '模式读取');
        mode = (mb.data || {}).mode || '-';
        var pill = document.getElementById('modePill');
        pill.textContent = 'ZK_MODE=' + mode;
        pill.className = mode === 'assist' ? 'ok-pill'
            : (mode === 'shadow' ? 'warn-pill' : 'risk-pill');
    } catch (e) { showError(e.message); }
    try {
        var b = await fetchJson(api('/api/member-ai/overview'),
            { headers: adminHeaders() }, '总览');
        var d = b.data || {};
        cells('ovStatus', [
            { k: '模式', v: mode },
            { k: '会员总数', v: d.totalMembers || d.total || 0,
              cls: 'blue' },
            { k: '订单总数', v: d.totalOrders || 0 },
            { k: '活跃会员', v: d.activeMembers == null ? '-'
                : d.activeMembers, cls: 'green' },
            { k: '月均消费', v: '¥' + (d.avgMonthlyConsume == null ? '-'
                : d.avgMonthlyConsume) },
        ]);
    } catch (e) {
        cells('ovStatus', [{ k: '总览', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

/* ② 会员总览(等级分布) */
async function loadOverview() {
    var el = document.getElementById('overviewBox');
    try {
        var b = await fetchJson(api('/api/member-ai/overview'),
            { headers: adminHeaders() }, '会员总览');
        var d = b.data || {};
        var dist = d.levelDistribution || d.levels || {};
        var keys = Object.keys(dist);
        if (!keys.length) {
            el.innerHTML = '<div class="dash-empty">暂无会员数据</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>等级</th>'
            + '<th>人数</th></tr>';
        keys.sort().forEach(function (k) {
            html += '<tr><td>' + esc(k) + '</td><td>'
                + esc(dist[k]) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ③ 流失预警 */
async function loadChurns() {
    var el = document.getElementById('churnBox');
    try {
        var b = await fetchJson(api('/api/member-ai/churns?limit=10'),
            { headers: adminHeaders() }, '流失预警');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无流失预警记录'
                + '(每日扫描产生)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>会员</th><th>风险</th><th>分值</th></tr>';
        rows.forEach(function (r) {
            var lv = r.riskLevel || r.level || '-';
            var cls = lv === '红' || String(lv).indexOf('red') >= 0
                ? 'risk-pill' : (lv === '黄' ? 'warn-pill' : 'ok-pill');
            html += '<tr><td>' + esc(when(r.scannedAt || r.createdAt))
                + '</td><td>' + esc(r.memberId == null ? '-'
                    : r.memberId) + '</td><td><span class="' + cls
                + '">' + esc(lv) + '</span></td><td>'
                + esc(r.score == null ? '-' : r.score) + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ④ 唤醒建议 */
async function loadWakeups() {
    var el = document.getElementById('wakeupBox');
    try {
        var b = await fetchJson(api('/api/member-ai/wakeups?limit=10'),
            { headers: adminHeaders() }, '唤醒建议');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无唤醒建议记录'
                + '(每日扫描产生; 建议永不自动发送)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>会员</th><th>级别</th><th>方案</th></tr>';
        rows.forEach(function (r) {
            html += '<tr><td>' + esc(when(r.suggestedAt
                || r.createdAt)) + '</td><td>'
                + esc(r.memberId == null ? '-' : r.memberId)
                + '</td><td><span class="warn-pill">'
                + esc(r.tier || r.level || '-') + '</span></td><td>'
                + esc(String(r.plan || r.detail || '-').slice(0, 36))
                + '</td></tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑤ 三检测器 */
async function loadDetect() {
    var el = document.getElementById('detectBox');
    try {
        var b = await fetchJson(api('/api/member-ai/detect'),
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

/* ⑥ 进化闭环 */
async function loadEvolution() {
    var el = document.getElementById('evoBox');
    try {
        var b = await fetchJson(api('/api/member-ai/params'),
            { headers: adminHeaders() }, '进化参数');
        var d = b.data || {};
        var f = d.ltvRetainFactor == null ? '0.6' : d.ltvRetainFactor;
        el.innerHTML = '<div><b style="font-size:13px">'
            + 'ltvRetainFactor</b> = <span class="ok-pill">'
            + esc(f) + '</span> <span class="gray-pill">clamp '
            + esc(d.clamp || '[0.4, 0.8]') + '</span>'
            + '<div style="font-size:11px;color:#888;margin-top:6px">'
            + '反馈裁决(adopted/corrected/rejected)驱动 LTV 留存因子'
            + '学习——安全阀内, 建议永不自动</div></div>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑦ 画像快照 */
async function loadPortraits() {
    var el = document.getElementById('portraitBox');
    try {
        var b = await fetchJson(api('/api/member-ai/portraits?limit=10'),
            { headers: adminHeaders() }, '画像');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无画像快照'
                + '(对会员计算 RFM 画像即产生)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>会员</th><th>RFM 标签</th></tr>';
        rows.forEach(function (r) {
            html += '<tr><td>' + esc(when(r.portraitAt || r.createdAt))
                + '</td><td>' + esc(r.memberId == null ? '-'
                    : r.memberId) + '</td><td><span class="ok-pill">'
                + esc(r.label || r.rfmLabel || '-') + '</span></td>'
                + '</tr>';
        });
        el.innerHTML = html + '</table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败</div>';
        showError(e.message);
    }
}

/* ⑧ 反馈流 */
async function loadFeedbacks() {
    var el = document.getElementById('fbBox');
    try {
        var b = await fetchJson(api('/api/member-ai/feedbacks?limit=10'),
            { headers: adminHeaders() }, '反馈流');
        var rows = b.data || [];
        if (!rows.length) {
            el.innerHTML = '<div class="dash-empty">暂无反馈'
                + '(对预测/建议标记裁决即产生)</div>';
            return;
        }
        var html = '<table class="dash-table"><tr><th>时间</th>'
            + '<th>目标</th><th>裁决</th><th>因子</th></tr>';
        rows.forEach(function (r) {
            var v = r.verdict === 'adopted'
                ? '<span class="ok-pill">采纳</span>'
                : (r.verdict === 'rejected'
                   ? '<span class="risk-pill">拒绝</span>'
                   : '<span class="warn-pill">修正</span>');
            html += '<tr><td>' + esc(when(r.feedbackAt)) + '</td><td>'
                + esc(r.targetType) + '</td><td>' + v + '</td><td>'
                + esc(r.paramAfter == null ? '-'
                    : (r.paramBefore + '→' + r.paramAfter))
                + (r.evolved === false ? ' (冻结)' : '') + '</td></tr>';
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
        var b = await fetchJson(api('/api/member-ai/scan/run'),
            { method: 'POST', headers: adminHeaders() }, '立即扫描');
        var d = b.data || {};
        showError('扫描完成: 红色流失 ' + (d.churnRed == null ? 0
            : d.churnRed) + ' · 黄色 ' + (d.churnYellow == null ? 0
            : d.churnYellow) + ' · 检测告警 '
            + (d.detectorAlerts || []).length + ' 项');
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function loadAll() {
    markUpdate();
    await Promise.all([
        loadStatus(), loadOverview(), loadChurns(), loadWakeups(),
        loadDetect(), loadEvolution(), loadPortraits(), loadFeedbacks(),
    ]);
}

(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
    setInterval(loadAll, 30000);
})();
