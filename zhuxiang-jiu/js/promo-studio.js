/**
 * 36号·AI智能推广模块 v1.1 · 内容工厂与发布中心脚本
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
 * P1: 受众画像与权威信源配置
 *   - 受众画像库: GET /api/promo/audience/profiles 表格展示(场景/产品调性顿号连接);
 *     行内「编辑」弹窗(audience/tone/format 文本 + scenes/productTones 逗号分隔输入)
 *     → PUT /api/promo/audience/profiles/{platform}(仅提交有值字段, 留空保持原值)
 *   - 三维匹配测试: 平台下拉 + 角度输入 + 产品调性下拉
 *     → POST /api/promo/audience/match, 展示匹配分/matched 徽章/
 *     三分项(angleAffinity/toneAffinity/sceneAffinity)
 *   - 站内画像回传: GET /api/promo/audience/onsite?platform=xx
 *     展示会员总数/高价值占比/等级分布/校准建议
 *   - 权威信源库: GET /api/promo/authority/sources?keyword= 关键词过滤
 *     (内容截断 80 字, title 悬浮全文); 新增信源 POST /api/promo/authority/sources;
 *     RAG 检索测试 GET /api/promo/authority/search?query=xx&topK=3
 *     展示标题 + 相似度百分比 + content 摘要
 *   - 30s 自动刷新仅重渲染表格数据, 不触碰任何表单输入值
 *
 * P2: 发布通道与百度SEO看板
 *   - 发布通道状态: GET /api/promo/channels/status 表格
 *     (平台中文/总模式 mock灰·real蓝徽章/keyConfigured 已配置绿·未配置灰/
 *     effectiveMode real绿·mock_fallback黄·mock灰/endpoint 截断悬浮全文)
 *   - 百度SEO推送: 「立即推送」POST /api/promo/seo/push(常规当日去重);
 *     「强制重推」confirm 确认后 POST /api/promo/seo/push?force=true 全量重推
 *   - 推送记录: GET /api/promo/seo/pushes 表格
 *     (pushId/createdAt 本地时间/mode 徽章/submitted/skipped/success/failed/
 *     status ok绿·failed红/error 截断悬浮/urls 数量点击展开明细·悬浮预览前 3 条)
 *   - 推送按钮操作后仅局部刷新推送记录表; 自动刷新保留展开态, 不清空任何状态
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
    audienceProfiles: [],  // P1: 平台受众画像列表
    profilesSig: null,     // P1: 画像平台集合签名(无变化不重绘平台下拉, 保持选择; null=首次)
    channels: [],          // P2: 发布通道状态列表
    seoPushes: [],         // P2: 百度SEO推送记录列表
    seoExpanded: {},       // P2: 推送记录 URL 明细展开: {pushId: true}
};

/* 中文标签映射 */
var CONTENT_STATUS_LABEL = { pending: '待审核', approved: '已通过', rejected: '已拒绝', queued: '已入队', published: '已发布' };
var CONTENT_STATUS_BADGE = { pending: 'yellow', approved: 'green', rejected: 'red', queued: 'blue', published: 'gold' };
var PLATFORM_LABEL = { baidu: '百度', douyin: '抖音', weibo: '微博', zhihu: '知乎', xiaohongshu: '小红书', wechat_moments: '微信朋友圈', wechat_channels: '视频号' };
/* P1: 信源类别 → 中文徽章(standard=国标/association=协会/media=媒体) */
var AUTHORITY_CATEGORY_LABEL = { standard: '国标', association: '协会', media: '媒体' };
var AUTHORITY_CATEGORY_BADGE = { standard: 'green', association: 'blue', media: 'gold' };
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

/* P1: 逗号/中文逗号/顿号分隔文本 → 去空数组(画像场景与产品调性输入解析) */
function splitList(text) {
    return String(text || '').split(/[,，、]/)
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s; });
}

/* P1: 文本截断(超长加省略号, 配合 title 属性悬浮全文) */
function truncate(text, max) {
    var s = String(text || '');
    return s.length > max ? s.slice(0, max) + '…' : s;
}

