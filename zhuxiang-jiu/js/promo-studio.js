/**
 * 36号·AI智能推广模块 v1.0 · 内容工厂与发布中心脚本
 *
 * 页面: ai-promo-studio.html
 * 职责:
 *   - 生成表单: 已跟进热点下拉(GET /api/promo/radar/hotspots?status=engaged)
 *     + 平台多选(douyin/xiaohongshu/wechat_moments)
 *     → POST /api/promo/contents/generate {"hotspotId": N, "platforms": [...]}
 *   - 内容列表(GET /api/promo/contents, 状态筛选): ID/平台/标题/状态徽章/合规分/
 *     agentTrace 四步走轨(glm-5.3/glm-4-flash/rule)/短码; 点击行展开正文
 *   - 人工审核: POST /api/promo/contents/{id}/review {"approved": true/false}
 *   - 入发布队列: POST /api/promo/contents/{id}/publish {"publishAt": ""}(空=黄金时段自动)
 *   - 发布队列(GET /api/promo/publish/queue)与到期处理(POST /api/promo/publish/process)
 *   - 报表: GET /api/promo/report/overview + GET /api/promo/report/platform
 *   - 30 秒自动刷新
 *
 * 鉴权: X-Role: admin 兼容头(与 ai-hub-dashboard 一致; strict 模式叠加 JWT)
 * 响应约定: 统一解包 {"success": true, "data": ...}; 失败时横幅展示后端 detail
 */
'use strict';

/* ========= 全局状态 ========= */
var API_BASE_KEY = 'promoDash.apiBase';   // 与热点雷达页共用后端地址
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    contents: [],          // 当前筛选下的内容列表
    expanded: {},          // 展开行: {contentId: true}
    engagedSig: null,      // 已跟进热点下拉选项签名(无变化不重绘, 保持下拉展开态; null=首次)
    autoTimer: null,       // 自动刷新句柄
    AUTO_MS: 30000,
};

/* 中文标签映射 */
var CONTENT_STATUS_LABEL = { pending: '待审核', approved: '已通过', rejected: '已拒绝', queued: '已入队', published: '已发布' };
var CONTENT_STATUS_BADGE = { pending: 'yellow', approved: 'green', rejected: 'red', queued: 'blue', published: 'gold' };
var PLATFORM_LABEL = { baidu: '百度', douyin: '抖音', weibo: '微博', zhihu: '知乎', xiaohongshu: '小红书', wechat_moments: '微信朋友圈' };
/* agentTrace 四步键 → 中文名 */
var TRACE_STEPS = [
    ['step1Analysis', '①分析'],
    ['step2Audience', '②受众'],
    ['step3Generate', '③生成'],
    ['step4SelfCheck', '④自查'],
];

/* HTML 转义(防注入, 统一出口) */
function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

/* ISO 时间显示(UTC, 截断毫秒与时区后缀) */
function fmtTime(iso) {
    if (!iso) { return '--'; }
    return String(iso).replace('T', ' ').replace(/\.\d+/, '').replace(/(\+00:00|Z)$/, '') + ' UTC';
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
/* 全量刷新: 热点下拉 + 内容列表 + 发布队列 + 报表 并行 */
async function refreshData() {
    var btn = document.getElementById('btnRefresh');
    btn.disabled = true; btn.textContent = '刷新中…';
    try {
        await Promise.all([loadEngagedHotspots(), loadContents(), loadQueue(), loadReports()]);
        hideBanner('errorBanner');
    } catch (err) {
        showBanner('errorBanner', '数据加载失败：' + err.message + '（请确认后端已启动且地址正确）');
    } finally {
        btn.disabled = false; btn.textContent = '刷新';
        document.getElementById('lastUpdate').textContent = '最后刷新 ' + new Date().toLocaleTimeString('zh-CN');
    }
}

/* 已跟进热点下拉(生成表单数据源) */
async function loadEngagedHotspots() {
    var list = await fetchJson(state.apiBase + '/api/promo/radar/hotspots?status=engaged');
    var sel = document.getElementById('hotspotSelect');
    var sig = list.map(function (h) { return h.hotspotId; }).join(',');
    if (state.engagedSig !== null && sig === state.engagedSig) { return; }   // 无变化不重绘(避免打断下拉选择)
    state.engagedSig = sig;
    var prev = sel.value;
    if (!list.length) {
        sel.innerHTML = '<option value="">暂无已跟进热点(请先在热点雷达页跟进)</option>';
        return;
    }
    sel.innerHTML = list.map(function (h) {
        return '<option value="' + h.hotspotId + '">#' + h.hotspotId + ' '
            + esc(h.title) + '(评分 ' + h.score + ' · '
            + esc(PLATFORM_LABEL[h.platform] || h.platform) + ')</option>';
    }).join('');
    /* 尽量保留刷新前的选择 */
    if (prev && list.some(function (h) { return String(h.hotspotId) === prev; })) {
        sel.value = prev;
    }
}

/* 内容列表(状态筛选, 服务端参数) */
async function loadContents() {
    var status = document.getElementById('contentStatusFilter').value;
    var url = state.apiBase + '/api/promo/contents'
        + (status ? '?status=' + encodeURIComponent(status) : '');
    state.contents = await fetchJson(url);
    renderContents();
}

/* 发布队列 */
async function loadQueue() {
    var list = await fetchJson(state.apiBase + '/api/promo/publish/queue');
    var tbody = document.getElementById('queueBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="dash-empty">队列为空(审核通过的内容可入队)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (q) {
        return '<tr>' +
            '<td class="cap-id">#' + q.contentId + '</td>' +
            '<td>' + esc(PLATFORM_LABEL[q.platform] || q.platform || '--') + '</td>' +
            '<td style="white-space:nowrap;">' + esc(fmtTime(q.scheduledAt)) + '</td>' +
            '<td>' + (q.inGoldenWindow
                ? '<span class="badge green">窗口内</span>'
                : '<span class="badge weak">未到窗口</span>') + '</td>' +
        '</tr>';
    }).join('');
}

