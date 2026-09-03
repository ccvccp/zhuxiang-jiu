/**
 * 44号·AI智能API管理模块 · API 管理面板(P0-P5)
 * 范式: js/security-dashboard.js(43号)平移——ES5、localStorage 连接、
 * 区块化加载(台账静态, 手动/保存触发, 不进自动刷新)。
 * 依赖后端: /api/api-manager/*(44号 api_manager_routes)
 * 区块: ①API 资产总览(分布/台账/生命周期转换) ②调用观测(P3)
 *       ③健康评分(P3) ④AI 自治(P4) ⑤治理闭环(P5:
 *       裁决回流三连/对外目录/try-out 在线调试)
 */
'use strict';

var API_BASE_KEY = 'apiDash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000' };

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

function saveConn() {
    var el = document.getElementById('apiBase');
    state.apiBase = el.value.trim().replace(/\/+$/, '');
    if (!state.apiBase) { state.apiBase = 'http://localhost:8000'; }
    el.value = state.apiBase;
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    loadRegistry();
}

/* ============================================================
 * ① API 资产总览
 * ============================================================ */

async function loadRegistry() {
    try {
        var mod = document.getElementById('filterModule').value || '';
        var st = document.getElementById('filterStatus').value || '';
        var qs = [];
        if (mod) { qs.push('module=' + encodeURIComponent(mod)); }
        if (st) { qs.push('status=' + encodeURIComponent(st)); }
        var b = await fetchJson(
            api('/api/api-manager/admin/apis' + (qs.length ? '?' + qs.join('&') : '')),
            { headers: headers() }, 'API 台账');

        // 统计卡
        var bs = b.byStatus || {};
        var missing = 0;
        (b.entries || []).forEach(function (e) { if (e.missing) { missing++; } });
        var cells = [
            { k: 'API 总数', v: b.total || 0, cls: 'blue' },
            { k: '模块数', v: b.moduleCount || 0 },
            { k: 'published', v: bs.published || 0, cls: 'green' },
            { k: 'development', v: bs.development || 0 },
            { k: 'deprecated', v: bs.deprecated || 0, cls: 'yellow' },
            { k: 'offline', v: bs.offline || 0, cls: 'red' }
        ];
        document.getElementById('apiStats').innerHTML =
            cells.map(function (c) {
                return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                    '</div><div class="v ' + (c.cls || '') + '">' + esc(c.v) +
                    '</div></div>';
            }).join('');

        // 模块分布(全部口径, 不随过滤变)
        var bm = b.byModule || {};
        var items = Object.keys(bm).map(function (k) {
            return '<li>' + esc(k) + ' <b>' + bm[k] + '</b></li>';
        });
        document.getElementById('moduleList').innerHTML =
            items.join('') || '<li>暂无</li>';

        // 模块过滤下拉(保留当前选择)
        var sel = document.getElementById('filterModule');
        var cur = sel.value;
        sel.innerHTML = '<option value="">全部模块</option>' +
            Object.keys(bm).map(function (k) {
                return '<option value="' + esc(k) + '">' + esc(k) +
                    ' (' + bm[k] + ')</option>';
            }).join('');
        if (cur && bm[cur] !== undefined) { sel.value = cur; }

        // 台账列表(含 P5 生命周期主链操作)
        var rows = (b.entries || []).map(function (e) {
            var dot = '<i class="dot ' + esc(e.status || 'development') +
                '"></i>' + esc(e.status || 'development');
            if (e.missing) {
                dot += ' <i class="dot missing" title="路由已消失"></i>missing';
            }
            var modBadge = esc(e.module || 'uncategorized') +
                (e.moduleSource === 'manual'
                 ? ' <span class="badge manual">人工</span>' : '');
            // 主链下一步(development→published→deprecated→offline;
            // offline→development 重启); 其余转换走 API
            var next = { development: ['published', '发布'],
                         published: ['deprecated', '弃用'],
                         deprecated: ['offline', '下线'],
                         offline: ['development', '重启'] }[
                e.status || 'development'];
            var act = next
                ? '<button class="btn-mini" onclick="lifecycleAction(' +
                  e.apiId + ',\'' + next[0] + '\')">' + next[1] + '</button>'
                : '-';
            if (e.deprecatedAt) {
                act += ' <span class="badge" title="弃用于 ' +
                    esc(e.deprecatedAt) + '">弃用中</span>';
            }
            return '<tr><td>' + esc(e.apiId) + '</td><td>' +
                esc(e.method) + '</td><td style="word-break:break-all">' +
                esc(e.path) + '</td><td>' + modBadge + '</td><td>' +
                dot + '</td><td style="color:#888">' +
                esc(e.summary || '') + '</td><td>' + act + '</td></tr>';
        });
        document.getElementById('apiList').innerHTML =
            rows.join('') ||
            '<tr><td colspan="7" class="dash-empty">暂无台账(点「重扫台账」同步)</td></tr>';
        document.getElementById('listCount').textContent =
            '共 ' + (b.total || 0) + ' 条';
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* 手动重扫(diff 返回后刷新台账) */
async function syncRegistry() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/sync'),
            { method: 'POST', headers: headers() }, '重扫台账');
        var lines = ['重扫完成: 发现 ' + (b.discovered || 0) + ' 条'];
        lines.push('新增 ' + (b.added || 0) +
                   (b.addedList && b.addedList.length
                    ? ' — ' + b.addedList.join(', ')
                      + (b.added > b.addedList.length ? ' …' : '') : ''));
        lines.push('消失 ' + (b.disappeared || 0) +
                   (b.disappearedList && b.disappearedList.length
                    ? ' — ' + b.disappearedList.join(', ')
                      + (b.disappeared > b.disappearedList.length ? ' …' : '') : ''));
        lines.push('module 修正 ' + (b.moduleUpdated || 0));
        var el = document.getElementById('syncDiff');
        el.textContent = lines.join('\n');
        el.style.display = 'block';
        showInfo('台账重扫完成(新增 ' + (b.added || 0) +
                 ' / 消失 ' + (b.disappeared || 0) + ')');
        await loadRegistry();
    } catch (e) { showError(e.message); }
}

