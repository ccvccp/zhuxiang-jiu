/* 43号·AI智能安全管理面板(配套 ai-security-dashboard.html)
 * 范式: js/invoice-dashboard.js(42号)平移——ES5、localStorage 连接、
 * 30s 自动刷新、区块化加载。
 * 区块: 态势卡/事件裁决/申诉/IP处置/UEBA基线/学习回流/运营成熟化
 * 依赖后端: /api/security/*(43号 security_routes, 29 端点)
 * ⑦区: 日报序列按钮刷新; Redis 实况体检按需加载(KEYS/SLOWLOG
 *      有执行开销, 不进 30s 自动刷新)
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
 * ⑦ 运营成熟化(P4: 日报序列 + Redis 键族健康)
 * ============================================================ */

/* 日报序列图(近14天事件量纯 CSS 柱状, hover 见误报率;
 * 按钮触发刷新, 不进 30s 自动刷新(14×daily_report 有读取开销) */
async function loadReportSeries() {
    var b = await fetchJson(api('/api/security/admin/reports/daily?days=14'),
                            { headers: headers() }, '日报序列');
    var reports = b.reports || [];
    var max = 0;
    reports.forEach(function (r) {
        max = Math.max(max, r.eventsTotal || 0);
    });
    var bars = reports.map(function (r) {
        var total = r.eventsTotal || 0;
        var fpr = r.falsePositiveRate || 0;
        var h = max > 0 ? Math.max(2, Math.round(total / max * 80)) : 2;
        var cls = 'series-col-bar' + (total ? (fpr > 0.1 ? ' fpr' : '')
                                           : ' zero');
        return '<div class="series-col" title="' + esc(r.date) +
            ' 事件 ' + total + ' · 误报率 ' + pct(fpr) + '">' +
            '<div class="' + cls + '" style="height:' + h + 'px"></div></div>';
    });
    document.getElementById('seriesChart').innerHTML = bars.join('');
    document.getElementById('seriesLabels').innerHTML =
        reports.map(function (r) {
            return '<span>' + esc((r.date || '').slice(5)) + '</span>';
        }).join('');

    var s = b.summary || {};
    var cells = [
        { k: '事件总量(14天)', v: s.eventsTotal || 0 },
        { k: '确认攻击', v: s.confirmed || 0, cls: 'red' },
        { k: '误报', v: s.falsePositive || 0, cls: 'blue' },
        { k: '误报率', v: pct(s.falsePositiveRate),
          cls: (s.falsePositiveRate || 0) > 0.1 ? 'red' : 'green' },
        { k: '活跃天数', v: s.activeDays || 0 },
        { k: 'D5 样本', v: s.d5Samples || 0, cls: 'yellow' }
    ];

    // P5-1: D5 联动状态卡(off/达标未启/已启用, 边界区口径)
    try {
        var d5 = await fetchJson(api('/api/security/admin/reports/d5'),
                                 { headers: headers() }, 'D5 观测');
        var enf = d5.d5Enforce || {};
        var state = enf.active
            ? '已启用(区' + (enf.band || '25-50') + ')'
            : (d5.recommendation === 'enable_strict_linkage'
               ? '达标未启' : 'off(观察中)');
        cells.push({ k: 'D5 强制联动', v: state,
                     cls: enf.active ? 'red' :
                          (d5.recommendation === 'enable_strict_linkage'
                           ? 'yellow' : '') });
    } catch (e) { /* D5 卡缺省不阻断序列图 */ }

    document.getElementById('seriesSummary').innerHTML =
        cells.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                '</div><div class="v ' + (c.cls || '') + '">' +
                esc(c.v) + '</div></div>';
        }).join('');
}

/* Redis 实况体检(键族/内存水位/慢日志/大 key/告警;
 * 仅按钮按需加载——KEYS/SLOWLOG/MEMORY USAGE 有执行开销) */
async function loadRedisHealth() {
    var el = document.getElementById('redisHealth');
    el.innerHTML = '<div class="dash-empty">采集中…(KEYS/SLOWLOG/MEMORY USAGE)</div>';
    try {
        var b = await fetchJson(api('/api/security/admin/redis/health'),
                                { headers: headers() }, 'Redis 实况');
        renderRedisHealth(b);
    } catch (e) {
        el.innerHTML = '<div class="dash-error" style="display:block">' +
            esc(e.message) + '</div>';
    }
}

