/* 40号·平台流量DV博主模块 v1.0 · 博主引流看板脚本
 * 页面: ai-blogger-dashboard.html
 * 鉴权: X-Member-Id + X-Role 头(页面身份输入框)
 * localStorage 键: bloggerDash.apiBase / bloggerDash.memberId / bloggerDash.role
 */
'use strict';

var API_BASE_KEY = 'bloggerDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    memberId: localStorage.getItem('bloggerDash.memberId') || '2',
    role: localStorage.getItem('bloggerDash.role') || 'admin',
    timer: null,
};

/* ========= 基础工具 ========= */

function $(id) { return document.getElementById(id); }

function headers() {
    return {
        'X-Member-Id': state.memberId,
        'X-Role': state.role,
    };
}

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

function showError(msg) {
    var el = $('errorBanner');
    el.textContent = '数据加载失败：' + msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}

function showInfo(msg) {
    var el = $('infoBanner');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

function num(v, dft) {
    var n = Number(v);
    return isNaN(n) ? (dft || 0) : n;
}

/* ========= 徽章 ========= */

var WORK_BADGE = {
    detected: ['已侦测', 'blue'], auto_follow: ['全自动跟随', 'green'],
    manual_queue: ['人工确认', 'yellow'], passed: ['跳过留痕', 'weak'],
    discarded: ['风险否决', 'red'], following: ['跟随中', 'purple'],
};
var FOLLOW_BADGE = {
    pending: ['待人工审', 'yellow'], approved: ['已通过', 'green'],
    rejected: ['已拒绝', 'red'], queued: ['已入队', 'blue'],
    published: ['已发布', 'purple'],
};
var POOL_BADGE = {
    active: ['在池', 'green'], paused: ['已暂停', 'orange'],
};

function badge(map, st) {
    var m = map[st] || [st, 'weak'];
    return '<span class="badge ' + m[1] + '">' + m[0] + '</span>';
}

function pausedReasonBadge(b) {
    if (b.status !== 'paused') { return ''; }
    var reason = b.pausedReason || 'manual';
    var label = reason === 'auto_loss_cut' ? 'AI止损'
        : (reason === 'fraud_suspect' ? '疑似刷量' : '手动');
    return ' <span class="badge red">' + label + '</span>';
}

/* ========= 工具栏 ========= */

function saveConn() {
    state.apiBase = $('apiBase').value.trim().replace(/\/+$/, '');
    state.memberId = $('memberId').value.trim() || '2';
    state.role = $('roleSel').value;
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    localStorage.setItem('bloggerDash.memberId', state.memberId);
    localStorage.setItem('bloggerDash.role', state.role);
    showInfo('连接已保存: ' + state.apiBase);
    refreshData();
}

function toggleAutoRefresh() {
    if ($('autoRefresh').checked) { startTimer(); }
    else { clearInterval(state.timer); state.timer = null; }
}

function startTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(refreshData, 30000);
}

function markUpdate() {
    $('lastUpdate').textContent = '更新于 ' + new Date().toLocaleTimeString();
}

function api(path) { return state.apiBase + path; }

/* ========= ① 全景统计 ========= */

