/* 43号·AI智能安全管理面板(配套 ai-security-dashboard.html)
 * 范式: js/invoice-dashboard.js(42号)平移——ES5、localStorage 连接、
 * 30s 自动刷新、区块化加载。
 * 区块: 态势卡/事件裁决/申诉/IP处置/UEBA基线/学习回流
 * 依赖后端: /api/security/*(43号 security_routes, 23 端点)
 */
'use strict';

var API_BASE_KEY = 'securityDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    posture: null,
    events: [],
    lastDetail: ''
};

function headers() { return { 'X-Role': 'admin' }; }

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

function api(path) { return state.apiBase + path; }

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}

function showError(msg) {
    var el = document.getElementById('errBar');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}

function showInfo(msg) {
    var el = document.getElementById('infoBar');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}

function markUpdate() {
    document.getElementById('lastUpdate').textContent =
        '更新于 ' + new Date().toLocaleTimeString();
}

function pct(v) { return ((v || 0) * 100).toFixed(1) + '%'; }

function saveConn() {
    var el = document.getElementById('apiBase');
    state.apiBase = el.value.trim().replace(/\/+$/, '');
    if (!state.apiBase) { state.apiBase = 'http://localhost:8000'; }
    el.value = state.apiBase;
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    refreshData();
}

/* ============================================================
 * ① 态势卡 + 事件/申诉统计
 * ============================================================ */

var ACTION_BADGES = {
    block: ['red', '封禁'], challenge: ['yellow', '挑战'],
    throttle: ['blue', '减速'], allow: ['green', '放行'],
    challenge_exempt: ['green', '豁免(真人)'],
    behavior_alert: ['purple', '行为预警'],
    verify_pass: ['green', '验证通过']
};
var VERDICT_BADGES = {
    pending: ['weak', '待裁决'], confirmed: ['red', '确认攻击'],
    false_positive: ['blue', '误报']
};
var POSTURE_NAMES = { peace: ['green', '和平'], alert: ['yellow', '警戒'],
                      wartime: ['red', '战时'] };

function actionBadge(action) {
    var m = ACTION_BADGES[action] || ['weak', action || '-'];
    return '<span class="badge ' + m[0] + '">' + m[1] + '</span>';
}

function verdictBadge(v) {
    var m = VERDICT_BADGES[v] || ['weak', v || '-'];
    return '<span class="badge ' + m[0] + '">' + m[1] + '</span>';
}

async function loadOverview() {
    var b = await fetchJson(api('/api/security/admin/dashboard'),
                            { headers: headers() }, '态势总览');
    var p = await fetchJson(api('/api/security/admin/posture'),
                            { headers: headers() }, '防御态势');
    state.posture = p;

    var pm = POSTURE_NAMES[p.posture] || ['weak', p.posture];
    var fpr = (b.events || {}).falsePositiveRate || 0;
    var cells = [
        { k: '防御态势(系数×' + (p.rateFactor || 1) + ')',
          v: pm[1], cls: pm[0] },
        { k: '攻击密度EMA', v: (p.densityEma || 0).toFixed(1),
          cls: (p.densityEma || 0) > 5 ? 'red' : '' },
        { k: '灰度模式', v: b.enforceLevel || 'observe' },
        { k: '事件总数', v: (b.events || {}).total || 0 },
        { k: '待裁决', v: (b.events || {}).pending || 0,
          cls: (b.events || {}).pending > 50 ? 'red' : 'yellow' },
        { k: '误报率', v: pct(fpr),
          cls: fpr > 0.1 ? 'red' : 'green' },
        { k: '申诉待裁决', v: (b.appeals || {}).pending || 0,
          cls: (b.appeals || {}).pending > 0 ? 'yellow' : '' },
        { k: '当前封禁', v: b.blocks || 0,
          cls: b.blocks > 10 ? 'red' : '' }
    ];
    document.getElementById('ovCells').innerHTML = cells.map(function (c) {
        return '<div class="ov-cell"><div class="k">' + esc(c.k) +
            '</div><div class="v ' + (c.cls || '') + '">' +
            esc(c.v) + '</div></div>';
    }).join('');
}