/* ============================================================
 * ② 调用观测(P3: 三视图) / ③ 健康评分(第27档案)
 * ============================================================ */

function pct1(v) { return ((v || 0) * 100).toFixed(1) + '%'; }

async function loadUsage() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/usage'),
            { headers: headers() }, '调用观测');
        var cells = [
            { k: '总调用(今日)', v: b.totalCalls || 0, cls: 'blue' },
            { k: '总错误', v: b.totalErrors || 0,
              cls: (b.totalErrors || 0) > 0 ? 'red' : 'green' },
            { k: '活跃 API', v: (b.byApi || []).length },
            { k: '活跃 Key', v: (b.byKey || []).length }
        ];
        document.getElementById('usageStats').innerHTML =
            cells.map(function (c) {
                return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                    '</div><div class="v ' + (c.cls || '') + '">' + esc(c.v) +
                    '</div></div>';
            }).join('');

        document.getElementById('usageByApi').innerHTML =
            (b.byApi || []).map(function (a) {
                var er = a.errorRate || 0;
                return '<tr><td style="word-break:break-all">' + esc(a.template) +
                    '</td><td>' + esc(a.total) + '</td><td class="' +
                    (er > 0.1 ? 'err-red' : '') + '">' + pct1(er) +
                    '</td><td>' + esc(a.avgMs) + '</td><td>' + esc(a.maxMs) +
                    '</td><td>' + esc(a.callers) + '</td></tr>';
            }).join('') ||
            '<tr><td colspan="6" class="dash-empty">暂无调用(发布 API 并用 Key 访问后产生观测)</td></tr>';

        document.getElementById('usageByKey').innerHTML =
            (b.byKey || []).map(function (k) {
                var er = k.errorRate || 0;
                var q = (b.quota || []).find(function (x) {
                    return x.keyId === k.keyId;
                }) || {};
                var hit = q.hitRate || 0;
                return '<tr><td>#' + esc(k.keyId) + '</td><td>' +
                    esc(k.name || '-') + '</td><td>' + esc(k.tier || '-') +
                    '</td><td>' + esc(k.total) + '</td><td class="' +
                    (er > 0.1 ? 'err-red' : '') + '">' + pct1(er) +
                    '</td><td class="' + (hit >= 0.9 ? 'quota-high' : '') +
                    '">' + pct1(hit) +
                    (hit >= 0.9 ? ' ⚠' : '') + '</td></tr>';
            }).join('') ||
            '<tr><td colspan="6" class="dash-empty">暂无</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

