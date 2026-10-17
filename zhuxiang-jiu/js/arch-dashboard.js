/* 竹智·Arch-Agent 观测页数据层(对齐 T2 观测页范式: live+回落) */
(function () {
    'use strict';
    function apiBase() {
        return localStorage.getItem('archDash.apiBase')
            || localStorage.getItem('knowledgeDash.apiBase')
            || '';
    }
    async function getJSON(path) {
        var r = await fetch(apiBase() + path);
        var b = await r.json();
        if (!r.ok || !b.success) throw new Error((b && b.error) || ('HTTP ' + r.status));
        return b.data;
    }
    var MODE_LABEL = { off: '关', shadow: '留痕', assist: '辅助', full: '全量', observe: '观察' };
    function badge(mode, degraded) {
        if (degraded) return '<span class="badge weak">离线</span>';
        return '<span class="badge ' + (mode === 'off' ? 'weak' : 'green') + '">' +
            (MODE_LABEL[mode] || mode || '--') + '</span>';
    }
    function fallback() {
        document.getElementById('bambooLabel').textContent = '观测面暂不可达 · 点击「手动刷新」重试';
        document.getElementById('bambooEmoji').textContent = '🌫️';
        document.getElementById('bambooDesc').textContent = '';
        document.getElementById('moduleGrid').innerHTML =
            '<div class="arch-card"><div class="m">--</div><div class="l">--</div></div>';
    }
    async function load() {
        var eb = document.getElementById('errBar');
        eb.style.display = 'none';
        try {
            var d = await getJSON('/api/arch/observe');
            document.getElementById('bambooEmoji').textContent = d.bamboo.emoji;
            document.getElementById('bambooLabel').textContent = d.bamboo.label;
            document.getElementById('bambooDesc').textContent = d.bamboo.desc;
            document.getElementById('stTotal').textContent = d.total;
            document.getElementById('stHealthy').textContent = d.healthy;
            document.getElementById('stDegraded').textContent = d.bamboo.degradedCount;
            document.getElementById('stPaused').textContent = d.bamboo.pausedCount;
            var html = '';
            (d.modules || []).forEach(function (m) {
                html += '<div class="arch-card"><div class="m">' + m.module + ' ' +
                    badge(m.mode, m.degraded) + '</div><div class="l">' +
                    (m.label || '') + '</div></div>';
            });
            document.getElementById('moduleGrid').innerHTML = html || '<div class="arch-card"><div class="m">--</div></div>';
            document.getElementById('lastUpdate').textContent = '更新于 ' + String(d.collectedAt || '').slice(0, 19).replace('T', ' ');
        } catch (e) {
            document.getElementById('errBar').textContent = '加载失败: ' + e.message;
            document.getElementById('errBar').style.display = 'block';
            fallback();
        }
    }
    window.ArchDash = { load: load };
    load();
})();
