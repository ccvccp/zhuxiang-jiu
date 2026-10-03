/**
 * 智码·AI智能二维码大模型看板(八区块, 70号)
 * 范式: js/zhike-dashboard.js 平移——ES5、同源默认、401 汉化、
 * 宽松取值降级渲染。
 * 依赖后端: /api/qr70/*(qr70_routes; X-Role admin)
 */
'use strict';

var API_BASE_KEY = 'qr70Dash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY) || '' };

var KIND_CN = {
    manage: '管理码', auth: '认证码', trace: '溯源码',
    receiving: '收货码', shipping: '发货码', collect: '收款码'
};
var LIFECYCLE_CN = {
    generated: '已生成', scanned: '已扫描', redeemed: '已核销',
    expired: '已过期', voided: '已作废'
};

function adminHeaders() {
    var h = { 'X-Role': 'admin', 'Content-Type': 'application/json' };
    var auth = (typeof Auth !== 'undefined') ? Auth.apiHeaders() : null;
    return auth ? Object.assign(h, auth) : h;
}

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
            if (resp.status === 401) {
                throw new Error(label + '：请先以管理员账号登录'
                    + '（登录页登录后回到本页刷新）');
            }
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

function markUpdate() {
    document.getElementById('lastUpdate').textContent =
        '更新于 ' + new Date().toLocaleTimeString();
}