async function loadHealth() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/health'),
            { headers: headers() }, '健康评分');
        var o = b.overall || {};
        var cells = [
            { k: '总健康分', v: o.score || 0,
              cls: (o.grade === 'healthy') ? 'green'
                   : (o.grade === 'watch') ? 'yellow' : 'red' },
            { k: '档位', v: o.grade || '-' },
            { k: '参评 API', v: (b.apis || []).length },
            { k: '口径', v: '建议型' }
        ];
        document.getElementById('healthOverall').innerHTML =
            cells.map(function (c) {
                return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                    '</div><div class="v ' + (c.cls || '') + '">' + esc(c.v) +
                    '</div></div>';
            }).join('');

        document.getElementById('healthByApi').innerHTML =
            (b.apis || []).map(function (a) {
                var summary = (a.factors || []).map(function (f) {
                    return esc(f.detail || f.name);
                }).join(' · ');
                return '<tr><td style="word-break:break-all">' + esc(a.template) +
                    '</td><td><b>' + esc(a.health) + '</b></td><td><span class="grade-pill ' +
                    esc(a.grade) + '">' + esc(a.grade) + '</span></td><td style="color:#888">' +
                    summary + '</td></tr>';
            }).join('') ||
            '<tr><td colspan="4" class="dash-empty">暂无评分样本</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* ============================================================
 * ④ AI 自治(P4: 异常检测 + NL 助手)
 * ============================================================ */

var KIND_NAMES = { spike: '尖刺', drop: '骤降',
                   error_burst: '错误激增' };

async function detectAnomalies() {
    try {
        var r = await fetchJson(
            api('/api/api-manager/admin/apis/anomalies/detect'),
            { method: 'POST', headers: headers() }, '异常检测');
        var events = r.events || [];
        var html = events.length
            ? events.map(function (e) {
                return '<div class="redis-alert warn">[' +
                    (KIND_NAMES[e.kind] || e.kind) + '] ' +
                    esc(e.summary) +
                    ' <button class="btn-mini" onclick="decideAnomaly(' +
                    e.eventId + ',true)">真异常</button>' +
                    ' <button class="btn-mini ghost" onclick="decideAnomaly(' +
                    e.eventId + ',false)">误报</button></div>';
            }).join('')
            : '<div class="dash-empty">无异常(基线 vs 当日正常)</div>';
        document.getElementById('anomalyList').innerHTML = html;
        showInfo('检测完成: ' + (r.detected || 0) + ' 个异常');
    } catch (e) { showError(e.message); }
}

async function decideAnomaly(eventId, confirm) {
    try {
        await fetchJson(
            api('/api/api-manager/admin/apis/anomalies/' +
                eventId + '/decide'),
            { method: 'POST',
              headers: Object.assign(headers(),
                  { 'Content-Type': 'application/json' }),
              body: JSON.stringify({ confirm: confirm }) },
            '事件裁决');
        showInfo('已裁决: ' + (confirm ? '真异常' : '误报'));
    } catch (e) { showError(e.message); }
}

