/* v3 低延迟对话流水线功能测试
   提取语音页真实函数: splitSpeech(切分)/vtick 语义断句段/
   ttsEnqueue+pumpTts(队列接力)/stopTtsNow(打断清理)/speakCloud(整链)
   桩注入: AudioContext/BufferSource/fetch/rAF/计时可控 */
var fs = require("fs");
var src = fs.readFileSync(
  "d:/网站架构设计/zhuxiang-jiu/backend/xiaozhu-voice.html", "utf8");

function extractFn(name) {
  var m = src.match(new RegExp(
    "function " + name + "\\([\\s\\S]*?\n}", "m"));
  if (!m) throw new Error("extract fail: " + name);
  return m[0];
}
function extractVtick() {
  var m = src.match(/\(function vtick\(\) \{[\s\S]*?\n    \}\)\(\);/);
  if (!m) throw new Error("extract fail: vtick");
  return m[0];
}

var pass = 0, fail = 0;
function ok(label, cond, detail) {
  if (cond) { pass++; console.log("  PASS " + label); }
  else {
    fail++; console.log("  FAIL " + label +
      (detail !== undefined ? " — got: " + JSON.stringify(detail) : ""));
  }
}

/* ---------- A. splitSpeech 纯函数 ---------- */
console.log("[A] splitSpeech 切分");
var ss = new Function(extractFn("splitSpeech") + "\nreturn splitSpeech;")();
ok("A1 空串 → []", JSON.stringify(ss("")) === "[]", ss(""));
ok("A2 短句≤24字 不切分",
   JSON.stringify(ss("好的，已为您打开活动中心")) ===
   '["好的，已为您打开活动中心"]',
   ss("好的，已为您打开活动中心"));
var long1 = "好的，已为您加入清单，这款竹香春42度是山东中质华检检测合格的匠心之作，需要看看检测报告吗？";
var parts1 = ss(long1);
ok("A3 长句多块切分", parts1.length > 2, parts1);
ok("A4 每块≥6字(cogtts 极短不稳)",
   parts1.every(function (p) { return p.trim().length >= 6; }), parts1);
ok("A5 首块最小(首包快)",
   parts1[0].length <= 12, parts1[0]);
ok("A6 切分无损(拼接≈原文)",
   parts1.join("").replace(/\s/g, "") ===
   long1.replace(/\s/g, ""), parts1.join(""));
ok("A7 无标点长串硬切(22字)",
   ss("一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五六").length > 1,
   ss("一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五六"));
ok("A8 全标点碎片并句不落碎块",
   ss("你好，吗，嗯。今天天气怎么样呢，还可以吧。").every(
     function (p) { return p.length >= 6 || "。！？；;，,".indexOf(
       p.charAt(p.length - 1)) >= 0; }),
   ss("你好，吗，嗯。今天天气怎么样呢，还可以吧。"));

/* ---------- B. vtick 语义感知断句 ---------- */
console.log("[B] VAD 语义感知断句");
var vtickCode = extractVtick();
var STOP_AT = {}; /* {cloudStop: n} 计数对象由每次 makeVtick 新建 */
function makeVtick(streamPartial, silentAgoMs, speakVol, vadSpoke,
                   cloudActive, extraS) {
  var S = { handfree: true, vadSpoke: (vadSpoke !== false),
            streamPartial: streamPartial };
  var k;
  for (k in (extraS || {})) { S[k] = extraS[k]; }
  var stops = 0;
  var v = speakVol === undefined ? 0.005 : speakVol; /* 默认静音 */
  var abuf = new Uint8Array(512);
  for (var i = 0; i < abuf.length; i++) {
    /* 直流偏置: d=v 恒定 → RMS 精确可控(sin 波 RMS≈0.707v 会卡阈值边界) */
    abuf[i] = Math.max(0, Math.min(255, Math.round(128 + v * 128)));
  }
  var fn = new Function("analyser", "abuf", "waveEl", "vadOpenAt",
    "S", "vadSilentAt", "requestAnimationFrame", "cloudStop",
    "document", "cloudActive",
    vtickCode);
  var analyser = { fftSize: 512,
    getByteTimeDomainData: function (a) { a.set(abuf); } };
  fn(analyser, abuf,
     { classList: { add: function () {}, remove: function () {} },
       style: { setProperty: function () {} } },
     Date.now() - 10000, /* vadOpenAt 远古 → 阈值取常态 0.035 */
     S,
     silentAgoMs > 0 ? Date.now() - silentAgoMs : 0,
     function () { return 0; },        /* rAF: 不循环(单帧) */
     function () { stops++; },         /* cloudStop 计数 */
     { getElementById: function () { return {
         classList: { add: function () {}, remove: function () {} },
         style: { setProperty: function () {}, removeProperty: function () {} } }; } },
     cloudActive === false ? 0 : 1); /* 段外=0(播报回声门控) */
  return stops;
}
ok("B1 疑问尾字「吗」静音650ms → 提前断句",
   makeVtick("有52度的吗", 650) === 1);
