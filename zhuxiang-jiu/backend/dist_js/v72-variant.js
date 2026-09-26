/* v72 落地页变体渲染 · SPA 通用 chunk(72号 P4)
 * ============================================================
 * 背景: /r/{code} 302 落地 URL 携 v72=trust_first/
 *   benefit_first(72号 P4 变体决策留痕)——Taro H5 SPA
 *   (注册页/产品页等)统一在此渲染差异化横幅;
 *   activity.html 自包含页有自己的内联版(勿重复注入)。
 * 注入: index.html <script defer src="/js/v72-variant.js?v=1">
 * 渲染: #app 前 flow 式插入(position static, 随页滚动,
 *   不与 Taro fixed 导航冲突); default/无参数零 DOM 痕迹。
 * 缓存: /js/ immutable 30d——内容变更必须 bump ?v=N
 * ============================================================ */
(function () {
    'use strict';
    var m = location.search.match(/[?&]v72=([a-z_]+)/);
    if (!m) { return; }
    var variant = m[1];
    if (!variant || variant === 'default') { return; }
    var CONF = {
        trust_first: {
            icon: '🛡',
            label: '品牌直供 · 官方正品',
            sub: '一物一码防伪查验 · 县区网点联保 · 放心选购',
            border: 'rgba(74,124,89,.5)', bg: 'rgba(74,124,89,.07)',
            color: '#355c44',
        },
        benefit_first: {
            icon: '🎁',
            label: '会员专享 · 下单有礼',
            sub: '会员价直降 · 积分抵现 · 下单赢好礼',
            border: 'rgba(201,169,97,.55)', bg: 'rgba(201,169,97,.08)',
            color: '#8a6d2f',
        },
    };
    var c = CONF[variant];
    if (!c) { return; }

    function render() {
        if (document.getElementById('v72Strip')) { return; }
        var app = document.getElementById('app');
        if (!app || !app.parentNode) { return; }
        var strip = document.createElement('div');
        strip.id = 'v72Strip';
        strip.style.cssText =
            'box-sizing:border-box;max-width:680px;margin:0 auto;'
            + 'padding:10px 12px;border:1px dashed ' + c.border + ';'
            + 'background:' + c.bg + ';border-radius:8px;'
            + 'font-size:13px;color:' + c.color + ';display:flex;'
            + 'align-items:center;gap:8px;';
        strip.innerHTML = '<span style="font-size:16px">' + c.icon
            + '</span><b>' + c.label + '</b>'
            + '<span style="font-size:11.5px;color:#6b6b6b">'
            + c.sub + '</span>';
        app.parentNode.insertBefore(strip, app);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', render);
    } else {
        render();
    }
})();