async function askAssistant() {
    var q = (document.getElementById('assistantQ').value || '').trim();
    if (!q) { showError('请输入问题'); return; }
    try {
        var r = await fetchJson(
            api('/api/api-manager/apis/assistant'),
            { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ q: q }) }, 'NL 助手');
        document.getElementById('assistantAnswer').textContent =
            r.answer || '(空回答)';
        document.getElementById('assistantMode').textContent =
            '模式: ' + (r.mode || 'mock') +
            ' · 意图: ' + (r.intent || '-');
    } catch (e) { showError(e.message); }
}

/* ============================================================
 * ⑤ 治理闭环(P5: 生命周期转换 / 裁决回流三连 / 对外目录 / try-out)
 * ============================================================ */

/* 生命周期转换(主链人工触发; 下线带近 7 日存量软护栏,
 * forceOffline 勾选时 force=true 强制留痕) */
async function lifecycleAction(apiId, status) {
    var force = false;
    var box = document.getElementById('forceOffline');
    if (status === 'offline' && box && box.checked) { force = true; }
    if (status === 'offline' && !force &&
        !window.confirm('下线前将检查近 7 日 Key 面调用量, ' +
                        '有存量会被阻断。继续?')) { return; }
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/' + apiId + '/lifecycle'),
            { method: 'POST',
              headers: Object.assign(headers(),
                  { 'Content-Type': 'application/json' }),
              body: JSON.stringify(
                  { status: status, force: force }) },
            '生命周期转换');
        showInfo('已转换: #' + apiId + ' → ' + b.status);
        await loadRegistry();
        await loadCatalog();
    } catch (e) { showError(e.message); }
}

/* 裁决真值批量回流(已裁决未回流 → 第27档案反馈) */
async function learningCollect() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/learning/collect'),
            { method: 'POST', headers: headers() }, '裁决回流');
        document.getElementById('learningPanel').textContent =
            '回流完成: 提交 ' + (b.submitted || 0) +
            ' 条 / 跳过 ' + (b.skipped || 0) +
            ' 条(pending 未裁决或已回流幂等跳过)';
        showInfo('裁决回流: 提交 ' + (b.submitted || 0) + ' 条');
    } catch (e) { showError(e.message); }
}

/* 触发一轮 Hedge 学习(第27档案) */
async function learningRun() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/learning/run'),
            { method: 'POST', headers: headers() }, '触发学习');
        var delta = b.weightDelta || {};
        var moved = Object.keys(delta).filter(function (k) {
            return Math.abs(delta[k]) > 0.0001;
        });
        document.getElementById('learningPanel').textContent =
            '学习完成: ' + (b.newVersion || '-') + '(' +
            (b.newStatus || '-') + ') · 样本 ' +
            (b.learnedFrom || 0) + ' · 权重变化 ' +
            (moved.length ? moved.map(function (k) {
                return k + ' ' + (delta[k] > 0 ? '+' : '') +
                    delta[k].toFixed(4);
            }).join(', ') : '(护栏内无变化)');
        showInfo('学习一轮完成(' + (b.newStatus || '-') + ')');
    } catch (e) { showError(e.message); }
}

/* 学习状态视图(档案/裁决/已回流/当前权重视图) */
async function learningStatus() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/admin/apis/learning/status'),
            { headers: headers() }, '学习状态');
        var w = b.weights || {};
        var champ = (w.champion && w.champion.weights) || w.defaults || {};
        var rows = Object.keys(champ).map(function (k) {
            return k + '=' + Number(champ[k]).toFixed(3);
        });
        document.getElementById('learningPanel').textContent =
            '档案: ' + (b.scorer || '-') + ' · 已裁决 ' +
            (b.decided || 0) + ' / 已回流 ' + (b.fed || 0) +
            ' / 待裁决 ' + (b.pending || 0) +
            ' · 冠军权重: ' + rows.join(' ');
    } catch (e) { showError(e.message); }
}

