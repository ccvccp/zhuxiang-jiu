/* 62号·无形资产估值工作台 js/asset-dashboard.js
 * 四区块渲染(对齐 invoice-dashboard.js 模式):
 *   ①模型状态 ②四区看板 ③资产列表 ④评估流水
 * 观测面端点: /model/status /dashboard /assets /assessments
 */
var API_BASE_KEY = 'assetDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    registryDomains: [],
};

function headers() { return { 'X-Role': 'admin' }; }

async function fetchJson(url, options, label) {
    var opts = Object.assign({ headers: headers() }, options || {});
    if (opts.body && typeof opts.body === 'object') {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
    }
    var resp = await fetch(url, opts);
    var body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    if (!resp.ok) {
        var msg = (body && (body.detail || body.error)) || resp.status;
        throw new Error(label + ': ' + msg);
    }
    return body;
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
    el.textContent = msg; el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}
function markUpdate() {
    document.getElementById('lastUpdate').textContent =
        '更新于 ' + new Date().toLocaleTimeString();
}
function pct(v) { return ((v || 0) * 100).toFixed(1) + '%'; }
var ROLE_L = { enterprise: '企业', organization: '组织', personal: '个人' };
var TIER_C = { high: 'green', medium: 'yellow', low: 'red' };
var TIER_L = { high: '高', medium: '中', low: '低' };

function saveConn() {
    state.apiBase = document.getElementById('apiBase').value.trim()
        || 'http://localhost:8000';
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    refreshData();
}

function refreshData() {
    loadModelStatus();
    loadDashboard();
    loadAssets();
    loadAssessments();
}

/* ---------- ① 模型状态 ---------- */
async function loadModelStatus() {
    try {
        var b = await fetchJson(api('/api/av62/model/status'), {}, '模型状态');
        var s = b.status || {};
        var mode = s.mode || 'off';
        var badge = document.getElementById('modeBadge');
        badge.textContent = 'AV62_MODE=' + mode;
        badge.className = 'badge ' + (mode === 'off' ? 'weak' : 'green');
        var cells = [
            ['模块', s.module || 'av62'],
            ['治理档案', s.scorerId || '-'],
            ['当前档位', mode],
            ['生效版本', s.activeVersion || '-'],
            ['决策域',
                (s.decisions || []).join('/') || '-'],
        ];
        document.getElementById('modelCells').innerHTML =
            cells.map(function (c) {
                return '<div class="ov-cell"><div class="k">' + esc(c[0])
                    + '</div><div class="v">' + esc(c[1]) + '</div></div>';
            }).join('');
        var fm = s.factorsMeta || {};
        document.getElementById('factorGrid').innerHTML =
            Object.keys(fm).map(function (k) {
                return '<div class="kv"><b>' + esc(fm[k])
                    + '</b>' + esc(k) + '</div>';
            }).join('');
    } catch (e) { showError(e.message); }
}

