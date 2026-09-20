/* 小竹语音全站浮层(voice-entry) —— 主站(Taro H5)任意页面嵌入式语音对话
 *
 * 形态: 右下角悬浮球点击 → 原地展开语音精灵浮层(iframe 复用全功能
 *       语音页: 免提对话/购物/订单/支付确认), 关闭后隐藏保留会话可重开
 * 技术: 同域 iframe 共享登录态(localStorage); allow=microphone 授权
 *       iframe 内 getUserMedia 录音; 移动端全屏/宽屏居中聊天窗
 * 兼容: 纯标准 JS, PC/微信内置浏览器通用
 */
(function () {
  "use strict";
  var VER = "v=18";

  var css = document.createElement("style");
  css.textContent = [
    "#xiaozhu-voice-panel{position:fixed;inset:0;z-index:2147483100;",
    "background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center}",
    "#xiaozhu-voice-panel.open{display:flex}",
    "#xiaozhu-voice-panel .xwrap{position:relative;width:100%;height:100%;",
    "background:#f7f4ee}",
    "#xiaozhu-voice-panel .xclose{position:absolute;top:10px;right:12px;",
    "z-index:5;width:34px;height:34px;border-radius:50%;border:0;",
    "background:rgba(53,92,68,.92);color:#fff;font-size:17px;line-height:1;",
    "cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25)}",
    "@media(min-width:768px){#xiaozhu-voice-panel .xwrap{width:430px;",
    "height:82vh;border-radius:16px;overflow:hidden;",
    "box-shadow:0 12px 40px rgba(0,0,0,.3)}}",
    "#xiaozhu-voice-panel iframe{width:100%;height:100%;border:0}"
  ].join("");
  document.head.appendChild(css);

  var panel = document.createElement("div");
  panel.id = "xiaozhu-voice-panel";
  var wrap = document.createElement("div");
  wrap.className = "xwrap";
  var x = document.createElement("button");
  x.type = "button";
  x.className = "xclose";
  x.setAttribute("aria-label", "关闭语音精灵");
  x.textContent = "×";
  x.onclick = closePanel;
  var fr = document.createElement("iframe");
  fr.src = "/xiaozhu-voice.html?" + VER + "&embed=1";
  fr.setAttribute("allow", "microphone");
  fr.setAttribute("title", "小竹语音精灵");
  wrap.appendChild(fr);
  wrap.appendChild(x);
  panel.appendChild(wrap);
  document.body.appendChild(panel);

  function openPanel() { panel.classList.add("open"); }
  function closePanel() { panel.classList.remove("open"); }

  var b = document.createElement("button");
  b.id = "xiaozhu-entry-ball";
  b.type = "button";
  b.setAttribute("aria-label", "小竹语音精灵");
  b.style.cssText = [
    "position:fixed", "right:16px", "bottom:84px", "z-index:2147483000",
    "width:52px", "height:52px", "border-radius:50%",
    "background:#355c44", "color:#fff", "border:0",
    "display:flex", "flex-direction:column", "align-items:center",
    "justify-content:center",
    "font:12px/1.2 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif",
    "box-shadow:0 3px 12px rgba(0,0,0,.25)",
    "transition:transform .15s ease", "cursor:pointer"
  ].join(";");
  b.innerHTML = '<span style="font-size:18px;line-height:1">🎤</span>'
    + "<span>小竹</span>";
  b.onclick = function () {
    panel.classList.contains("open") ? closePanel() : openPanel();
  };
  b.onmouseenter = function () { b.style.transform = "scale(1.08)"; };
  b.onmouseleave = function () { b.style.transform = "scale(1)"; };

  function fitWide() {
    b.style.bottom = window.innerWidth >= 768 ? "32px" : "84px";
  }
  fitWide();
  window.addEventListener("resize", fitWide);

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && panel.classList.contains("open")) closePanel();
  });

  /* 语音页(iframe) jump 导航请求: postMessage 机制(微信 WebView 拦截
     iframe 直接改 parent.location, 由父窗口自身执行导航绕开限制) */
  window.addEventListener("message", function (ev) {
    var d = ev && ev.data;
    if (!d || d.type !== "xz-jump" || !d.href) return;
    try {
      /* 仅接受同源相对/锚点路径, 防注入外链 */
      if (!/^\/#?\//.test(String(d.href))) return;
      closePanel();
      window.location.href = d.href;
    } catch (e) { /* 导航异常忽略 */ }
  });

  document.body.appendChild(b);
})();