ok("B2 命令式尾字静音650ms → 不提前(防截断错指令)",
   makeVtick("来两件竹香春", 650) === 0);
ok("B3 命令式静音1300ms → 常规断句",
   makeVtick("来两件竹香春", 1300) === 1);
ok("B4 疑问尾字静音550ms(未达600) → 不断",
   makeVtick("有52度的吗", 550) === 0);
ok("B5 说话中(音量>阈值) → 重置静音计时不断",
   makeVtick("有52度的吗", 650, 0.05) === 0);
ok("B6 无 partial(流式轨未出字) 静音650ms → 不提前",
   makeVtick("", 650) === 0);
ok("B7 段外(播报中回声 rms>阈值) 不断句不提交——回声轰炸根因修复",
   makeVtick("有52度的吗", 650, 0.05, false, false) === 0);

/* ---------- B+. 双轨 barge-in v2(文本确认版) ---------- */
console.log("[B+] barge-in v2 文本判定(bargeIsEcho/bargeShouldCut)");
var bargeCode = [extractFn("bargeIsEcho"),
                 extractFn("bargeShouldCut")].join("\n");
function makeBargeJudge(S, stFinalCb) {
  return new Function("S", "stFinalCb",
    bargeCode + "\nreturn bargeShouldCut;")(S, stFinalCb || null);
}
function mkS(ttsPlaying) {
  return { ttsPlaying: ttsPlaying !== false,
           lastSpoken: "已加「竹奕·竹香尊享」，售价 24690 元",
           selfUtter: ["我在，请吩咐"],
           selfUtterAt: [Date.now()] };
}
function bargeIsEchoOf(t, S) {
  return new Function("S", extractFn("bargeIsEcho")
    + "\nreturn bargeIsEcho;")(S || mkS(true))(t);
}
ok("B8 播报内容丢字回声(11:05 实证「竹奕，24690」) LCS 拦",
   bargeIsEchoOf("竹奕，24690。") === true);
ok("B9 混合拼接段(回声+widget 应答 11:05:55 原文) 逐句全拦",
   bargeIsEchoOf("竹奕，24690。我在，请吩咐。") === true);
ok("B10 插语气词回声(「竹奕啊，24690」) 归一化拦",
   bargeIsEchoOf("竹奕啊，24690。") === true);
ok("B11 同音替换回声(「逐奕，24690」竹→逐) LCS 拦",
   bargeIsEchoOf("逐奕，24690。") === true);
ok("B12 selfUtter(wake 应答)12s 窗内拦",
   bargeIsEchoOf("我在，请吩咐") === true);
ok("B13 selfUtter 12s 窗外不拦(放行)",
   (function () {
     var S = mkS(true);
     S.selfUtterAt = [Date.now() - 13000];
     return bargeIsEchoOf("我在，请吩咐", S) === false;
   })());
ok("B14 用户新指令(带播报外新词)放行切断",
   makeBargeJudge(mkS(true))("介绍一款竹奕42度酒") === true);
ok("B15 纠正元词强制放行(「不对，查库存」无视相似度)",
   makeBargeJudge(mkS(true))("不对，查库存") === true);
ok("B16 纠正元词「停」单字放行",
   makeBargeJudge(mkS(true))("停") === true);
ok("B17 「小竹」开头 2 字即切断(明确新指令意图)",
   makeBargeJudge(mkS(true))("小竹") === true);
ok("B18 非「小竹」开头 2 字碎片不切断",
   makeBargeJudge(mkS(true))("好的") === false);
ok("B19 收段中(stFinalCb 挂起)不重复切断",
   makeBargeJudge(mkS(true), function () {})("有什么新品") === false);
