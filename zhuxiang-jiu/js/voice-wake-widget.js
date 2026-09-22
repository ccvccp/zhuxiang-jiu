/* 小竹语音唤醒(voice-wake) —— 主站(Taro H5)全站语音入口
 *
 * 形态: 常态无浮球——开启唤醒后喊「小竹、小竹」弹出语音面板
 *       (iframe 复用全功能语音页); 未开启时右下角显示引导球,
 *       点击开启唤醒(麦克风授权)或直接打开面板手动使用
 * 引擎: 云端流式 ASR 唤醒词检测(/api/xiaozhu/ws/asr, 与语音页
 *       P1 流式轨同协议: auth→ready→PCM 帧→partial/final)
 *       + VAD 音量门限(静默期不推流不烧额度, ring buffer
 *       防漏唤醒词开头) + 频率熔断(单段 6s 封顶/分钟 6 段)
 * 兼容: 纯标准 JS, PC/微信内置浏览器通用; 游客无 JWT 不启动
 *       监听(WS 首条鉴权), 球保留为普通入口
 * 开关: localStorage 'xiaozhu.wake' = on/off; 语音面板内可切换
 *       (同源共享 + postMessage 'xz-wake-on/off' 即时通知)
 * 唤醒词: localStorage 'xiaozhu.wakeword'(面板「⚙️ 唤醒词」设置;
 *       空默认两声/预设「你好小竹」/自定义 2-8 字精确匹配;
 *       postMessage 'xz-wake-word' 即时重建匹配器)
 * 部署: 生产 index.html 注入 <script defer src=/js/voice-wake-widget.js?v=2>
 *       (替换 voice-entry-widget.js?v=25; widget 内容更新须同步 bump ?v=N)
 */
