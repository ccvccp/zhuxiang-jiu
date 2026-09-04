/**
 * 47号·L2/L3 信值验真风控看板(P0-P4 五区块)
 * 范式: js/trust-dashboard.js(45号)平移——ES5、localStorage
 * 连接、区块化加载(手动刷新, 不进自动刷新)。
 * 依赖后端: /api/trust/risk/*(47号 trust_risk_routes; admin)
 * 区块: ①风险排行 ②命中统计 ③嫌疑视图 ④复核队列 ⑤回流状态
 */
'use strict';

var API_BASE_KEY = 'trustRiskDash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY)
              || 'http://localhost:8000' };
var ADMIN_HEADERS = { 'X-Role': 'admin',
                      'Content-Type': 'application/json' };

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

function tierPill(tier) {
    return '<span class="tier-pill ' + esc(tier) + '">' +
        esc(tier) + '</span>';
}

function sigChips(hitCounts) {
    var parts = Object.keys(hitCounts || {}).map(function (k) {
        return '<span class="sig-chip">' + esc(k) + ' ×' +
            esc(hitCounts[k]) + '</span>';
    });
    return parts.join('') || '—';
}

/* ============================================================
 * 五区块加载(单聚合端点)
 * ============================================================ */

async function loadAll() {
    try {
        var b = await fetchJson(api('/api/trust/risk/dashboard'),
            { headers: ADMIN_HEADERS }, '风控看板');
        var zones = b.zones || {};
        markUpdate();

        // ① 风险排行
        var rk = zones.ranking || {};
        if (rk.error) {
            cells('ovRanking', [{ k: '风险排行', v: '区块异常',
                cls: 'red' }]);
        } else {
            var bt = rk.byTier || {};
            cells('ovRanking', [
                { k: '画像总数', v: rk.total || 0, cls: 'blue' },
                { k: 'trusted', v: bt.trusted || 0, cls: 'green' },
                { k: 'standard', v: bt.standard || 0 },
                { k: 'watched', v: bt.watched || 0, cls: 'yellow' },
                { k: 'restricted', v: bt.restricted || 0, cls: 'red' },
            ]);
            var wl = rk.watchlist || [];
            document.getElementById('watchlist').innerHTML =
                wl.map(function (e) {
                    return '<tr><td>' + esc(e.trustId) + '</td><td>' +
                        tierPill(e.tier) + '</td><td>' +
                        esc((e.riskEMA || 0).toFixed(4)) +
                        '</td><td>' + esc(e.trustLevel) + '</td><td>' +
                        esc(e.eventCount || 0) + '</td><td>' +
                        sigChips(e.hitCounts) + '</td><td>' +
                        esc(String(e.lastUpdated || '').replace('T', ' ')
                            .slice(0, 19)) + '</td></tr>';
                }).join('') ||
                '<tr><td colspan="7" class="dash-empty">' +
                '暂无 watched/restricted 档案</td></tr>';
        }

        // ② 命中统计
        var ht = zones.hits || {};
        if (ht.error) {
            cells('ovHits', [{ k: '命中统计', v: '区块异常',
                cls: 'red' }]);
        } else {
            var totals = ht.totals || {};
            var totalHits = Object.keys(totals)
                .reduce(function (s, k) { return s + totals[k]; }, 0);
            cells('ovHits', [
                { k: '画像事件总量', v: ht.totalEvents || 0,
                  cls: 'blue' },
                { k: '命中总数', v: totalHits, cls: 'red' },
                { k: '复用(semantic)', v: totals.semantic_reuse || 0,
                  cls: 'yellow' },
                { k: '价值(value)', v: totals.value_anomaly || 0,
                  cls: 'yellow' },
                { k: '团伙(collusive)', v:
                  totals.collusive_suspect || 0, cls: 'red' },
                { k: '伪善(hypocrisy)', v: totals.hypocrisy || 0 },
            ]);
            document.getElementById('hitChips').innerHTML =
                Object.keys(totals).map(function (k) {
                    return '<span class="sig-chip">' + esc(k) +
                        ' <b>' + totals[k] + '</b>(' +
                        esc((ht.affectedProfiles || {})[k] || 0) +
                        '档案)</span>';
                }).join('') || '暂无命中';
        }

        // ③ 嫌疑视图
        var co = zones.collusion || {};
        if (co.error) {
            cells('ovCollusion', [{ k: '嫌疑视图', v: '区块异常',
                cls: 'red' }]);
        } else {
            var ct = co.totals || {};
            cells('ovCollusion', [
                { k: '互证对', v: ct.mutualPairs || 0, cls: 'blue' },
                { k: '共享指纹', v: ct.sharedFingerprints || 0 },
                { k: '嫌疑档案', v: ct.suspects || 0, cls: 'red' },
                { k: '扫描存证', v: ct.depositEvents || 0 },
            ]);
            var sus = co.suspects || [];
            document.getElementById('suspectList').innerHTML =
                sus.map(function (s) {
                    var pairs = (s.mutualPairs || []).map(
                        function (p) {
                            return esc(p.partner) + '×' + p.mutual;
                        }).join(', ');
                    return '<tr><td>' + esc(s.trustId) + '</td><td>' +
                        esc(pairs || '—') + '</td><td>' +
                        esc(s.shareCount || 0) + '</td><td>' +
                        (s.marked ? '<b style="color:#c0392b">✓</b>'
                            : '—') + '</td></tr>';
                }).join('') ||
                '<tr><td colspan="4" class="dash-empty">' +
                '暂无团伙嫌疑</td></tr>';
        }

        // ④ 复核队列
        var rv = zones.reviews || {};
        if (rv.error) {
            cells('ovReviews', [{ k: '复核队列', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovReviews', [
                { k: '待复核', v: rv.pendingCount || 0,
                  cls: (rv.pendingCount || 0) > 0 ? 'red' : 'green' },
                { k: '近期已决', v: (rv.recent || []).length },
            ]);
            var rows = (rv.pending || []).concat(rv.recent || []);
            document.getElementById('reviewList').innerHTML =
                rows.map(function (r) {
                    var st = r.status === 'pending' ? 'yellow'
                        : (r.status === 'calibrated' ? 'green' : 'gray');
                    return '<tr><td class="v ' + st + '" style="font-size:' +
                        '11px;font-weight:600">' + esc(r.status) +
                        '</td><td>' + esc(r.trustId) + '</td><td>' +
                        tierPill(r.tierAtRequest || '') +
                        '</td><td>' + esc(String(r.reason || '')
                            .slice(0, 40)) + '</td><td>' +
                        esc(String(r.requestedAt || '')
                            .replace('T', ' ').slice(0, 19)) +
                        '</td></tr>';
                }).join('') ||
                '<tr><td colspan="5" class="dash-empty">' +
                '暂无复核记录</td></tr>';
        }

        // ⑤ 回流状态
        var pr = zones.prior || {};
        if (pr.error) {
            cells('ovPrior', [{ k: '回流状态', v: '区块异常',
                cls: 'red' }]);
        } else {
            var g = pr.tierGates || {};
            cells('ovPrior', [
                { k: '先验回流开关',
                  v: pr.enabled ? 'ON' : 'OFF',
                  cls: pr.enabled ? 'green' : 'gray' },
                { k: '环境变量', v: pr.envVar || 'RISK_PRIOR_MODE',
                  cls: 'gray' },
                { k: '叠乘封底', v: '×' + (pr.combinedFloor || 0.4) },
                { k: '加速封顶', v: '×' + (pr.accelCap || 1.15) },
                { k: 'restricted 档', v: '×' + (g.restricted || 0.5),
                  cls: 'red' },
                { k: 'trusted 档', v: '×' + (g.trusted || 1.1),
                  cls: 'green' },
            ]);
            document.getElementById('priorNote').textContent =
                pr.note || '';
        }
    } catch (e) {
        showError(String(e.message || e));
    }
}

/* ============================================================
 * 公平性桥接(46号 tier 维度上报)
 * ============================================================ */

async function runFairnessBridge() {
    try {
        var r = await fetchJson(
            api('/api/trust/risk/dashboard/fairness-bridge'),
            { method: 'POST', headers: ADMIN_HEADERS },
            '公平性桥接');
        showInfo('公平性桥接完成: 上报 ' + (r.bridged || 0) +
            ' 个分组(' + (r.groups || []).join(', ') +
            ')——46号公平性审计已含风险等级维度');
    } catch (e) {
        showError(String(e.message || e));
    }
}

/* ============================================================
 * 画像详情查询(审计追溯)
 * ============================================================ */

async function loadAudit() {
    var tid = document.getElementById('auditTrustId').value.trim();
    if (!tid) {
        showError('请输入 trustId');
        return;
    }
    try {
        var p = await fetchJson(api('/api/trust/risk/' + tid),
            { headers: ADMIN_HEADERS }, '画像查询');
        cells('ovPrior', [
            { k: 'trustId', v: p.trustId, cls: 'blue' },
            { k: '分层', v: p.tier, cls: p.tier === 'trusted'
                ? 'green' : (p.tier === 'restricted' ? 'red' : '') },
            { k: '风险指数', v: (p.riskEMA || 0).toFixed(4) },
            { k: '信任度', v: p.trustLevel },
            { k: '画像事件', v: p.eventCount || 0 },
            { k: '待复核', v: p.pendingReview ? '是' : '否',
              cls: p.pendingReview ? 'red' : 'gray' },
        ]);
        document.getElementById('auditList').innerHTML =
            (p.riskHistory || []).map(function (h) {
                return '<tr><td>' +
                    esc(String(h.ts || '').replace('T', ' ')
                        .slice(0, 19)) + '</td><td>' +
                    esc(h.source) + '</td><td>' +
                    sigChips((h.signals || []).reduce(
                        function (o, s) { o[s] = 1; return o; }, {})) +
                    '</td><td>' + esc(h.risk) + '</td></tr>';
            }).join('') ||
            '<tr><td colspan="4" class="dash-empty">' +
            '暂无风险历史</td></tr>';
        showInfo('画像 ' + tid + ': ' + p.tier +
            ' 层(风险指数 ' + p.riskEMA + ')');
    } catch (e) {
        showError(String(e.message || e));
    }
}

/* 启动 */
loadAll();
