/* 71号·AI智能支付端口大模型 v1.0 · 端口运营工作台脚本
 * 页面: ai-payport-dashboard.html
 * 鉴权: Auth.apiHeaders() Bearer 叠加(46+1 后 X-Role 裸头被剥,
 *       登录后的真实 Bearer 才是唯一有效轨——同 entry-dashboard 惯例)
 * localStorage 键: payportDash.apiBase
 * 三区: 观测(ports/panorama/model/dicts) 红队(immunity/redteam)
 *       治理(evolution governance/audit tracegraph)
 * 渲染铁律: 后端字段不做假设——通用 kv 网格 + 深层结构 raw-json
 *           折叠展示(字段演进不炸前端); 409/403 以提示语义呈现
 */
'use strict';

var API_BASE_KEY = 'payportDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY)
        || (location.hostname === 'localhost'
            || location.hostname === '127.0.0.1'
            ? 'http://localhost:8000' : ''),
};

function $(id) { return document.getElementById(id); }

function headers(extra) {
    var h = {};
    var auth = (typeof Auth !== 'undefined') ? Auth.apiHeaders() : null;
    if (auth) { Object.assign(h, auth); }
    else { h['X-Role'] = 'admin'; }
    return Object.assign(h, extra || {});
}

async function api(method, path, body) {
    var opts = { method: method, headers: headers() };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    var resp = await fetch(state.apiBase + '/api/pay71' + path, opts);
    var data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!resp.ok) {
        var msg = (data && (data.detail || data.error)) || ('HTTP ' + resp.status);
        var err = new Error(msg);
        err.status = resp.status;
        throw err;
    }
    return data;
}

function showError(msg) {
    var el = $('errorBanner');
    el.textContent = '数据加载失败：' + msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 9000);
}

function showInfo(msg) {
    var el = $('infoBanner');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}

function stamp() {
    $('lastUpdate').textContent = '已刷新 ' + new Date().toLocaleTimeString();
}

/* 通用渲染: 浅层标量/计数 → kv 网格; 数组/深层结构 → 折叠 raw-json */
function renderMixed(el, data, title) {
    var keys = data && typeof data === 'object' ? Object.keys(data) : [];
    if (!keys.length) {
        el.innerHTML = '<div class="dash-empty">无数据</div>';
        return;
    }
    var cells = '', raws = '';
    keys.forEach(function (k) {
        var v = data[k];
        if (v === null || typeof v === 'string'
            || typeof v === 'number' || typeof v === 'boolean') {
            var shown = (v === null || v === '') ? '—' : String(v);
            cells += '<div class="kv-cell"><b>' + esc(shown)
                + '</b><span>' + esc(k) + '</span></div>';
        } else if (Array.isArray(v)) {
            raws += rawBlock(k, v);
        } else if (typeof v === 'object') {
            var sub = Object.keys(v);
            var allScalar = sub.every(function (s) {
                return v[s] === null || typeof v[s] !== 'object';
            });
            if (allScalar && sub.length <= 16) {
                sub.forEach(function (s) {
                    var sv = (v[s] === null || v[s] === '') ? '—' : String(v[s]);
                    cells += '<div class="kv-cell"><b>' + esc(sv)
                        + '</b><span>' + esc(k + '.' + s) + '</span></div>';
                });
            } else {
                raws += rawBlock(k, v);
            }
        }
    });
    el.innerHTML = (title ? '<div class="kv-grid" style="padding-top:2px"></div>' : '')
        + (cells ? '<div class="kv-grid">' + cells + '</div>' : '')
        + raws;
}

function rawBlock(k, v) {
    return '<div style="margin-top:10px"><span style="font-size:12px;color:var(--color-text-light)">'
        + esc(k) + '</span><pre class="raw-json">'
        + esc(JSON.stringify(v, null, 1)) + '</pre></div>';
}

function unwrap(data) {
    if (data && typeof data === 'object' && 'data' in data
        && Object.keys(data).length <= 3) {
        return data.data || data;
    }
    return data;
}

