/* 75号共鸣采集 beacon(RA-P1 通道B) — 零依赖, 匿名会话级, 无 PII
   事件: click(带 interval_ms)/scroll_depth/error/back/long_stay/pagehide
   上报: 攒 8 条或 15s flush → POST /api/v1/resonance/collect(白名单端点)
   失败: 静默丢弃(观测面不碰业务) */
(function () {
  "use strict";
  var URL = "/api/v1/resonance/collect";
  var MAX_AGE = 30 * 60 * 1000;      /* 会话 30min 滑动过期 */
  var BUF_MAX = 8, FLUSH_MS = 15000;
  var buf = [], lastClick = 0, maxScroll = 0,
      t0 = Date.now(), sentStay = false;

  function sid() {
    try {
      var k = "zx_rs", o;
      try { o = JSON.parse(localStorage.getItem(k) || "null"); }
      catch (e) { o = null; }
      if (!o || Date.now() - o.at > MAX_AGE) {
        o = { id: "s_" + Date.now().toString(36) + "_" +
                    Math.random().toString(36).slice(2, 10),
              at: Date.now() };
      } else {
        o.at = Date.now();
      }
      localStorage.setItem(k, JSON.stringify(o));
      return o.id;
    } catch (e) {                    /* localStorage 禁用 → 内存降级 */
      return "s_mem_" + Math.random().toString(36).slice(2, 10);
    }
  }
  var SESSION = sid();

  function push(etype, payload) {
    buf.push({
      event_type: etype,
      page_id: (location.pathname || "/").slice(0, 64),
      payload: payload || {}
    });
    if (buf.length >= BUF_MAX) { flush(); }
  }

  function flush() {
    if (!buf.length) { return; }
    var body = JSON.stringify({
      session_id: SESSION, events: buf.splice(0) });
    try {
      fetch(URL, {
        method: "POST", keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: body
      }).catch(function () { });
    } catch (e) { /* 静默 */ }
  }

  document.addEventListener("click", function () {
    var now = Date.now();
    /* 首点 9999ms(不算快速连点, 与引擎 <300 阈值口径一致) */
    push("click", { interval_ms: lastClick ? now - lastClick : 9999 });
    lastClick = now;
  }, { passive: true });

  window.addEventListener("scroll", function () {
    var denom = Math.max(1, (document.body.scrollHeight || 1) -
                     window.innerHeight);
    var d = Math.round((window.scrollY || 0) / denom * 100) / 100;
    if (d > maxScroll) { maxScroll = d; }
  }, { passive: true });

  window.addEventListener("pagehide", function () {
    if (maxScroll > 0) { push("scroll", { scroll_depth: maxScroll }); }
    if (!sentStay && Date.now() - t0 > 30000) {
      sentStay = true;
      push("long_stay", {});
    }
    push("pagehide", {});
    flush();
  });

  window.addEventListener("pageshow", function (ev) {
    if (ev && ev.persisted) { push("back", {}); }
  });

  window.addEventListener("error", function (e) {
    push("error", {
      message: String((e && e.message) || "").slice(0, 120)
    });
  });

  setInterval(flush, FLUSH_MS);
})();
