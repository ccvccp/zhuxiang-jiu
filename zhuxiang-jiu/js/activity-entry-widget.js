/* 活动中心入口浮球(activity-entry) —— 主站(Taro H5)全站嵌入式活动入口
 *
 * 形态: 左下角悬浮球(避开右侧小竹语音球)点击 → 跳转 /activity.html
 *       活动中心 C端(游客浏览/登录报名/抽奖/擂台赛)
 * 兼容: 纯标准 JS, PC/微信内置浏览器通用; 无外部依赖
 * 部署: 生产 index.html 追加 <script src=/js/activity-entry-widget.js defer>
 */
(function () {
  "use strict";

  var b = document.createElement("button");
  b.id = "activity-entry-ball";
  b.type = "button";
  b.setAttribute("aria-label", "活动中心");
  b.style.cssText = [
    "position:fixed", "left:16px", "bottom:84px", "z-index:2147483000",
    "width:52px", "height:52px", "border-radius:50%",
    "background:#4a7c59", "color:#fff", "border:0",
    "display:flex", "flex-direction:column", "align-items:center",
    "justify-content:center",
    "font:12px/1.2 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif",
    "box-shadow:0 3px 12px rgba(0,0,0,.25)",
    "transition:transform .15s ease", "cursor:pointer"
  ].join(";");
  b.innerHTML = '<span style="font-size:18px;line-height:1">🎁</span>'
    + "<span>活动</span>";
  b.onclick = function () { window.location.href = "/activity.html"; };
  b.onmouseenter = function () { b.style.transform = "scale(1.08)"; };
  b.onmouseleave = function () { b.style.transform = "scale(1)"; };

  /* 与语音球同规则: 宽屏上移避开 PC 底栏, 移动端避开 H5 tabbar */
  function fitWide() {
    b.style.bottom = window.innerWidth >= 768 ? "32px" : "84px";
  }
  fitWide();
  window.addEventListener("resize", fitWide);

  document.body.appendChild(b);
})();