/* ---------- ① 端口池总览 ---------- */
async function loadPorts() {
    var el = $('secPorts');
    el.innerHTML = '<div class="dash-empty">加载中…</div>';
    try {
        var ports = unwrap(await api('GET', '/ports'));
        var rows = '', list = [];
        if (Array.isArray(ports)) { list = ports; }
        else if (ports && typeof ports === 'object') {
            list = Object.keys(ports).map(function (k) {
                return Object.assign({ portId: k }, ports[k] || {});
            });
        }
        if (list.length) {
            rows = '<table class="kv-table"><tr><th>端口</th><th>状态</th><th>详情</th></tr>';
            list.forEach(function (p) {
                var st = p.state || p.portState || p.status || '';
                var badge = st === 'healthy' ? 'green'
                    : st === 'degraded' ? 'yellow'
                    : st === 'broken' ? 'red' : 'weak';
                rows += '<tr><td>' + esc(p.portId || p.port || '?')
                    + '</td><td><span class="badge ' + badge + '">'
                    + esc(st || '—') + '</span></td><td style="white-space:nowrap;max-width:420px;overflow:hidden;text-overflow:ellipsis">'
                    + esc(JSON.stringify(p).slice(0, 160)) + '</td></tr>';
            });
            rows += '</table>';
            el.innerHTML = rows;
        } else {
            renderMixed(el, ports);
        }
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">端口池加载失败: '
            + esc(e.message) + '(403=未登录 admin; 后端未启动?)</div>';
        throw e;
    }
}

/* ---------- ② 全景 + 模型状态 ---------- */
async function loadModel() {
    var el = $('secModel');
    el.innerHTML = '<div class="dash-empty">加载中…</div>';
    try {
        var pano = unwrap(await api('GET', '/panorama'));
        var status = unwrap(await api('GET', '/model/status'));
        var half = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
        half += '<div><div style="font-size:12px;font-weight:700;color:var(--color-primary-dark);margin:6px 0 2px">健康全景(聚合 69号)</div></div>';
        half += '<div><div style="font-size:12px;font-weight:700;color:var(--color-primary-dark);margin:6px 0 2px">模型状态</div></div>';
        half += '</div>';
        el.innerHTML = half;
        renderInto(el.children[0].children[0], pano);
        renderInto(el.children[0].children[1], status);
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">加载失败: ' + esc(e.message) + '</div>';
        throw e;
    }
}

function renderInto(host, data) {
    var box = document.createElement('div');
    host.appendChild(box);
    renderMixed(box, unwrap(data));
}

/* ---------- ③ 字典四联 ---------- */
async function loadDicts() {
    var el = $('secDicts');
    el.innerHTML = '<div class="dash-empty">加载中…</div>';
    var sections = [
        ['自愈字典(前兆信号/保护方向)', '/selfheal/dict'],
        ['调配字典(四维/情境/帕累托)', '/allocation/dict'],
        ['预判字典(意图/免密/拆分)', '/predict/dict'],
        ['对账字典(核验/差错/幂等域)', '/recon/dict'],
    ];
    el.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px">'
        + sections.map(function () { return '<div></div>'; }).join('') + '</div>';
    var boxes = el.firstElementChild.children;
    var errs = 0;
    for (var i = 0; i < sections.length; i++) {
        var title = document.createElement('div');
        title.style.cssText = 'font-size:12px;font-weight:700;color:var(--color-primary-dark);margin:6px 0 2px';
        title.textContent = sections[i][0];
        boxes[i].appendChild(title);
        try {
            renderInto(boxes[i], await api('GET', sections[i][1]));
        } catch (e) { errs++; renderInto(boxes[i], { 错误: e.message }); }
    }
    if (errs) { showInfo(errs + ' 个字典加载失败(可能后端未启动或未登录)'); }
}

/* ---------- ④ 红队防御区 ---------- */
async function loadImmunity() {
    var el = $('secImmunity');
    el.innerHTML = '<div class="dash-empty">加载中…</div>';
    try {
        var imm = unwrap(await api('GET', '/immunity'));
        var runs = null;
        try { runs = unwrap(await api('GET', '/immunity/redteam/runs')); }
        catch (e2) { runs = { 提示: '红队历史不可读(' + e2.message + ')' }; }
        el.innerHTML = '<div style="font-size:12px;font-weight:700;color:var(--color-primary-dark);margin:6px 0 2px">免疫视图(冻结/监控状态)</div>';
        renderInto(el, imm);
        var t = document.createElement('div');
        t.style.cssText = 'font-size:12px;font-weight:700;color:var(--color-primary-dark);margin:12px 0 2px';
        t.textContent = '红队批次历史';
        el.appendChild(t);
        renderInto(el, runs);
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">免疫视图加载失败: ' + esc(e.message) + '</div>';
        throw e;
    }
}

