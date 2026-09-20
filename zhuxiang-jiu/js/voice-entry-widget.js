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
  var VER = "v=33";

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
    "#xiaozhu-voice-panel iframe{width:100%;height:100%;border:0}",
    "/* 迷你态: 跳转后浮层缩为底部小条——iframe 保持可见(录音",
    "质量保障)免提持续在线, 点小竹/展开钮恢复全屏 */",
    "#xiaozhu-voice-panel.mini{pointer-events:none;",
    "align-items:flex-end;background:transparent}",
    "#xiaozhu-voice-panel.mini .xwrap{pointer-events:auto;",
    "height:104px;border-radius:14px 14px 0 0;",
    "box-shadow:0 -4px 20px rgba(0,0,0,.22)}",
    "@media(min-width:768px){#xiaozhu-voice-panel.mini .xwrap{",
    "width:330px;height:104px;border-radius:14px;margin-bottom:100px}}",
    "#xiaozhu-voice-panel .xexpand{position:absolute;top:8px;right:52px;",
    "z-index:6;display:none;height:28px;padding:0 14px;border-radius:14px;",
    "border:0;background:rgba(53,92,68,.92);color:#fff;font-size:13px;",
    "cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25)}",
    "#xiaozhu-voice-panel.mini .xexpand{display:block}"
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
  var xe = document.createElement("button");
  xe.type = "button";
  xe.className = "xexpand";
  xe.textContent = "🎙 展开对话";
  xe.onclick = maximizePanel;
  var fr = document.createElement("iframe");
  fr.src = "/xiaozhu-voice.html?" + VER + "&embed=1";
  fr.setAttribute("allow", "microphone");
  fr.setAttribute("title", "小竹语音精灵");
  wrap.appendChild(fr);
  wrap.appendChild(x);
  wrap.appendChild(xe);
  panel.appendChild(wrap);
  document.body.appendChild(panel);

  function openPanel() {
    panel.classList.add("open");
    panel.classList.remove("mini");
    notifyFrame("show"); /* 通知语音页恢复免提聆听 */
  }
  function closePanel() {
    panel.classList.remove("open");
    panel.classList.remove("mini");
    notifyFrame("hide"); /* 通知语音页暂停监听/停TTS(防烧额度+杂音污染) */
  }
  /* 跳转迷你化: 缩为底部小条保持 iframe 可见与免提在线
     (隐藏 iframe 在 X5 录音降权为不可懂音频致转写"#") */
  function minimizePanel() {
    panel.classList.add("open");
    panel.classList.add("mini");
  }
  function maximizePanel() {
    panel.classList.remove("mini");
  }
  function notifyFrame(state) {
    try {
      fr.contentWindow.postMessage({ type: "xz-panel-" + state }, "*");
    } catch (e) { /* iframe 未就绪忽略 */ }
  }

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
    if (panel.classList.contains("mini")) {
      maximizePanel(); /* 迷你态点球 = 恢复全屏对话 */
      return;
    }
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

  /* 语音页(iframe) 请求: jump 导航(postMessage 机制——微信 WebView
     拦截 iframe 直接改 parent.location, 由父窗口自身执行导航绕开
     限制; 跳转后浮层迷你化保持语音持续在线) + 交易类回复自动
     弹回全屏(mini 小条看不清加购卡片, 用户不敢下单) */
  window.addEventListener("message", function (ev) {
    var d = ev && ev.data;
    if (!d || !d.type) return;
    if (d.type === "xz-panel-maximize") {
      maximizePanel();
      return;
    }
    if (d.type !== "xz-jump" || !d.href) return;
    try {
      /* 仅接受同源相对/锚点路径, 防注入外链 */
      if (!/^\/#?\//.test(String(d.href))) return;
      minimizePanel();
      window.location.href = d.href;
    } catch (e) { /* 导航异常忽略 */ }
  });

  document.body.appendChild(b);
})();
