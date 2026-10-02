/**
 * 智运看板⑦风控区块渲染单元验证
 * 用生产真实 risk_assess 样本(node 环境)跑渲染核心逻辑,
 * 检查输出无 undefined/NaN/[object。
 */
'use strict';

/* 生产实测样本(zw_risk_check.py 输出的结构) */
var rows = [
    { riskId: 4, riskScore: 40.7, riskLevel: 'medium',
      damageScore: 52.0, lossScore: 50.0, delayScore: 25.0,
      factors: ['偏远地区(新疆)+30', '高货值 ¥1200+20'],
      suggestions: ['偏远地区: 建议预留 +2 天时效并提前告知用户'],
      assessedAt: '2026-10-02T23:17:21' },
    { riskId: 3, riskScore: 60.5, riskLevel: 'high',
      damageScore: 60.0, lossScore: 55.0, delayScore: 30.0,
      factors: [], suggestions: ['高风险单: 建议加固包装(木架/双层气泡膜)并足额保价'],
      assessedAt: '2026-09-15T16:12:10' },
    { riskId: 2, riskScore: 60.5, riskLevel: 'high',
      damageScore: 60.0, lossScore: 55.0, delayScore: 30.0,
      factors: [], suggestions: [],
      assessedAt: '2026-09-15T16:12:10' },
];

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}
function when(s) { return String(s || '').replace('T', ' ').slice(5, 16); }

/* === loadRisks 渲染核心(与 js/zhiyun-dashboard.js 同步) === */
var LEVEL = { extreme: ['极高', 'risk-pill'], high: ['高', 'risk-pill'],
              medium: ['中', 'warn-pill'], low: ['低', 'ok-pill'] };
var html = rows.map(function (r) {
    var lv = LEVEL[r.riskLevel] || [r.riskLevel || '-', 'gray-pill'];
    var scoreCls = r.riskScore >= 60 ? 'red'
        : (r.riskScore >= 40 ? '' : 'green');
    return '<tr><td>#' + esc(r.riskId) + '</td>' +
        '<td class="' + scoreCls + '"><b>' + esc(r.riskScore) + '</b></td>' +
        '<td><span class="' + lv[1] + '">' + lv[0] + '</span></td>' +
        '<td>' + esc(r.damageScore) + '</td>' +
        '<td>' + esc(r.lossScore) + '</td>' +
        '<td>' + esc(r.delayScore) + '</td>' +
        '<td>' + esc((r.suggestions || []).join('；').slice(0, 40)) + '</td>' +
        '<td>' + when(r.assessedAt) + '</td></tr>';
}).join('');

/* === 断言 === */
var fails = 0;
function chk(name, ok) { console.log((ok ? '  ✓ ' : '  ✗ ') + name);
    if (!ok) fails++; }

chk('无 undefined', html.indexOf('undefined') === -1);
chk('无 NaN', html.indexOf('NaN') === -1);
chk('无 [object', html.indexOf('[object') === -1);
chk('等级中文映射', html.indexOf('>中<') > -1 && html.indexOf('>高<') > -1);
chk('时间格式 10-02 23:17', html.indexOf('10-02 23:17') > -1);
chk('缓解建议渲染', html.indexOf('预留 +2 天时效') > -1);
chk('空建议渲染为空串(非null)',
    rows[2].suggestions.length === 0
    && html.indexOf('null') === -1);
chk('行数=3', (html.match(/<tr>/g) || []).length === 3);

console.log('----');
console.log(fails === 0 ? '全部通过' : fails + ' 项失败');
process.exit(fails ? 1 : 0);