ok("B20 非播报中不切断",
   makeBargeJudge(mkS(false))("有什么新品") === false);
ok("B21 中文数字回声(「竹奕，二四六九零」24690 读法) 数字归一拦",
   bargeIsEchoOf("竹奕，二四六九零。") === true
   && bargeIsEchoOf("竹奕，两万四千六百九十。") === true);
ok("B22 中文数字形态回声(「四十二度的竹叶酒」vs 42度) 归一拦",
   (function () {
     var S = mkS(true);
     S.lastSpoken = "竹海至尊是42度的竹叶酒，口感绵柔顺喉";
     return bargeIsEchoOf("四十二度的竹叶酒，口感绵柔顺喉", S)
       === true;
   })());

/* ---------- W. 二级对话唤醒窗口判定 ---------- */
console.log("[W] 二级唤醒窗口(不问不答/问即唤醒/答完即退)");
var lwo = new Function(extractFn("listenWindowOver")
  + "\nreturn listenWindowOver;")();
ok("W1 窗口 4s 无话 不关(5s 阈值内)",
   lwo(4, false) === false);
ok("W2 窗口 5s 无话 关窗回归一级(S3 续问窗)",
   lwo(5, false) === true);
ok("W3 已说话 29s 不关(30s 硬上限内)",
   lwo(29, true) === false);
ok("W4 已说话 30s 关(ASR 单段上限)",
   lwo(30, true) === true);
ok("W5 应答 VAD 静默窗内(已说+静音1300) 不断句(11:28 回环修复)",
   makeVtick("来两件竹香春", 1300, 0.005, true, true,
             { wakeVadMute: Date.now() + 2500 }) === 0);
ok("W6 静默窗过期后 正常断句恢复",
   makeVtick("来两件竹香春", 1300, 0.005, true, true,
             { wakeVadMute: Date.now() - 100 }) === 1);

/* ---------- C. 流水线队列(pumpTts/ttsEnqueue/stopTtsNow) ---------- */
console.log("[C] TTS 分句流水线队列");
var pipeCode = [extractFn("ttsEnqueue"), extractFn("pumpTts"),
                extractFn("stopTtsNow")].join("\n");
function makePipe() {
  var S = { ttsAbort: { abort: function () {} }, ttsCtl: null,
            ttsSrc: null, ttsCur: null, ttsGapSrc: null,
            ttsChunks: null, ttsNextIdx: 0, ttsPlaying: true,
            latMarks: null, ttsBubble: null };
  var playing = [];      /* 已 start 的块序(按 label) */
  var current = null;
  var srcs = [];
  var ctx = {
    destination: {},
    createBufferSource: function () {
      var s = { buffer: null, started: false, stopped: false,
                onended: null, onerror: null,
                connect: function () {}, start: function () {
                  s.started = true; playing.push(s.label); current = s;
                },
                stop: function () {
                  s.stopped = true; current = null;
                  var f = s.onended; if (f) f();
                } };
      srcs.push(s);
      return s;
    } };
  var doneCalls = [];
  var hf = [];
  var fn = new Function("S", "ctx", "scheduleHandfree",
    "document", "window", "ttsPlayCtx", "latReport", "smT",
    pipeCode + "\nreturn { ttsEnqueue: ttsEnqueue, pumpTts: pumpTts, stopTtsNow: stopTtsNow, api: {} };");
  var api = fn(S, { ttsPlayCtx: ctx }, function (d) { hf.push(d); },
    { querySelectorAll: function () { return {
        forEach: function () {} }; } },
    { speechSynthesis: { cancel: function () {} } },
    function () { return ctx; },
    function () { /* latReport 桩 */ },
    function () { /* v82 smT 桩(stopTtsNow 影子转换留痕,
                     沙箱外真实面板有定义) */ });
  /* 队首可控: 每块解码产物为 AudioBuffer 桩 {duration: 2} */
  function buf() { return { duration: 2 }; }
  return {
    S: S, api: api, playing: playing, doneCalls: doneCalls, hf: hf,
    srcs: srcs, ctx: ctx,
    ctl: function (c) { return c; },
    mkBuf: buf,
    endCurrent: function () { /* 模拟当前块播完(最后 start 的块) */
      if (current && typeof current.onended === "function") {
        current.onended();
      }
    } };
}

