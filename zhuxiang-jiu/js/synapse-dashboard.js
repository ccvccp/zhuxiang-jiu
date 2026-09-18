/**
 * synapse-dashboard.js · 76号·织智 Synapse-Weave 治理看板数据层
 * ============================================================
 * 数据源: 全公开观测面 GET(游客白名单, 零鉴权只读):
 *   GET /api/synapse/mode      灰度态+护栏+full自主域
 *   GET /api/synapse/metrics   织造统计(护栏/路由/反馈)
 *   GET /api/synapse/hotspots  热点源
 *   GET /api/synapse/evolution 织补史+黄金语料计数
 *   GET /api/synapse/diary     织智日记
 * 设计: 手动刷新(与 zyh-dashboard 同范式)——观测面只读,
 *   无管理面写操作(永不自主红线为文案公示)。
 * ============================================================
 */
(function () {
    'use strict';

    var KEY = 'synapseDash.apiBase';

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
        if (d.paused) {
            pill += ' <span class="grade-pill paused">GUARD_PAUSED</span>';
        }
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
            return m.label + '(恶化 &gt;3% 暂停——只计漏网)';
        }).join(' · ')));
        if (g.pausedAt) {
            lines.push('<span style="color:#c0392b">最近暂停: '
                + esc(g.pausedAt) + ' — '
                + esc(g.pausedReason || '') + '</span>');
        } else {
            lines.push('护栏状态: 运行中(未暂停)');
        }
        lines.push('永不自主: ' + esc(d.neverAutonomous || ''));
        lines.push('观测面永不关停: ' + esc(d.observablesNeverOff || ''));
        $('modeDetail').innerHTML = lines.join('<br>');
    }

    // ---------- ② Router 分布 ----------

    function renderRouter(router) {
        var labels = {
            reasoning: '推理域', empathy: '情感域',
            creative: '创意域', compliance: '合规域',
            general: '通用域',
        };
        var cells = Object.keys(labels).map(function (k) {
            return cell(labels[k], router[k] || 0,
                k === 'reasoning' ? 'blue'
                : k === 'empathy' ? 'yellow' : '');
        });
        $('ovRouter').innerHTML = cells.join('');
    }

    // ---------- ③ 织造统计 ----------

    function renderStats(d) {
        var g = d.guard || {};
        var fb = d.feedback || {};
        $('ovStats').innerHTML = [
            cell('总织造数', d.totalWeaves || 0, 'blue'),
            cell('事实失真计数', g.factFail || 0,
                g.factFail ? 'red' : 'green'),
            cell('人格漂移(漏网)', g.personaFail || 0,
                g.personaFail ? 'red' : 'green'),
            cell('合规漏网', g.complianceHit || 0,
                g.complianceHit ? 'red' : 'green'),
            cell('👍/👎 反馈', (fb.like || 0) + ' / ' + (fb.dislike || 0)),
            cell('重织/追问', (fb.rewrite || 0) + ' / '
                + (fb.follow_up || 0)),
        ].join('');
    }

    // ---------- ④ 热点源 ----------

    function renderHotspots(d) {
        var rows = (d.items || []).map(function (h) {
            return '<tr><td>' + esc(h.hotspotId) + '</td><td>'
                + esc(h.title) + '</td><td>'
                + esc(h.sourceDate || '-') + '</td></tr>';
        });
        $('hotspotRows').innerHTML = rows.join('')
            || '<tr><td colspan="3" class="dash-empty">暂无热点</td></tr>';
    }

    // ---------- ⑤ 织补式进化 ----------

    function renderEvolution(d) {
        $('ovEvolution').innerHTML = [
            cell('织补记录数', (d.patches || []).length, 'blue'),
            cell('黄金语料数', d.corpusTotal || 0, 'green'),
        ].join('');
        var rows = (d.patches || []).map(function (p) {
            var rate = Math.round((p.stitchRate || 0) * 100);
            return '<tr><td>' + esc(p.patchId) + '</td><td>'
                + esc(p.taskType) + '</td><td>'
                + esc(p.repairRequested || 0) + ' 请求/'
                + esc((p.repaired || []).length) + ' 成功</td><td>'
                + rate + '%</td><td>'
                + (p.recommendation === 'crystallize'
                    ? '<span style="color:#2f9e44">建议结晶</span>'
                    : '<span style="color:#a37400">回滚审查</span>')
                + '</td></tr>';
        });
        $('patchRows').innerHTML = rows.join('')
            || '<tr><td colspan="5" class="dash-empty">暂无织补记录</td></tr>';
    }

    // ---------- ⑥ 织智日记 ----------

    function loadDiary() {
        getJSON('/api/synapse/diary').then(function (d) {
            var diary = (d || {}).diary || {};
            $('diaryBox').textContent = diary.diaryText
                || '今日日记尚未生成。';
        }).catch(function (e) {
            err('日记加载失败: ' + e.message);
        });
    }

    // ---------- 装配 ----------

    function loadAll() {
        err('');
        $('apiBase').value = apiBase();
        getJSON('/api/synapse/mode').then(renderMode)
            .catch(function (e) { err('灰度态加载失败: ' + e.message); });
        getJSON('/api/synapse/metrics').then(function (d) {
            renderRouter(d.router || {});
            renderStats(d);
        }).catch(function (e) { err('统计加载失败: ' + e.message); });
        getJSON('/api/synapse/hotspots').then(renderHotspots)
            .catch(function (e) { err('热点加载失败: ' + e.message); });
        getJSON('/api/synapse/evolution').then(renderEvolution)
            .catch(function (e) { err('进化史加载失败: ' + e.message); });
        loadDiary();
        $('lastUpdate').textContent = '更新于 '
            + new Date().toLocaleTimeString();
    }

    // 导出(页面 onclick 调用)
    window.saveConn = saveConn;
    window.loadAll = loadAll;
    window.loadDiary = loadDiary;

    loadAll();
})();
