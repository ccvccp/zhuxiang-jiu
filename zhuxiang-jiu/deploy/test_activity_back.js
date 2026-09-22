/* 功能测试: activity.html goBack 返回分支(同源 back / 外源兜底首页) */
var fs = require("fs");
var html = fs.readFileSync(
  "d:/网站架构设计/zhuxiang-jiu/activity.html", "utf8");
var m = html.match(/window\.goBack = function \(\) \{[\s\S]*?\n    \};/);
if (!m) { throw new Error("goBack extract fail"); }

var pass = 0, fail = 0;
function eq(label, got, want) {
  var ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { pass++; } else { fail++; console.log("  FAIL " + label +
    "\n    got : " + JSON.stringify(got) + "\n    want: " + JSON.stringify(want)); }
}

function runGoBack(origin, referrer, histLen) {
  var calls = { back: 0, href: "" };
  var document = { referrer: referrer };
  var location = { origin: origin, href: "about:blank" };
  var history = { length: histLen, back: function () { calls.back++; } };
  Object.defineProperty(location, "href", {
    set: function (v) { calls.href = v; },
    get: function () { return "about:blank"; }
  });
  var win = {};
  var fn = new Function("window", "document", "location", "history", m[0] +
    "\nreturn window.goBack;");
  fn(win, document, location, history)();
  return calls;
}

/* 1. 商城"我的"页跳来(同源 referrer + 有历史) → history.back() */
var c = runGoBack("https://zxjiu.com", "https://zxjiu.com/mine", 5);
eq("同源有历史: back 一次", c.back, 1);
eq("同源有历史: 不跳首页", c.href, "");

/* 2. 新标签直达(空 referrer) → 跳商城首页 */
c = runGoBack("https://zxjiu.com", "", 1);
eq("直达: 跳首页", c.href, "/");
eq("直达: 不 back", c.back, 0);

/* 3. 外源(百度/扫码浏览器替换) → 跳首页 */
c = runGoBack("https://zxjiu.com", "https://www.baidu.com/s", 3);
eq("外源: 跳首页", c.href, "/");
eq("外源: 不 back", c.back, 0);

/* 4. 同源 referrer 但无历史(replace 进来) → 跳首页兜底 */
c = runGoBack("https://zxjiu.com", "https://zxjiu.com/zyh.html", 1);
eq("同源无历史: 跳首页", c.href, "/");
eq("同源无历史: 不 back", c.back, 0);

/* 5. 本地调试(localhost 静态服) → 同源 back */
c = runGoBack("http://localhost:3000", "http://localhost:3000/mine", 3);
eq("本地调试: back 一次", c.back, 1);

console.log("pass=" + pass + " fail=" + fail);
process.exit(fail ? 1 : 0);