async function loadOverview() {
    try {
        var r = await fetchJson(api('/api/blogger/report/overview'), {}, '全景统计');
        var d = r.data || {};
        var pool = d.pool || {}, works = d.works || {},
            follows = d.follows || {}, attr = d.attribution || {},
            limits = d.limits || {};
        var html = ''
            + '<div class="ov-group"><div class="ov-gtitle">博主池</div><div class="ov-cells">'
            + ovCell(pool.total, '总数') + ovCell(pool.active, '在池')
            + ovCell(pool.paused, '暂停') + ovCell(pool.autoPaused, 'AI止损')
            + ovCell(pool.evolved, '已进化')
            + '</div></div>'
            + '<div class="ov-group"><div class="ov-gtitle">作品侦测</div><div class="ov-cells">'
            + ovCell(works.total, '总数') + ovCell(works.autoFollow, '全自动')
            + ovCell(works.manualQueue, '人工队列') + ovCell(works.passed, '跳过')
            + ovCell(works.discarded, '风险否决') + ovCell(works.following, '跟随中')
            + '</div></div>'
            + '<div class="ov-group"><div class="ov-gtitle">跟随内容</div><div class="ov-cells">'
            + ovCell(follows.total, '总数') + ovCell(follows.pending, '待审')
            + ovCell(follows.queued, '已入队') + ovCell(follows.published, '已发布')
            + ovCell(follows.rejected, '已拒')
            + '</div></div>'
            + '<div class="ov-group"><div class="ov-gtitle">归因漏斗</div><div class="ov-cells">'
            + ovCell(attr.clicks, '点击') + ovCell(attr.registered, '注册')
            + ovCell(attr.ordered, '下单') + ovCell('¥' + num(attr.gmv), 'GMV')
            + '</div></div>'
            + '<div class="ov-group"><div class="ov-gtitle">发布三限</div><div class="ov-cells">'
            + ovCell(limits.dailyCap, '单日上限') + ovCell(limits.bloggerCooldownHours + 'h', '同博主冷却')
            + ovCell(limits.followGapHours + 'h', '间隔错峰')
            + '</div></div>';
        $('overviewBox').innerHTML = html;
        markUpdate();
    } catch (e) { showError(e.message); }
}

function ovCell(v, label) {
    return '<div class="ov-cell"><b>' + esc(v) + '</b><span>' + esc(label) + '</span></div>';
}

/* ========= ② 博主池 ========= */

var poolCache = [];

