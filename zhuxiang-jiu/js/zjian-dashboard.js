/**
 * zjian-dashboard.js · 77号·竹鉴 BambooVerify 治理看板数据层
 * 数据源: 全公开观测面 GET(游客白名单, 零鉴权只读):
 *   GET /api/zjian/mode     灰度态+护栏+full自主域
 *   GET /api/zjian/metrics  典藏统计+指标命中分布
 *   GET /api/zjian/reports  双报告典藏
 *   GET /api/zjian/asks     问答留痕
 *   GET /api/zjian/catalog  指标目录(15 项)
 */
(function () {
    'use strict';

    var KEY = 'zjianDash.apiBase';

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
        $('ovMode').innerHTML = [
            cell('灰度档位', pill, '', true),
            cell('读取来源', d.source || '-'),
            cell('决策计数 decisionSeq', fa.decisionSeq != null ? fa.decisionSeq : '-',
                mode === 'full' ? 'green' : ''),
            cell('自主巡检节流', '每 ' + (fa.autoPatrolEvery || 10) + ' 次/巡'),
            cell('护栏巡检留痕', (d.guard && d.guard.checkCount) || 0, 'blue'),
            cell('恶化暂停次数', (d.guard && d.guard.breachCount) || 0,
                (d.guard && d.guard.breachCount) ? 'red' : 'green'),
        ].join('');

        var g = d.guard || {};
        var lines = [];
        lines.push('模式域: ' + (d.modeValues || []).join(' / ')
            + '（full = 低风险自主域: '
            + ((fa.domains || []).join(', ') || '-') + '）');
        lines.push('护栏三指标: ' + ((g.metrics || []).map(function (m) {
            return m.label + '(恶化 &gt;3% 暂停——只计漏网)';
        }).join(' · ')));
        if (g.pausedAt) {
            lines.push('<span style="color:#B5453C">最近暂停: '
                + esc(g.pausedAt) + ' — '
                + esc(g.pausedReason || '') + '</span>');
        } else {
            lines.push('护栏状态: 运行中(未暂停)');
        }
        lines.push('永不自主: ' + esc(d.neverAutonomous || ''));
        lines.push('观测面永不关停: ' + esc(d.observablesNeverOff || ''));
        $('modeDetail').innerHTML = lines.join('<br>');
    }

    // ---------- ② 典藏统计 ----------

    function renderStats(d) {
        var g = d.guard || {};
        $('ovStats').innerHTML = [
            cell('总问答数', d.totalAsks || 0, 'blue'),
            cell('医疗/夸大拦截', g.blocked || 0, 'green'),
            cell('未命中引导', g.noHit || 0),
        ].join('');
    }

    // ---------- ③ 指标命中分布 ----------

    function renderMetricHits(hits) {
        var entries = Object.entries(hits || {})
            .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 8);
        $('ovMetricHits').innerHTML = entries.length
            ? entries.map(function (e) {
                return cell(e[0], e[1], 'blue');
            }).join('')
            : '<div style="color:#999;font-size:12px;padding:12px">'
              + '暂无指标命中(问答流量待积累)</div>';
    }

    // ---------- ④ 问答留痕 ----------

    function renderAsks(items) {
        var keyLabels = {
            alcohol: '酒精度', methanol: '甲醇', cyanide: '氰化物',
            lead: '铅', benzoate: '苯甲酸', sorbate: '山梨酸',
            saccharin: '糖精钠', cyclamate: '甜蜜素', so2: '二氧化硫',
            label: '标签', manganese: '锰', total_acid: '总酸',
            total_ester: '总酯', solids: '固形物', fusel_oil: '杂醇油',
        };
        var rows = (items || []).map(function (a) {
            var keys = (a.metricKeys || []).map(function (k) {
                return keyLabels[k] || k;
            }).join('、') || '-';
            var specs = (a.specIds || []).length
                ? (a.specIds.length === 2 ? '双型' : '单型')
                : '-';
            return '<tr><td>' + esc(a.askId) + '</td><td>'
                + esc(a.question) + '</td><td>' + esc(keys)
                + '</td><td>' + esc(specs) + '</td></tr>';
        });
        $('askRows').innerHTML = rows.join('')
            || '<tr><td colspan="4" style="color:#999;font-size:12px;'
            + 'text-align:center;padding:12px">暂无留痕</td></tr>';
    }

    // ---------- ⑤ 双报告典藏 ----------

    function renderReports(items) {
        var rows = (items || []).map(function (r) {
            return '<tr><td><span class="cite-tag">'
                + esc(r.reportId) + '</span></td><td>'
                + esc(r.spec) + '</td><td>' + esc(r.agency)
                + '</td><td>' + esc(r.signDate) + '</td></tr>';
        });
        $('reportRows').innerHTML = rows.join('')
            || '<tr><td colspan="4" style="color:#999;font-size:12px;'
            + 'text-align:center;padding:12px">暂无典藏</td></tr>';
    }

    // ---------- ⑥ 指标目录 ----------

    function renderCatalog(d) {
        var rows = (d.items || []).map(function (it) {
            return '<tr><td>' + esc(it.name) + '</td><td>'
                + esc(it.requirement) + '</td><td>'
                + esc(it.unit) + '</td><td>'
                + esc(it.method) + '</td></tr>';
        });
        $('catalogRows').innerHTML = rows.join('')
            || '<tr><td colspan="4" style="color:#999;font-size:12px;'
            + 'text-align:center;padding:12px">暂无数据</td></tr>';
    }

    // ---------- 装配 ----------

    function loadAll() {
        err('');
        $('apiBase').value = apiBase();
        getJSON('/api/zjian/mode').then(renderMode)
            .catch(function (e) { err('灰度态加载失败: ' + e.message); });
        getJSON('/api/zjian/metrics').then(function (d) {
            renderStats(d);
            renderMetricHits(d.metricHits);
        }).catch(function (e) { err('统计加载失败: ' + e.message); });
        getJSON('/api/zjian/asks').then(function (d) {
            renderAsks(d.items);
        }).catch(function (e) { err('留痕加载失败: ' + e.message); });
        getJSON('/api/zjian/reports').then(function (d) {
            renderReports(d.items);
        }).catch(function (e) { err('典藏加载失败: ' + e.message); });
        getJSON('/api/zjian/catalog').then(renderCatalog)
            .catch(function (e) { err('目录加载失败: ' + e.message); });
        $('lastUpdate').textContent = '更新于 '
            + new Date().toLocaleTimeString();
    }

    window.saveConn = saveConn;
    window.loadAll = loadAll;

    loadAll();
})();
