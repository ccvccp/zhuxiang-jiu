/* 小竹语音入口悬浮球(voice-entry) —— 主站(Taro H5)语音功能入口
 *
 * 形态: 右下角悬浮球(🎤小竹), 点击直达全功能语音页
 *       /xiaozhu-voice.html(双模语音/TTS/购物/订单/支付确认)
 * 兼容: 纯标准 JS, PC/微信内置浏览器通用, 零鉴权依赖
 *       bottom 84px 避开移动端 tabbar, 宽屏降 32px
 */
(function () {
  "use strict";
  var b = document.createElement("a");
  b.id = "xiaozhu-entry-ball";
  b.href = "/xiaozhu-voice.html";
  b.setAttribute("aria-label", "小竹语音精灵");
  b.style.cssText = [
    "position:fixed", "right:16px", "bottom:84px", "z-index:2147483000",
    "width:52px", "height:52px", "border-radius:50%",
    "background:#355c44", "color:#fff",
    "display:flex", "flex-direction:column", "align-items:center",
    "justify-content:center", "text-decoration:none",
    "font:12px/1.2 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif",
    "box-shadow:0 3px 12px rgba(0,0,0,.25)",
    "transition:transform .15s ease"
  ].join(";");
  b.innerHTML = '<span style="font-size:18px;line-height:1">🎤</span>'
    + "<span>小竹</span>";
  b.onmouseenter = function () { b.style.transform = "scale(1.08)"; };
  b.onmouseleave = function () { b.style.transform = "scale(1)"; };

  function fitWide() {
    b.style.bottom = window.innerWidth >= 768 ? "32px" : "84px";
  }
  fitWide();
  window.addEventListener("resize", fitWide);

  document.body.appendChild(b);
})();