async function setPosture(posture) {
    if (!posture) { return; }
    try {
        var b = await fetchJson(api('/api/security/admin/posture'), {
            method: 'POST', headers: headers(),
            body: JSON.stringify({ posture: posture })
        }, '切换态势');
        showInfo('态势已切换: ' + b.posture + '(系数×' + b.rateFactor + ')');
    } catch (e) { showError(e.message); }
    document.getElementById('postureSel').value = '';
    refreshData();
}

async function togglePin() {
    var pinned = !(state.posture && state.posture.pinned);
    try {
        var b = await fetchJson(api('/api/security/admin/posture/pin'), {
            method: 'POST', headers: headers(),
            body: JSON.stringify({ pinned: pinned })
        }, '态势钉住');
        showInfo(b.pinned ? '已钉住(自动升降级暂停)' : '已解钉(恢复自动)');
    } catch (e) { showError(e.message); }
    refreshData();
}

/* ============================================================
 * ② 攻击事件(复核因子 → 裁决)
 * ============================================================ */

async function loadEvents() {
    var params = [];
    var action = document.getElementById('eventActionSel').value;
    var verdict = document.getElementById('eventVerdictSel').value;
    if (action) { params.push('action=' + action); }
    if (verdict) { params.push('verdict=' + verdict); }
    var b = await fetchJson(api('/api/security/admin/events' +
        (params.length ? '?' + params.join('&') : '')),
        { headers: headers() }, '事件流水');
    state.events = b.events || [];
    var rows = state.events.slice(0, 50).map(function (e) {
        var ops = '';
        if (e.verdict === 'pending') {
            ops += '<button class="btn-mini danger" onclick="decideEvent(' +
                e.eventId + ',true)">确认攻击</button>';
            ops += '<button class="btn-mini blue" onclick="decideEvent(' +
                e.eventId + ',false)">误报</button>';
        }
        ops += '<button class="btn-mini ghost" onclick="showFactors(' +
            e.eventId + ')">因子</button>';
        return '<tr><td>' + e.eventId + '</td><td>' + esc(e.ip) +
            '</td><td>' + esc((e.path || '').slice(0, 40)) + '</td><td>' +
            actionBadge(e.action) + '</td><td>' +
            (e.score == null ? '-' : e.score) + '</td><td>' +
            verdictBadge(e.verdict) + '</td><td>' + ops + '</td></tr>';
    });
    document.querySelector('#eventTable tbody').innerHTML = rows.join('');
    document.getElementById('eventEmpty').style.display =
        rows.length ? 'none' : 'block';
}

async function decideEvent(id, confirm) {
    if (!window.localStorage) { /* noop */ }
    var reason = confirm ? '确认攻击' : '误报';
    if (!window.confirm('事件 #' + id + ' 裁决为「' + reason + '」?' +
            (confirm ? '' : '(误报将自动恢复 IP 信誉与封禁)'))) { return; }
    try {
        await fetchJson(api('/api/security/admin/events/' + id + '/decide'), {
            method: 'POST', headers: headers(),
            body: JSON.stringify({ confirm: confirm, reviewer: 'panel',
                                   note: '面板裁决' })
        }, '事件裁决');
        showInfo('事件 #' + id + ' 已裁决(' + reason + ')');
    } catch (e) { showError(e.message); }
    refreshData();
}

function showFactors(id) {
    var el = document.getElementById('factorDetail');
    var e = state.events.filter(function (x) {
        return String(x.eventId) === String(id);
    })[0];
    if (!e) { return; }
    var lines = (e.factors || []).map(function (f) {
        return (f.name || f.code || '?').padEnd(20) +
            ' 分=' + (f.score != null ? f.score : '-') +
            '  ' + (f.detail || f.label || '');
    });
    el.textContent = '事件 #' + id + ' 因子明细:\n' +
        (lines.join('\n') || '(无因子——合成事件)');
    el.style.display = el.style.display === 'block' ? 'none' : 'block';
}

/* ============================================================
 * ③ 申诉队列
 * ============================================================ */