/* 报表: 全景 + 平台维度 */
async function loadReports() {
    await Promise.all([loadOverviewReport(), loadPlatformReport()]);
}

/* ========= 渲染 ========= */
function renderContents() {
    var tbody = document.getElementById('contentBody');
    var list = state.contents;
    document.getElementById('contentCount').textContent = '共 ' + list.length + ' 条';
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="dash-empty">暂无内容(在上方选择已跟进热点并生成)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (c) {
        return renderContentRow(c) + (state.expanded[c.contentId] ? renderDetailRow(c) : '');
    }).join('');
}

/* 内容行: ID/平台/标题/状态徽章/合规分/走轨/短码/操作 */
function renderContentRow(c) {
    var status = c.status || 'pending';
    var score = c.complianceScore != null ? c.complianceScore : 0;
    var scoreCls = score >= 80 ? 'green' : (score >= 60 ? 'yellow' : 'red');
    return '<tr class="row-toggle" data-cid="' + c.contentId + '">' +
        '<td class="cap-id">#' + c.contentId + '</td>' +
        '<td>' + esc(PLATFORM_LABEL[c.platform] || c.platform || '--') + '</td>' +
        '<td style="white-space:normal;max-width:260px;">' +
            '<span class="exp-arrow">' + (state.expanded[c.contentId] ? '▾' : '▸') + '</span>' +
            esc(c.title || '(无标题)') + '</td>' +
        '<td><span class="badge ' + (CONTENT_STATUS_BADGE[status] || 'weak') + '">' +
            esc(CONTENT_STATUS_LABEL[status] || status) + '</span></td>' +
        '<td><span class="badge ' + scoreCls + '">' + score + '</span></td>' +
        '<td>' + traceHtml(c.agentTrace) + '</td>' +
        '<td class="cap-id">' + esc(c.shortCode || '—') + '</td>' +
        '<td>' + actionHtml(c) + '</td>' +
    '</tr>';
}

/* 四步走轨徽章: ①分析/②受众/③生成/④自查 → glm-5.3/glm-4-flash/rule */
function traceHtml(trace) {
    trace = trace || {};
    return '<div class="trace-chips">' + TRACE_STEPS.map(function (s) {
        var track = trace[s[0]] || '--';
        var cls = track === 'glm-5.3' ? 'green'
            : (track === 'glm-4-flash' ? 'yellow' : 'weak');
        return '<span class="trace-chip ' + cls + '" title="' + esc(s[0]) + ': ' + esc(track) + '">'
            + s[1] + ' ' + esc(track) + '</span>';
    }).join('') + '</div>';
}

/* 操作列: 待审核→通过/拒绝; 已通过→入发布队列 */
function actionHtml(c) {
    if (c.status === 'pending') {
        return '<button class="btn-mini" onclick="reviewContent(' + c.contentId + ', true)">通过</button> ' +
               '<button class="btn-mini danger" onclick="reviewContent(' + c.contentId + ', false)">拒绝</button>';
    }
    if (c.status === 'approved') {
        return '<button class="btn-mini" onclick="publishContent(' + c.contentId + ')">入发布队列</button>';
    }
    return '—';
}