/* C1 顺序到达: 块0入队即播, 块1等待, 0 完播 1 接力 */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  var doneCalled = 0;
  h.api.ttsEnqueue(h.mkBuf(), 0, null, ctl);
  ok("C1a 块0入队即播", h.playing.length === 1, h.playing);
  h.api.ttsEnqueue(h.mkBuf(), 1, function () { doneCalled++; }, ctl);
  ok("C1b 播放中块1入队不抢播", h.playing.length === 1, h.playing);
  h.endCurrent();
  ok("C1c 块0播完块1接力", h.playing.length === 2, h.playing);
  h.endCurrent();
  ok("C1d 最后块播完触发 done", doneCalled === 1, doneCalled);
})();

/* C2 乱序到达: 块1先到不播, 块0到后按序播放 */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  h.api.ttsEnqueue(h.mkBuf(), 1, null, ctl);
  ok("C2a 块1先到不播(序号保序)", h.playing.length === 0, h.playing);
  h.api.ttsEnqueue(h.mkBuf(), 0, null, ctl);
  ok("C2b 块0到达立即开播", h.playing.length === 1, h.playing);
  h.endCurrent();
  ok("C2c 块0完→已缓存的块1接力", h.playing.length === 2, h.playing);
})();

/* C3 失败块跳过不卡队列 */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  var doneCalled = 0;
  h.api.ttsEnqueue(null, 0, null, ctl);          /* 块0合成失败 */
  h.api.ttsEnqueue(null, 1, null, ctl);          /* 块1也失败 */
  h.api.ttsEnqueue(h.mkBuf(), 2, function () { doneCalled++; }, ctl);
  ok("C3a 失败块连跳直达有效块", h.playing.length === 1, h.playing);
  h.endCurrent();
  ok("C3b 尾块播完 done 触发(队列未卡死)", doneCalled === 1, doneCalled);
})();

/* C4 全部失败: 最后块失败也触发 done(对话不挂死) */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  var doneCalled = 0;
  h.api.ttsEnqueue(null, 0, function () { doneCalled++; }, ctl);
  ok("C4 全失败轮也收尾", doneCalled === 1, doneCalled);
})();

/* C5 打断: stopTtsNow 清队列, 旧轮 enqueue 全废 */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  h.api.ttsEnqueue(h.mkBuf(), 0, null, ctl);
  h.api.stopTtsNow();
  ok("C5a 打断清空队列", h.S.ttsChunks === null, h.S.ttsChunks);
  ok("C5b 打断停播当前块", h.playing.length >= 1
     && h.srcs[0].stopped === true, h.srcs[0].stopped);
  var n = h.playing.length;
  h.api.ttsEnqueue(h.mkBuf(), 1, null, ctl); /* 旧 ctl 已废 */
  ok("C5c 旧轮回调身份比对作废", h.playing.length === n, h.playing);
  var ctl2 = {};
  h.S.ttsAbort = ctl2; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  h.api.ttsEnqueue(h.mkBuf(), 0, null, ctl2);
  ok("C5d 新轮正常开播", h.playing.length === n + 1, h.playing);
})();

/* C6 过渡音让位: 首块 pump 时 stop gap */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  var g = h.ctx.createBufferSource(); g.label = "gap";
  h.S.ttsGapSrc = g; g.start();
  h.api.ttsEnqueue(h.mkBuf(), 0, null, ctl);
  ok("C6a 首块开播(过渡音后)",
     h.srcs.length === 2 && h.srcs[1].started === true,
     h.srcs.map(function (s) { return s.started; }));
  ok("C6b 过渡音被让位停止", g.stopped === true, g.stopped);
  ok("C6c 过渡音引用清空", h.S.ttsGapSrc === null, h.S.ttsGapSrc);
})();

/* C7 队首块未到: 空转不炸 */
(function () {
  var h = makePipe();
  var ctl = {};
  h.S.ttsAbort = ctl; h.S.ttsChunks = {}; h.S.ttsNextIdx = 0;
  h.api.ttsEnqueue(h.mkBuf(), 1, null, ctl); /* 块1 到, 块0 缺 */
  ok("C7 队首缺失空转等待", h.playing.length === 0, h.playing);
})();

console.log("\nRESULT: " + pass + " pass / " + fail + " fail");
process.exit(fail ? 1 : 0);
