/**
 * 36号·AI智能推广模块 v1.0 · 热点雷达大屏脚本
 *
 * 页面: ai-promo-radar.html
 * 职责:
 *   - 五平台热榜扫描(POST /api/promo/radar/scan)与扫描结果展示(new/discarded/skipped)
 *   - 顶部统计卡(热点总数/engaged/passed/discarded/待人工裁决)
 *   - 热点列表(GET /api/promo/radar/hotspots, 支持 status/platform 服务端筛选)
 *   - 待人工裁决列表(GET /api/promo/decisions?pendingOnly=true)与跟进/放弃裁决
 *   - 30 秒自动刷新
 *
 * 鉴权: X-Role: admin 兼容头(与 ai-hub-dashboard 一致; strict 模式叠加 JWT)
 * 响应约定: 统一解包 {"success": true, "data": ...}; 失败时横幅展示后端 detail
 */
'use strict';

/* ========= 全局状态 ========= */
var API_BASE_KEY = 'promoDash.apiBase';   // 与内容工厂页共用后端地址
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    scanResult: null,      // 最近一次扫描结果(scanned/new/discarded/skipped)
    autoTimer: null,       // 自动刷新句柄
    AUTO_MS: 30000,
};

/* 中文标签映射 */
var HOTSPOT_STATUS_LABEL = { active: '待裁决', engaged: '已跟进', passed: '已放弃', discarded: '风险否决' };
var HOTSPOT_STATUS_BADGE = { active: 'yellow', engaged: 'green', passed: 'weak', discarded: 'red' };
var PLATFORM_LABEL = { baidu: '百度', douyin: '抖音', weibo: '微博', zhihu: '知乎', xiaohongshu: '小红书' };

/* HTML 转义(防注入, 统一出口) */
function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

/* ========= API ========= */
/* 鉴权头: compat 模式 X-Role: admin; 已登录时叠加 Authorization Bearer */
function apiHeaders() {
    var h = { 'X-Role': 'admin', 'Content-Type': 'application/json' };
    var auth = (typeof Auth !== 'undefined') ? Auth.apiHeaders() : null;
    return auth ? Object.assign(h, auth) : h;
}

/* 统一请求: 解包 {"success": true, "data": ...}; 失败抛出含 detail 的错误 */
async function fetchJson(url, options) {
    var res = await fetch(url, Object.assign({ headers: apiHeaders() }, options || {}));
    if (res.status === 401) {
        if (typeof Auth !== 'undefined') { Auth.requireLogin(); }
        throw new Error('登录已失效, 正在跳转登录页…');
    }
    var body = null;
    try { body = await res.json(); } catch (e) { /* 非 JSON 错误体 */ }
    if (!res.ok) {
        var msg = body && (body.detail || body.error || body.message)
            ? (body.detail || body.error || body.message) : ('HTTP ' + res.status);
        throw new Error(msg);
    }
    if (!body || body.success !== true) {
        throw new Error((body && (body.detail || body.error)) || '响应格式异常(缺少 success/data 包裹)');
    }
    return body.data;
}

/* ========= 数据加载 ========= */
/* 全量刷新: 统计 + 热点表格 + 待裁决列表 并行 */
async function refreshData() {
    var btn = document.getElementById('btnRefresh');
    btn.disabled = true; btn.textContent = '刷新中…';
    try {
        await Promise.all([loadStats(), loadHotspotTable(), loadDecisions()]);
        hideBanner('errorBanner');
    } catch (err) {
        showBanner('errorBanner', '数据加载失败：' + err.message + '（请确认后端已启动且地址正确）');
    } finally {
        btn.disabled = false; btn.textContent = '刷新';
        document.getElementById('lastUpdate').textContent = '最后刷新 ' + new Date().toLocaleTimeString('zh-CN');
    }
}

/* 顶部统计: 全量热点按状态计数 */
async function loadStats() {
    var list = await fetchJson(state.apiBase + '/api/promo/radar/hotspots');
    var count = function (st) {
        return list.filter(function (h) { return h.status === st; }).length;
    };
    document.getElementById('statTotal').textContent = list.length;
    document.getElementById('statEngaged').textContent = count('engaged');
    document.getElementById('statPassed').textContent = count('passed');
    document.getElementById('statDiscarded').textContent = count('discarded');
}

/* 热点表格: 状态/平台筛选(服务端参数) */
async function loadHotspotTable() {
    var params = [];
    var status = document.getElementById('statusFilter').value;
    var platform = document.getElementById('platformFilter').value;
    if (status) { params.push('status=' + encodeURIComponent(status)); }
    if (platform) { params.push('platform=' + encodeURIComponent(platform)); }
    var url = state.apiBase + '/api/promo/radar/hotspots'
        + (params.length ? '?' + params.join('&') : '');
    var list = await fetchJson(url);
    var tbody = document.getElementById('hotspotBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="dash-empty">暂无热点数据(点击「立即扫描」抓取五平台热榜)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(renderHotspotRow).join('');
}

/* 待人工裁决列表(pendingOnly=true) */
async function loadDecisions() {
    var list = await fetchJson(state.apiBase + '/api/promo/decisions?pendingOnly=true');
    document.getElementById('statPending').textContent = list.length;
    document.getElementById('decisionCount').textContent = list.length
        ? '共 ' + list.length + ' 条待裁决' : '当前无待裁决热点';
    var tbody = document.getElementById('decisionBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="dash-empty">暂无待人工裁决的热点(扫描后评分 50-70 区间的热点会进入此队列)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (d) {
        return '<tr>' +
            '<td><b>' + esc(d.hotspotTitle) + '</b><br><span class="cap-id">#' + d.hotspotId + '</span></td>' +
            '<td>' + esc(PLATFORM_LABEL[d.platform] || d.platform || '--') + '</td>' +
            '<td class="num"><b>' + (d.score != null ? d.score : '--') + '</b></td>' +
            '<td class="reason-cell">' + esc(d.reason || '') + '</td>' +
            '<td>' +
                '<button class="btn-mini" onclick="decideHotspot(' + d.hotspotId + ', true)">跟进</button> ' +
                '<button class="btn-mini danger" onclick="decideHotspot(' + d.hotspotId + ', false)">放弃</button>' +
            '</td>' +
        '</tr>';
    }).join('');
}