/* 展开行: 正文 body + 元信息(标签/CTA/违规/自查/回执等) */
function renderDetailRow(c) {
    var selfCheck = c.selfCheck || {};
    var receipt = c.receipt || {};
    var meta = [];
    meta.push(['热点ID', c.hotspotId != null ? '#' + c.hotspotId : '--']);
    meta.push(['内容组', c.contentGroupId != null ? '#' + c.contentGroupId : '--']);
    if (c.hashtags) { meta.push(['话题标签', c.hashtags]); }
    if (c.cta) { meta.push(['行动号召', c.cta]); }
    if (c.coverHint) { meta.push(['封面建议', c.coverHint]); }
    if (c.complianceViolations && c.complianceViolations.length) {
        meta.push(['违规项', c.complianceViolations.join('、')]);
    }
    meta.push(['自查',
        (selfCheck.disclaimerOk ? '警示语✓' : '警示语✗')
        + ' ' + (selfCheck.ageTipOk ? '年龄提示✓' : '年龄提示✗')
        + (selfCheck.notes ? ' · ' + selfCheck.notes : '')]);
    if (c.scheduledAt) { meta.push(['计划发布', fmtTime(c.scheduledAt)]); }
    if (c.publishedAt) { meta.push(['实际发布', fmtTime(c.publishedAt)]); }
    if (receipt && receipt.mode) {
        meta.push(['发布回执', (receipt.mode === 'mock' ? '模拟轨' : receipt.mode)
            + (receipt.exposureEstimate != null ? ' · 预估曝光 ' + receipt.exposureEstimate : '')]);
    }
    meta.push(['创建时间', fmtTime(c.createdAt)]);
    return '<tr class="detail-row"><td colspan="8"><div class="detail-box">' +
        '<div class="detail-body">' + esc(c.body || '(无正文)') + '</div>' +
        '<div class="detail-meta">' + meta.map(function (m) {
            return '<span>' + esc(m[0]) + ': <b>' + esc(m[1]) + '</b></span>';
        }).join('') + '</div>' +
    '</div></td></tr>';
}

/* 全景报表卡: 热点/内容/归因/dailyCap */
async function loadOverviewReport() {
    var d = await fetchJson(state.apiBase + '/api/promo/report/overview');
    var h = d.hotspots || {};
    var c = d.contents || {};
    var a = d.attribution || {};
    var cap = d.dailyCap || {};
    document.getElementById('ovDailyCap').textContent =
        '单日发布上限 ' + (cap.used != null ? cap.used : '--') + ' / ' + (cap.limit != null ? cap.limit : '--');
    var cell = function (v, label) {
        return '<div class="ov-cell"><b>' + (v != null ? v : '--') + '</b><span>' + label + '</span></div>';
    };
    document.getElementById('ovWrap').innerHTML =
        '<div class="ov-group"><div class="ov-gtitle">热点</div><div class="ov-cells">' +
            cell(h.total, '总数') + cell(h.engaged, '已跟进') + cell(h.passed, '已放弃') + cell(h.pendingManual, '待裁决') +
        '</div></div>' +
        '<div class="ov-group"><div class="ov-gtitle">内容</div><div class="ov-cells">' +
            cell(c.total, '总数') + cell(c.pending, '待审核') + cell(c.published, '已发布') + cell(c.rejected, '已拒绝') +
        '</div></div>' +
        '<div class="ov-group"><div class="ov-gtitle">归因(attract 短码)</div><div class="ov-cells">' +
            cell(a.clicks, '点击') + cell(a.registered, '注册') + cell(a.ordered, '下单')
            + cell('¥' + (a.gmv != null ? a.gmv : '--'), 'GMV') +
        '</div></div>';
}

/* 平台维度报表 */
async function loadPlatformReport() {
    var rows = await fetchJson(state.apiBase + '/api/promo/report/platform');
    var tbody = document.getElementById('platformReportBody');
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="dash-empty">暂无平台报表数据</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(function (r) {
        return '<tr>' +
            '<td><b>' + esc(PLATFORM_LABEL[r.platform] || r.platform) + '</b></td>' +
            '<td class="num">' + (r.contents != null ? r.contents : '--') + '</td>' +
            '<td class="num">' + (r.published != null ? r.published : '--') + '</td>' +
            '<td class="num">' + (r.clicks != null ? r.clicks : '--') + '</td>' +
            '<td class="num">' + (r.registered != null ? r.registered : '--') + '</td>' +
            '<td class="num">' + (r.ordered != null ? r.ordered : '--') + '</td>' +
            '<td class="num">' + (r.gmv != null ? Number(r.gmv).toFixed(2) : '--') + '</td>' +
            '<td class="num">' + (r.gmvPerClick != null ? Number(r.gmvPerClick).toFixed(2) : '--') + '</td>' +
        '</tr>';
    }).join('');
}

