/**
 * 48号·小竹智能语音中枢看板(P0-P4 六区块)
 * 范式: js/trust-risk-dashboard.js(47号)平移——ES5、localStorage
 * 连接、区块化加载(手动刷新, 不进自动刷新)。
 * 依赖后端: /api/xiaozhu/*(48号 xiaozhu_routes; admin)
 * 区块: ①使用总览 ②指令命中 ③高敏台账 ④积分账本
 *       ⑤共创队列 ⑥治理桥接
 */
'use strict';

var API_BASE_KEY = 'xiaozhuDash.apiBase';
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

function pct(v) {
    return (v == null) ? '—' : v + '%';
}

function kindChips(counts) {
    var parts = Object.keys(counts || {}).map(function (k) {
        return '<span class="sig-chip">' + esc(k) + ' ×' +
            esc(counts[k]) + '</span>';
    });
    return parts.join('') || '—';
}

/* ============================================================
 * 六区块加载(单聚合端点)
 * ============================================================ */

async function loadAll() {
    try {
        var b = await fetchJson(api('/api/xiaozhu/dashboard'),
            { headers: ADMIN_HEADERS }, '语音中枢看板');
        var zones = b.zones || {};
        markUpdate();
        if ((b.zoneErrors || []).length) {
            showInfo('部分区块降级: ' + (b.zoneErrors || []).join(', '));
        }

        // ① 使用总览
        var u = zones.usage || {};
        if (u.error) {
            cells('ovUsage', [{ k: '使用总览', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovUsage', [
                { k: '会话量', v: u.sessions || 0, cls: 'blue' },
                { k: '语音会话', v: u.voiceSessions || 0 },
                { k: '轮次总量', v: u.turns || 0 },
                { k: '指令命中', v: u.commandTurns || 0,
                  cls: 'green' },
                { k: '直达率', v: pct(u.directRate),
                  cls: u.directRate != null && u.directRate < 50
                      ? 'yellow' : 'green' },
                { k: '语音轮次占比', v: pct(u.voiceShare) },
            ]);
            document.getElementById('usageNote').textContent =
                (u.note || '') + ' · 未唤醒 ' + (u.notWoken || 0) +
                ' · 转写失败 ' + (u.asrFailed || 0);
        }

        // ② 指令命中排行
        var c = zones.commands || {};
        if (c.error) {
            cells('ovCommands', [{ k: '指令命中', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovCommands', [
                { k: '指令种类', v: c.totalActions || 0, cls: 'blue' },
                { k: '兜底轮次', v: c.fallbackTurns || 0,
                  cls: 'yellow' },
                { k: '兜底率', v: pct(c.fallbackRate),
                  cls: c.fallbackRate != null && c.fallbackRate > 30
                      ? 'red' : 'green' },
            ]);
            var rk = c.ranking || [];
            document.getElementById('cmdRanking').innerHTML =
                rk.map(function (e, i) {
                    return '<tr><td>' + (i + 1) + '</td><td>' +
                        '<span class="kind-pill">' + esc(e.action) +
                        '</span></td><td>' + esc(e.hits) + '</td></tr>';
                }).join('') ||
                '<tr><td colspan="3" class="dash-empty">' +
                '暂无指令命中</td></tr>';
        }

        // ③ 高敏操作台账
        var cf = zones.confirm || {};
        if (cf.error) {
            cells('ovConfirm', [{ k: '高敏台账', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovConfirm', [
                { k: '令牌发放', v: cf.issued || 0, cls: 'blue' },
                { k: '核销成功', v: cf.confirmed || 0, cls: 'green' },
                { k: '通过率', v: pct(cf.passRate),
                  cls: cf.passRate != null && cf.passRate < 60
                      ? 'yellow' : 'green' },
                { k: '码错', v: cf.wrongCode || 0, cls: 'red' },
                { k: '过期', v: cf.expired || 0, cls: 'gray' },
                { k: '冷静期拦截', v: cf.cooldown || 0, cls: 'red' },
                { k: '幂等去重', v: cf.duplicate || 0, cls: 'gray' },
            ]);
            document.getElementById('confirmNote').textContent =
                cf.note || '';
        }

        // ④ 积分账本
        var p = zones.points || {};
        if (p.error) {
            cells('ovPoints', [{ k: '积分账本', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovPoints', [
                { k: '累计发放', v: p.awarded || 0, cls: 'green' },
                { k: '累计兑换', v: p.redeemed || 0, cls: 'blue' },
                { k: '余额总量', v: p.balanceTotal || 0 },
                { k: '持分会员', v: p.holders || 0, cls: 'blue' },
                { k: '流水条数', v: p.ledgerCount || 0, cls: 'gray' },
            ]);
            document.getElementById('pointsChips').innerHTML =
                kindChips(p.byKind);
        }

        // ⑤ 共创队列 + 失败聚类
        var co = zones.cocreate || {};
        if (co.error) {
            cells('ovCocreate', [{ k: '共创队列', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovCocreate', [
                { k: '待审共创', v: co.pendingCount || 0,
                  cls: (co.pendingCount || 0) > 0 ? 'yellow' : 'green' },
                { k: '已上架', v: co.approvedCount || 0, cls: 'green' },
                { k: '失败案例', v: co.failuresTotal || 0,
                  cls: (co.failuresTotal || 0) > 0 ? 'yellow'
                      : 'green' },
            ]);
            var pending = co.pending || [];
            document.getElementById('pendingList').innerHTML =
                pending.map(function (r) {
                    return '<tr><td>' + esc(r.cmdId) + '</td><td>' +
                        esc(r.phrase) + '</td><td>' +
                        '<span class="kind-pill">' + esc(r.action) +
                        '</span></td><td>' +
                        '<button class="dash-btn small approve" ' +
                        'onclick="reviewCustom(' + esc(r.cmdId) +
                        ', true)">上架</button> ' +
                        '<button class="dash-btn small reject" ' +
                        'onclick="reviewCustom(' + esc(r.cmdId) +
                        ', false)">驳回</button></td></tr>';
                }).join('') ||
                '<tr><td colspan="4" class="dash-empty">' +
                '暂无待审共创指令</td></tr>';
            var tp = co.topPhrases || [];
            document.getElementById('failureChips').innerHTML =
                tp.map(function (t) {
                    return '<span class="sig-chip">' + esc(t.phrase) +
                        ' ×' + esc(t.count) + '</span>';
                }).join('') || '暂无高频未兜住短语';
        }

        // ⑥ 治理桥接
        var f = zones.fairness || {};
        if (f.error) {
            cells('ovFairness', [{ k: '治理桥接', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovFairness', [
                { k: '采样档案', v: f.scorerId || '—', cls: 'blue' },
                { k: '等级分组', v: (f.groups || []).length },
            ]);
            var gs = f.groups || [];
            document.getElementById('fairnessList').innerHTML =
                gs.map(function (g) {
                    return '<tr><td><span class="kind-pill">' +
                        esc(g.group) + '</span></td><td>' +
                        esc(g.turns) + '</td><td>' + pct(g.directRate) +
                        '</td></tr>';
                }).join('') ||
                '<tr><td colspan="3" class="dash-empty">' +
                '暂无等级分组</td></tr>';
            document.getElementById('fairnessNote').textContent =
                f.note || '';
        }
    } catch (e) {
        showError(e.message);
    }
}

/* ============================================================
 * 干预闭环(共创审核 / 公平性桥接)
 * ============================================================ */

async function reviewCustom(cmdId, approve) {
    try {
        await fetchJson(
            api('/api/xiaozhu/commands/custom/' + cmdId + '/review'),
            { method: 'POST', headers: ADMIN_HEADERS,
              body: JSON.stringify({ approve: approve,
                                     note: '看板一键' +
                                           (approve ? '上架' : '驳回') }) },
            '共创审核');
        showInfo('共创指令 ' + cmdId +
                 (approve ? ' 已上架(贡献者 +100)' : ' 已驳回'));
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function runFairnessBridge() {
    try {
        var b = await fetchJson(
            api('/api/xiaozhu/dashboard/fairness-bridge'),
            { method: 'POST', headers: ADMIN_HEADERS }, '公平性桥接');
        showInfo('桥接完成: 上报 ' + (b.bridged || 0) + ' 组(' +
                 ((b.groups || []).join(', ') || '无有效分组') + ')');
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

/* ============================================================
 * 初始化
 * ============================================================ */

(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
})();