function saveConn() {
    var el = document.getElementById('apiBase');
    state.apiBase = el.value.trim().replace(/\/+$/, '');
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

function when(s) {
    return String(s || '').replace('T', ' ').slice(5, 16);
}

/* ① 模式 + 总览 */
async function loadStatus() {
    var mode = '-';
    try {
        var mb = await fetchJson(api('/api/qr70/mode'),
            { headers: adminHeaders() }, '模式读取');
        mode = (mb.data || {}).mode || '-';
        var pill = document.getElementById('modePill');
        pill.textContent = 'QR70_MODE=' + mode +
            (((mb.data || {}).source === 'override') ? '(override)' : '');
        pill.className = mode === 'assist' ? 'ok-pill'
            : (mode === 'shadow' ? 'warn-pill' : 'risk-pill');
    } catch (e) { showError(e.message); }
    try {
        var b = await fetchJson(api('/api/qr70/model/status'),
            { headers: adminHeaders() }, '模型状态');
        var d = b.data || {};
        var total = d.totalCodes || d.total || 0;
        var byK = d.byKind || {};
        var active = (byK.generated || 0) + (byK.scanned || 0);
        cells('ovStatus', [
            { k: '模式', v: mode },
            { k: '码实例总数', v: total, cls: 'blue' },
            { k: '活跃码(生成/扫描)', v: active, cls: 'green' },
            { k: '码型数(注册表)', v: d.codeTypes || d.codeCount || '-' },
            { k: '模型版本', v: d.modelVersion || '-' },
        ]);
    } catch (e) {
        cells('ovStatus', [{ k: '总览', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

/* ② 六类码分布 + ③ 生命周期(同一数据源 model/status) */
async function loadDist() {
    try {
        var b = await fetchJson(api('/api/qr70/model/status'),
            { headers: adminHeaders() }, '码域分布');
        var d = b.data || {};
        var byKind = d.byKind || d.kindCounts || {};
        var byLc = d.byLifecycle || d.lifecycleCounts || {};
        var rows = Object.keys(KIND_CN).map(function (k) {
            return { kind: k, n: byKind[k] || 0 };
        });
        if (!rows.length) { rows = [{ kind: '-', n: 0 }]; }
        document.getElementById('kindBox').innerHTML =
            '<table class="dash-table"><tr><th>码类</th><th>实例数</th>' +
            '<th>说明</th></tr>' + rows.map(function (r) {
                return '<tr><td>' + esc(KIND_CN[r.kind] || r.kind) +
                    '</td><td>' + esc(r.n) + '</td><td>' +
                    esc(kindNote(r.kind)) + '</td></tr>';
            }).join('') + '</table>';
        var lcItems = Object.keys(LIFECYCLE_CN).map(function (s) {
            return { k: LIFECYCLE_CN[s], v: byLc[s] || 0 };
        });
        if (!lcItems.length) {
            lcItems = [{ k: '生命周期', v: '-' }];
        }
        cells('lifecycleBox', lcItems);
    } catch (e) {
        document.getElementById('kindBox').innerHTML =
            '<div class="dash-empty">降级：' + esc(e.message) + '</div>';
        cells('lifecycleBox', [{ k: '漏斗', v: '降级', cls: 'red' }]);
        showError(e.message);
    }
}

function kindNote(k) {
    return {
        manage: '角色办事台(会话码)',
        auth: '无感安全链(once)',
        trace: '信任叙事(public 永不消费)',
        receiving: '三要素交付(once)',
        shipping: '仓配交接(once)',
        collect: '安心收付(once)'
    }[k] || '';
}

/* ④ 码实例列表 */
async function loadCodes() {
    var el = document.getElementById('codesBox');
    try {
        var b = await fetchJson(api('/api/qr70/codes?limit=15'),
            { headers: adminHeaders() }, '码实例');
        var list = (b.data && b.data.codes) || b.data || [];
        if (Array.isArray(list) && list.length) {
            el.innerHTML = '<table class="dash-table"><tr><th>#</th>' +
                '<th>码型</th><th>码类</th><th>场景</th><th>状态</th>' +
                '<th>生成时间</th></tr>' + list.map(function (c) {
                return '<tr><td>' + esc(c.codeSeq || '-') + '</td><td>' +
                    esc(c.codeId || '-') + '</td><td>' +
                    esc(KIND_CN[c.kind] || c.kind || '-') + '</td><td>' +
                    esc(c.scene || '-') + '</td><td>' +
                    esc(LIFECYCLE_CN[c.status] || c.status || '-') +
                    '</td><td>' + esc(when(c.generatedAt)) + '</td></tr>';
            }).join('') + '</table>';
        } else {
            el.innerHTML = '<div class="dash-empty">暂无码实例' +
                '（决策面开档后生成/业务挂接产生）</div>';
        }
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">降级：' +
            esc(e.message) + '</div>';
        showError(e.message);
    }
}

/* ⑤ 事件流 */
async function loadEvents() {
    var el = document.getElementById('eventsBox');
    try {
        var b = await fetchJson(api('/api/qr70/events?limit=15'),
            { headers: adminHeaders() }, '事件流');
        var list = (b.data && b.data.events) || b.data || [];
        if (Array.isArray(list) && list.length) {
            el.innerHTML = '<table class="dash-table"><tr><th>时间</th>' +
                '<th>类型</th><th>码型</th><th>详情摘要</th></tr>' +
                list.map(function (ev) {
                    return '<tr><td>' + esc(when(ev.at)) + '</td><td>' +
                        esc(eventCn(ev.type)) + '</td><td>' +
                        esc(ev.codeId || '-') + '</td><td>' +
                        esc(sumDetail(ev.detail)) + '</td></tr>';
                }).join('') + '</table>';
        } else {
            el.innerHTML = '<div class="dash-empty">暂无事件</div>';
        }
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">降级：' +
            esc(e.message) + '</div>';
        showError(e.message);
    }
}

function eventCn(t) {
    return {
        code_generated: '码生成', code_redeem: '码核销',
        code_voided: '码作废', code_replay_rejected: '重放拒绝',
        code_shadow_generated: 'shadow生成(留痕)',
        code_shadow_redeem: 'shadow核销(留痕)',
        daily_scan: '每日扫描', mode_override: '模式切档',
        joy_sample: '愉悦度样本', joy_drift: '漂移检测'
    }[t] || t || '-';
}

function sumDetail(d) {
    if (!d || typeof d !== 'object') { return '-'; }
    var parts = [];
    if (d.kind) { parts.push('码类:' + d.kind); }
    if (d.dryRun) { parts.push('dryRun'); }
    if (d.action) { parts.push(d.action + (d.to ? '→' + d.to : '')); }
    if (d.verifyStatus) { parts.push('验签:' + d.verifyStatus); }
    if (d.codeSnapshot && d.codeSnapshot.total != null) {
        parts.push('码总数:' + d.codeSnapshot.total);
    }
    if (d.immunityAction) { parts.push('免疫:' + d.immunityAction); }
    return parts.slice(0, 4).join(' | ') || JSON.stringify(d).slice(0, 60);
}

/* ⑥ 愉悦度统计 */
async function loadJoy() {
    var el = document.getElementById('joyBox');
    try {
        var b = await fetchJson(api('/api/qr70/joy/stats'),
            { headers: adminHeaders() }, '愉悦度统计');
        var d = b.data || {};
        var rows = d.byCodeId || d.stats || d.items || [];
        var html = '';
        if (Array.isArray(rows) && rows.length) {
            html = '<table class="dash-table"><tr><th>码型</th>' +
                '<th>样本</th><th>均耗时(ms)</th><th>完成率</th>' +
                '<th>误触率</th></tr>' + rows.map(function (r) {
                return '<tr><td>' + esc(r.codeId || '-') + '</td><td>' +
                    esc(r.samples || r.count || 0) + '</td><td>' +
                    esc(r.avgDurationMs || r.avgDuration || '-') +
                    '</td><td>' + esc(r.completionRate || r.completedRate ||
                        '-') + '</td><td>' +
                    esc(r.misTouchRate || '-') + '</td></tr>';
            }).join('') + '</table>';
        } else {
            html = '<div class="dash-empty">暂无愉悦度样本' +
                '（端侧上报积累中——快环纯统计）</div>';
        }
        try {
            var hb = await fetchJson(api('/api/qr70/joy/health'),
                { headers: adminHeaders() }, '愉悦健康');
            var hd = hb.data || {};
            html += '<div class="note-bar">进化健康: ' +
                esc(hd.status || hd.health || '-') +
                (hd.frozen ? ' · <b style="color:#c0392b">已冻结</b>' : '') +
                (hd.note ? ' · ' + esc(hd.note) : '') + '</div>';
        } catch (e2) { /* 健康摘要缺失不阻断 */ }
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">降级：' +
            esc(e.message) + '</div>';
        showError(e.message);
    }
}

/* ⑦ 免疫/红队 */
async function loadImmunity() {
    var el = document.getElementById('immunityBox');
    try {
        var b = await fetchJson(api('/api/qr70/immunity'),
            { headers: adminHeaders() }, '免疫系统');
        var d = b.data || {};
        var frozen = d.frozen || d.isFrozen || false;
        var vecs = d.redteamVectors || d.vectors || [];
        var html = '<table class="dash-table"><tr><th>冻结态</th>' +
            '<th>原因</th><th>红队向量</th></tr><tr><td>' +
            (frozen ? '<span class="risk-pill">已冻结</span>'
                : '<span class="ok-pill">运行中</span>') +
            '</td><td>' + esc(d.frozenReason || d.reason || '-') +
            '</td><td>' + esc(Array.isArray(vecs) ? vecs.length : '-') +
            ' 类</td></tr></table>';
        var runs = d.redteamRuns || d.runs || [];
        if (Array.isArray(runs) && runs.length) {
            html += '<table class="dash-table"><tr><th>时间</th>' +
                '<th>向量</th><th>结果</th></tr>' + runs.slice(0, 8)
                .map(function (r) {
                    return '<tr><td>' + esc(when(r.at)) + '</td><td>' +
                        esc(r.vector || '-') + '</td><td>' +
                        esc(r.verdict || r.status || '-') + '</td></tr>';
                }).join('') + '</table>';
        }
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">降级：' +
            esc(e.message) + '</div>';
        showError(e.message);
    }
}

/* ⑧ 模式控制 */
async function loadModeCtl() {
    var el = document.getElementById('modeBox');
    try {
        var b = await fetchJson(api('/api/qr70/mode'),
            { headers: adminHeaders() }, '模式');
        var d = b.data || {};
        el.innerHTML = '<table class="dash-table"><tr><th>当前档</th>' +
            '<th>来源</th><th>说明</th></tr><tr><td>' +
            esc(d.mode || '-') + '</td><td>' + esc(d.source || '-') +
            '</td><td>' + esc(d.note || '-') + '</td></tr></table>';
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">降级：' +
            esc(e.message) + '</div>';
        showError(e.message);
    }
}

async function setMode(mode) {
    try {
        await fetchJson(api('/api/qr70/mode/override?mode=' +
            encodeURIComponent(mode)), {
            method: 'POST', headers: adminHeaders()
        }, '切档');
        loadAll();
    } catch (e) { showError(e.message); }
}

async function runScan() {
    try {
        var b = await fetchJson(api('/api/qr70/scan/run'), {
            method: 'POST', headers: adminHeaders()
        }, '立即扫描');
        var d = b.data || {};
        var snap = d.codeSnapshot || {};
        showError('扫描完成：码总数 ' + (snap.total == null ? '-' :
            snap.total) + ' · 免疫 ' + (d.immunityAction || 'stable'));
        loadEvents();
        loadDist();
    } catch (e) { showError(e.message); }
}

async function loadAll() {
    markUpdate();
    loadStatus();
    loadDist();
    loadCodes();
    loadEvents();
    loadJoy();
    loadImmunity();
    loadModeCtl();
}

window.addEventListener('DOMContentLoaded', loadAll);