/* ---------- ② 四区看板 ---------- */
function kvBlock(label, value, sub) {
    return '<div class="kv"><b>' + esc(label) + '</b>'
        + esc(value) + (sub ? '<div style="color:#999;font-size:11px">'
            + esc(sub) + '</div>' : '') + '</div>';
}
function distLine(d) {
    if (!d) return '-';
    return Object.keys(d).map(function (k) {
        return k + ':' + d[k];
    }).join(' · ') || '-';
}
async function loadDashboard() {
    try {
        var b = await fetchJson(api('/api/av62/dashboard'), {}, '四区看板');
        var z = b.zones || {};

        var m = z.metrics || {};
        var fair = m.fairness || {};
        var scorer = m.scorer || {};
        var mCells = [
            ['估值准确率', m.valuationAccuracy != null
                ? pct(m.valuationAccuracy) : '—'],
            ['归因锚定率', m.attributionGrounded != null
                ? pct(m.attributionGrounded) : '—'],
            ['公平态势', fair.compliant === true ? '达标'
                : (fair.insufficient ? '样本不足'
                : (fair.flagged ? '警示' : '—'))],
            ['申诉翻转率', pct(m.appealOverturnRate)],
            ['档案信任分', scorer.trustScore != null
                ? Number(scorer.trustScore).toFixed(2) : '—'],
            ['已验证/评估', (m.verifiedCount || 0) + '/' + (m.assessedCount || 0)],
        ];
        document.getElementById('zoneMetrics').innerHTML =
            mCells.map(function (c) {
                return '<div class="ov-cell"><div class="k">' + esc(c[0])
                    + '</div><div class="v">' + esc(c[1]) + '</div></div>';
            }).join('');

        var a = z.assets || {};
        document.getElementById('zoneAssets').innerHTML =
            kvBlock('资产总数', a.total || 0)
            + kvBlock('角色分布', distLine(a.byRole))
            + kvBlock('域分布', distLine(a.byDomain), '九正域+risk')
            + kvBlock('流动性档', distLine(a.byLiquidity))
            + kvBlock('状态分布', distLine(a.byStatus))
            + kvBlock('负资产', (a.negativeCount || 0) + ' 项');

        var s = z.assessments || {};
        document.getElementById('zoneAssess').innerHTML =
            kvBlock('评估总数', s.total || 0)
            + kvBlock('置信档分布', distLine(s.byConfidence))
            + kvBlock('最大版本链', 'v' + (s.maxVersionChain || 0))
            + kvBlock('当前目标', s.objective || 'stability')
            + kvBlock('主体级聚合', (s.pooled || 0) + ' 次');

        var d = z.defense || {};
        var latest = d.redteamLatest || {};
        document.getElementById('zoneDefense').innerHTML =
            kvBlock('红队批次', (d.redteamRuns || 0) + ' 轮')
            + kvBlock('最近一轮',
                latest.allDefended === true ? '全防御'
                : (latest.allDefended === false ? '有失守' : '未执行'))
            + kvBlock('off 零影响断言',
                d.modeOffAssertion === true ? '通过' : '非 off 态')
            + kvBlock('看板口径', b.note || '');
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* ---------- ③ 资产列表 ---------- */
function fillDomainOptions(domains) {
    var sel = document.getElementById('domainSel');
    var cur = sel.value;
    domains.forEach(function (dm) {
        if (!Array.from(sel.options)
                .some(function (o) { return o.value === dm; })) {
            var o = document.createElement('option');
            o.value = dm; o.textContent = dm;
            sel.appendChild(o);
        }
    });
    sel.value = cur;
}
async function loadAssets() {
    try {
        var role = document.getElementById('roleSel').value;
        var domain = document.getElementById('domainSel').value;
        var q = [];
        if (role) q.push('role=' + role);
        if (domain) q.push('domain=' + domain);
        var b = await fetchJson(api('/api/av62/assets'
            + (q.length ? '?' + q.join('&') : '')), {}, '资产列表');
        var rows = b.assets || [];
        fillDomainOptions(Object.keys(b.byDomain || {}));
        var tbody = document.querySelector('#assetTable tbody');
        tbody.innerHTML = rows.map(function (r) {
            return '<tr><td>' + esc(r.assetId) + '</td>'
                + '<td>' + esc(r.subjectId) + '</td>'
                + '<td>' + esc(ROLE_L[r.role] || r.role) + '</td>'
                + '<td>' + esc(r.domain) + '</td>'
                + '<td>' + esc(r.label || '-') + '</td>'
                + '<td><span class="badge blue">' + esc(r.status) + '</span></td>'
                + '<td>' + (r.negative
                    ? '<span class="badge red">负</span>'
                    : '<span class="badge weak">正</span>') + '</td>'
                + '<td>' + esc((r.createdAt || '').replace('T', ' ')
                    .slice(0, 19)) + '</td></tr>';
        }).join('');
        document.getElementById('assetEmpty').style.display =
            rows.length ? 'none' : 'block';
    } catch (e) { showError(e.message); }
}

/* ---------- ④ 评估流水 ---------- */
async function loadAssessments() {
    try {
        var b = await fetchJson(api('/api/av62/assessments?limit=50'),
            {}, '评估流水');
        var rows = b.assessments || [];
        var tbody = document.querySelector('#assessTable tbody');
        tbody.innerHTML = rows.map(function (r) {
            var tier = r.confidenceTier || '?';
            return '<tr><td>' + esc(r.assessId) + '</td>'
                + '<td>' + esc(r.assetId) + '</td>'
                + '<td>' + esc(r.domain) + '</td>'
                + '<td>v' + esc(r.version) + '</td>'
                + '<td><span class="badge ' + (TIER_C[tier] || 'weak')
                    + '">' + esc(TIER_L[tier] || tier) + '</span></td>'
                + '<td>' + esc(r.elementScore) + '</td>'
                + '<td>' + esc(r.netContribution) + '</td>'
                + '<td>' + esc(r.baseValue) + '</td>'
                + '<td>' + esc(r.ruleId || '-') + '</td>'
                + '<td>' + esc((r.createdAt || '').replace('T', ' ')
                    .slice(0, 19)) + '</td></tr>';
        }).join('');
        document.getElementById('assessEmpty').style.display =
            rows.length ? 'none' : 'block';
    } catch (e) { showError(e.message); }
}

/* ---------- 启动 ---------- */
document.getElementById('apiBase').value = state.apiBase;
refreshData();
setInterval(refreshData, 60000);
