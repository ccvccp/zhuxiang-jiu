/**
 * 45号·信值模块治理看板(P0-P5 六区块)
 * 范式: js/api-dashboard.js(44号)平移——ES5、localStorage
 * 连接、区块化加载(手动刷新, 不进自动刷新)。
 * 依赖后端: /api/trust/open/*(45号 trust_value_routes)
 * 区块: ①角色总览 ②雷达态势 ③修复引擎 ④信值资产
 *       ⑤自进化面板 ⑥审计日志
 */
'use strict';

var API_BASE_KEY = 'trustDash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY)
              || 'http://localhost:8000' };

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

/* ============================================================
 * 六区块加载(单聚合端点 + 审计查询)
 * ============================================================ */

async function loadAll() {
    try {
        var b = await fetchJson(
            api('/api/trust/open/dashboard'), {}, '治理看板');

        // ① 角色档案总览
        var ov = b.overview || {};
        var bg = ov.byGrade || {};
        cells('ovOverview', [
            { k: '角色总数', v: ov.total || 0, cls: 'blue' },
            { k: '个人', v: ov.persons || 0 },
            { k: '企业/机构', v: ov.orgs || 0 },
            { k: '熔断档案', v: ov.fused || 0, cls: 'red' },
            { k: '熔断率', v: ((ov.fusedRate || 0) * 100).toFixed(1) + '%',
              cls: (ov.fusedRate || 0) > 0.1 ? 'red' : 'green' },
            { k: 'healthy', v: bg.healthy || 0, cls: 'green' },
            { k: 'watch', v: bg.watch || 0, cls: 'yellow' },
            { k: 'strained', v: bg.strained || 0, cls: 'yellow' },
            { k: 'critical', v: bg.critical || 0, cls: 'red' },
        ]);

        // ② AI 雷达态势
        var rd = b.radar || {};
        var bySrc = rd.bySource || {};
        cells('ovRadar', [
            { k: '事件总量', v: rd.eventsTotal || 0, cls: 'blue' },
            { k: '公开域雷达(radar)', v: bySrc.radar || 0 },
            { k: '授权探针(probe)', v: bySrc.probe || 0 },
            { k: '自愿存证(deposit)', v: bySrc.deposit || 0 },
            { k: '修复(repair)', v: bySrc.repair || 0 },
            { k: '存证拒(rejected)', v:
              (bySrc.deposit_rejected || 0) +
              (bySrc.repair_rejected || 0), cls: 'yellow' },
        ]);
        var srcRows = Object.keys(bySrc).map(function (k) {
            return esc(k) + ' <b>' + bySrc[k] + '</b>';
        });
        document.getElementById('radarBySource').innerHTML =
            srcRows.join(' · ') || '暂无通道数据';

        // ③ 修复引擎监控
        var repairable = (bySrc.appeal_reversal !== undefined)
            ? '含翻转回滚' : '';
        cells('ovRepair', [
            { k: '修复事件(repair)', v: bySrc.repair || 0, cls: 'blue' },
            { k: '修复拒(rejected)', v: bySrc.repair_rejected || 0,
              cls: 'yellow' },
            { k: '申诉翻转回滚', v: bySrc.appeal_reversal || 0 },
            { k: '人工灌入(manual)', v: bySrc.manual || 0 },
            { k: '口径', v: 'α/β/γ 引擎', repairable },
        ]);

        // ④ 信值资产总览
        var at = b.assets || {};
        var cov = (at.reserveCoverage || 0) * 100;
        cells('ovAssets', [
            { k: '累计发行 TV', v: at.issuedTotal || 0, cls: 'blue' },
            { k: '累计销毁 TV', v: at.burnedTotal || 0, cls: 'red' },
            { k: '流通中(≈)', v:
              ((at.issuedTotal || 0) - (at.burnedTotal || 0)).toFixed(1),
              cls: 'green' },
            { k: '准备金池 TV', v: at.reservePool || 0 },
            { k: '准备金覆盖率', v: cov.toFixed(1) + '%',
              cls: cov < 80 ? 'red' : 'green' },
            { k: '面值锚定', v: '1 TV = 1 元货品' },
        ]);

        // ⑤ 自进化面板
        var ev = b.evolution || {};
        cells('ovEvolution', [
            { k: '申诉总数', v: ev.appeals || 0, cls: 'blue' },
            { k: '待复核申诉', v: ev.appealsPending || 0,
              cls: (ev.appealsPending || 0) > 0 ? 'yellow' : 'green' },
            { k: '伦理补丁数', v: ev.patches || 0 },
            { k: '最近补丁', v: (ev.patchesLatest || '-')
              .slice(0, 10) },
            { k: '学习档案', v: '第28档案' },
            { k: '宪法护栏', v: '50/30/20 恒定' },
        ]);
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* ⑥ 监管审计日志(开放面访问留痕查询) */
async function loadAudit() {
    var tid = (document.getElementById('auditTrustId').value
               || '').trim();
    if (!tid || !/^\d+$/.test(tid)) {
        showError('请输入数字 trustId');
        return;
    }
    try {
        var b = await fetchJson(
            api('/api/trust/open/audit/' + tid),
            { headers: { 'X-App-Code': 'dashboard' } }, '审计查询');
        var logs = (b.accessLog || []);
        document.getElementById('auditLog').innerHTML =
            logs.map(function (l) {
                return '<tr><td>' + esc((l.ts || '').slice(0, 19)) +
                    '</td><td>' + esc(l.action) +
                    '</td><td>' + esc(l.caller) +
                    '</td><td>#' + esc(l.trustId) +
                    '</td><td style="color:#888">' +
                    esc(l.detail || '') + '</td></tr>';
            }).join('') ||
            '<tr><td colspan="5" class="dash-empty">该档案暂无开放面访问记录</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* 初始化 */
(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
})();