(function () {
  "use strict";
  var VER = "v=2";
  var WAKE_KEY = "xiaozhu.wake";
  var WORD_KEY = "xiaozhu.wakeword";

  /* ---------- 会话令牌(商城 auth_session / 静态页 zhuxiang.auth) ---------- */
  function authToken() {
    try {
      var s = JSON.parse(localStorage.getItem("zhuxiang.auth") || "null");
      if (s && s.token) { return s.token; }
    } catch (e) { /* 忽略 */ }
    try {
      var w = JSON.parse(localStorage.getItem("auth_session") || "null");
      var d = (w && w.data) ? w.data : w;
      if (d && d.accessToken) { return d.accessToken; }
    } catch (e) { /* 忽略 */ }
    return "";
  }

  /* ---------- 样式与面板(与原 voice-entry 同构: 球+iframe 浮层) ---------- */
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
    "#xiaozhu-voice-panel.mini .xexpand{display:block}",
    "#xiaozhu-wake-tip{position:fixed;right:16px;bottom:84px;",
    "z-index:2147482999;max-width:230px;background:rgba(53,92,68,.95);",
    "color:#fff;font:12px/1.6 -apple-system,'PingFang SC',",
    "'Microsoft YaHei',sans-serif;border-radius:10px;padding:10px 12px;",
    "box-shadow:0 4px 16px rgba(0,0,0,.3);display:none}",
    "#xiaozhu-wake-tip.show{display:block}",
    "@media(min-width:768px){#xiaozhu-wake-tip{bottom:32px}}"
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
    notifyFrame("show");
    stopWake(); /* 面板打开期间暂停唤醒监听(面板内即语音会话) */
  }
  function closePanel() {
    panel.classList.remove("open");
    panel.classList.remove("mini");
    notifyFrame("hide");
    startWake(); /* 关面板 → 恢复唤醒监听 */
  }
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

  /* ---------- 引导球(仅唤醒未开启时显示: 开启唤醒/普通入口) ---------- */
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
  b.onclick = onBallClick;
  b.onmouseenter = function () { b.style.transform = "scale(1.08)"; };
  b.onmouseleave = function () { b.style.transform = "scale(1)"; };
  document.body.appendChild(b);

  function fitWide() {
    b.style.bottom = window.innerWidth >= 768 ? "32px" : "84px";
  }
  fitWide();
  window.addEventListener("resize", fitWide);

  /* 引导提示条 */
  var tip = document.createElement("div");
  tip.id = "xiaozhu-wake-tip";
  document.body.appendChild(tip);
  function showTip(msg, ms) {
    tip.textContent = msg;
    tip.classList.add("show");
    setTimeout(function () { tip.classList.remove("show"); }, ms || 4000);
  }

  function wakeOn() {
    return localStorage.getItem(WAKE_KEY) === "on";
  }

  /* 球点击: 已开启→开面板(手动); 未开启→引导开启唤醒, 取消则直接开面板 */
  function onBallClick() {
    if (panel.classList.contains("mini")) { maximizePanel(); return; }
    if (wakeOn()) { openPanel(); return; }
    var ok = confirm('开启「小竹小竹」语音唤醒？\n\n'
      + '开启后本页无浮球常驻, 对着麦克风呼唤"小竹、小竹"\n'
      + '即可唤出语音精灵(需使用麦克风, 仅在检测到人声时上传音频)。');
    if (!ok) { openPanel(); return; } /* 不开唤醒 → 当普通入口用 */
    enableWake();
  }

  /* ---------- 唤醒引擎(VAD + 云端流式 ASR) ---------- */

  /* 唤醒词匹配器(可配置, 语音面板「⚙️ 唤醒词」设置 → localStorage
     'xiaozhu.wakeword' + postMessage 'xz-wake-word' 实时重建):
     - 空(默认): 「小竹」两声(中间 ≤4 字符填充, 同音容错)
     - 预设「你好小竹」: 单声短语(小竹段保留同音容错)
     - 自定义 2-8 字: 普通话转写精确匹配(正则元字符转义) */
  var XZ = "(?:小竹|小主|小猪|小朱|小珠|晓竹|小助|小逐|小烛)";
  function buildWakeRe() {
    var w = "";
    try { w = String(localStorage.getItem(WORD_KEY) || "").trim(); } catch (e) { /* 忽略 */ }
    if (!w) {
      return new RegExp(XZ + "[\\s\\S]{0,4}?" + XZ);
    }
    if (w === "你好小竹") {
      return new RegExp("你好[\\s，,、。]?" + XZ);
    }
    var esc = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(esc);
  }
  var WAKE_RE = buildWakeRe();
  function matchWake(text) {
    return WAKE_RE.test(String(text || ""));
  }

  var eng = {
    on: false,          /* 引擎运行中 */
    stream: null, ctx: null, analyser: null, proc: null,
    ws: null, wsReady: false,
    ring: [],           /* 待发缓存(Int16Array 段): 唤醒词开头防漏 */
    ringSamples: 0,
    speaking: false,    /* VAD 判定人声段进行中 */
    hiStreak: 0, loStreak: 0,
    segStart: 0,        /* 当前段开始时刻 */
    feed: null,         /* 16k Int16 待发队列 */
    lastPartial: "", pendingFinal: false,
    segTimes: [],       /* 分钟频率熔断 */
  };
  var RING_MAX = 16000 * 1.5;   /* 环形缓存 1.5s@16k */
  var TH_ON = 0.012;            /* 起 VAD 门限(RMS) */
  var TH_OFF = 0.006;           /* 止 VAD 门限 */
  var FRAME = 4096;             /* ScriptProcessor 帧长 */

  async function enableWake() {
    if (!authToken()) {
      showTip("请先登录商城后再开启语音唤醒（语音助手需要会员会话）");
      openPanel();
      return;
    }
    try {
      eng.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true, noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (e) {
      showTip("麦克风不可用，无法开启唤醒——浮球保留，点击可直接打开语音面板");
      return;
    }
    localStorage.setItem(WAKE_KEY, "on");
    b.style.display = "none";
    startWake();
    showTip("语音唤醒已开启——呼唤「小竹、小竹」试试（面板内可关闭）", 5000);
  }

  function startWake() {
    if (!wakeOn() || !eng.stream || eng.on) { return; }
    try {
      eng.ctx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) { return; }
    var src = eng.ctx.createMediaStreamSource(eng.stream);
    eng.analyser = eng.ctx.createAnalyser();
    eng.analyser.fftSize = 512;
    src.connect(eng.analyser);
    eng.proc = eng.ctx.createScriptProcessor(FRAME, 1, 1);
    eng.proc.onaudioprocess = onAudioFrame;
    src.connect(eng.proc);
    eng.proc.connect(eng.ctx.destination); /* 静音直连避免部分内核不触发 */
    eng.on = true;
    eng.speaking = false;
    eng.hiStreak = 0; eng.loStreak = 0;
  }

  function stopWake() {
    if (!eng.on) { return; }
    eng.on = false;
    teardownSeg();
    try { if (eng.proc) { eng.proc.disconnect(); eng.proc.onaudioprocess = null; } } catch (e) { /* 忽略 */ }
    try { if (eng.analyser) { eng.analyser.disconnect(); } } catch (e) { /* 忽略 */ }
    try { if (eng.ctx) { eng.ctx.close(); } } catch (e) { /* 忽略 */ }
    eng.ctx = null; eng.analyser = null; eng.proc = null;
  }

  /* 每音频帧: RMS 能量 VAD + 重采样 16k 入环形缓存/推流 */
  function onAudioFrame(ev) {
    if (!eng.on) { return; }
    var f32 = ev.inputBuffer.getChannelData(0);
    var sum = 0;
    for (var i = 0; i < f32.length; i++) { sum += f32[i] * f32[i]; }
    var rms = Math.sqrt(sum / f32.length);

    /* 重采样 48k→16k(线性, 与语音页 streamFeed 同法) */
    var ratio = (eng.ctx.sampleRate || 48000) / 16000;
    var out = [];
    for (var j = 0; j < f32.length; j += ratio) {
      var i0 = Math.floor(j), fr = j - i0;
      var v = f32[i0] * (1 - fr);
      if (i0 + 1 < f32.length) { v += f32[i0 + 1] * fr; }
      out.push(v);
    }
    pushRing(out);

    if (!eng.speaking) {
      if (rms > TH_ON) {
        eng.hiStreak++;
        if (eng.hiStreak >= 3) { beginSegment(); }
      } else { eng.hiStreak = 0; }
    } else {
      segFeed(out);
      var now = Date.now();
      var dur = now - eng.segStart;
      if (rms < TH_OFF) {
        eng.loStreak++;
        /* 静默 1.2s → 收段; 单段 6s 强制收段(防长语音) */
        if ((eng.loStreak >= 12 && dur > 900) || dur > 6000) { endSegment(); }
      } else { eng.loStreak = 0; }
    }
  }

  function pushRing(samples) {
    for (var i = 0; i < samples.length; i++) {
      var s = Math.max(-1, Math.min(1, samples[i]));
      eng.ring.push(s < 0 ? s * 32768 : s * 32767);
    }
    while (eng.ring.length > RING_MAX) { eng.ring.splice(0, eng.ring.length - RING_MAX); }
  }

  /* 人声段开始: 分钟熔断校验 → 建流(带环形缓存回补) */
  function beginSegment() {
    var now = Date.now();
    eng.segTimes = eng.segTimes.filter(function (t) {
      return now - t < 60000;
    });
    if (eng.segTimes.length >= 6) { /* 噪音环境熔断: 本分钟段数封顶 */
      eng.hiStreak = 0;
      return;
    }
    eng.segTimes.push(now);
    eng.speaking = true;
    eng.segStart = now;
    eng.loStreak = 0;
    eng.lastPartial = "";
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    try {
      eng.ws = new WebSocket(proto + location.host + "/api/xiaozhu/ws/asr");
      eng.ws.binaryType = "arraybuffer";
    } catch (e) { teardownSeg(); return; }
    var failTimer = setTimeout(function () {
      if (eng.ws && eng.ws.readyState !== 1) { teardownSeg(); }
    }, 5000);
    eng.ws.onopen = function () {
      eng.ws.send(JSON.stringify({
        type: "auth", token: authToken(),
      }));
    };
    eng.ws.onmessage = function (e) {
      if (typeof e.data !== "string") { return; }
      var m;
      try { m = JSON.parse(e.data); } catch (ex) { return; }
      if (m.type === "ready") {
        eng.wsReady = true;
        clearTimeout(failTimer);
        /* 回补环形缓存(唤醒词开头不漏)——按百炼帧约束
           分帧发送(3200 样本=200ms=6.4KB, 勿超 16KB/帧) */
        while (eng.ring.length >= 3200) {
          var i16 = new Int16Array(3200);
          for (var k = 0; k < 3200; k++) { i16[k] = eng.ring[k]; }
          try { eng.ws.send(i16.buffer); } catch (er) { return; }
          eng.ring.splice(0, 3200);
        }
      } else if (m.type === "partial" && m.text) {
        eng.lastPartial = m.text;
        if (matchWake(m.text)) { onWakeHit(); }
      } else if (m.type === "final") {
        if (matchWake(m.text || eng.lastPartial)) { onWakeHit(); }
        teardownSeg();
      } else if (m.type === "error") {
        teardownSeg();
      }
    };
    eng.ws.onclose = function () { clearTimeout(failTimer); teardownSeg(); };
    eng.ws.onerror = function () { /* onclose 兜底 */ };
  }

  /* 推流: 攒 200ms(3200 样本@16k)帧发送(对齐百炼约束) */
  function segFeed(samples16k) {
    for (var i = 0; i < samples16k.length; i++) {
      var s = Math.max(-1, Math.min(1, samples16k[i]));
      eng.ring.push(s < 0 ? s * 32768 : s * 32767);
    }
    while (eng.ring.length > RING_MAX * 2) { eng.ring.splice(0, eng.ring.length - RING_MAX * 2); }
    if (!eng.wsReady) { return; }
    while (eng.ring.length >= 3200) {
      var i16 = new Int16Array(3200);
      for (var k = 0; k < 3200; k++) { i16[k] = eng.ring[k]; }
      try { eng.ws.send(i16.buffer); } catch (e) { return; }
      eng.ring.splice(0, 3200);
    }
  }

  /* 人声段结束: finish → 等 final(部分内核立即断) */
  function endSegment() {
    eng.speaking = false;
    eng.hiStreak = 0;
    if (eng.ws && eng.ws.readyState === 1) {
      try { eng.ws.send(JSON.stringify({ type: "finish" })); } catch (e) { teardownSeg(); }
    } else { teardownSeg(); }
    /* final 由 onmessage 消费; 3s 未回 → 兜底拆除 */
    setTimeout(function () {
      if (eng.speaking === false && eng.ws) { teardownSeg(); }
    }, 3000);
  }

  function teardownSeg() {
    eng.speaking = false;
    eng.wsReady = false;
    if (eng.ws) { try { eng.ws.close(); } catch (e) { /* 忽略 */ } }
    eng.ws = null;
    /* 保留尾部 1.5s 环形缓存供下段回补 */
    while (eng.ring.length > RING_MAX) { eng.ring.splice(0, eng.ring.length - RING_MAX); }
  }

  /* ---------- 唤醒命中 ---------- */
  function onWakeHit() {
    teardownSeg();
    wakeBeep();
    openPanel();
  }

  /* 唤醒提示音: WebAudio 生成两声上行「叮-咚」 */
  function wakeBeep() {
    try {
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.18].forEach(function (t0) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = t0 ? 880 : 660;
        gain.gain.setValueAtTime(0.0001, ctx.currentTime + t0);
        gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + t0 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t0 + 0.16);
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start(ctx.currentTime + t0);
        osc.stop(ctx.currentTime + t0 + 0.2);
      });
      setTimeout(function () { ctx.close(); }, 600);
    } catch (e) { /* 提示音失败不阻断唤醒 */ }
  }

  /* ---------- 语音页/外层消息协议 ---------- */
  window.addEventListener("message", function (ev) {
    var d = ev && ev.data;
    if (!d || !d.type) { return; }
    if (d.type === "xz-panel-maximize") { maximizePanel(); return; }
    if (d.type === "xz-wake-on") {
      /* 面板内开启唤醒: 球隐藏 + 取麦克风 + 引擎待命
         (面板打开期间监听暂停, 关闭面板后自动恢复) */
      b.style.display = "none";
      if (eng.stream) {
        startWake();
      } else {
        enableWakeQuiet();
      }
      return;
    }
    if (d.type === "xz-wake-off") {
      localStorage.setItem(WAKE_KEY, "off");
      stopWake();
      if (eng.stream) { try { eng.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { /* 忽略 */ } }
      eng.stream = null;
      b.style.display = "";
      return;
    }
    if (d.type === "xz-wake-word") {
      /* 唤醒词已由语音页写入同源 localStorage, 此处重建匹配器
         (消息仅作即时通知; 词值本身只信 localStorage, 第三方
         伪造 postMessage 无法注入任意正则) */
      WAKE_RE = buildWakeRe();
      return;
    }
    if (d.type !== "xz-jump" || !d.href) { return; }
    try {
      if (!/^\/#?\//.test(String(d.href))) { return; }
      minimizePanel();
      window.location.href = d.href;
    } catch (e) { /* 导航异常忽略 */ }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && panel.classList.contains("open")) { closePanel(); }
  });

  /* ---------- 启动: 已开启唤醒 → 恢复监听(麦克风权限仍在) ---------- */
  if (wakeOn()) {
    b.style.display = "none";
    enableWakeQuiet();
  } else {
    b.style.display = "";
  }

  /* 恢复流程: 不弹 confirm, 直接试拿麦克风(权限已记住则静默成功) */
  async function enableWakeQuiet() {
    if (!authToken()) {
      /* 令牌过期/退出登录: 唤醒暂不可用, 球恢复引导 */
      b.style.display = "";
      showTip("唤醒待命需要登录——登录后点小竹球恢复唤醒");
      return;
    }
    try {
      eng.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      startWake();
    } catch (e) {
      b.style.display = "";
      showTip("唤醒待命失败（麦克风不可用）——点击小竹球重新开启或直接打开面板");
    }
  }
})();
