/* 时空情景问候横幅(scene-widget) —— 主站(Taro H5)情景模块集成
 *
 * 数据源: GET /api/scene/context(游客白名单, XFF 真实 IP 定位,
 *         登录态自动带出"老朋友"问候)
 * 形态: 顶部悬浮横幅(问候+天气+weatherTip 关怀), 12 秒自动淡出,
 *       可点 × 立即关闭; 不触碰 Taro 渲染树(#app), 独立 DOM
 * 兼容: 纯标准 JS + fetch, PC/微信内置浏览器通用, 失败静默降级
 */
(function () {
  "use strict";
  var AUTO_HIDE_MS = 12000;

  fetch("/api/scene/context", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      var d = res && res.success && res.data;
      if (!d || !d.greeting || !d.greeting.greeting) return;
      build(d.greeting);
    })
    .catch(function () { /* 静默: 接口异常不展示横幅 */ });

  function build(g) {
    var bar = document.createElement("div");
    bar.id = "scene-greet-bar";
    bar.style.cssText = [
      "position:fixed", "top:0", "left:0", "right:0", "z-index:2147483000",
      "background:rgba(53,92,68,.94)", "color:#fff",
      "font:14px/1.6 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif",
      "padding:8px 42px 8px 14px", "box-sizing:border-box",
      "text-align:center", "opacity:0", "transition:opacity .45s ease",
      "box-shadow:0 1px 6px rgba(0,0,0,.18)"
    ].join(";");

    var html = escapeHtml(g.greeting || "");
    if (g.sub) {
      html += '　<span style="opacity:.85">' + escapeHtml(g.sub) + "</span>";
    }
    if (g.weatherTip) {
      html += '<div style="opacity:.92;font-size:13px;padding-top:2px">'
        + "🍃 " + escapeHtml(g.weatherTip) + "</div>";
    }
    bar.innerHTML = html;

    var x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", "关闭问候");
    x.textContent = "×";
    x.style.cssText = "position:absolute;right:4px;top:50%;"
      + "transform:translateY(-50%);background:none;border:0;"
      + "color:#fff;font-size:20px;line-height:1;padding:8px 10px;"
      + "cursor:pointer;opacity:.8";
    x.onclick = function () { hide(); };
    bar.appendChild(x);

    document.body.appendChild(bar);
    requestAnimationFrame(function () { bar.style.opacity = "1"; });
    setTimeout(hide, AUTO_HIDE_MS);

    function hide() {
      bar.style.opacity = "0";
      setTimeout(function () { bar.remove(); }, 500);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
