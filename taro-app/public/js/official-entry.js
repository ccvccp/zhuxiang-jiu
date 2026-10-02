/*! official-entry.js v1 · 官网 PC 首页入口(商城 SPA 左下浮标)
 * 背景: 2026-10-02 官网 PC 首页以 official.html 上线, nginx 已做
 *       PC 裸根分流; 移动端商城保持主场——此浮标给商城访客一个
 *       官网直达入口(品牌故事/同盟臻选/竹奕佳酿)。
 * 位置: 左下角(右下已被 xiaozhu 语音浮球占用, 顶部为 IP 情景条);
 *       iPhone 底部安全区适配 env(safe-area-inset-bottom)。
 * 注入: src/index.html 模板 + 生产 dist/index.html(免重建直接生效,
 *       下次 Taro 构建经 public/ copy 出包不丢——裸 dist 事故铁律)。
 */
(function () {
  if (document.getElementById('zxjiu-official-entry')) return;
  var a = document.createElement('a');
  a.id = 'zxjiu-official-entry';
  a.href = '/official.html';
  a.title = '品牌故事 · 同盟臻选 · 竹奕佳酿';
  a.appendChild(document.createTextNode('\u{1F3DB} 官网'));
  a.style.cssText = [
    'position:fixed', 'left:16px',
    'bottom:calc(16px + env(safe-area-inset-bottom,0px))',
    'z-index:2147482000',
    'display:inline-flex', 'align-items:center',
    'padding:9px 16px', 'border-radius:999px',
    'background:linear-gradient(135deg,#355c44,#4a7c59)',
    'color:#fff', 'font-size:13px', 'letter-spacing:1px',
    'text-decoration:none', 'line-height:1',
    'box-shadow:0 3px 12px rgba(53,92,68,.45)',
    'opacity:.92', 'transition:opacity .2s'
  ].join(';');
  a.onmouseenter = function () { a.style.opacity = '1'; };
  a.onmouseleave = function () { a.style.opacity = '.92'; };
  document.body.appendChild(a);
})();