async function loadPool() {
    try {
        var status = $('poolStatusSel').value;
        var q = status ? '?status=' + status : '';
        var r = await fetchJson(api('/api/blogger/pool' + q), {}, '博主池');
        poolCache = r.data || [];
        var rows = '';
        poolCache.forEach(function (b) {
            var adjust = num(b.weightAdjust);
            var adjustTxt = adjust === 0 ? '0'
                : '<span style="color:' + (adjust > 0 ? '#2e8b57' : '#c0392b') + '">'
                  + (adjust > 0 ? '+' : '') + adjust.toFixed(2) + '</span>';
            var actions = b.status === 'active'
                ? '<button class="btn-mini danger" onclick="togglePool(' + b.bloggerId + ',\'paused\')">暂停</button>'
                : '<button class="btn-mini" onclick="togglePool(' + b.bloggerId + ',\'active\')">恢复</button>';
            rows += '<tr>'
                + '<td class="cap-id">' + b.bloggerId + '</td>'
                + '<td>' + esc(b.nickname) + '<div class="cap-id">@' + esc(b.account) + '</div></td>'
                + '<td>' + esc(b.platform) + '</td>'
                + '<td>' + esc(b.domain) + '</td>'
                + '<td class="num">' + num(b.fansWan).toFixed(0) + '</td>'
                + '<td class="num">' + num(b.weightBase).toFixed(2) + '</td>'
                + '<td class="num">' + adjustTxt + '</td>'
                + '<td class="num"><b>' + num(b.weight).toFixed(2) + '</b></td>'
                + '<td>' + badge(POOL_BADGE, b.status) + pausedReasonBadge(b) + '</td>'
                + '<td class="num">' + num(b.zeroTrafficStreak) + '</td>'
                + '<td class="num">' + num(b.fraudStreak) + '</td>'
                + '<td class="num">' + num(b.probeRemaining) + '</td>'
                + '<td>' + actions + '</td>'
                + '</tr>';
        });
        $('poolBody').innerHTML = rows
            || '<tr><td colspan="13" class="dash-empty">暂无博主</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

async function togglePool(id, status) {
    try {
        await fetchJson(api('/api/blogger/pool/' + id + '/'
                            + (status === 'paused' ? 'pause' : 'activate')),
                        { method: 'POST' }, '博主状态');
        showInfo('博主 ' + id + ' 已' + (status === 'paused' ? '暂停' : '恢复'));
        loadPool(); loadOverview();
    } catch (e) { showError(e.message); }
}

/* ========= ③ 作品流 ========= */

var workCache = [];

async function loadWorks() {
    try {
        var status = $('workStatusSel').value;
        var q = status ? '?status=' + status + '&limit=50' : '?limit=50';
        var r = await fetchJson(api('/api/blogger/works' + q), {}, '作品流');
        workCache = r.data || [];
        var bloggerMap = {};
        poolCache.forEach(function (b) { bloggerMap[b.bloggerId] = b.nickname; });
        var rows = '';
        workCache.forEach(function (w) {
            var decision = w.decision === 'auto_follow' ? 'green'
                : (w.decision === 'manual_queue' ? 'yellow' : 'weak');
            rows += '<tr>'
                + '<td class="cap-id">' + w.workId + '</td>'
                + '<td>' + esc(bloggerMap[w.bloggerId] || ('#' + w.bloggerId)) + '</td>'
                + '<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis">'
                  + esc(w.title) + '</td>'
                + '<td class="num">' + num(w.likes) + '/' + num(w.comments)
                  + '/' + num(w.shares) + '</td>'
                + '<td class="num"><b>' + num(w.score).toFixed(1) + '</b></td>'
                + '<td><span class="badge ' + decision + '">'
                  + esc(w.decision || '—') + '</span></td>'
                + '<td>' + badge(WORK_BADGE, w.status) + '</td>'
                + '<td><button class="btn-mini" onclick="showWorkDetail('
                  + w.workId + ')">评分快照</button></td>'
                + '</tr>';
        });
        $('workBody').innerHTML = rows
            || '<tr><td colspan="8" class="dash-empty">暂无作品(先执行雷达扫描)</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

function showWorkDetail(workId) {
    var w = null;
    for (var i = 0; i < workCache.length; i++) {
        if (workCache[i].workId === workId) { w = workCache[i]; break; }
    }
    if (!w) { return; }
    var snap = w.scoreSnapshot || {};
    var html = '【作品】#' + w.workId + ' ' + esc(w.title) + '\n'
        + '评分 ' + num(w.score).toFixed(1) + ' → 决策 ' + esc(w.decision) + '\n';
    if (snap.platformBias) {
        html += '平台校准偏置 +' + num(snap.platformBias).toFixed(1)
            + '(原始分 ' + num(snap.rawScore).toFixed(1) + ')\n';
    }
    (snap.factors || []).forEach(function (f) {
        html += factorBar(f);
    });
    if (w.riskFlags && w.riskFlags.length) {
        html += '\n风险否决词: ' + esc(w.riskFlags.join(', '));
    }
    $('workDetailBody').innerHTML = html;
    $('workDetail').style.display = 'block';
}

function factorBar(f) {
    var pct = Math.max(0, Math.min(100, num(f.score)));
    return '<div class="factor-bar">'
        + '<span class="f-name">' + esc(f.label || f.name) + '</span>'
        + '<span class="f-track"><span class="f-fill" style="width:' + pct + '%"></span></span>'
        + '<span class="f-val">' + pct.toFixed(0) + '分 × 权重'
        + num(f.weight).toFixed(2) + ' = ' + num(f.contribution).toFixed(1) + '</span>'
        + '</div>';
}

/* ========= ④ 跟随内容 ========= */

var followCache = [];

async function loadFollows() {
    try {
        var status = $('followStatusSel').value;
        var q = status ? '?status=' + status + '&limit=50' : '?limit=50';
        var r = await fetchJson(api('/api/blogger/follows' + q), {}, '跟随内容');
        followCache = r.data || [];
        var bloggerMap = {};
        poolCache.forEach(function (b) { bloggerMap[b.bloggerId] = b.nickname; });
        var rows = '';
        followCache.forEach(function (f) {
            var lm = f.learningMetrics || {};
            var lmTxt = f.learningFed
                ? '点击' + num(lm.clicks) + '(q' + num(lm.clickQuality).toFixed(2)
                  + ') r=' + num(lm.reward).toFixed(2)
                  + (lm.fraudSuspect ? ' <span class="badge red">疑似刷量</span>' : '')
                : '—';
            var actions = '';
            if (f.status === 'approved') {
                actions = '<button class="btn-mini" onclick="publishFollow('
                    + f.followId + ')">入队发布</button>';
            }
            rows += '<tr>'
                + '<td class="cap-id">' + f.followId + '</td>'
                + '<td>' + esc(bloggerMap[f.bloggerId] || ('#' + f.bloggerId)) + '</td>'
                + '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis">'
                  + esc(f.title) + '</td>'
                + '<td class="cap-id">' + esc(f.shortCode || '—') + '</td>'
                + '<td class="num">' + (num(f.overlapRatio) * 100).toFixed(1) + '%</td>'
                + '<td class="num">' + num(f.complianceScore) + '</td>'
                + '<td>' + badge(FOLLOW_BADGE, f.status) + '</td>'
                + '<td>' + lmTxt + '</td>'
                + '<td>' + actions
                  + '<button class="btn-mini" onclick="showFollowDetail('
                  + f.followId + ')">文案</button></td>'
                + '</tr>';
        });
        $('followBody').innerHTML = rows
            || '<tr><td colspan="9" class="dash-empty">暂无跟随内容</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

function showFollowDetail(followId) {
    var f = null;
    for (var i = 0; i < followCache.length; i++) {
        if (followCache[i].followId === followId) { f = followCache[i]; break; }
    }
    if (!f) { return; }
    var lm = f.learningMetrics || {};
    var html = '【跟随内容】#' + f.followId + ' ' + esc(f.title) + '\n'
        + '短码: ' + esc(f.shortCode || '—') + ' · 搬运重合度 '
        + (num(f.overlapRatio) * 100).toFixed(1) + '% · 合规分 '
        + num(f.complianceScore) + '\n'
        + '存证哈希: ' + esc(f.evidenceHash || '—') + '\n'
        + '─'.repeat(36) + '\n'
        + esc(f.body);
    if (f.learningFed) {
        html += '\n' + '─'.repeat(36)
            + '\n【回流指标】原始点击' + num(lm.clickRaw)
            + ' → 有效' + num(lm.clicks)
            + '(质量' + num(lm.clickQuality).toFixed(2) + ')'
            + ' · reward=' + num(lm.reward).toFixed(2)
            + ' · 注册' + num(lm.registrations)
            + ' · GMV ¥' + num(lm.gmv);
    }
    $('followDetailBody').textContent = html;
    $('followDetail').style.display = 'block';
}

async function publishFollow(followId) {
    try {
        await fetchJson(api('/api/blogger/follows/' + followId + '/publish'),
                        { method: 'POST', body: { publishAt: '' } }, '入队发布');
        showInfo('跟随内容 ' + followId + ' 已入发布队列(黄金时段)');
        loadFollows();
    } catch (e) { showError(e.message); }
}

/* ========= ⑤ 待人工队列 ========= */

async function loadPending() {
    try {
        var r = await fetchJson(api('/api/blogger/reviews/pending?limit=50'),
                                {}, '待人工队列');
        var list = r.data || [];
        var bloggerMap = {};
        poolCache.forEach(function (b) { bloggerMap[b.bloggerId] = b.nickname; });
        var rows = '';
        list.forEach(function (f) {
            rows += '<tr>'
                + '<td class="cap-id">' + f.followId + '</td>'
                + '<td>' + esc(bloggerMap[f.bloggerId] || ('#' + f.bloggerId)) + '</td>'
                + '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis">'
                  + esc(f.title) + '</td>'
                + '<td class="num">' + num(f.complianceScore) + '</td>'
                + '<td>'
                + '<button class="btn-mini" onclick="reviewFollow(' + f.followId
                + ',true)">通过</button> '
                + '<button class="btn-mini danger" onclick="reviewFollow('
                + f.followId + ',false)">拒绝</button>'
                + '</td>'
                + '</tr>';
        });
        $('pendingBody').innerHTML = rows
            || '<tr><td colspan="5" class="dash-empty">队列为空</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

async function reviewFollow(followId, approved) {
    try {
        await fetchJson(api('/api/blogger/follows/' + followId + '/review'),
                        { method: 'POST',
                          body: { approved: approved, reviewer: '看板admin' } },
                        '人工审核');
        showInfo('跟随内容 ' + followId + ' 已'
                 + (approved ? '通过' : '拒绝'));
        loadPending(); loadFollows(); loadOverview();
    } catch (e) { showError(e.message); }
}

/* ========= ⑥⑦ 雷达与发布 ========= */

async function runScan() {
    try {
        var r = await fetchJson(api('/api/blogger/radar/scan'),
                                { method: 'POST' }, '雷达扫描');
        var d = r.data || {};
        var decisions = d.decisions || [];
        var autoN = 0, manualN = 0, passN = 0;
        decisions.forEach(function (x) {
            var st = (x.work || {}).status;
            if (st === 'auto_follow') { autoN++; }
            else if (st === 'manual_queue') { manualN++; }
            else if (st === 'passed') { passN++; }
        });
        $('opsResult').textContent = ''
            + '【雷达扫描】扫描 ' + num(d.scanned) + ' 条 · 新增 '
            + num(d.new) + ' · 风险否决 ' + num(d.discarded)
            + ' · 指纹跳过 ' + num(d.skipped) + '\n'
            + '决策: 全自动 ' + autoN + ' / 人工确认 ' + manualN
            + ' / 跳过 ' + passN;
        showInfo('雷达扫描完成: 新增 ' + d.new + ' 条');
        loadWorks(); loadOverview();
    } catch (e) { showError(e.message); }
}

async function runPublish() {
    try {
        var r = await fetchJson(api('/api/blogger/publish/run'),
                                { method: 'POST' }, '发布出队');
        var d = r.data || {};
        var published = d.published || [];
        var text = '【发布出队】本次发布 ' + num(d.count) + ' 条';
        published.forEach(function (p) {
            var receipt = p.receipt || {};
            text += '\n· #' + p.followId + ' → ' + esc(p.platform)
                + '(回执 ' + esc(receipt.mode) + ' 曝光~'
                + num(receipt.exposureEstimate) + ')';
        });
        $('opsResult').textContent = text;
        showInfo('发布出队完成: ' + d.count + ' 条');
        loadFollows(); loadOverview();
    } catch (e) { showError(e.message); }
}

/* ========= ⑧ 学习闭环 ========= */

async function loadLearning() {
    try {
        var r = await fetchJson(api('/api/blogger/learning/status'),
                                {}, '学习状态');
        var d = r.data || {};
        var fb = d.feedback || {};
        var w = d.weights || {};
        var drift = d.drift || {};
        var champion = w.champion || {};
        var weights = champion.weights || {};
        var html = '【回流】已发布 ' + num(fb.published) + ' · 已回流 '
            + num(fb.fed) + ' · 待回流 ' + num(fb.pending)
            + '(沉淀窗口 ' + num(fb.settleHours) + 'h)\n'
            + '【层1 因子权重】版本 ' + esc(champion.version || '—') + '\n';
        Object.keys(weights).forEach(function (k) {
            html += factorBar({ name: k, label: k,
                                score: num(weights[k]) * 100,
                                weight: 1,
                                contribution: num(weights[k]) * 100 });
        });
        var driftLevel = drift.driftLevel || '—';
        html += '\n【漂移监控】级别 ' + esc(driftLevel)
            + ' · 样本 ' + num(drift.count);
        $('learningBox').innerHTML = '<div class="detail-body">' + html + '</div>';
        markUpdate();
    } catch (e) {
        $('learningBox').innerHTML = '<div class="dash-empty">'
            + esc(e.message) + '</div>';
    }
}

async function runCollect() {
    try {
        var r = await fetchJson(api('/api/blogger/learning/collect'),
                                { method: 'POST' }, '批量回流');
        var d = r.data || {};
        showInfo('批量回流: 提交 ' + num(d.submitted) + ' / 跳过 '
                 + num(d.skipped));
        loadLearning(); loadEvolution();
    } catch (e) { showError(e.message); }
}

async function runLearning() {
    try {
        var r = await fetchJson(api('/api/blogger/learning/run'),
                                { method: 'POST' }, '触发学习');
        showInfo('Hedge 学习完成(版本 ' + (r.data || {}).version + ')');
        loadLearning();
    } catch (e) { showError(e.message); }
}

/* ========= ⑨ 权重自进化榜 ========= */

async function loadEvolution() {
    try {
        var r = await fetchJson(api('/api/blogger/learning/status'),
                                {}, '进化榜');
        var d = r.data || {};
        var evo = d.weightEvolution || {};
        var bloggerMap = {};
        poolCache.forEach(function (b) {
            bloggerMap[b.bloggerId] = {
                nickname: b.nickname, platform: b.platform,
                pausedReason: b.pausedReason, status: b.status,
            };
        });
        var rows = '';
        var render = function (b) {
            var adjust = num(b.weightAdjust);
            var info = bloggerMap[b.bloggerId] || {};
            rows += '<tr>'
                + '<td>' + esc(b.nickname || info.nickname || ('#' + b.bloggerId)) + '</td>'
                + '<td>' + esc(b.platform || info.platform || '—') + '</td>'
                + '<td class="num">' + num(b.weightBase).toFixed(2) + '</td>'
                + '<td class="num" style="color:' + (adjust > 0 ? '#2e8b57'
                    : (adjust < 0 ? '#c0392b' : 'inherit')) + '">'
                  + (adjust > 0 ? '+' : '') + adjust.toFixed(2) + '</td>'
                + '<td class="num"><b>' + num(b.weight).toFixed(2) + '</b></td>'
                + '<td class="num">' + num(b.zeroTrafficStreak) + '</td>'
                + '<td>' + (info.status === 'paused'
                    ? badge(POOL_BADGE, 'paused') + pausedReasonBadge(
                        { status: 'paused',
                          pausedReason: info.pausedReason })
                    : badge(POOL_BADGE, 'active')) + '</td>'
                + '</tr>';
        };
        (evo.top || []).forEach(render);
        (evo.bottom || []).forEach(render);
        $('evoBody').innerHTML = rows
            || '<tr><td colspan="7" class="dash-empty">暂无进化数据(回流后生成)</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* ========= ⑩ 学习健康 ========= */

async function loadHealth() {
    try {
        var r = await fetchJson(api('/api/blogger/learning/health'),
                                {}, '学习健康');
        var d = r.data || {};
        var l1 = d.layer1 || {}, l2 = d.layer2 || {}, qg = d.qualityGate || {};
        var bias = d.bias || {};
        var html = '【层1 学习引擎】'
            + (l1.learningPaused
               ? '<span class="badge red">污染熔断中</span>' : '<span class="badge green">正常</span>')
            + ' · 待学习反馈疑似刷量占比 '
            + (num(l1.fraudSharePending) * 100).toFixed(1) + '%\n'
            + '【层2 池治理】冻结 ' + (l2.frozen || []).length
            + ' · AI止损 ' + (l2.autoPaused || []).length
            + ' · 疑似刷量止损 ' + (l2.fraudPaused || []).length
            + ' · 缓刑复扫中 ' + (l2.onProbation || []).length + '\n'
            + '【质量门】已回流 ' + num(qg.fedTotal)
            + ' · 刷量标记率 ' + (num(qg.fraudRate) * 100).toFixed(1) + '%'
            + ' · 有效点击率 ' + (num(qg.effectiveClickRate) * 100).toFixed(1) + '%\n'
            + '【平台校准偏置】';
        ['douyin', 'xiaohongshu', 'weibo', 'wechat_channels'].forEach(
            function (p) {
                var v = num(bias[p]);
                html += '\n· ' + p + ': '
                    + (v > 0 ? '+' : '') + v.toFixed(1) + ' 分';
            });
        $('healthBox').innerHTML = '<div class="detail-body">' + html + '</div>';
        markUpdate();
    } catch (e) {
        $('healthBox').innerHTML = '<div class="dash-empty">'
            + esc(e.message) + '</div>';
    }
}

async function runCalibrate() {
    try {
        var r = await fetchJson(api('/api/blogger/learning/calibrate'),
                                { method: 'POST' }, '偏置重算');
        showInfo('平台偏置已重算');
        loadHealth();
    } catch (e) { showError(e.message); }
}

/* ========= 主流程 ========= */

async function refreshData() {
    await Promise.all([
        loadOverview(), loadPool(), loadWorks(), loadFollows(),
        loadPending(), loadLearning(), loadEvolution(), loadHealth(),
    ]);
}

(function init() {
    $('apiBase').value = state.apiBase;
    $('memberId').value = state.memberId;
    $('roleSel').value = state.role;
    refreshData();
})();
