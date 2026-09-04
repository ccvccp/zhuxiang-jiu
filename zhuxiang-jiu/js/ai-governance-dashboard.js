/**
 * 46号·AI 治理与合规中枢治理看板(P0-P5 六区块, 压轴)
 * 范式: js/trust-dashboard.js(45号)平移——ES5、localStorage
 * 连接、区块化加载(手动刷新, 不进自动刷新)。
 * 依赖后端: /api/ai-gov/*(46号 ai_governance_routes)
 * 区块: ①档案总览 ②审批队列 ③健康排行 ④公平性视图
 *       ⑤回放轨迹 ⑥合规入口 + 干预闭环
 */
'use strict';

var API_BASE_KEY = 'aiGovDash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY)
              || 'http://localhost:8000' };

function api(path) { return state.apiBase + path; }

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}

function adminHeaders() {
    return { 'Content-Type': 'application/json',
             'X-Role': 'admin' };
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

/* ============================================================
 * 六区块加载(单聚合端点)
 * ============================================================ */

async function loadAll() {
    try {
        var b = await fetchJson(
            api('/api/ai-gov/dashboard'),
            { headers: adminHeaders() }, '治理看板');
        var z = b.zones || {};
        if ((b.zoneErrors || []).length) {
            showInfo('部分区块降级: ' + (b.zoneErrors || []).join(', '));
        }

        // ① 档案总览
        renderRegistry(z.registry || {});
        // ② 审批队列
        renderApprovals(z.approvals || {});
        // ③ 健康排行
        renderHealth(z.health || {});
        // ④ 公平性视图
        renderFairness(z.fairness || {});
        // ⑤ 回放轨迹
        renderReplay(z.replay || {});
        // ⑥ 合规入口
        renderCompliance(z.compliance || {});

        markUpdate();
    } catch (e) {
        showError(String(e.message || e));
    }
}

function renderRegistry(r) {
    var st = r.byStatus || {};
    cells('ovRegistry', [
        { k: '档案总数', v: r.total || 0, cls: 'blue' },
        { k: 'active', v: st.active || 0, cls: 'green' },
        { k: 'frozen', v: st.frozen || 0,
          cls: (st.frozen || 0) > 0 ? 'red' : 'green' },
        { k: 'retired', v: st.retired || 0 },
        { k: 'batch 覆盖', v: Object.keys(r.byBatch || {}).length },
        { k: '最近同步', v: (r.recentSyncedAt || '-').slice(0, 10) },
    ]);
    var fl = r.frozenScorers || [];
    document.getElementById('regFrozen').innerHTML = fl.length
        ? ('冻结档案: ' + fl.map(esc).join(', '))
        : '当前无冻结档案(28 档案全部可学习)';
}

function renderApprovals(a) {
    var st = a.byStatus || {};
    cells('ovApprovals', [
        { k: '待审批', v: a.pendingCount || 0,
          cls: (a.pendingCount || 0) > 0 ? 'red' : 'green' },
        { k: '累计 approved', v: st.approved || 0, cls: 'green' },
        { k: '累计 rejected', v: st.rejected || 0 },
    ]);
    var rows = a.pendingChanges || [];
    var tbl = document.getElementById('pendingTable');
    if (!rows.length) {
        tbl.innerHTML = '<tr><td class="dash-empty">审批队列清空 ✓</td></tr>';
        return;
    }
    tbl.innerHTML = '<tr><th>changeId</th><th>档案</th><th>类型</th>'
        + '<th>理由</th><th>申请人</th><th>操作</th></tr>'
        + rows.map(function (c) {
            return '<tr><td>' + esc(c.changeId) + '</td><td>'
                + esc(c.scorerId) + '</td><td><span class="kind-pill">'
                + esc(c.kind) + '</span></td><td>' + esc(c.reason)
                + '</td><td>' + esc(c.requestedBy)
                + '</td><td><button class="dash-btn approve" '
                + 'onclick="reviewChange(' + c.changeId + ', true)">'
                + '批准</button> <button class="dash-btn danger" '
                + 'onclick="reviewChange(' + c.changeId + ', false)">'
                + '驳回</button></td></tr>';
        }).join('');
}

function renderHealth(h) {
    if (h.note) {
        cells('ovHealth', [
            { k: '巡检状态', v: '暂无快照' },
        ]);
        document.getElementById('healthTop').innerHTML =
            '<tr><td class="dash-empty">' + esc(h.note) + '</td></tr>';
        document.getElementById('healthBottom').innerHTML = '';
        return;
    }
    var hits = h.hits || {};
    cells('ovHealth', [
        { k: '平均健康分', v: h.avgScore == null ? '-' : h.avgScore,
          cls: (h.avgScore || 100) >= 90 ? 'green' : 'yellow' },
        { k: 'healthy', v: (h.byLevel || {}).healthy || 0, cls: 'green' },
        { k: 'watch', v: (h.byLevel || {}).watch || 0, cls: 'yellow' },
        { k: 'risk', v: (h.byLevel || {}).risk || 0, cls: 'red' },
        { k: '停滞命中', v: hits.stagnation || 0,
          cls: hits.stagnation ? 'red' : 'green' },
        { k: '枯竭命中', v: hits.depletion || 0,
          cls: hits.depletion ? 'red' : 'green' },
        { k: '漂移高命中', v: hits.drift_high || 0,
          cls: hits.drift_high ? 'red' : 'green' },
        { k: '最近巡检', v: (h.lastScan || {}).scanId || '-' },
    ]);
    var rowHtml = function (e) {
        return '<tr><td>' + esc(e.scorerId) + '</td><td>'
            + esc(e.label) + '</td><td><b>' + esc(e.healthScore)
            + '</b></td><td><span class="grade-pill '
            + esc(e.healthLevel) + '">' + esc(e.healthLevel)
            + '</span></td><td>' + (e.signals || []).map(esc).join(', ')
            + '</td></tr>';
    };
    var head = '<tr><th>档案</th><th>名称</th><th>健康分</th>'
        + '<th>层级</th><th>命中信号</th></tr>';
    document.getElementById('healthBottom').innerHTML = head
        + (h.bottom || []).map(rowHtml).join('');
    document.getElementById('healthTop').innerHTML = head
        + (h.top || []).map(rowHtml).join('');
}

function renderFairness(f) {
    cells('ovFairness', [
        { k: '审计报告数', v: f.reportsTotal || 0, cls: 'blue' },
        { k: 'flagged 偏疑', v: f.flaggedCount || 0,
          cls: f.flaggedCount ? 'red' : 'green' },
        { k: '无偏疑档案', v: f.normalCount || 0 },
    ]);
    var rows = f.flagged || [];
    var tbl = document.getElementById('flaggedTable');
    if (!rows.length) {
        tbl.innerHTML = '<tr><td class="dash-empty">'
            + (f.note ? esc(f.note) : '无偏疑档案 ✓(阈值: 均值差>20% 或 通过率差>15pp)')
            + '</td></tr>';
        return;
    }
    tbl.innerHTML = '<tr><th>档案</th><th>采样</th><th>均值差</th>'
        + '<th>通过率差</th><th>结论</th></tr>'
        + rows.map(function (r) {
            return '<tr><td>' + esc(r.scorerId) + '</td><td>'
                + esc(r.sampleCount) + '</td><td class="drift-mark">'
                + esc(((r.meanDiffRatio || 0) * 100).toFixed(1))
                + '%</td><td class="drift-mark">'
                + esc(r.passRateGap) + 'pp</td><td>'
                + esc(r.conclusion) + '</td></tr>';
        }).join('');
}

function renderReplay(r) {
    cells('ovReplay', [
        { k: '决策日志', v: r.logsTotal || 0, cls: 'blue' },
        { k: '漂移标记', v: r.driftedCount || 0,
          cls: r.driftedCount ? 'red' : 'green' },
    ]);
    var rows = r.recentLogs || [];
    var tbl = document.getElementById('replayTable');
    if (!rows.length) {
        tbl.innerHTML = '<tr><td class="dash-empty">暂无决策日志'
            + '(POST /api/ai-gov/replay 上报)</td></tr>';
        return;
    }
    tbl.innerHTML = '<tr><th>replayId</th><th>档案</th><th>脱敏引用</th>'
        + '<th>原分</th><th>版本</th><th>漂移</th></tr>'
        + rows.map(function (l) {
            return '<tr><td>' + esc(l.replayId) + '</td><td>'
                + esc(l.scorerId) + '</td><td>' + esc(l.subjectRef)
                + '</td><td>' + esc(l.score) + '</td><td>'
                + esc(l.weightVersion) + '</td><td>'
                + (l.drifted ? '<span class="drift-mark">⚠ 漂移</span>'
                   : '✓') + '</td></tr>';
        }).join('');
}

function renderCompliance(c) {
    var audit = c.lastAudit || {};
    cells('ovCompliance', [
        { k: '在册档案', v: c.registryCount || 0, cls: 'blue' },
        { k: '公平性报告', v: c.fairnessReports || 0 },
        { k: '审计窗(天)', v: audit.windowDays || '-' },
        { k: '窗内变更', v: audit.changes == null ? '-' : audit.changes },
        { k: '窗内告警', v: audit.alerts == null ? '-' : audit.alerts },
        { k: '公平性偏疑', v: audit.flagged == null ? '-' : audit.flagged,
          cls: audit.flagged > 0 ? 'red' : 'green' },
    ]);
    var eps = c.endpoints || {};
    document.getElementById('complianceNote').innerHTML =
        '入口: <code>' + esc(eps.filing) + '</code> / <code>'
        + esc(eps.report) + '</code> / <code>'
        + esc(eps.fairnessReport) + '</code>'
        + (audit.conclusion ? ('<br>最近审计: ' + esc(audit.conclusion))
           : '');
}

/* ============================================================
 * 干预闭环(冻结/解冻: 申请→审批→生效→守卫)
 * ============================================================ */

async function submitGate() {
    var scorer = document.getElementById('gateScorer').value;
    var kind = document.getElementById('gateKind').value;
    var reason = document.getElementById('gateReason').value.trim();
    if (!reason) {
        showError('干预申请必须填写理由(审计留痕)');
        return;
    }
    try {
        await fetchJson(api('/api/ai-gov/changes'), {
            method: 'POST',
            headers: adminHeaders(),
            body: JSON.stringify({
                scorerId: scorer, kind: kind,
                reason: reason, payload: { via: 'dashboard' },
            }),
        }, '提交申请');
        document.getElementById('gateReason').value = '';
        showInfo('申请已受理(pending)——审批通过后注册中心生效, '
                 + 'run_learning 守卫即刻拦截');
        loadAll();
    } catch (e) {
        showError(String(e.message || e));
    }
}

async function reviewChange(changeId, approve) {
    try {
        await fetchJson(
            api('/api/ai-gov/changes/' + changeId + '/review'), {
            method: 'POST',
            headers: adminHeaders(),
            body: JSON.stringify({
                approve: approve,
                reviewNote: '看板一键审批',
            }),
        }, '审批');
        showInfo('变更 ' + changeId + ' 已'
                 + (approve ? '批准并执行生效' : '驳回(留痕)'));
        loadAll();
    } catch (e) {
        showError(String(e.message || e));
    }
}

async function reviewFirst() {
    try {
        var b = await fetchJson(
            api('/api/ai-gov/changes?status=pending'),
            { headers: adminHeaders() }, '审批队列');
        var changes = b.changes || [];
        if (!changes.length) {
            showInfo('审批队列已清空');
            return;
        }
        await reviewChange(changes[0].changeId, true);
    } catch (e) {
        showError(String(e.message || e));
    }
}

async function runScan() {
    try {
        var b = await fetchJson(
            api('/api/ai-gov/health/scan'),
            { method: 'POST', headers: adminHeaders() }, '健康巡检');
        showInfo('巡检完成 scanId=' + (b.scanId || '-')
                 + ' 命中=' + JSON.stringify(b.hits || {}));
        loadAll();
    } catch (e) {
        showError(String(e.message || e));
    }
}

/* ============================================================
 * 初始化
 * ============================================================ */

(async function init() {
    document.getElementById('apiBase').value = state.apiBase;
    // 干预闭环下拉: 填充在册档案
    try {
        var b = await fetchJson(
            api('/api/ai-gov/registry'),
            { headers: adminHeaders() }, '台账');
        var sel = document.getElementById('gateScorer');
        sel.innerHTML = (b.entries || []).map(function (g) {
            return '<option value="' + esc(g.scorerId) + '">'
                + esc(g.scorerId) + '</option>';
        }).join('');
    } catch (e) { /* 下拉保持默认 */ }
    loadAll();
})();