async function triggerRedteam() {
    showInfo('红队四向量执行中(确定性攻击样本)…');
    try {
        var r = unwrap(await api('POST', '/immunity/redteam', {}));
        var vectors = r.vectors || [];
        var rows = '<table class="kv-table"><tr><th>向量</th><th>攻击样本</th><th>防御</th></tr>';
        vectors.forEach(function (v) {
            var d = v.defended === true;
            rows += '<tr><td>' + esc(v.vector || '?')
                + '</td><td style="white-space:normal">' + esc((v.attacks || []).length) + ' 样本'
                + '</td><td><span class="badge ' + (d ? 'green' : 'red') + '">'
                + (d ? '全防御' : '失守') + '</span></td></tr>';
        });
        rows += '</table>';
        $('secImmunity').insertAdjacentHTML('afterbegin',
            '<div style="font-size:12px;font-weight:700;color:#c0392b;margin:6px 0 2px">本次红队执行结果</div>' + rows);
        showInfo('红队执行完成: allDefended='
            + vectors.every(function (v) { return v.defended === true; }));
        stamp();
    } catch (e) {
        if (e.status === 409) {
            showError('红队触发被拒(HTTP 409): 决策面未开放——容器需 PAY71_MODE=shadow/assist(off 态属正常拦截)');
        } else {
            showError('红队触发失败: ' + e.message);
        }
    }
}

/* ---------- ⑤ 治理区 ---------- */
async function loadGov() {
    var el = $('secGov');
    el.innerHTML = '<div class="dash-empty">加载中…</div>';
    try {
        var gov = unwrap(await api('GET', '/evolution/governance'));
        var drift = null, trace = null;
        try { drift = unwrap(await api('GET', '/evolution/dict')); }
        catch (e2) { drift = { 提示: e2.message }; }
        try { trace = unwrap(await api('GET', '/audit/tracegraph')); }
        catch (e2) { trace = { 提示: e2.message }; }
        el.innerHTML = '<div style="font-size:12px;font-weight:700;color:#7b3fa0;margin:6px 0 2px">进化治理视图(分级/冻结/假设)</div>';
        renderInto(el, gov);
        var t1 = document.createElement('div');
        t1.style.cssText = 'font-size:12px;font-weight:700;color:#7b3fa0;margin:12px 0 2px';
        t1.textContent = '进化字典';
        el.appendChild(t1);
        renderInto(el, drift);
        var t2 = document.createElement('div');
        t2.style.cssText = 'font-size:12px;font-weight:700;color:#7b3fa0;margin:12px 0 2px';
        t2.textContent = '决策链路图(五域只读导出)';
        el.appendChild(t2);
        renderInto(el, trace);
    } catch (e) {
        el.innerHTML = '<div class="dash-empty">治理视图加载失败: ' + esc(e.message) + '</div>';
        throw e;
    }
}

/* ---------- 总装 ---------- */
async function loadAll() {
    state.apiBase = ($('apiBaseInput').value || '').trim() || state.apiBase;
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    var firstErr = null;
    var loaders = [loadPorts, loadModel, loadDicts, loadImmunity, loadGov];
    for (var i = 0; i < loaders.length; i++) {
        try { await loaders[i](); }
        catch (e) { if (!firstErr) { firstErr = e; } }
    }
    if (firstErr) {
        showError(firstErr.message
            + (firstErr.status === 403
                ? '(403: 请先点右上「管理员登录」获取 Bearer——46+1 后裸头已失效)' : ''));
    } else {
        showInfo('五区加载完成');
    }
    stamp();
}

document.addEventListener('DOMContentLoaded', function () {
    $('apiBaseInput').value = state.apiBase || '';
    $('btnLoadAll').addEventListener('click', loadAll);
    $('btnLogin').addEventListener('click', function () {
        if (typeof Auth !== 'undefined' && Auth.login) { Auth.login(); }
        else { showInfo('auth.js 未加载或无登录入口, 请检查部署'); }
    });
    $('btnRedteam').addEventListener('click', triggerRedteam);
    Array.prototype.forEach.call(
        document.querySelectorAll('button[data-sec]'),
        function (btn) {
            btn.addEventListener('click', function () {
                var map = { ports: loadPorts, model: loadModel,
                            dicts: loadDicts, immunity: loadImmunity, gov: loadGov };
                var fn = map[btn.getAttribute('data-sec')];
                if (fn) { fn().then(stamp).catch(function (e) { showError(e.message); }); }
            });
        });
    loadAll();
});