/* ========= 管理操作 ========= */
/* 生成内容: engaged 热点 + 平台多选 → 一源多态 */
async function generateContents() {
    var hotspotId = Number(document.getElementById('hotspotSelect').value);
    if (!hotspotId) {
        showBanner('errorBanner', '请先选择一个已跟进热点(雷达页裁决「跟进」后出现在此处)');
        return;
    }
    var platforms = Array.prototype.slice.call(
        document.querySelectorAll('#platformChecks input[type="checkbox"]:checked')
    ).map(function (cb) { return cb.value; });
    if (!platforms.length) {
        showBanner('errorBanner', '请至少勾选一个发布平台(抖音/小红书/微信朋友圈)');
        return;
    }
    var btn = document.getElementById('btnGenerate');
    btn.disabled = true; btn.textContent = '生成中(Agent 四步链)…';
    try {
        var list = await fetchJson(state.apiBase + '/api/promo/contents/generate', {
            method: 'POST',
            body: JSON.stringify({ hotspotId: hotspotId, platforms: platforms }),
        });
        var pending = list.filter(function (c) { return c.status === 'pending'; }).length;
        showBanner('infoBanner', '已生成 ' + list.length + ' 条内容(内容组 #'
            + ((list[0] && list[0].contentGroupId) || '--') + '), '
            + pending + ' 条待人工审核, ' + (list.length - pending) + ' 条被合规闸门直接拒绝');
        await refreshData();
    } catch (err) {
        showBanner('errorBanner', '生成失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '生成内容';
    }
}

/* 人工审核(三审): POST /api/promo/contents/{id}/review {"approved": true/false} */
async function reviewContent(contentId, approved) {
    if (!approved && !confirm('确认拒绝内容 #' + contentId + '? 拒绝后不可再审核。')) { return; }
    try {
        await fetchJson(
            state.apiBase + '/api/promo/contents/' + encodeURIComponent(contentId) + '/review',
            { method: 'POST', body: JSON.stringify({ approved: approved }) });
        showBanner('infoBanner', '内容 #' + contentId
            + (approved ? ' 已通过审核, 可入发布队列' : ' 已拒绝'));
        refreshData();
    } catch (err) {
        showBanner('errorBanner', '审核失败：' + err.message);
    }
}

/* 入发布队列: publishAt 为空 = 按平台黄金时段自动调度 */
async function publishContent(contentId) {
    try {
        var c = await fetchJson(
            state.apiBase + '/api/promo/contents/' + encodeURIComponent(contentId) + '/publish',
            { method: 'POST', body: JSON.stringify({ publishAt: '' }) });
        showBanner('infoBanner', '内容 #' + contentId + ' 已入发布队列, 计划 '
            + fmtTime(c && c.scheduledAt));
        refreshData();
    } catch (err) {
        showBanner('errorBanner', '入队失败：' + err.message);
    }
}

/* 立即处理到期发布(调度器亦可周期触发) */
async function processQueue() {
    var btn = document.getElementById('btnProcess');
    btn.disabled = true; btn.textContent = '处理中…';
    try {
        var list = await fetchJson(state.apiBase + '/api/promo/publish/process',
            { method: 'POST', body: JSON.stringify({}) });
        showBanner('infoBanner', list.length
            ? '本轮发布 ' + list.length + ' 条到期内容(模拟轨回执已生成)'
            : '当前没有到期内容需要发布');
        await refreshData();
    } catch (err) {
        showBanner('errorBanner', '处理发布失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '立即处理到期发布';
    }
}

/* ========= 交互: 行展开/收起(事件委托, 按钮点击不触发) ========= */
document.getElementById('contentBody').addEventListener('click', function (evt) {
    if (evt.target.closest('button')) { return; }
    var tr = evt.target.closest('tr[data-cid]');
    if (!tr) { return; }
    var cid = Number(tr.getAttribute('data-cid'));
    state.expanded[cid] = !state.expanded[cid];
    renderContents();
});

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