/* P5-2: 一键发送 Redis 告警站内信(管理员触达, 24h 规则级去重) */
async function sendRedisAlert() {
    try {
        var b = await fetchJson(
            api('/api/security/admin/redis/alert/test'), {
                method: 'POST', headers: headers()
            }, '发送告警站内信');
        showInfo('告警站内信: 已发送 ' + (b.sent || 0) + ' 名管理员' +
                 '(去重 ' + (b.deduped || 0) + ' 条)');
    } catch (e) { showError(e.message); }
}

function renderRedisHealth(b) {
    var html = '';

    // 告警条(critical/warn/info)
    var hasP1 = (b.alerts || []).some(function (a) {
        return a.level === 'critical' || a.level === 'warn';
    });
    (b.alerts || []).forEach(function (a) {
        html += '<div class="redis-alert ' + esc(a.level || 'info') +
            '">[' + esc(a.rule) + '] ' + esc(a.message) + '</div>';
    });
    if (!(b.alerts || []).length) {
        html += '<div class="redis-alert info">无告警(阈值: 单键>100KB / ' +
            'rate键>100k / 内存>80%)</div>';
    }
    // P5-2: 有 P1 级告警时提供一键触达(24h 规则级去重)
    if (hasP1) {
        html += '<button class="dash-btn" style="margin-bottom:6px" ' +
            'onclick="sendRedisAlert()">发送告警站内信</button>';
    }

    // 内存水位 + 概览 cells
    var m = b.memory;
    var cells = [{ k: '存储模式', v: b.mode === 'redis' ? 'Redis' : '内存' }];
    if (m) {
        cells.push({ k: '已用内存', v: m.usedHuman || '-' });
        cells.push({ k: '内存上限', v: m.maxHuman || '无限制' });
        cells.push({ k: '内存水位',
            v: m.usedPct == null ? '-' : pct(m.usedPct),
            cls: (m.usedPct || 0) > 0.8 ? 'red' : 'green' });
        cells.push({ k: '碎片率', v: m.fragmentationRatio || '-',
            cls: (m.fragmentationRatio || 0) > 1.5 ? 'yellow' : '' });
        cells.push({ k: '淘汰策略', v: m.policy || '-' });
    }
    if (b.dbSize != null) { cells.push({ k: '总键数(DBSIZE)', v: b.dbSize }); }
    html += '<div class="ov-cells">' + cells.map(function (c) {
        return '<div class="ov-cell"><div class="k">' + esc(c.k) +
            '</div><div class="v ' + (c.cls || '') + '">' +
            esc(c.v) + '</div></div>';
    }).join('') + '</div>';

    // 键族计数表(12 族; rate 超 10 万红标=窗口泄漏检查)
    var fam = b.keyFamilies || {};
    var famRows = Object.keys(fam).map(function (k) {
        var red = k === 'rate' && fam[k] > 100000;
        return '<tr><td>' + esc(k) + '</td><td class="' +
            (red ? 'red' : '') + '">' + fam[k] + '</td></tr>';
    }).join('');
    html += '<table class="matrix redis-fam" style="margin-top:10px">' +
        '<thead><tr><th>键族(zhuxiang:security43:)</th><th>键数</th>' +
        '</tr></thead><tbody>' + famRows + '</tbody></table>';

    // 慢日志 Top10 + 大 key
    var lines = [];
    (b.slowlog || []).forEach(function (s) {
        lines.push('慢[' + s.durationMs + 'ms] ' + s.command);
    });
    (b.bigKeys || []).forEach(function (k) {
        lines.push('大key ' + k.human + ' ' + k.key);
    });
    if (lines.length) {
        html += '<div class="detail-body" style="display:block">' +
            esc(lines.join('\n')) + '</div>';
    }
    html += '<div style="font-size:11px;color:#999;margin-top:8px">' +
        '采集于 ' + esc(b.collectedAt || '-') + ' · 手动体检' +
        '(不进自动刷新) · 阈值口径见操作指南 §七</div>';

    document.getElementById('redisHealth').innerHTML = html;
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
    loadReportSeries().catch(function (e) { showError(e.message); });
    setInterval(function () { refreshData(); }, 30000);
})();
