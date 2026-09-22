/* 功能测试: 唤醒词设置双态(独立页/embed) + 前缀补全
   从 xiaozhu-voice.html 提取真实函数源码, DOM/localStorage 打桩验证 */
var fs = require("fs");
var html = fs.readFileSync(
  "d:/网站架构设计/zhuxiang-jiu/backend/xiaozhu-voice.html", "utf8");

function extract(name) {
  var m = html.match(new RegExp(
    "function " + name + "\\([\\s\\S]*?\\n}", "m"));
  if (!m) { throw new Error("extract fail: " + name); }
  return m[0];
}
var src = [extract("normalizeCustomWord"), extract("saveWakeWord")].join("\n");

var pass = 0, fail = 0;
function eq(label, got, want) {
  var ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { pass++; } else { fail++; console.log("  FAIL " + label +
    "\n    got : " + JSON.stringify(got) + "\n    want: " + JSON.stringify(want)); }
}

/* ---- 桩 ---- */
function makeEnv(embed) {
  var store = {}, msgs = [], toasts = [];
  var localStorage = {
    getItem: function (k) { return (k in store) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; }
  };
  var document = {
    body: { classList: {
      contains: function (c) { return embed ? c === "embed" : false; } } },
    getElementById: function () { return { value: "", textContent: "",
      classList: { remove: function () {}, add: function () {} } }; }
  };
  var window = { parent: { postMessage: function (d) { msgs.push(d); } } };
  var fn = new Function("localStorage", "document", "window",
    "showToast", "showWakeWordSet", src +
    "\nreturn { normalizeCustomWord: normalizeCustomWord, saveWakeWord: saveWakeWord };");
  var api = fn(localStorage, document, window,
    function (t) { toasts.push(t); }, function () {});
  return { store: store, msgs: msgs, toasts: toasts, api: api };
}

/* ---- 1. normalizeCustomWord ---- */
var env = makeEnv(false);
var n = env.api.normalizeCustomWord;
eq("补全: 精灵", n("精灵"), "小竹精灵");
eq("补全: 你好", n("你好"), "小竹你好");
eq("已带前缀原样", n("小竹精灵"), "小竹精灵");
eq("空白清空", n("  "), "");
eq("去空格", n(" 精灵 "), "小竹精灵");
eq("超长 null(6字+前缀=8)", n("六六六六六六"), "小竹六六六六六六");
eq("超长 null(7字)", n("七七七七七七七"), null);

/* ---- 2. 独立页保存(非 embed) ---- */
env = makeEnv(false);
env.api.saveWakeWord("小竹精灵");
eq("独立页: localStorage 写入", env.store["xiaozhu.wakeword"], "小竹精灵");
eq("独立页: 不 postMessage", env.msgs, []);
eq("独立页: toast 提示主站生效",
  env.toasts[0].indexOf("在主站任意页呼唤即可唤出") !== -1, true);

/* ---- 3. embed 面板保存 ---- */
env = makeEnv(true);
env.api.saveWakeWord("小竹精灵");
eq("embed: localStorage 写入", env.store["xiaozhu.wakeword"], "小竹精灵");
eq("embed: postMessage 即时重建",
  env.msgs, [{ type: "xz-wake-word", word: "小竹精灵" }]);
eq("embed: toast 呼唤试试", env.toasts[0].indexOf("呼唤试试") !== -1, true);

/* ---- 4. 恢复默认(清空) ---- */
env = makeEnv(true);
env.api.saveWakeWord("");
eq("清空: 键移除", "xiaozhu.wakeword" in env.store, false);
eq("清空: postMessage 空词",
  env.msgs, [{ type: "xz-wake-word", word: "" }]);
eq("清空: toast 默认", env.toasts[0].indexOf("恢复默认") !== -1, true);

console.log("pass=" + pass + " fail=" + fail);
process.exit(fail ? 1 : 0);
