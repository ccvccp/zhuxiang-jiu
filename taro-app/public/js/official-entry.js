/*! official-entry.js v2 · 已退役(2026-10-04 皇冠球修复)
 * 官网浮标注入已从 index.html 撤除; 此文件保留在原路径改写为
 * 清道夫——兜底旧缓存页面/X5 WebView 残留引用回源执行时:
 * 只移除可能已渲染的浮标, 不再创建任何元素。
 */
(function () {
  var el = document.getElementById('zxjiu-official-entry');
  if (el && el.parentNode) { el.parentNode.removeChild(el); }
})();
