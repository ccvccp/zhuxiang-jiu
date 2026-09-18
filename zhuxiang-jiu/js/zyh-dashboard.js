/**
 * zyh-dashboard.js · 75号·竹韵·智衡治理看板数据层
 * ============================================================
 * 数据源: 全公开观测面 GET(游客白名单, 零鉴权只读):
 *   GET /api/zyh/mode      灰度态+护栏+full自主域
 *   GET /api/zyh/stats     守门拦截分布+语义缓存
 *   GET /api/zyh/qa        问答留痕(最近N条)
 *   GET /api/zyh/knowledge 知识内核(8条事实锚点)
 *   GET /api/zyh/graph     工艺图谱(节点+关系)
 * 设计: 手动刷新(与 45号 trust-dashboard 同范式)——
 *   观测面只读, 无任何管理面写操作(永不自主红线公示为文案)。
 * ============================================================
 */
(function () {
    'use strict';

    var KEY = 'zyhDash.apiBase';

    function $(id) { return document.getElementById(id); }

    function apiBase() {
        return localStorage.getItem(KEY)
            || localStorage.getItem('knowledgeDash.apiBase')
            || localStorage.getItem('aiLearningDash.apiBase')
            || 'http://localhost:8000';
    }

    function err(msg) {
        var bar = $('errBar');
        if (!msg) { bar.style.display = 'none'; return; }
        bar.textContent = msg;
        bar.style.display = 'block';
    }

    function getJSON(path) {
        return fetch(apiBase() + path).then(function (r) {
            if (!r.ok) { throw new Error(path + ' → HTTP ' + r.status); }
            return r.json();
        });
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function cell(k, v, cls, raw) {
        var val = raw ? v : esc(v);
        return '<div class="ov-cell"><div class="k">' + esc(k)
            + '</div><div class="v ' + (cls || '') + '">'
            + val + '</div></div>';
    }

    function saveConn() {
        var v = $('apiBase').value.trim();
        if (v) { localStorage.setItem(KEY, v); }
        loadAll();
    }

    // ---------- ① 灰度态与护栏 ----------

    function renderMode(d) {
        var mode = d.mode || '?';
        var pill = '<span class="grade-pill ' + esc(mode) + '">'
            + esc(mode.toUpperCase()) + '</span>';
        var paused = d.paused;
        if (paused) { pill += ' <span class="grade-pill paused">GUARD_PAUSED</span>'; }
        var fa = d.fullAutonomy || {};
        var cells = [
            cell('灰度档位', pill, '', true),
            cell('读取来源', d.source || '-'),
            cell('决策计数 decisionSeq', fa.decisionSeq != null ? fa.decisionSeq : '-',
                mode === 'full' ? 'green' : ''),
            cell('自主巡检节流', '每 ' + (fa.autoPatrolEvery || 10) + ' 次/巡'),
            cell('护栏巡检留痕', (d.guard && d.guard.checkCount) || 0, 'blue'),
            cell('恶化暂停次数', (d.guard && d.guard.breachCount) || 0,
                (d.guard && d.guard.breachCount) ? 'red' : 'green'),
        ];
        $('ovMode').innerHTML = cells.join('');

        var g = d.guard || {};
        var lines = [];
        lines.push('模式域: ' + (d.modeValues || []).join(' / ')
            + '（full = 低风险自主域: '
            + ((fa.domains || []).join(', ') || '-') + '）');
        lines.push('护栏三指标: ' + ((g.metrics || []).map(function (m) {
            return m.label + '(阈值恶化 &gt;3%)';
        }).join(' · ')));
        if (g.pausedAt) {
            lines.push('<span style="color:#c0392b">最近暂停: '
                + esc(g.pausedAt) + ' — ' + esc(g.pausedReason || '') + '</span>');
        } else {
            lines.push('护栏状态: 运行中(未暂停)');
        }
        lines.push('永不自主: ' + esc(d.neverAutonomous || ''));
        lines.push('观测面永不关停: ' + esc(d.observablesNeverOff || ''));
        $('modeDetail').innerHTML = lines.join('<br>');
    }

    // ---------- ②③ 守门分布 + 缓存 ----------

    function renderStats(d) {
        var g = d.guard || {};
        var total = d.totalRequests || 0;
        $('ovGuard').innerHTML = [
            cell('总问答请求', total, 'blue'),
            cell('L1 旧工艺表述', g.l1Craft || 0, (g.l1Craft ? 'red' : '')),
            cell('L1 医疗/注入', (g.l1Medical || 0) + ' / ' + (g.l1Injection || 0)),
            cell('L2 工艺混淆', g.l2CraftConfusion || 0, (g.l2CraftConfusion ? 'red' : '')),
            cell('L2 他企等同化', g.l2Equivalence || 0, (g.l2Equivalence ? 'red' : '')),
            cell('L2 医疗/无引用', (g.l2Medical || 0) + ' / ' + (g.l2Uncited || 0)),
            cell('L3 溯源拦截', g.l3Block || 0, (g.l3Block ? 'yellow' : '')),
        ].join('');

        var c = d.cache || {};
        $('ovCache').innerHTML = [
            cell('命中率', (c.hitRate != null ? (c.hitRate * 100).toFixed(1) + '%' : '-'),
                c.hitRate >= 0.5 ? 'green' : 'yellow'),
            cell('命中 hit', c.hit || 0, 'green'),
            cell('未命中 miss', c.miss || 0),
            cell('缓存条目', c.entries || 0, 'blue'),
        ].join('');
    }

    // ---------- ④ 知识内核 ----------

    function renderKnowledge(d) {
        var items = d.items || [];
        var rows = items.map(function (it) {
            var cites = (it.citations || []).map(function (c) {
                return '<span class="cite-tag">' + esc(c.type) + ': '
                    + esc(c.id) + '</span>';
            }).join('') || '-';
            return '<tr><td>' + esc(it.id) + '</td><td>'
                + esc(it.title) + '</td><td>' + cites + '</td></tr>';
        });
        $('knowledgeRows').innerHTML = rows.join('')
            || '<tr><td colspan="3" class="dash-empty">无数据</td></tr>';
    }

    // ---------- ⑤ 工艺图谱 ----------

    function renderGraph(d) {
        var nodes = d.nodes || [];
        var edges = d.edges || [];
        $('ovGraph').innerHTML = [
            cell('节点数', d.nodeCount != null ? d.nodeCount : nodes.length, 'blue'),
            cell('关系数', d.edgeCount != null ? d.edgeCount : edges.length, 'blue'),
        ].join('');
        var lines = edges.map(function (e) {
            var meta = e[3] ? '(' + e[3] + ')' : '';
            return esc(e[0]) + ' —' + esc(e[1]) + '→ ' + esc(e[2]) + meta;
        });
        $('graphEdges').innerHTML = lines.join('<br>');
    }

    // ---------- ⑥ 问答留痕 ----------

    function renderQa(d) {
        var items = d.items || [];
        var layerLabel = { 0: '放行', 1: 'L1 拦截', 2: 'L2 拦截', 3: 'L3 拦截' };
        var rows = items.map(function (q) {
            var layer = q.layer != null ? q.layer : '-';
            var pill = layer === 0
                ? '<span style="color:#2f9e44">' + (layerLabel[0]) + '</span>'
                : '<span style="color:#c0392b">' + (layerLabel[layer] || layer) + '</span>';
            return '<tr><td>' + esc(q.qaId) + '</td><td>'
                + esc(q.prompt) + '</td><td>' + pill + '</td><td>'
                + esc(q.knowledgeId || '-') + '</td><td>'
                + (q.cacheHit ? '✓ hit' : 'miss') + '</td></tr>';
        });
        $('qaRows').innerHTML = rows.join('')
            || '<tr><td colspan="5" class="dash-empty">暂无留痕</td></tr>';
    }

    // ---------- 装配 ----------

    function loadQa() {
        var limit = $('qaLimit').value || 10;
        getJSON('/api/zyh/qa?limit=' + limit)
            .then(renderQa)
            .catch(function (e) { err('QA 留痕加载失败: ' + e.message); });
    }

    function loadAll() {
        err('');
        $('apiBase').value = apiBase();
        getJSON('/api/zyh/mode').then(renderMode)
            .catch(function (e) { err('灰度态加载失败: ' + e.message); });
        getJSON('/api/zyh/stats').then(renderStats)
            .catch(function (e) { err('统计加载失败: ' + e.message); });
        getJSON('/api/zyh/knowledge').then(renderKnowledge)
            .catch(function (e) { err('知识内核加载失败: ' + e.message); });
        getJSON('/api/zyh/graph').then(renderGraph)
            .catch(function (e) { err('图谱加载失败: ' + e.message); });
        loadQa();
        $('lastUpdate').textContent = '更新于 ' + new Date().toLocaleTimeString();
    }

    // 导出(页面 onclick 调用)
    window.saveConn = saveConn;
    window.loadAll = loadAll;
    window.loadQa = loadQa;

    loadAll();
})();