async function loadAppeals() {
    var status = document.getElementById('appealStatusSel').value;
    var b = await fetchJson(api('/api/security/admin/appeals' +
        (status ? '?status=' + status : '')),
        { headers: headers() }, '申诉队列');
    var rows = (b.appeals || []).map(function (a) {
        var ops = '';
        if (a.status === 'pending') {
            ops += '<button class="btn-mini danger" onclick="decideAppeal(' +
                a.appealId + ',false)">维持</button>';
            ops += '<button class="btn-mini" onclick="decideAppeal(' +
                a.appealId + ',true)">误报恢复</button>';
        }
        var sm = a.status === 'approved' ? ['green', '已恢复'] :
                 a.status === 'rejected' ? ['red', '维持处置'] :
                 ['weak', '待裁决'];
        return '<tr><td>' + a.appealId + '</td><td>' + a.eventId +
            '</td><td>' + a.memberId + '</td><td>' + esc(a.ip) +
            '</td><td>' + esc((a.reason || '').slice(0, 30)) + '</td><td>' +
            '<span class="badge ' + sm[0] + '">' + sm[1] + '</span>' +
            '</td><td>' + ops + '</td></tr>';
    });
    document.querySelector('#appealTable tbody').innerHTML = rows.join('');
    document.getElementById('appealEmpty').style.display =
        rows.length ? 'none' : 'block';
}

async function decideAppeal(id, approve) {
    var label = approve ? '误报恢复(信誉返还+解封)' : '维持处置';
    if (!window.confirm('申诉 #' + id + ' → ' + label + '?')) { return; }
    try {
        await fetchJson(api('/api/security/admin/appeals/' + id + '/decide'), {
            method: 'POST', headers: headers(),
            body: JSON.stringify({ approve: approve, reviewer: 'panel' })
        }, '申诉裁决');
        showInfo('申诉 #' + id + ' 已裁决: ' + label);
    } catch (e) { showError(e.message); }
    refreshData();
}

/* ============================================================
 * ④ IP 处置
 * ============================================================ */

async function loadIps() {
    var b = await fetchJson(api('/api/security/admin/ips'),
                            { headers: headers() }, 'IP 信誉');
    var statusMap = { normal: ['green', '正常'],
                      suspicious: ['yellow', '可疑'],
                      blacklisted: ['red', '黑名单'] };
    var rows = (b.ips || []).slice(0, 30).map(function (r) {
        var m = statusMap[r.status] || ['weak', r.status];
        return '<tr><td>' + esc(r.ip) + '</td><td>' +
            (r.score == null ? '-' : r.score) + '</td><td>' +
            '<span class="badge ' + m[0] + '">' + m[1] + '</span>' +
            '</td><td>' + (r.attackCount || 0) + '</td><td>' +
            (r.pinned ? '🔒 是' : '否') + '</td></tr>';
    });
    document.querySelector('#ipTable tbody').innerHTML = rows.join('');
    document.getElementById('ipEmpty').style.display =
        rows.length ? 'none' : 'block';
}

async function banIp() {
    var ip = document.getElementById('ipInput').value.trim();
    if (!ip) { showError('请输入 IP'); return; }
    try {
        await fetchJson(api('/api/security/admin/ips/' + ip + '/ban'), {
            method: 'POST', headers: headers(),
            body: JSON.stringify({ reason: '面板手动封禁' })
        }, '封禁');
        showInfo('已封禁 ' + ip);
    } catch (e) { showError(e.message); }
    refreshData();
}

async function unbanIp() {
    var ip = document.getElementById('ipInput').value.trim();
    if (!ip) { showError('请输入 IP'); return; }
    try {
        await fetchJson(api('/api/security/admin/ips/' + ip + '/unban'), {
            method: 'POST', headers: headers()
        }, '解封');
        showInfo('已解封 ' + ip);
    } catch (e) { showError(e.message); }
    refreshData();
}

async function pinIp(pinned) {
    var ip = document.getElementById('ipInput').value.trim();
    if (!ip) { showError('请输入 IP'); return; }
    try {
        var b = await fetchJson(
            api('/api/security/admin/ips/' + ip + '/pin'), {
                method: 'POST', headers: headers(),
                body: JSON.stringify({ pinned: pinned })
            }, '钉住');
        showInfo(ip + (b.ip && b.ip.pinned ? ' 已钉住' : ' 已解钉'));
    } catch (e) { showError(e.message); }
    refreshData();
}

/* ============================================================
 * ⑤ UEBA 基线
 * ============================================================ */

function hoursBar(hours) {
    var max = Math.max.apply(null, (hours || []).concat([0.0001]));
    var cells = (hours || []).map(function (h) {
        var w = max > 0 ? Math.round(h / max * 100) : 0;
        return '<span title="' + (h * 100).toFixed(1) + '%" ' +
            'style="width:4px;opacity:' + (0.3 + 0.7 * (w / 100)) +
            ';display:inline-block;height:12px;background:#2d3748;' +
            'margin-right:1px"></span>';
    });
    return '<span class="hours-bar">' + cells.join('') + '</span>';
}

