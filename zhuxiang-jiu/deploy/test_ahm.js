/* 故障注入测试: AHM 音频健康监测(哑流检出/自动恢复/退避告警)
   提取 widget 真实 onAudioFrame + ahmRecover, 帧数据注入全 0
   buffer 模拟 X5 路由错乱哑流 */
var fs = require("fs");
var src = fs.readFileSync(
  "d:/网站架构设计/zhuxiang-jiu/js/voice-wake-widget.js", "utf8");

function extract(name) {
  var m = src.match(new RegExp(
    "function " + name + "\\([\\s\\S]*?\\n  }", "m"));
  if (!m) { throw new Error("extract fail: " + name); }
  return m[0];
}
var code = extract("onAudioFrame") + "\n" + extract("ahmRecover");

var pass = 0, fail = 0;
function eq(label, got, want) {
  var ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { pass++; } else { fail++; console.log("  FAIL " + label +
    "\n    got : " + JSON.stringify(got) + "\n    want: " + JSON.stringify(want)); }
}

function makeHarness() {
  var calls = { reset: 0, tips: [] };
  var eng = {
    on: true, stream: {}, ctx: { sampleRate: 48000 },
    analyser: null, proc: null, ws: null, wsReady: false,
    ring: [], ringSamples: 0, speaking: false,
    hiStreak: 0, loStreak: 0, segStart: 0, feed: null,
    lastPartial: "", pendingFinal: false, segTimes: [],
    zeroFrames: 0, ahmDead: false, ahmResetAt: 0,
  };
  var fn = new Function("eng", "TH_ON", "TH_OFF", "RING_MAX",
    "pushRing", "beginSegment", "segFeed", "endSegment",
    "stopWake", "releaseMic", "enableWakeQuiet", "showTip",
    code + "\nreturn { onAudioFrame: onAudioFrame };");
  var api = fn(eng, 0.012, 0.006, 24000,
    function () {},            /* pushRing */
    function () {},            /* beginSegment */
    function () {},            /* segFeed */
    function () {},            /* endSegment */
    function () { calls.reset++; },        /* stopWake */
    function () {},                          /* releaseMic */
    function (loud) { if (loud) { calls.reset++; } }, /* enableWakeQuiet */
    function (msg) { calls.tips.push(msg); });
  return { eng: eng, calls: calls, api: api };
}
function frame(zero, n) {
  var f = new Float32Array(n || 4096);
  if (!zero) {
    for (var i = 0; i < f.length; i++) { f[i] = 0.002 * Math.sin(i); }
  }
  return { inputBuffer: { getChannelData: function () { return f; } } };
}

/* ---- 1. 正常底噪帧: 不触发 ---- */
var h = makeHarness();
for (var i = 0; i < 10; i++) { h.api.onAudioFrame(frame(false)); }
eq("正常帧零计数", h.eng.zeroFrames, 0);
eq("正常帧无复位", h.calls.reset, 0);

/* ---- 2. 连续 6 全零帧(≈500ms): 判定哑流 → 硬复位一次 ---- */
h = makeHarness();
for (i = 0; i < 5; i++) { h.api.onAudioFrame(frame(true)); }
eq("5 帧未达阈值", h.calls.reset, 0);
h.api.onAudioFrame(frame(true));
eq("6 帧触发硬复位", h.calls.reset > 0, true);
eq("复位后状态重置(备复发检测)", [h.eng.ahmDead, h.eng.zeroFrames],
   [false, 0]);

/* ---- 3. 复位后新流正常: 计数与标记恢复 ---- */
h.api.onAudioFrame(frame(false));
eq("恢复帧清零计数", h.eng.zeroFrames, 0);
eq("恢复帧清哑流标记", h.eng.ahmDead, false);

/* ---- 4. 30s 内复发(重置后仍哑): 告警不再循环复位 ---- */
h = makeHarness();
for (i = 0; i < 6; i++) { h.api.onAudioFrame(frame(true)); }
var resetsAfterFirst = h.calls.reset;
for (i = 0; i < 12; i++) { h.api.onAudioFrame(frame(true)); }
eq("复发不再复位(同值)", h.calls.reset, resetsAfterFirst);
eq("复发告警提示", h.calls.tips.length > 0
   && /链路异常/.test(h.calls.tips[0]), true);

/* ---- 5. 30s 退避后可再次自动复位 ---- */
h.eng.ahmResetAt -= 31000;
h.eng.ahmDead = false;
h.eng.zeroFrames = 5;
h.api.onAudioFrame(frame(true));
eq("退避后允许再次复位", h.calls.reset > resetsAfterFirst, true);

console.log("pass=" + pass + " fail=" + fail);
process.exit(fail ? 1 : 0);