/* 对外目录(published + deprecated 迁移窗口) */
async function loadCatalog() {
    try {
        var b = await fetchJson(
            api('/api/api-manager/apis/catalog'), {}, '对外目录');
        var rows = (b.apis || []).map(function (a) {
            var st = a.deprecated
                ? '<span style="color:#a37400">⚠ deprecated</span>'
                : '<span style="color:#2f9e44">published</span>';
            var sunset = a.deprecated
                ? (sunsetDays(a.sunsetAt) + ' 天') : '-';
            return '<tr><td>' + esc(a.method) + '</td>' +
                '<td style="word-break:break-all">' + esc(a.path) +
                '</td><td>' + esc(a.module || '-') + '</td><td>' +
                st + '</td><td>' + esc(sunset) + '</td><td style="color:#888">' +
                esc(a.summary || '') + '</td><td><button class="btn-mini" ' +
                'onclick="fillTryOut(\'' + esc(a.method) + '\',\'' +
                esc(a.path) + '\')">调试</button></td></tr>';
        });
        document.getElementById('catalogList').innerHTML =
            rows.join('') ||
            '<tr><td colspan="7" class="dash-empty">目录为空(发布 API 后展示)</td></tr>';
        markUpdate();
    } catch (e) { showError(e.message); }
}

/* 日落倒计时(天数; 过期为红字"已到期") */
function sunsetDays(sunsetAt) {
    if (!sunsetAt) { return '?'; }
    var t = Date.parse(sunsetAt);
    if (isNaN(t)) { return '?'; }
    var days = Math.ceil((t - Date.now()) / 86400000);
    if (days <= 0) { return '已到期'; }
    return days;
}

/* try-out: 目录带入 */
function fillTryOut(method, path) {
    document.getElementById('tryMethod').value = method;
    document.getElementById('tryPath').value = path;
    document.getElementById('tryOutResult').textContent =
        '已带入 ' + method + ' ' + path +
        ' —— 填 X-Api-Key / X-App-Code 后点「发起调试」';
}

/* try-out: 发起调试(真实请求, 弃用预警头/410/401 可实测) */
async function runTryOut() {
    var method = document.getElementById('tryMethod').value;
    var path = (document.getElementById('tryPath').value || '').trim();
    var key = (document.getElementById('tryKey').value || '').trim();
    var app = (document.getElementById('tryApp').value || '').trim();
    var out = document.getElementById('tryOutResult');
    if (!path) { showError('请填写调试路径'); return; }
    if (!key || !app) {
        out.textContent = '提示: 该 API 已发布(Key 面)时需 X-Api-Key ' +
            '与 X-App-Code 双头凭证; 仅 JWT 面接口可免 Key 调试';
    }
    try {
        var resp = await fetch(state.apiBase + path, {
            method: method,
            headers: { 'X-Api-Key': key, 'X-App-Code': app }
        });
        var text = await resp.text();
        var dep = resp.headers.get('X-Api-Deprecated');
        var retry = resp.headers.get('Retry-After');
        var lines = ['HTTP ' + resp.status + ' ' + (resp.statusText || '')];
        if (dep) { lines.push('⚠ X-Api-Deprecated: ' + dep +
                              '(弃用预警——请迁移)'); }
        if (retry) { lines.push('⏳ Retry-After: ' + retry + 's'); }
        lines.push('');
        lines.push(text.length > 800 ? text.slice(0, 800) + ' …' : text);
        out.textContent = lines.join('\n');
    } catch (e) {
        out.textContent = '请求失败: ' + e.message +
            '(检查路径/跨域/后端地址)';
    }
}

/* 初始化 */
(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadRegistry();
    loadUsage();
    loadHealth();
    loadCatalog();
})();