/* P2: ISO 时间 → 本地时间显示(百度SEO推送记录时间列; 不同于 fmtTime 的 UTC 直显) */
function fmtLocalTime(iso) {
    if (!iso) { return '--'; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return String(iso); }
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
        + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
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
/* 全量刷新: 热点下拉 + 内容列表 + 发布队列 + 报表 + P1画像/信源 + P2通道/SEO推送 并行(只重渲染表格数据, 不触碰表单输入) */
async function refreshData() {
    var btn = document.getElementById('btnRefresh');
    btn.disabled = true; btn.textContent = '刷新中…';
    try {
        await Promise.all([loadEngagedHotspots(), loadContents(), loadQueue(), loadReports(),
            loadAudienceProfiles(), loadAuthoritySources(), loadChannels(), loadSeoPushes()]);
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

/* ============================================================ */
/* P1: 受众画像库                                                */
/* ============================================================ */

/* 平台画像列表(首次访问后端自动初始化种子) */
async function loadAudienceProfiles() {
    state.audienceProfiles = await fetchJson(state.apiBase + '/api/promo/audience/profiles');
    renderAudienceProfiles();
}

/* 画像表格: 平台/人群/调性/格式/场景(顿号)/产品调性(顿号)/编辑 */
function renderAudienceProfiles() {
    var list = state.audienceProfiles;
    document.getElementById('audienceCount').textContent = '共 ' + list.length + ' 个平台';
    var tbody = document.getElementById('audienceBody');
    tbody.innerHTML = list.length ? list.map(function (p) {
        var scenes = (p.scenes || []).join('、');
        var productTones = (p.productTones || []).join('、');
        return '<tr>' +
            '<td><b>' + esc(PLATFORM_LABEL[p.platform] || p.platform) + '</b></td>' +
            '<td style="white-space:normal;max-width:200px;" title="' + esc(p.audience) + '">' + esc(p.audience || '--') + '</td>' +
            '<td style="white-space:normal;max-width:170px;" title="' + esc(p.tone) + '">' + esc(p.tone || '--') + '</td>' +
            '<td style="white-space:normal;max-width:170px;" title="' + esc(p.format) + '">' + esc(p.format || '--') + '</td>' +
            '<td style="white-space:normal;max-width:210px;" title="' + esc(scenes) + '">' + esc(scenes || '--') + '</td>' +
            '<td style="white-space:normal;max-width:210px;" title="' + esc(productTones) + '">' + esc(productTones || '--') + '</td>' +
            '<td><button class="btn-mini" onclick="editAudienceProfile(\'' + esc(p.platform) + '\')">编辑</button></td>' +
        '</tr>';
    }).join('') : '<tr><td colspan="7" class="dash-empty">暂无画像数据</td></tr>';
    renderProfilePlatformSelects();
}

/* 平台下拉(三维匹配/站内回传共用): 平台集合签名无变化不重绘, 保持刷新前的选择 */
function renderProfilePlatformSelects() {
    var list = state.audienceProfiles;
    var sig = list.map(function (p) { return p.platform; }).join(',');
    if (state.profilesSig !== null && sig === state.profilesSig) { return; }
    state.profilesSig = sig;
    var options = '<option value="">选择平台</option>' + list.map(function (p) {
        return '<option value="' + esc(p.platform) + '">'
            + esc(PLATFORM_LABEL[p.platform] || p.platform) + '</option>';
    }).join('');
    ['matchPlatform', 'onsitePlatform'].forEach(function (id) {
        var sel = document.getElementById(id);
        var prev = sel.value;
        sel.innerHTML = options;
        if (prev && list.some(function (p) { return p.platform === prev; })) { sel.value = prev; }
    });
    syncOnsiteButton();
}

/* 站内画像回传按钮: 选平台后可用 */
function syncOnsiteButton() {
    document.getElementById('btnOnsite').disabled = !document.getElementById('onsitePlatform').value;
}

/* 打开画像编辑弹窗(预填当前画像值, 留空字段保存时不动) */
function editAudienceProfile(platform) {
    var profile = null;
    state.audienceProfiles.some(function (p) {
        if (p.platform === platform) { profile = p; return true; }
        return false;
    });
    if (!profile) { return; }
    document.getElementById('profileModalTitle').textContent =
        '编辑画像 · ' + (PLATFORM_LABEL[platform] || platform);
    document.getElementById('editProfilePlatform').value = platform;
    document.getElementById('editAudience').value = profile.audience || '';
    document.getElementById('editTone').value = profile.tone || '';
    document.getElementById('editFormat').value = profile.format || '';
    document.getElementById('editScenes').value = (profile.scenes || []).join(',');
    document.getElementById('editProductTones').value = (profile.productTones || []).join(',');
    hideBanner('profileModalError');
    document.getElementById('profileModal').classList.add('open');
}

/* 关闭画像编辑弹窗(取消/点击遮罩) */
function closeProfileModal() {
    document.getElementById('profileModal').classList.remove('open');
}

/* 保存画像: PUT 仅提交有值字段(留空字段保持原值) */
async function saveAudienceProfile() {
    var platform = document.getElementById('editProfilePlatform').value;
    var body = {};
    var audience = document.getElementById('editAudience').value.trim();
    var tone = document.getElementById('editTone').value.trim();
    var format = document.getElementById('editFormat').value.trim();
    var scenes = splitList(document.getElementById('editScenes').value);
    var productTones = splitList(document.getElementById('editProductTones').value);
    if (audience) { body.audience = audience; }
    if (tone) { body.tone = tone; }
    if (format) { body.format = format; }
    if (scenes.length) { body.scenes = scenes; }
    if (productTones.length) { body.productTones = productTones; }
    if (!Object.keys(body).length) {
        showBanner('profileModalError', '请至少填写一个字段(留空字段保持原值)');
        return;
    }
    var btn = document.getElementById('btnSaveProfile');
    btn.disabled = true; btn.textContent = '保存中…';
    try {
        await fetchJson(
            state.apiBase + '/api/promo/audience/profiles/' + encodeURIComponent(platform),
            { method: 'PUT', body: JSON.stringify(body) });
        closeProfileModal();
        showBanner('infoBanner', '画像已更新: ' + (PLATFORM_LABEL[platform] || platform)
            + ', 下一次生成内容即时生效');
        await loadAudienceProfiles();
    } catch (err) {
        showBanner('profileModalError', '保存失败：' + err.message);
        showBanner('errorBanner', '画像保存失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '保存';
    }
}

/* 三维匹配测试: 平台 × 内容角度 × 产品调性 → 匹配分/徽章/三分项 */
async function runAudienceMatch() {
    var platform = document.getElementById('matchPlatform').value;
    var angle = document.getElementById('matchAngle').value.trim();
    var productTone = document.getElementById('matchTone').value;
    if (!platform) { showBanner('errorBanner', '请先选择测试平台'); return; }
    if (!angle) { showBanner('errorBanner', '请输入内容角度(如 节日送礼)'); return; }
    var btn = document.getElementById('btnMatch');
    btn.disabled = true; btn.textContent = '匹配中…';
    try {
        var r = await fetchJson(state.apiBase + '/api/promo/audience/match', {
            method: 'POST',
            body: JSON.stringify({ platform: platform, angle: angle, productTone: productTone }),
        });
        renderMatchResult(r);
    } catch (err) {
        showBanner('errorBanner', '匹配测试失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '匹配测试';
    }
}

/* 匹配结果: 匹配分(0-1)+百分比+matched 徽章 + 三分项格子 */
function renderMatchResult(r) {
    var pct = function (v) { return v != null ? (v * 100).toFixed(1) + '%' : '--'; };
    var comp = r.components || {};
    var cell = function (v, label) {
        return '<div class="ov-cell"><b>' + pct(v) + '</b><span>' + label + '</span></div>';
    };
    document.getElementById('matchResult').innerHTML =
        '<div class="match-summary">匹配分 <b>' + (r.score != null ? r.score : '--') + '</b>'
        + '(' + pct(r.score) + ') '
        + (r.matched
            ? '<span class="badge green">匹配</span>'
            : '<span class="badge red">不匹配</span>') + '</div>'
        + '<div class="ov-cells" style="padding-top:8px;">'
        + cell(comp.angleAffinity, '角度亲和(50%)')
        + cell(comp.toneAffinity, '调性亲和(30%)')
        + cell(comp.sceneAffinity, '场景命中(20%)')
        + '</div>';
}

/* 站内画像回传: 会员等级分布聚合 + 校准建议(高价值 = Lv3+) */
async function loadOnsiteFeedback() {
    var platform = document.getElementById('onsitePlatform').value;
    if (!platform) { showBanner('errorBanner', '请先选择回传平台'); return; }
    var btn = document.getElementById('btnOnsite');
    btn.disabled = true; btn.textContent = '回传中…';
    try {
        var d = await fetchJson(state.apiBase + '/api/promo/audience/onsite?platform='
            + encodeURIComponent(platform));
        renderOnsiteResult(d);
    } catch (err) {
        showBanner('errorBanner', '站内画像回传失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '站内画像回传';
        syncOnsiteButton();
    }
}

/* 回传结果: 会员总数/高价值占比/等级分布/校准建议 */
function renderOnsiteResult(d) {
    var levels = d.levelDistribution || {};
    var levelText = Object.keys(levels).sort(function (a, b) { return Number(a) - Number(b); })
        .map(function (lv) { return 'Lv' + lv + ' ' + levels[lv] + '人'; })
        .join('、') || '--';
    var cell = function (v, label) {
        return '<div class="ov-cell"><b>' + v + '</b><span>' + label + '</span></div>';
    };
    document.getElementById('onsiteResult').innerHTML =
        '<div class="ov-cells" style="padding-top:2px;">'
        + cell(d.onsiteMembers != null ? d.onsiteMembers : '--', '会员总数')
        + cell(d.highValueRatio != null ? (d.highValueRatio * 100).toFixed(1) + '%' : '--',
            '高价值占比(Lv3+)')
        + '</div>'
        + '<div class="tool-lines">等级分布: <b>' + esc(levelText) + '</b></div>'
        + '<div class="tool-lines">校准建议: <b>' + esc(d.calibrationSuggestion || '--') + '</b></div>';
}

/* ============================================================ */
/* P1: 权威信源库                                                */
/* ============================================================ */

/* 信源列表(keyword 过滤, 自动刷新沿用当前关键词输入值) */
async function loadAuthoritySources() {
    var keyword = document.getElementById('sourceKeyword').value.trim();
    var url = state.apiBase + '/api/promo/authority/sources'
        + (keyword ? '?keyword=' + encodeURIComponent(keyword) : '');
    renderAuthoritySources(await fetchJson(url));
}

/* 信源表格: ID/标题/类别徽章/内容(截断80字悬浮全文)/允许用法 */
function renderAuthoritySources(list) {
    document.getElementById('sourceCount').textContent = '共 ' + list.length + ' 条';
    var tbody = document.getElementById('authorityBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="dash-empty">暂无信源(可调整关键词或新增信源)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (s) {
        var content = String(s.content || '');
        return '<tr>' +
            '<td class="cap-id">#' + s.sourceId + '</td>' +
            '<td style="white-space:normal;max-width:190px;" title="' + esc(s.title) + '">' + esc(s.title || '--') + '</td>' +
            '<td><span class="badge ' + (AUTHORITY_CATEGORY_BADGE[s.category] || 'weak') + '">'
                + esc(AUTHORITY_CATEGORY_LABEL[s.category] || s.category || '--') + '</span></td>' +
            '<td style="white-space:normal;max-width:330px;" title="' + esc(content) + '">'
                + esc(truncate(content, 80)) + '</td>' +
            '<td style="white-space:normal;max-width:190px;" title="' + esc(s.allowedUsage) + '">'
                + esc(s.allowedUsage || '--') + '</td>' +
        '</tr>';
    }).join('');
}

/* 新增信源(类别白名单 + 权威背书红线词由后端校验) */
async function addAuthoritySource() {
    var title = document.getElementById('srcTitle').value.trim();
    var category = document.getElementById('srcCategory').value;
    var content = document.getElementById('srcContent').value.trim();
    var allowedUsage = document.getElementById('srcAllowedUsage').value.trim();
    if (!title || !content) {
        showBanner('errorBanner', '信源标题与内容不能为空');
        return;
    }
    var btn = document.getElementById('btnAddSource');
    btn.disabled = true; btn.textContent = '入库中…';
    try {
        var s = await fetchJson(state.apiBase + '/api/promo/authority/sources', {
            method: 'POST',
            body: JSON.stringify({
                title: title, category: category,
                content: content, allowedUsage: allowedUsage,
            }),
        });
        showBanner('infoBanner', '信源已入库 #' + (s && s.sourceId != null ? s.sourceId : '--')
            + ': ' + title);
        /* 清空表单便于连续录入(类别选择保留) */
        document.getElementById('srcTitle').value = '';
        document.getElementById('srcContent').value = '';
        document.getElementById('srcAllowedUsage').value = '';
        await loadAuthoritySources();
    } catch (err) {
        showBanner('errorBanner', '新增信源失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '入库';
    }
}

/* RAG 检索测试: 与生成链引用池同一链路(2-gram 余弦 top-3) */
async function runAuthoritySearch() {
    var query = document.getElementById('ragQuery').value.trim();
    if (!query) { showBanner('errorBanner', '请输入检索查询(如 清香型白酒执行标准)'); return; }
    var btn = document.getElementById('btnRagSearch');
    btn.disabled = true; btn.textContent = '检索中…';
    try {
        var list = await fetchJson(state.apiBase + '/api/promo/authority/search?query='
            + encodeURIComponent(query) + '&topK=3');
        renderAuthoritySearch(list);
    } catch (err) {
        showBanner('errorBanner', 'RAG 检索失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = '检索 top3';
    }
}

/* 检索结果: 标题 + 相似度百分比 + 内容摘要(截断80字悬浮全文) */
function renderAuthoritySearch(list) {
    var box = document.getElementById('ragResult');
    if (!list.length) {
        box.innerHTML = '<div class="dash-empty" style="padding:18px 0;">无相似信源(相似度低于阈值)</div>';
        return;
    }
    box.innerHTML = list.map(function (s) {
        var content = String(s.content || '');
        return '<div class="rag-item">' +
            '<div class="rag-head">' +
                '<span class="cap-id">#' + s.sourceId + '</span>' +
                '<span class="badge ' + (AUTHORITY_CATEGORY_BADGE[s.category] || 'weak') + '">'
                    + esc(AUTHORITY_CATEGORY_LABEL[s.category] || s.category) + '</span>' +
                '<b>' + esc(s.title || '--') + '</b>' +
                '<span class="badge gold">相似度 '
                    + (s.similarity != null ? (s.similarity * 100).toFixed(1) + '%' : '--') + '</span>' +
            '</div>' +
            '<div class="rag-content" title="' + esc(content) + '">' + esc(truncate(content, 80)) + '</div>' +
        '</div>';
    }).join('');
}

/* ============================================================ */
/* P2: 发布通道与百度SEO推送                                      */
/* ============================================================ */

/* 发布通道状态表(总模式 + 各平台 Key 配置与实际生效模式) */
async function loadChannels() {
    state.channels = await fetchJson(state.apiBase + '/api/promo/channels/status');
    renderChannels();
}

/* 通道表格: 平台中文/总模式/Key 配置/生效模式/端点(截断悬浮全文) */
function renderChannels() {
    var list = state.channels;
    document.getElementById('channelCount').textContent = '共 ' + list.length + ' 个通道';
    var tbody = document.getElementById('channelBody');
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="dash-empty">暂无通道数据</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (ch) {
        var endpoint = String(ch.endpoint || '--');
        return '<tr>' +
            '<td><b>' + esc(PLATFORM_LABEL[ch.platform] || ch.platform || '--') + '</b></td>' +
            '<td>' + modeBadge(ch.mode) + '</td>' +
            '<td>' + (ch.keyConfigured
                ? '<span class="badge green">已配置</span>'
                : '<span class="badge weak">未配置</span>') + '</td>' +
            '<td>' + effectiveModeBadge(ch.effectiveMode) + '</td>' +
            '<td style="white-space:normal;max-width:320px;" title="' + esc(endpoint) + '">'
                + esc(truncate(endpoint, 46)) + '</td>' +
        '</tr>';
    }).join('');
}

/* 总模式/推送模式徽章: mock 灰 / real 蓝 */
function modeBadge(mode) {
    var m = String(mode || '');
    return '<span class="badge ' + (m === 'real' ? 'blue' : 'weak') + '">' + esc(m || '--') + '</span>';
}

/* 生效模式徽章: real 绿 / mock_fallback 黄 / mock 灰 */
function effectiveModeBadge(mode) {
    var m = String(mode || '');
    var cls = m === 'real' ? 'green' : (m === 'mock_fallback' ? 'yellow' : 'weak');
    return '<span class="badge ' + cls + '">' + esc(m || '--') + '</span>';
}

/* 百度SEO推送记录表 */
async function loadSeoPushes() {
    state.seoPushes = await fetchJson(state.apiBase + '/api/promo/seo/pushes');
    renderSeoPushes();
}

/* 推送记录表格: ID/时间(本地)/模式/提交/跳过/成功/失败/状态/错误(截断悬浮)/URL数(展开态由 seoExpanded 保持) */
function renderSeoPushes() {
    var tbody = document.getElementById('seoPushBody');
    var list = state.seoPushes;
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="dash-empty">暂无推送记录(点击「立即推送」提交站点 URL)</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (p) {
        return renderSeoPushRow(p) + (state.seoExpanded[p.pushId] ? renderSeoUrlRow(p) : '');
    }).join('');
}

/* 推送记录行 */
function renderSeoPushRow(p) {
    var urls = p.urls || [];
    /* 悬浮预览前 3 条 URL, 超出提示总数 */
    var hover = urls.slice(0, 3).join('\n') + (urls.length > 3 ? '\n…共 ' + urls.length + ' 条' : '');
    var error = String(p.error || '');
    var status = String(p.status || '');
    return '<tr data-pid="' + p.pushId + '">' +
        '<td class="cap-id">#' + p.pushId + '</td>' +
        '<td style="white-space:nowrap;">' + esc(fmtLocalTime(p.createdAt)) + '</td>' +
        '<td>' + modeBadge(p.mode) + '</td>' +
        '<td class="num">' + (p.submitted != null ? p.submitted : '--') + '</td>' +
        '<td class="num">' + (p.skipped != null ? p.skipped : '--') + '</td>' +
        '<td class="num">' + (p.success != null ? p.success : '--') + '</td>' +
        '<td class="num">' + (p.failed != null ? p.failed : '--') + '</td>' +
        '<td><span class="badge ' + (status === 'ok' ? 'green' : (status === 'failed' ? 'red' : 'weak')) + '">'
            + esc(status || '--') + '</span></td>' +
        '<td style="white-space:normal;max-width:170px;" title="' + esc(error) + '">'
            + (error ? esc(truncate(error, 24)) : '—') + '</td>' +
        '<td class="cap-id seo-urls" title="' + esc(hover) + '">'
            + '<span class="exp-arrow">' + (state.seoExpanded[p.pushId] ? '▾' : '▸') + '</span>'
            + urls.length + ' 条</td>' +
    '</tr>';
}

/* URL 明细展开行(复用内容列表 detail-row 结构) */
function renderSeoUrlRow(p) {
    var urls = p.urls || [];
    return '<tr class="detail-row"><td colspan="10"><div class="detail-box">' +
        '<div class="detail-body">' + (urls.length ? esc(urls.join('\n')) : '(无 URL)') + '</div>' +
    '</div></td></tr>';
}

/* 百度SEO推送: force=false 常规(当日未推送 URL); force=true 强制全量重推(confirm 二次确认) */
async function pushSeoUrls(force) {
    if (force && !confirm('确认强制重推? 将忽略当日去重规则, 全量重新提交所有 URL。')) { return; }
    var btn = document.getElementById(force ? 'btnSeoForce' : 'btnSeoPush');
    var label = force ? '强制重推' : '立即推送';
    btn.disabled = true; btn.textContent = '推送中…';
    try {
        var r = await fetchJson(
            state.apiBase + '/api/promo/seo/push' + (force ? '?force=true' : ''),
            { method: 'POST', body: JSON.stringify({}) });
        var modeText = r && r.mode ? (r.mode === 'mock' ? '模拟轨' : r.mode) : '--';
        showBanner('infoBanner', '百度SEO推送完成(' + modeText + '): 提交 '
            + (r && r.submitted != null ? r.submitted : '--') + ' 条, 跳过 '
            + (r && r.skipped != null ? r.skipped : '--') + ' 条, 成功 '
            + (r && r.success != null ? r.success : '--') + ' 条, 失败 '
            + (r && r.failed != null ? r.failed : '--') + ' 条');
        await loadSeoPushes();   // 局部刷新推送记录表(不动其他区块)
    } catch (err) {
        showBanner('errorBanner', '百度SEO推送失败：' + err.message);
    } finally {
        btn.disabled = false; btn.textContent = label;
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

/* ========= P2: SEO 推送记录 URL 明细展开/收起(点击 URL 数量列, 事件委托) ========= */
document.getElementById('seoPushBody').addEventListener('click', function (evt) {
    if (evt.target.closest('button')) { return; }
    var cell = evt.target.closest('td.seo-urls');
    if (!cell) { return; }
    var tr = cell.closest('tr[data-pid]');
    if (!tr) { return; }
    var pid = Number(tr.getAttribute('data-pid'));
    state.seoExpanded[pid] = !state.seoExpanded[pid];
    renderSeoPushes();
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
