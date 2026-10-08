/* 时空情景问候横幅(scene-widget) —— 主站(Taro H5)情景模块集成
 *
 * 数据源: GET /api/scene/context(游客白名单, XFF 真实 IP 定位,
 *         登录态自动带出"老朋友"问候)
 * 精确修正(2026-10-09): 运营商 CGNAT 池 IP 归属≠物理位置(丽水景宁
 *         显示衢州实证)——横幅渲染后 H5 Geolocation 静默取坐标调
 *         /api/scene/geo(高德逆地理), 城市不同则刷新横幅; 坐标
 *         localStorage 缓存 30min(下次免问权限秒修正); 拒绝授权/
 *         失败保持 IP 归属现状
 * 形态: 顶部悬浮横幅(问候+天气+weatherTip 关怀), 12 秒自动淡出,
 *       可点 × 立即关闭; 不触碰 Taro 渲染树(#app), 独立 DOM
 * 兼容: 纯标准 JS + fetch, PC/微信内置浏览器通用, 失败静默降级
 */
(function () {
  "use strict";
  var AUTO_HIDE_MS = 12000;
  var GEO_CACHE_KEY = "scene.geo.coord";
  var GEO_CACHE_TTL = 30 * 60 * 1000;

  fetch("/api/scene/context", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      var d = res && res.success && res.data;
      if (!d || !d.greeting || !d.greeting.greeting) return;
      build(d.greeting);
      refineByGeo(d);
    })
    .catch(function () { /* 静默: 接口异常不展示横幅 */ });

  /* 坐标修正: 缓存坐标优先, 无则 Geolocation 静默请求(仅 HTTPS) */
  function refineByGeo(ctx) {
    try {
      var cached = JSON.parse(
        localStorage.getItem(GEO_CACHE_KEY) || "null");
      if (cached && cached.at
          && Date.now() - cached.at < GEO_CACHE_TTL
          && cached.cityCode
          && cached.cityCode !== cityCodeOf(ctx)) {
        fetchGeo(cached.lng, cached.lat);
        return;
      }
    } catch (e) { /* 忽略缓存解析 */ }
    if (!navigator.geolocation
        || !window.isSecureContext) return;
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var c = pos.coords;
        if (!isFinite(c.longitude) || !isFinite(c.latitude)) return;
        fetchGeo(c.longitude, c.latitude);
      },
      function () { /* 拒绝授权/超时: 保持 IP 归属 */ },
      { timeout: 8000, maximumAge: GEO_CACHE_TTL });
  }

  function cityCodeOf(ctx) {
    return ((ctx && ctx.location) || {}).cityCode || "";
  }

  function fetchGeo(lng, lat) {
    fetch("/api/scene/geo?lng=" + encodeURIComponent(lng)
          + "&lat=" + encodeURIComponent(lat),
          { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        var d = res && res.success && res.data;
        if (!d || !d.located || !d.greeting) return;
        try {
          localStorage.setItem(GEO_CACHE_KEY, JSON.stringify({
            lng: lng, lat: lat, at: Date.now(),
            cityCode: cityCodeOf(d) }));
        } catch (e) { /* 忽略 */ }
        rebuild(d.greeting);
      })
      .catch(function () { /* 静默 */ });
  }

  var barEl = null, hideTimer = null;

  function build(g) { rebuild(g); }

  function rebuild(g) {
    if (barEl) { barEl.remove(); clearTimeout(hideTimer); }
    barEl = document.createElement("div");
    barEl.id = "scene-greet-bar";
    barEl.style.cssText = [
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
    barEl.innerHTML = html;

    var x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", "关闭问候");
    x.textContent = "×";
    x.style.cssText = "position:absolute;right:4px;top:50%;"
      + "transform:translateY(-50%);background:none;border:0;"
      + "color:#fff;font-size:20px;line-height:1;padding:8px 10px;"
      + "cursor:pointer;opacity:.8";
    x.onclick = function () { hide(); };
    barEl.appendChild(x);

    document.body.appendChild(barEl);
    requestAnimationFrame(function () { barEl.style.opacity = "1"; });
    hideTimer = setTimeout(hide, AUTO_HIDE_MS);

    function hide() {
      barEl.style.opacity = "0";
      setTimeout(function () {
        barEl.remove(); barEl = null;
      }, 500);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