async function loadBaselines() {
    var b = await fetchJson(api('/api/security/admin/behavior/baselines'),
                            { headers: headers() }, '行为基线');
    var rows = (b.baselines || []).slice(0, 20).map(function (bl) {
        var dist = Object.keys(bl.moduleDist || {}).map(function (m) {
            return m + ':' + bl.moduleDist[m];
        }).join(' ');
        return '<tr><td>' + esc(bl.actorKey) + '</td><td>' +
            esc(bl.role) + '</td><td>' + hoursBar(bl.hours) + '</td><td>' +
            (bl.p95OpsPerHour || 0) + '</td><td>' + esc(dist) +
            '</td></tr>';
    });
    document.querySelector('#baselineTable tbody').innerHTML = rows.join('');
    document.getElementById('baselineEmpty').style.display =
        rows.length ? 'none' : 'block';
}

async function rebuildBaselines() {
    try {
        var b = await fetchJson(
            api('/api/security/admin/behavior/rebuild'), {
                method: 'POST', headers: headers()
            }, '重建基线');
        showInfo('基线已重建: 个人 ' + b.personal + ' / 角色全局 ' +
                 b.roleGlobals);
        loadBaselines();
    } catch (e) { showError(e.message); }
}

async function loadDeviations() {
    var b = await fetchJson(api('/api/security/admin/behavior/deviations'),
                            { headers: headers() }, '偏离记录');
    var el = document.getElementById('deviationDetail');
    var lines = (b.deviations || []).slice(0, 10).map(function (d) {
        var ds = (d.factors || []).map(function (f) {
            return f.name + '(' + (f.detail || '') + ')';
        }).join(', ');
        return '#' + d.eventId + ' ' + d.ip + ' → ' + ds;
    });
    el.textContent = '近行为偏离记录:\n' +
        (lines.join('\n') || '(暂无——UEBA 运行中积累)');
    el.style.display = 'block';
}

/* ============================================================
 * ⑥ 学习回流
 * ============================================================ */

async function loadLearning() {
    try {
        var b = await fetchJson(api('/api/security/admin/learning/status'),
                                { headers: headers() }, '学习状态');
        var e = b.events || {};
        var cells = [
            { k: '事件总数', v: e.total || 0 },
            { k: '已裁决', v: e.decided || 0, cls: 'yellow' },
            { k: '已回流', v: e.fed || 0, cls: 'green' },
            { k: '确认攻击', v: e.confirmed || 0 },
            { k: '误报', v: e.falsePositive || 0, cls: 'blue' }
        ];
        document.getElementById('learningCells').innerHTML =
            cells.map(function (c) {
                return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                    '</div><div class="v ' + (c.cls || '') + '">' +
                    esc(c.v) + '</div></div>';
            }).join('');
    } catch (e) {
        document.getElementById('learningCells').innerHTML =
            '<div class="dash-empty">点 collect 后加载</div>';
    }
}

async function collectLearning() {
    try {
        var b = await fetchJson(api('/api/security/admin/learning/collect'), {
            method: 'POST', headers: headers()
        }, 'collect 回流');
        showInfo('回流完成: submitted=' + b.submitted +
                 ' skipped=' + b.skipped);
        loadLearning();
    } catch (e) { showError(e.message); }
}

async function runLearning() {
    try {
        var b = await fetchJson(api('/api/security/admin/learning/run'), {
            method: 'POST', headers: headers()
        }, 'run 学习');
        showInfo('学习完成: ' + JSON.stringify(b).slice(0, 120));
    } catch (e) {
        // 409 = 反馈不足, 属正常保护
        showError(e.message + '(反馈不足 min_feedback 属正常保护)');
    }
}

/* ============================================================
 * 刷新编排
 * ============================================================ */

async function refreshData() {
    try {
        await Promise.all([
            loadOverview(), loadEvents(), loadAppeals(),
            loadIps(), loadBaselines(), loadLearning()
        ]);
        markUpdate();
    } catch (e) { showError(e.message); }
}

(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    refreshData();
    setInterval(function () { refreshData(); }, 30000);
})();