/* ========= 渲染 ========= */
/* 热点行: 标题/平台/热度(万)/评分(进度条)/品牌命中词/状态徽章/风险标记 */
function renderHotspotRow(h) {
    var status = h.status || 'active';
    var comp = h.scoreComponents || {};
    var tip = '分项: 热度' + comp.heat + ' · 速度' + comp.velocity
        + ' · 相关度' + comp.brandRelevance + ' · 持续' + comp.persistence;
    var score = h.score || 0;
    var barCls = score >= 70 ? 'hi' : (score >= 50 ? 'mid' : 'lo');
    return '<tr>' +
        '<td style="white-space:normal;max-width:280px;" title="' + esc(h.summary || '') + '">' +
            '<b>' + esc(h.title) + '</b><br><span class="cap-id">#' + h.hotspotId + ' · ' + esc(h.summary || '') + '</span></td>' +
        '<td>' + esc(PLATFORM_LABEL[h.platform] || h.platform || '--') + '</td>' +
        '<td class="num">' + (h.heat != null ? h.heat : '--') + '</td>' +
        '<td><div class="score-cell" title="' + esc(tip) + '">' +
            '<div class="score-track"><div class="score-bar ' + barCls + '" style="width:' + Math.max(2, Math.min(100, score)) + '%"></div></div>' +
            '<span class="score-num">' + score + '</span></div></td>' +
        '<td>' + badgeList(h.brandHits, 'weak') + '</td>' +
        '<td><span class="badge ' + (HOTSPOT_STATUS_BADGE[status] || 'weak') + '">' +
            esc(HOTSPOT_STATUS_LABEL[status] || status) + '</span></td>' +
        '<td>' + badgeList(h.riskFlags, 'red') + '</td>' +
    '</tr>';
}

/* 词组徽章列表(品牌命中词/风险词), 空时显示 — */
function badgeList(arr, cls) {
    if (!arr || !arr.length) { return '—'; }
    return arr.map(function (w) {
        return '<span class="badge ' + cls + '">' + esc(w) + '</span>';
    }).join(' ');
}

/* 扫描结果摘要(按钮旁常显) */
function renderScanResult() {
    var r = state.scanResult;
    if (!r) { return; }
    document.getElementById('scanResult').textContent =
        '上次扫描：扫描 ' + r.scanned + ' · 新增 ' + r.new + ' · 否决 ' + r.discarded + ' · 跳过 ' + r.skipped;
}

/* ========= 管理操作 ========= */
/* 立即扫描: POST /api/promo/radar/scan, 展示 new/discarded/skipped */
async function scanNow() {
    var btn = document.getElementById('btnScan');
    btn.disabled = true; btn.textContent = '扫描中…';
    try {
        var r = await fetchJson(state.apiBase + '/api/promo/radar/scan',
            { method: 'POST', body: JSON.stringify({}) });
        state.scanResult = r;
        renderScanResult();
        showBanner('infoBanner', '扫描完成：共扫描 ' + r.scanned + ' 条 · 新增 ' + r.new
            + ' · 风险否决 ' + r.discarded + ' · 去重跳过 ' + r.skipped);
        await refreshData();
    } catch (err) {
        showBanner('errorBanner', '扫描失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '立即扫描';
    }
}

/* 人工裁决: POST /api/promo/decisions/{hotspotId}/decide, body {"engage": true/false} */
async function decideHotspot(hotspotId, engage) {
    if (!engage && !confirm('确认放弃热点 #' + hotspotId + '? 放弃后仅留痕, 不再跟进。')) { return; }
    try {
        await fetchJson(
            state.apiBase + '/api/promo/decisions/' + encodeURIComponent(hotspotId) + '/decide',
            { method: 'POST', body: JSON.stringify({ engage: engage }) });
        showBanner('infoBanner', '热点 #' + hotspotId + ' 已'
            + (engage ? '跟进, 可前往「内容工厂与发布中心」生成内容' : '放弃(留痕)'));
        refreshData();
    } catch (err) {
        showBanner('errorBanner', '裁决失败：' + err.message);
    }
}

/* ========= 横幅/工具栏 ========= */
function showBanner(id, msg) {
    var el = document.getElementById(id);
    el.textContent = msg;
    el.style.display = 'block';
}
function hideBanner(id) {
    document.getElementById(id).style.display = 'none';
}

function saveApiBase() {
    var v = document.getElementById('apiBase').value.trim().replace(/\/+$/, '');
    if (!v) { return; }
    state.apiBase = v;
    localStorage.setItem(API_BASE_KEY, v);
    showBanner('infoBanner', '后端地址已保存: ' + v);
    refreshData();
}

function toggleAutoRefresh() {
    if (state.autoTimer) { clearInterval(state.autoTimer); state.autoTimer = null; }
    if (document.getElementById('autoRefresh').checked) {
        state.autoTimer = setInterval(refreshData, state.AUTO_MS);
    }
}

/* ========= 初始化 ========= */
(function init() {
    if (typeof Auth !== 'undefined') {
        Auth.mountBadge(document.getElementById('authBadge'));
    }
    document.getElementById('apiBase').value = state.apiBase;
    toggleAutoRefresh();
    refreshData();
})();
