/* 小竹语音唤醒(voice-wake) —— 主站(Taro H5)全站语音入口
 *
 * 形态: 常态无浮球——开启唤醒后喊「小竹、小竹」或「你好小竹」弹出语音面板
 *       (iframe 复用全功能语音页); 未开启时右下角显示引导球,
 *       点击开启唤醒(麦克风授权)或直接打开面板手动使用
 * 引擎: 云端流式 ASR 唤醒词检测(/api/xiaozhu/ws/asr, 与语音页
 *       P1 流式轨同协议: auth→ready→PCM 帧→partial/final)
 *       + VAD 音量门限(静默期不推流不烧额度, ring buffer
 *       防漏唤醒词开头) + 频率熔断(单段 3s 封顶/分钟 10 段)
 * 兼容: 纯标准 JS, PC/微信内置浏览器通用; 游客无 JWT 不启动
 *       监听(WS 首条鉴权), 球保留为普通入口
 * 开关: localStorage 'xiaozhu.wake' = on/off; 语音面板内可切换
 *       (同源共享 + postMessage 'xz-wake-on/off' 即时通知)
 * 唤醒词: localStorage 'xiaozhu.wakeword'(面板「⚙️ 唤醒词」设置;
 *       空或「你好小竹」均双词匹配(两声+单声并存)/自定义 2-8 字精确匹配;
 *       postMessage 'xz-wake-word' 即时重建匹配器)
 * 部署: 生产 index.html 注入 <script defer src=/js/voice-wake-widget.js?v=27>
 *       (78号悦声灵犀 v=23: 语音页音色档案/情绪语调/suggest
 *       chips/思考微音效; 双 bump 规约: ①widget 内容
 *       更新须 bump index 引用 ?v=N(/js/ immutable); ②语音页内容
 *       更新须同步 bump 本 VER(iframe src 破语音页缓存))
 */
(function () {
  "use strict";
  var VER = "v=77";
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

  /* ---------- token 过期自愈(WS 鉴权 error 时单飞刷新一次) ----------
     accessToken 仅 2h: 面板 apiFetch 有 401 自愈, 但唤醒走 WS 静默
     失败——过期后喊不醒且无提示。此处对齐 apiFetch 的 refresh 链:
     从读取的同一源取 refreshToken 刷新回写, 下一段自然用新 token;
     单飞防并发, 失败 60s 退避防空烧(refreshToken 7 天也过期时)。 */
  var refBusy = false, refFailAt = 0, refOkAt = 0;
  function tryRefreshToken() {
    var now = Date.now();
    if (refBusy || now - refFailAt < 60000 || now - refOkAt < 30000) { return; }
    /* 跨 iframe 竞态锁(13:33:24 "Token 已被吊销"根因): 本引擎
       与面板 tryPanelRefreshToken 互不知情并发刷新——refresh
       token 轮换下 A 刷得新 token 吊销旧的, B 用旧 refreshToken
       再刷失败/拿到互相吊销的 token 死循环; localStorage 时间
       戳跨 iframe 共享: 20s 内有人刷过直接跳过 */
    try {
      var shared = parseInt(localStorage.getItem("xz.refAt") || "0", 10);
      if (now - shared < 20000) { return; }
    } catch (e) { /* 忽略 */ }
    var src = "", rt = "";
    try {
      var s = JSON.parse(localStorage.getItem("zhuxiang.auth") || "null");
      if (s && s.token && s.refreshToken) {
        src = "zhuxiang.auth"; rt = s.refreshToken;
      }
    } catch (e) { /* 忽略 */ }
    if (!src) {
      try {
        var w = JSON.parse(localStorage.getItem("auth_session") || "null");
        var d = (w && w.data) ? w.data : w;
        if (d && d.accessToken && d.refreshToken) {
          src = "auth_session"; rt = d.refreshToken;
        }
      } catch (e) { /* 忽略 */ }
    }
    if (!src) {
      /* 两源均无 refreshToken(未登录/登录态被清): 明确引导而非静默 */
      refFailAt = now;
      showTip("唤醒待命需要登录——点小竹球打开面板登录后再开启唤醒");
      return;
    }
    refBusy = true;
    try { localStorage.setItem("xz.refAt", String(Date.now())); }
    catch (e) { /* 忽略 */ }
    fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refreshToken: rt })
    }).then(function (r) { return r.json(); }).then(function (j) {
      refBusy = false;
      var nt = j && (j.accessToken || (j.data && j.data.accessToken));
      if (!nt) { refFailAt = Date.now(); return; }
      /* 回写所有存在的源: authToken() 优先读 zhuxiang.auth——若旧
         token 在该源而无 refreshToken, 刷新走 auth_session 只回写
         它, 新 token 永不生效("续期成功却再喊不醒"死循环) */
      try {
        if (localStorage.getItem("zhuxiang.auth")) {
          var s2 = JSON.parse(localStorage.getItem("zhuxiang.auth") || "{}");
          s2.token = nt;
          if (j.refreshToken) { s2.refreshToken = j.refreshToken; }
          localStorage.setItem("zhuxiang.auth", JSON.stringify(s2));
        }
        if (localStorage.getItem("auth_session")) {
          var w2 = JSON.parse(localStorage.getItem("auth_session") || "{}");
          var d2 = (w2 && w2.data) ? w2.data : w2;
          d2.accessToken = nt;
          if (j.refreshToken) { d2.refreshToken = j.refreshToken; }
          localStorage.setItem("auth_session", JSON.stringify(w2));
        }
      } catch (e) { /* 写失败忽略 */ }
      refOkAt = Date.now();
      showTip("登录已自动续期——再喊一声「小竹、小竹」或「你好小竹」即唤醒");
    }).catch(function () { refBusy = false; refFailAt = Date.now(); });
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
    "@keyframes xzPulse{0%,100%{opacity:.35}50%{opacity:.78}}",
    "#xiaozhu-entry-ball.xz-breathe{animation:xzPulse 2.4s ease-in-out infinite}",
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
  /* 加载完成即通知隐藏态: display:none iframe 在 X5 不触发
     visibilitychange, 语音页不知道自己隐藏着——会恢复会话自动
     开免提拿麦克风, 与唤醒引擎启动 getUserMedia 并发撞车
     (X5 独占 → "微信正在录音, 你无法录音") */
  fr.onload = function () {
    if (!panel.classList.contains("open")) { notifyFrame("hide"); }
  };
  wrap.appendChild(fr);
  wrap.appendChild(x);
  wrap.appendChild(xe);
  panel.appendChild(wrap);
  document.body.appendChild(panel);

  function openPanel() {
    panel.classList.add("open");
    panel.classList.remove("mini");
    notifyFrame("show");
    pushPageCtx(); /* 意图锚定: 打开即同步宿主页面上下文 */
    stopWake(); /* 面板打开期间暂停唤醒监听(面板内即语音会话) */
    releaseMic(); /* X5 麦克风独占: 面板免提要录音, 必须先释放唤醒流
                     (否则面板 getUserMedia 被堵——"微信正在录音"死锁) */
  }
  function closePanel() {
    panel.classList.remove("open");
    panel.classList.remove("mini");
    notifyFrame("hide");
    /* 面板会话结束 = 唤醒新起点: 清分钟段计数(用户连测唤醒
       易耗尽额度, 关面板后静默熔断——提示还被限频吞掉) */
    eng.segTimes = [];
    /* 关面板 → 恢复监听: 手势栈内先试(X5: setTimeout 后手势过期,
       延迟创建的 AudioContext 是 suspended——喊了没反应); 语音页
       停麦是异步链, 立即拿可能撞独占——失败再延迟+退避重试 */
    enableWakeQuiet(false).then(function (ok) {
      if (!ok) { resumeTries = 0; scheduleWakeResume(800); }
    });
  }
  /* 恢复监听调度: 800ms 起, 失败退避重试至多 5 次(~11s 窗口,
     覆盖 X5 慢释放), 全败才提示+挂交互兜底 */
  var resumeTimer = null, resumeTries = 0;
  function scheduleWakeResume(delay) {
    clearTimeout(resumeTimer);
    resumeTimer = setTimeout(function () {
      resumeTries++;
      enableWakeQuiet(resumeTries >= 5).then(function (ok) {
        if (ok) { resumeTries = 0; return; }
        if (resumeTries < 5) { scheduleWakeResume(700 + 700 * resumeTries); }
      });
    }, delay);
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

  /* ---------- 意图锚定: 宿主商品页上下文 → 面板 ----------
     Taro hash 路由 #/pages/product-detail/index?id=N →
     postMessage; 面板预取商品名, 用户说「这个多少钱」类
     指代词时前缀注入商品名——问答自动关联当前浏览商品 */
  function pageCtx() {
    var m = (location.hash || "").match(
      /product-detail\/index\?id=(\d+)/);
    return m ? { type: "product", id: m[1] } : null;
  }
  function pushPageCtx() {
    try {
      fr.contentWindow.postMessage(
        { type: "xz-page-ctx", ctx: pageCtx() }, "*");
    } catch (e) { /* iframe 未就绪忽略 */ }
  }
  window.addEventListener("hashchange", pushPageCtx);

  /* 情境提示(轻量引导, 不自动录音仅降门槛): 商品页停留 30s
     且滚动过半时一次性提示语音用法; localStorage 防重 */
  var ctxHintAt = 0, ctxHinted = false;
  try { ctxHinted = localStorage.getItem("xiaozhu.ctxHint") === "1"; }
  catch (e) { /* 忽略 */ }
  setInterval(function () {
    if (ctxHinted || !wakeOn() || !pageCtx()) { ctxHintAt = 0; return; }
    if (!ctxHintAt) { ctxHintAt = Date.now(); return; }
    var deep = window.scrollY + window.innerHeight
      > document.documentElement.scrollHeight * 0.55;
    if (deep && Date.now() - ctxHintAt > 30000) {
      ctxHinted = true;
      try { localStorage.setItem("xiaozhu.ctxHint", "1"); } catch (e) { }
      showTip("逛累了？喊「小竹小竹」可以说「详细介绍」听讲解", 5000);
    }
  }, 4000);

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

  /* 球态: 开启唤醒 → 缩小半透明常显(兜底入口——真机实证 X5 间歇
     坏流下唤醒引擎可能长期唤不醒, 球若隐藏=用户无路可走死锁 UX;
     小点常驻不碍观感, 点击直达面板) */
  function ballMini(on) {
    if (on) {
      b.style.width = "40px";
      b.style.height = "40px";
      b.style.opacity = "";
      b.classList.add("xz-breathe"); /* 待命呼吸动画: 示意监听中 */
      b.title = "唤醒待命中——喊「小竹小竹」或「你好小竹」（点击打开语音面板）";
    } else {
      b.style.width = "52px";
      b.style.height = "52px";
      b.style.opacity = "1";
      b.classList.remove("xz-breathe");
      b.title = "";
    }
  }

  /* 引导提示条 */
  var tip = document.createElement("div");
  tip.id = "xiaozhu-wake-tip";
  document.body.appendChild(tip);
  function showTip(msg, ms) {
    tip.textContent = msg;
    tip.classList.add("show");
    setTimeout(function () { tip.classList.remove("show"); }, ms || 4000);
  }
  /* 诊断提示(限频防刷屏): 熔断/通道错误/转写未匹配 —— 让用户能
     看见唤醒链路断在哪一环, 而非静默失败 */
  var diagLastAt = 0, diagLastMsg = "";
  function diagTip(msg, gapSec) {
    var now = Date.now();
    if (msg === diagLastMsg && now - diagLastAt < (gapSec || 10) * 1000) { return; }
    diagLastAt = now; diagLastMsg = msg;
    showTip(msg, 2600);
  }

  function wakeOn() {
    return localStorage.getItem(WAKE_KEY) === "on";
  }

  /* 球点击: 已开启→开面板(手动); 未开启→引导开启唤醒, 取消则直接开面板 */
  function onBallClick() {
    if (panel.classList.contains("mini")) { maximizePanel(); return; }
    if (wakeOn()) { openPanel(); return; }
    var ok = confirm('开启「小竹」语音唤醒？\n\n'
      + '开启后对着麦克风呼唤"小竹、小竹"或"你好小竹"即可唤出语音精灵\n'
      + '(需使用麦克风, 仅在检测到人声时上传音频);\n'
      + '小竹球将缩小为小点常驻, 点击它随时可直接打开面板。');
    if (!ok) { openPanel(); return; } /* 不开唤醒 → 当普通入口用 */
    enableWake();
  }

  /* ---------- 拼音容错表(GB2312 一级字库 3755 字 → 396 无声调音节,
     gen_pinyin_table.py 生成; 自定义唤醒词同音容错: 唤醒词与
     ASR 转写各转拼音序列做音节级包含匹配, 未收录字符原样保留) ---------- */
  var PYZ = {"a":"啊阿","ai":"埃挨哎唉哀皑癌蔼矮艾碍爱隘","an":"鞍氨安俺按暗岸胺案","ang":"肮昂盎","ao":"凹敖熬翱袄傲奥懊澳","ba":"芭捌扒叭吧笆八疤巴拔跋靶把耙坝霸罢爸","bai":"白柏百摆佰败拜稗","ban":"斑班搬扳般颁板版扮拌伴瓣半办绊","bang":"邦帮梆榜膀绑棒磅蚌镑傍谤","bao":"苞胞包褒薄雹保堡饱宝抱报暴豹鲍爆","bei":"杯碑悲卑北辈背贝钡倍狈备惫焙被","ben":"奔苯本笨","beng":"崩绷甭泵蹦迸","bi":"逼鼻比鄙笔彼碧蓖蔽毕毙毖币庇痹闭敝弊必壁臂避陛","bian":"鞭边编贬扁便变卞辨辩辫遍","biao":"标彪膘表","bie":"鳖憋别瘪","bin":"彬斌濒滨宾摈","bing":"兵冰柄丙秉饼炳病并","bo":"剥玻菠播拨钵波博勃搏铂箔伯帛舶脖膊渤驳卜","bu":"捕哺补埠不布步簿部怖","ca":"擦","cai":"猜裁材才财睬踩采彩菜蔡","can":"餐参蚕残惭惨灿掺","cang":"苍舱仓沧藏","cao":"操糙槽曹草","ce":"厕策侧册测","ceng":"层蹭曾","cha":"插叉茬茶查碴搽察岔差诧","chai":"拆柴豺","chan":"搀蝉馋谗缠铲产阐颤","chang":"昌猖场尝常偿肠厂敞畅唱倡","chao":"超抄钞朝嘲潮巢吵炒","che":"车扯撤掣彻澈","chen":"郴臣辰尘晨忱沉陈趁衬","cheng":"撑称城橙成呈乘程惩澄诚承逞骋秤","chi":"吃痴持池迟弛驰耻齿侈尺赤翅斥炽","chong":"充冲虫崇宠","chou":"抽酬畴踌稠愁筹仇绸瞅丑臭","chu":"初出橱厨躇锄雏滁除楚础储矗搐触处畜","chuai":"揣","chuan":"川穿椽传船喘串","chuang":"疮窗幢床闯创","chui":"吹炊捶锤垂椎","chun":"春椿醇唇淳纯蠢","chuo":"戳绰","ci":"疵茨磁雌辞慈瓷词此刺赐次伺","cong":"聪葱囱匆从丛","cou":"凑","cu":"粗醋簇促","cuan":"蹿篡窜","cui":"摧崔催脆瘁粹淬翠","cun":"村存寸","cuo":"磋撮搓措挫错","da":"搭达答瘩打大","dai":"呆歹傣戴带殆代贷袋待逮怠","dan":"耽担丹单郸掸胆旦氮但惮淡诞弹蛋","dang":"当挡党荡档","dao":"刀捣蹈倒岛祷导到稻悼道盗","de":"德得的","deng":"蹬灯登等瞪凳邓","di":"堤低滴迪敌笛狄涤翟嫡抵底地蒂第帝弟递缔","dian":"颠掂滇碘点典靛垫电佃甸店惦奠淀殿","diao":"碉叼雕凋刁掉吊钓调","die":"跌爹碟蝶迭谍叠","ding":"丁盯叮钉顶鼎锭定订","diu":"丢","dong":"东冬董懂动栋侗恫冻洞","dou":"兜抖斗陡豆逗痘都","du":"督毒犊独读堵睹赌杜镀肚度渡妒","duan":"端短锻段断缎","dui":"堆兑队对","dun":"墩吨蹲敦顿囤钝盾遁","duo":"掇哆多夺垛躲朵跺舵剁惰堕","e":"蛾峨鹅俄额讹娥恶厄扼遏鄂饿","en":"恩","er":"而儿耳尔饵洱二贰","fa":"发罚筏伐乏阀法珐","fan":"藩帆番翻樊矾钒繁凡烦反返范贩犯饭泛","fang":"坊芳方肪房防妨仿访纺放","fei":"菲非啡飞肥匪诽吠肺废沸费","fen":"芬酚吩氛分纷坟焚汾粉奋份忿愤粪","feng":"丰封枫蜂峰锋风疯烽逢冯缝讽奉凤","fou":"否","fu":"佛夫敷肤孵扶拂辐幅氟符伏俘服浮涪福袱弗甫抚辅俯釜斧腑府腐赴副覆赋复傅付阜父腹负富讣附妇缚咐","ga":"噶嘎","gai":"该改概钙盖溉","gan":"干甘杆柑竿肝赶感秆敢赣","gang":"冈刚钢缸肛纲岗港杠","gao":"篙皋高膏羔糕搞镐稿告","ge":"哥歌搁戈鸽胳疙割革葛格阁隔铬个各咯","gei":"给","gen":"根跟","geng":"耕更庚羹埂耿梗","gong":"工攻功恭龚供躬公宫弓巩汞拱贡共","gou":"钩勾沟苟狗垢构购够","gu":"辜菇咕箍估沽孤姑鼓古蛊骨谷股故顾固雇","gua":"刮瓜剐寡挂褂","guai":"乖拐怪","guan":"棺关官冠观管馆罐惯灌贯","guang":"光广逛","gui":"瑰规圭硅归龟闺轨鬼诡癸桂柜跪贵刽傀炔","gun":"辊滚棍","guo":"锅郭国果裹过","ha":"蛤哈","hai":"骸孩海氦亥害骇还","han":"酣憨邯韩含涵寒函喊罕翰撼捍旱憾悍焊汗汉","hang":"夯杭航","hao":"壕嚎豪毫郝好耗号浩貉","he":"呵喝荷菏核禾和何合盒阂河涸赫褐鹤贺","hei":"嘿黑","hen":"痕很狠恨","heng":"哼亨横衡恒","hong":"轰哄烘虹鸿洪宏弘红","hou":"喉侯猴吼厚候后","hu":"呼乎忽瑚壶葫胡蝴狐糊湖弧虎唬护互沪户","hua":"花哗华猾滑画划化话","huai":"槐徊怀淮坏","huan":"欢环桓缓换患唤痪豢焕涣宦幻","huang":"荒慌黄磺蝗簧皇凰惶煌晃幌恍谎","hui":"灰挥辉徽恢蛔回毁悔慧卉惠晦贿秽会烩汇讳诲绘","hun":"荤昏婚魂浑混","huo":"豁活伙火获或惑霍货祸","ji":"击圾基机畸稽积箕肌饥迹激讥鸡姬绩缉吉极棘辑籍集及急疾汲即嫉级挤几脊己蓟技冀季伎祭剂悸济寄寂计记既忌际妓继纪藉","jia":"嘉枷夹佳家加荚颊贾甲钾假稼价架驾嫁茄","jian":"歼监坚尖笺间煎兼肩艰奸缄茧检柬碱硷拣捡简俭剪减荐鉴践贱见键箭件健舰剑饯渐溅涧建","jiang":"僵姜将浆江疆蒋桨奖讲匠酱降","jiao":"蕉椒礁焦胶交郊浇骄娇搅铰矫侥脚狡角饺缴绞剿教酵轿较叫窖","jie":"揭接皆秸街阶截劫节杰捷睫竭洁结解姐戒芥界借介疥诫届","jin":"巾筋斤金今津襟紧锦仅谨进靳晋禁近烬浸尽劲","jing":"荆兢茎睛晶鲸京惊精粳经井警景颈静境敬镜径痉靖竟竞净","jiong":"炯窘","jiu":"揪究纠玖韭久灸九酒厩救旧臼舅咎就疚","ju":"桔鞠拘狙疽居驹菊局咀矩举沮聚拒据巨具距踞锯俱句惧炬剧","juan":"捐鹃娟倦眷卷绢","jue":"嚼撅攫抉掘倔爵觉决诀绝","jun":"均菌钧军君峻俊竣浚郡骏","ka":"喀咖卡","kai":"开揩楷凯慨","kan":"槛刊堪勘坎砍看","kang":"康慷糠扛抗亢炕","kao":"考拷烤靠","ke":"坷苛柯棵磕颗科壳咳可渴克刻客课","ken":"肯啃垦恳","keng":"坑吭","kong":"空恐孔控","kou":"抠口扣寇","ku":"枯哭窟苦酷库裤","kua":"夸垮挎跨胯","kuai":"块筷侩快","kuan":"宽款","kuang":"匡筐狂框矿眶旷况","kui":"亏盔岿窥葵奎魁馈愧溃","kun":"坤昆捆困","kuo":"括扩廓阔","la":"垃拉喇蜡腊辣啦","lai":"莱来赖","lan":"蓝婪栏拦篮阑兰澜谰揽览懒缆烂滥","lang":"琅榔狼廊郎朗浪","lao":"捞劳牢老佬姥酪烙涝潦","le":"乐肋了","lei":"勒雷镭蕾磊累儡垒擂类泪","leng":"棱楞冷","li":"厘梨犁黎篱狸离漓理李里鲤礼莉荔吏栗丽厉励砾历利傈例俐痢立粒沥隶力璃哩","lia":"俩","lian":"联莲连镰廉怜涟帘敛脸链恋炼练","liang":"粮凉梁粱良两辆量晾亮谅","liao":"撩聊僚疗燎寥辽撂镣廖料","lie":"列裂烈劣猎","lin":"琳林磷霖临邻鳞淋凛赁吝拎","ling":"玲菱零龄铃伶羚凌灵陵岭领另令","liu":"溜琉榴硫馏留刘瘤流柳六","long":"龙聋咙笼窿隆垄拢陇","lou":"楼娄搂篓漏陋","lu":"芦卢颅庐炉掳卤虏鲁麓碌露路赂鹿潞禄录陆戮","luan":"峦挛孪滦卵乱","lun":"抡轮伦仑沦纶论","luo":"萝螺罗逻锣箩骡裸落洛骆络","lv":"驴吕铝侣旅履屡缕虑氯律率滤绿","lve":"掠略","ma":"妈麻玛码蚂马骂嘛吗","mai":"埋买麦卖迈脉","man":"瞒馒蛮满蔓曼慢漫谩","mang":"芒茫盲氓忙莽","mao":"猫茅锚毛矛铆卯茂冒帽貌贸","me":"么","mei":"玫枚梅酶霉煤没眉媒镁每美昧寐妹媚","men":"门闷们","meng":"萌蒙檬盟锰猛梦孟","mi":"眯醚靡糜迷谜弥米秘觅泌蜜密幂","mian":"棉眠绵冕免勉娩缅面","miao":"苗描瞄藐秒渺庙妙","mie":"蔑灭","min":"民抿皿敏悯闽","ming":"明螟鸣铭名命","miu":"谬","mo":"摸摹蘑模膜磨摩魔抹末莫墨默沫漠寞陌","mou":"谋牟某","mu":"拇牡亩姆母墓暮幕募慕木目睦牧穆","na":"拿哪呐钠那娜纳","nai":"氖乃奶耐奈","nan":"南男难","nang":"囊","nao":"挠脑恼闹淖","ne":"呢","nei":"馁内","nen":"嫩","neng":"能","ni":"妮霓倪泥尼拟你匿腻逆溺","nian":"蔫拈年碾撵捻念辗","niang":"娘酿","niao":"鸟尿","nie":"捏聂孽啮镊镍涅","nin":"您","ning":"柠狞凝宁拧泞","niu":"牛扭钮纽","nong":"脓浓农弄","nu":"奴努怒","nuan":"暖","nuo":"挪懦糯诺","nv":"女","nve":"虐疟","o":"哦","ou":"欧鸥殴藕呕偶沤","pa":"啪趴爬帕怕琶","pai":"拍排牌徘湃派","pan":"攀潘盘磐盼畔判叛","pang":"乓庞旁耪胖","pao":"抛咆刨炮袍跑泡","pei":"呸胚培裴赔陪配佩沛","pen":"喷盆","peng":"砰抨烹澎彭蓬棚硼篷膨朋鹏捧碰","pi":"辟坯砒霹批披劈琵毗啤脾疲皮匹痞僻屁譬","pian":"篇偏片骗","piao":"飘漂瓢票","pie":"撇瞥","pin":"拼频贫品聘","ping":"乒坪苹萍平凭瓶评屏","po":"泊坡泼颇婆破魄迫粕","pou":"剖","pu":"脯扑铺仆莆葡菩蒲埔朴圃普浦谱曝瀑","qi":"期欺栖戚妻七凄漆柒沏其棋奇歧畦崎脐齐旗祈祁骑起岂乞企启契砌器气迄弃汽泣讫","qia":"掐恰洽","qian":"牵扦钎铅千迁签仟谦乾黔钱钳前潜遣浅谴堑嵌欠歉","qiang":"枪呛腔羌墙蔷强抢","qiao":"橇锹敲悄桥瞧乔侨巧鞘撬翘峭俏窍","qie":"切且怯窃","qin":"钦侵亲秦琴勤芹擒禽寝沁","qing":"青轻氢倾卿清擎晴氰情顷请庆","qiong":"琼穷","qiu":"秋丘邱球求囚酋泅","qu":"趋区蛆曲躯屈驱渠取娶龋趣去","quan":"圈颧权醛泉全痊拳犬券劝","que":"缺瘸却鹊榷确雀","qun":"裙群","ran":"然燃冉染","rang":"瓤壤攘嚷让","rao":"饶扰绕","re":"惹热","ren":"壬仁人忍韧任认刃妊纫","reng":"扔仍","ri":"日","rong":"戎茸蓉荣融熔溶容绒冗","rou":"揉柔肉","ru":"茹蠕儒孺如辱乳汝入褥","ruan":"软阮","rui":"蕊瑞锐","run":"闰润","ruo":"若弱","sa":"撒洒萨","sai":"腮鳃塞赛","san":"三叁伞散","sang":"桑嗓丧","sao":"搔骚扫嫂","se":"瑟色涩","sen":"森","seng":"僧","sha":"莎砂杀刹沙纱傻啥煞厦","shai":"筛晒","shan":"珊苫杉山删煽衫闪陕擅赡膳善汕扇缮","shang":"墒伤商赏晌上尚裳","shao":"梢捎稍烧芍勺韶少哨邵绍","she":"奢赊蛇舌舍赦摄射慑涉社设","shen":"砷申呻伸身深娠绅神沈审婶甚肾慎渗什","sheng":"声生甥牲升绳省盛剩胜圣","shi":"匙师失狮施湿诗尸虱十石拾时食蚀实识史矢使屎驶始式示士世柿事拭誓逝势是嗜噬适仕侍释饰氏市恃室视试似","shou":"收手首守寿授售受瘦兽","shu":"蔬枢梳殊抒输叔舒淑疏书赎孰熟薯暑曙署蜀黍鼠属术述树束戍竖墅庶数漱恕","shua":"刷耍","shuai":"摔衰甩帅","shuan":"栓拴","shuang":"霜双爽","shui":"谁水睡税","shun":"吮瞬顺舜","shuo":"说硕朔烁","si":"斯撕嘶思私司丝死肆寺嗣四饲巳","song":"松耸怂颂送宋讼诵","sou":"搜艘擞嗽","su":"苏酥俗素速粟僳塑溯宿诉肃","suan":"酸蒜算","sui":"虽隋随绥髓碎岁穗遂隧祟","sun":"孙损笋","suo":"蓑梭唆缩琐索锁所","ta":"塌他它她塔獭挞蹋踏","tai":"胎苔抬台泰酞太态汰","tan":"坍摊贪瘫滩坛檀痰潭谭谈坦毯袒碳探叹炭","tang":"汤塘搪堂棠膛唐糖倘躺淌趟烫","tao":"掏涛滔绦萄桃逃淘陶讨套","te":"特","teng":"藤腾疼誊","ti":"梯剔踢锑提题蹄啼体替嚏惕涕剃屉","tian":"天添填田甜恬舔腆","tiao":"挑条迢眺跳","tie":"贴铁帖","ting":"厅听烃汀廷停亭庭挺艇","tong":"通桐酮瞳同铜彤童桶捅筒统痛","tou":"偷投头透","tu":"凸秃突图徒途涂屠土吐兔","tuan":"湍团","tui":"推颓腿蜕褪退","tun":"吞屯臀","tuo":"拖托脱鸵陀驮驼椭妥拓唾","wa":"挖哇蛙洼娃瓦袜","wai":"歪外","wan":"豌弯湾玩顽丸烷完碗挽晚皖惋宛婉万腕","wang":"汪王亡枉网往旺望忘妄","wei":"威巍微危韦违桅围唯惟为潍维苇萎委伟伪尾纬未蔚味畏胃喂魏位渭谓尉慰卫","wen":"瘟温蚊文闻纹吻稳紊问","weng":"嗡翁瓮","wo":"挝蜗涡窝我斡卧握沃","wu":"巫呜钨乌污诬屋无芜梧吾吴毋武五捂午舞伍侮坞戊雾晤物勿务悟误","xi":"昔熙析西硒矽晰嘻吸锡牺稀息希悉膝夕惜熄烯溪汐犀檄袭席习媳喜铣洗系隙戏细","xia":"瞎虾匣霞辖暇峡侠狭下夏吓","xian":"掀锨先仙鲜纤咸贤衔舷闲涎弦嫌显险现献县腺馅羡宪陷限线","xiang":"相厢镶香箱襄湘乡翔祥详想响享项巷橡像向象","xiao":"萧硝霄哮嚣销消宵淆晓小孝校肖啸笑效","xie":"楔些歇蝎鞋协挟携邪斜胁谐写械卸蟹懈泄泻谢屑","xin":"薪芯锌欣辛新忻心信衅","xing":"星腥猩惺兴刑型形邢行醒幸杏性姓","xiong":"兄凶胸匈汹雄熊","xiu":"休修羞朽嗅锈秀袖绣","xu":"墟戌需虚嘘须徐许蓄酗叙旭序恤絮婿绪续吁","xuan":"轩喧宣悬旋玄选癣眩绚","xue":"削靴薛学穴雪血","xun":"勋熏循旬询寻驯巡殉汛训讯逊迅","ya":"压押鸦鸭呀丫芽牙蚜崖衙涯雅哑亚讶轧","yan":"焉咽阉烟淹盐严研蜒岩延言颜阎炎沿奄掩眼衍演艳堰燕厌砚雁唁彦焰宴谚验","yang":"殃央鸯秧杨扬佯疡羊洋阳氧仰痒养样漾","yao":"邀腰妖瑶摇尧遥窑谣姚咬舀药要耀钥","ye":"椰噎耶爷野冶也页掖业叶曳腋夜液","yi":"一壹医揖铱依伊衣颐夷遗移仪胰疑沂宜姨彝椅蚁倚已乙矣以艺抑易邑屹亿役臆逸肄疫亦裔意毅忆义益溢诣议谊译异翼翌绎","yin":"茵荫因殷音阴姻吟银淫寅饮尹引隐印","ying":"英樱婴鹰应缨莹萤营荧蝇迎赢盈影颖硬映","yo":"哟","yong":"拥佣臃痈庸雍踊蛹咏泳涌永恿勇用","you":"幽优悠忧尤由邮铀犹油游酉有友右佑釉诱又幼","yu":"迂淤于盂榆虞愚舆余俞逾鱼愉渝渔隅予娱雨与屿禹宇语羽玉域芋郁遇喻峪御愈欲狱育誉浴寓裕预豫驭","yuan":"鸳渊冤元垣袁原援辕园员圆猿源缘远苑愿怨院","yue":"曰约越跃岳粤月悦阅","yun":"耘云郧匀陨允运蕴酝晕韵孕","za":"匝砸杂咋","zai":"栽哉灾宰载再在仔","zan":"咱攒暂赞","zang":"赃脏葬","zao":"遭糟凿藻枣早澡蚤躁噪造皂灶燥","ze":"责择则泽","zei":"贼","zen":"怎","zeng":"增憎赠","zha":"扎喳渣札铡闸眨栅榨乍炸诈柞","zhai":"摘斋宅窄债寨","zhan":"瞻毡詹粘沾盏斩崭展蘸栈占战站湛绽","zhang":"长樟章彰漳张掌涨杖丈帐账仗胀瘴障","zhao":"招昭找沼赵照罩兆肇召爪","zhe":"遮折哲蛰辙者锗蔗这浙着","zhen":"珍斟真甄砧臻贞针侦枕疹诊震振镇阵帧","zheng":"蒸挣睁征狰争怔整拯正政症郑证","zhi":"芝枝支吱蜘知肢脂汁之织职直植殖执值侄址指止趾只旨纸志挚掷至致置帜峙制智秩稚质炙痔滞治窒","zhong":"中盅忠钟衷终种肿重仲众","zhou":"舟周州洲诌粥轴肘帚咒皱宙昼骤","zhu":"珠株蛛朱猪诸诛逐竹烛煮拄瞩嘱主著柱助蛀贮铸筑住注祝驻","zhua":"抓","zhuai":"拽","zhuan":"专砖转撰赚篆","zhuang":"桩庄装妆撞壮状","zhui":"锥追赘坠缀","zhun":"谆准","zhuo":"捉拙卓桌茁酌啄灼浊","zi":"兹咨资姿滋淄孜紫籽滓子自渍字","zong":"鬃棕踪宗综总纵","zou":"邹走奏揍","zu":"租足卒族祖诅阻组","zuan":"钻纂","zui":"嘴醉最罪","zun":"尊遵","zuo":"琢昨左佐做作坐座"};
  var PY_MAP = null;
  function charToPy(ch) {
    if (!PY_MAP) {
      PY_MAP = {};
      for (var syl in PYZ) {
        var str = PYZ[syl];
        for (var i = 0; i < str.length; i++) { PY_MAP[str[i]] = syl; }
      }
    }
    return PY_MAP[ch] || ch;
  }
  function toPinyinSeq(text) {
    var out = [];
    for (var i = 0; i < text.length; i++) { out.push(charToPy(text[i])); }
    return out.join("-");
  }

  /* ---------- 唤醒引擎(VAD + 云端流式 ASR) ---------- */

  /* 唤醒词匹配器(可配置, 语音面板「⚙️ 唤醒词」设置 → localStorage
     'xiaozhu.wakeword' + postMessage 'xz-wake-word' 实时重建):
     - 空(默认)/存量「你好小竹」: 双唤醒词并存——「小竹」两声
       (中间 ≤4 字符填充) OR「你好小竹」单声, 任一命中即唤醒,
       均同音容错(弱音量下两声的第一声易被 ASR 糊掉, 双词兜底)
     - 自定义 2-8 字: 双轨——精确文本(正则元字符转义) OR
       拼音序列包含(GB2312 一级字库同音容错, 音节级匹配) */
  var XZ = "(?:小竹|小主|小猪|小朱|小珠|晓竹|小助|小逐|小烛)";
  function getWord() {
    var w = "";
    try { w = String(localStorage.getItem(WORD_KEY) || "").trim(); }
    catch (e) { /* 忽略 */ }
    return w;
  }
  function buildWakeMatcher() {
    var w = getWord();
    if (!w || w === "你好小竹") {
      /* 双词并存 + 拼音兜底: 两声/单声正则之上, 短文本(≤6 字)
         拼音含 xiao-zhu 即命中——覆盖 XZ 表外同音(小住/小煮)、
         首声被吞(「猪小猪」)与裸单声「小竹」; 唤醒场景权衡
         宁误勿漏(误唤醒=面板弹出可关, 漏唤醒=功能失灵) */
      var def = new RegExp(XZ + "[\\s\\S]{0,4}?" + XZ);
      var hi = new RegExp("你好[\\s，,、。]?" + XZ);
      return { test: function (t) {
        if (!t) { return false; }
        if (def.test(t) || hi.test(t)) { return true; }
        if (t.length > 6) { return false; }
        return toPinyinSeq(
          t.replace(/[\s，,、。？！?!.!…·"'’]/g, "")
        ).indexOf("xiao-zhu") !== -1;
      } };
    }
    /* 自定义: 精确文本 OR 拼音序列(音节级包含, 标点原样保留断界) */
    var esc = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    var reExact = new RegExp(esc);
    var wordPy = toPinyinSeq(w);
    return { test: function (t) {
      if (reExact.test(t)) { return true; }
      return wordPy && t
        ? toPinyinSeq(t).indexOf(wordPy) !== -1
        : false;
    } };
  }
  var wakeMatcher = buildWakeMatcher();
  function matchWake(text) {
    return wakeMatcher.test(String(text || ""));
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
    /* AHM 音频健康度(Audio Health Monitor): X5 哑流检测——
       链路感知驱动: 判断唤醒词之前先判断音频流是否活着 */
    zeroFrames: 0,     /* 连续全零帧计数(真静音有非零底噪, 哑流是精确 0) */
    ahmDead: false,    /* 哑流已判定(防重复告警) */
    ahmResetAt: 0,     /* 上次哑流硬复位时刻(30s 退避防循环) */
  };
  /* V5.1 弱轮词头捕获: 失败轮实证 rms 456 vs 成功轮 2396+
     (同位置喊能量波动 5~10 倍)——弱轮音量缓升过门限, 起段
     慢词头被截; ring 1.5→2.5s 回补更早, TH_ON 0.012→0.009
     弱轮更早起段(误起段代价=快收段 1s+段熔断兜底) */
  var RING_MAX = 16000 * 2.5;   /* 环形缓存 2.5s@16k */
  var TH_ON = 0.009;            /* 起 VAD 门限(RMS) */
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
          /* AEC 统一关(与语音面板一致): X5 上 AEC on/off 流交替
             触发音频路由错乱——后续流静音(真机实证: 鉴权成功
             推流但 ASR 无转写, 唤醒无声无息) */
          echoCancellation: false, noiseSuppression: true,
          /* AGC 关(17:45-51 两批 dump 实证): X5 系统 AGC 把强
             音节在采集层拉到 ±1.0 削顶(源方波化, 后端软压缩救
             不了)——「小竹小竹」瞬态全废只出「爱」碎片; 关后源
             回归自然动态, 归一化交给 V3.4 双向增益(0.3~12x) */
          autoGainControl: false,
        },
      });
    } catch (e) {
      showTip("麦克风不可用，无法开启唤醒——浮球保留，点击可直接打开语音面板");
      return;
    }
    localStorage.setItem(WAKE_KEY, "on");
    ballMini(true); /* 缩小常显兜底入口(不再隐藏) */
    startWake();
    showTip("语音唤醒已开启——呼唤「小竹、小竹」或「你好小竹」试试（小竹球点击可直接打开面板）", 5000);
  }

  function startWake() {
    if (!wakeOn() || !eng.stream || eng.on) { return; }
    try {
      /* ctx 复用(13:42 二次唤不醒根修): 新建 ctx 在 X5 为
         suspended 态需用户手势解锁——rebuildWakeStream 后
         用户等唤醒不会点屏幕→引擎挂起→识别全碎片; 保留
         已解锁 ctx 只重挂流, 零手势依赖; closed 态(系统后台
         回收)例外——挂死实例不可复用, 重建+点击兜底提示 */
      if (!eng.ctx || eng.ctx.state === "closed") {
        eng.ctx = new (window.AudioContext
          || window.webkitAudioContext)();
      }
      if (eng.ctx.state === "suspended") {
        try { eng.ctx.resume(); } catch (e) { /* 点击兜底 */ }
      }
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
    /* X5: 无手势(页面加载自动恢复监听)创建的 ctx 处于 suspended——
       音频图不跑, VAD 失效(麦克风已允许但喊了没反应); 权限弹窗的
       点击不算页面手势。挂一次性交互 resume(任意触摸即活) */
    if (eng.ctx.state === "suspended") { armCtxResume(); }
    preconnect(); /* V2: 唤醒开启即预建 armed 连接 */
  }
  var ctxResumeHandler = null;
  function armCtxResume() {
    if (ctxResumeHandler || !eng.ctx) { return; }
    ctxResumeHandler = function () {
      document.removeEventListener("touchend", ctxResumeHandler, true);
      document.removeEventListener("click", ctxResumeHandler, true);
      ctxResumeHandler = null;
      try { if (eng.ctx && eng.ctx.state === "suspended") { eng.ctx.resume(); } }
      catch (e) { /* 忽略 */ }
    };
    document.addEventListener("touchend", ctxResumeHandler, true);
    document.addEventListener("click", ctxResumeHandler, true);
    /* 无提示的静默等待 = 用户不知要点——明确引导 */
    showTip("👆 点一下屏幕激活唤醒监听", 5000);
  }

  function stopWake() {
    if (!eng.on) { return; }
    eng.on = false;
    teardownSeg();
    dropPre(); /* 预热池拆(段连接已回池一并拆): 面板期间不留
                  连接——麦克风交接优先; closePanel 恢复时
                  startWake 末尾 preconnect() 重建 */
    try { if (eng.proc) { eng.proc.disconnect(); eng.proc.onaudioprocess = null; } } catch (e) { /* 忽略 */ }
    try { if (eng.analyser) { eng.analyser.disconnect(); } } catch (e) { /* 忽略 */ }
    /* ctx 保留不 close(rebuild 零手势依赖): X5 新建 ctx 为
       suspended 需点击解锁——已解锁的 ctx 跨 rebuild 复用 */
    eng.analyser = null; eng.proc = null;
  }
  /* 彻底释放麦克风流(X5 独占: 面板与唤醒必须交接, 不能并存持有) */
  function releaseMic() {
    try {
      if (eng.stream) {
        eng.stream.getTracks().forEach(function (t) { t.stop(); });
      }
    } catch (e) { /* 忽略 */ }
    eng.stream = null;
  }
  /* 面板归还麦克风后流重建(13:09:47 实证「二次唤醒不了」根因):
     面板取流期间本引擎旧流被 X5 系统单占 mute, 归还后旧流对象
     不自愈(识别质量残化只出「小。」碎片)——拆链重取; 10s 节流
     防频繁开关对话时空转重建 */
  var lastRebuildAt = 0;
  async function rebuildWakeStream() {
    if (!wakeOn()) { return; }
    var now = Date.now();
    if (now - lastRebuildAt < 10000) { return; }
    lastRebuildAt = now;
    try {
      stopWake();
      releaseMic();
      /* 300ms 释放竞态缓冲: 面板 tracks.stop() 后 X5 麦克风
         资源释放有延迟, 立即重取可能拿到哑流 */
      await new Promise(function (res) {
        setTimeout(res, 300);
      });
      eng.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false, noiseSuppression: true,
          autoGainControl: false,
        },
      });
      startWake();
      /* 结果留痕(降级可感知原则): 成功/失败 console 可查,
         失败明确 tip——静默失败=设备看似正常实则失聪 */
      try { console.info("[wake-rebuild] ok stream=1"); }
      catch (e) { /* 忽略 */ }
    } catch (e) {
      try { console.info("[wake-rebuild] failed: " + (e && e.message)); }
      catch (e2) { /* 忽略 */ }
      showTip("语音通道重连失败——点一下小竹球重试唤醒", 6000);
    }
  }

  /* 每音频帧: RMS 能量 VAD + 重采样 16k 入环形缓存/推流 */
  function onAudioFrame(ev) {
    if (!eng.on || eng.pendingResub) { return; }
    var f32 = ev.inputBuffer.getChannelData(0);
    var sum = 0, zeros = 0;
    for (var i = 0; i < f32.length; i++) {
      var v = f32[i];
      sum += v * v;
      if (v < 1e-5 && v > -1e-5) { zeros++; }
    }
    var rms = Math.sqrt(sum / f32.length);
    /* AHM 零值帧检测: 真静音含非零底噪, 哑流(X5 路由错乱)是
       精确 0 填充——连续全零帧 ≥6(约 500ms)判定链路死亡 */
    if (zeros >= f32.length) {
      eng.zeroFrames++;
      if (eng.zeroFrames >= 6 && !eng.ahmDead) {
        eng.ahmDead = true;
        ahmRecover();
      }
    } else {
      eng.zeroFrames = 0;
      eng.ahmDead = false;  /* 链路恢复(重置后新流正常) */
    }

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
      segFeed(); /* v3.3: 纯发送(入队已由上方 pushRing 完成) */
      var now = Date.now();
      var dur = now - eng.segStart;
      if (rms < TH_OFF) {
        eng.loStreak++;
        /* 静默 0.9s → 收段(21:12 dump 复盘修正: 0.5s 快收把
           「小竹-换气-小竹」切成两段, 两声匹配必败+间隙噪音
           段(rms~100)泛滥; 成功轮段长实证 2.2~4.6s——0.9s 给
           换气留 0.75s 窗口, 仍比原 1.2s 快 0.3s);
           单段 3s 强制收段(v77: 原 6s——KWS 轨只需唤醒词
           2s 内说完, 环境视频声持续有声不静默每段烧满 6s,
           24h 实证 62% 轮是环境声长转写; 3s 封顶砍半额度) */
        if ((eng.loStreak >= 9 && dur > 900) || dur > 3000) { endSegment(); }
      } else { eng.loStreak = 0; }
    }
  }

  /* AHM 哑流自动恢复: 硬复位(释放哑流重新获取)一次;
     30s 内复发(重置后仍哑, X5 路由坏到底) → 明确告警不循环 */
  function ahmRecover() {
    try { console.info("[AHM] silent stream detected"
      + " - hard mic reset"); } catch (e) { /* 忽略 */ }
    var now = Date.now();
    if (now - eng.ahmResetAt < 30000) {
      try { console.info("[AHM] persists after reset"); } catch (e) {}
      showTip("麦克风音频链路异常——请刷新页面恢复语音唤醒", 6000);
      return;
    }
    eng.ahmResetAt = now;
    eng.zeroFrames = 0;   /* 每轮复位重新判定: 新流再哑满 6 帧
                             二次进入 → 30s 退避内 → 告警不循环 */
    eng.ahmDead = false;
    stopWake();
    releaseMic();
    enableWakeQuiet(true);
  }

  function pushRing(samples) {
    /* V3.4 双向增益 + 软限幅(实测 dump 勘误: 非弱音频——peak 全
       钉 32768 削顶, 「小竹小竹」高频瞬态被砍致 ASR 只出「我/
       你」碎片): ①每帧独立算目标, 双向平滑跟随(限速 1.6x/帧,
       下限 0.3 上限 12)——根治"只升不降"下 X5 AGC 强帧撞顶;
       ②目标 rms 0.10→0.06 留削顶余量; ③±0.95 软压缩防硬削
       (仅救我方增益削顶; 源自身 AGC 削顶留 getUserMedia 约束
       后手); 段首重置(beginSegment), 静音帧不调 */
    var sq = 0;
    for (var i = 0; i < samples.length; i++) {
      sq += samples[i] * samples[i];
    }
    var rms = Math.sqrt(sq / samples.length);
    if (rms > 0.003) {
      var want = Math.min(Math.max(0.06 / rms, 0.3), 12);
      var cur = eng.gain || 1;
      eng.gain = want > cur
        ? Math.min(want, cur * 1.6)
        : Math.max(want, cur / 1.6);
    }
    var g = eng.gain || 1;
    for (var i = 0; i < samples.length; i++) {
      var s = samples[i] * g;
      /* V3.5 尖峰压缩收紧: 18:13 批 dump 峰值仍 32768——
         rms 仅 4000~5600 却削顶=瞬态尖峰(声母爆破音)打满,
         ±0.95 起压太晚(源 Float32 尖峰>1.0, 段首 gain 未
         收敛即 clamp); 0.8 起压×0.15 斜率×0.95 封顶——
         1.5 尖峰压到 0.905, 声母瞬态保真不再砍头 */
      if (s > 0.8) {
        s = 0.8 + (s - 0.8) * 0.15; if (s > 0.95) { s = 0.95; }
      } else if (s < -0.8) {
        s = -0.8 + (s + 0.8) * 0.15; if (s < -0.95) { s = -0.95; }
      }
      eng.ring.push(s < 0 ? s * 32768 : s * 32767);
      /* V5.2 段能量累计(源 rms)——空转写轮健康度判定:
         弱轮(源~0.001)重试无用走提示, 健康轮(源≥0.006)
         是百炼偶发抖动 → 缓存音频自动二打 */
      if (eng.speaking) {
        eng.segRmsSum = (eng.segRmsSum || 0) + rms;
        eng.segFrames = (eng.segFrames || 0) + 1;
      }
    }
    while (eng.ring.length > RING_MAX) { eng.ring.splice(0, eng.ring.length - RING_MAX); }
  }

  /* ---------- V2 连接预热池(握手+鉴权前置) ----------
     唤醒开启即预建 WS+auth(armed), VAD 起段取池直发 arm——
     省 ~1s 握手+鉴权往返(跨境 RTT×2); 段后保活回池, 连测
     唤醒复用 ×N; 25s ping 穿 nginx 空闲超时(默认 60s 拆);
     唤醒关闭/面板打开即拆(麦克风交接优先, 连接不占百炼流
     不挤 ASR 并发红线) */
  var preWs = null, pingTimer = null;
  function stopPing() {
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
  }
  function startPing() {
    stopPing();
    pingTimer = setInterval(function () {
      if (preWs && preWs.readyState === 1) {
        try { preWs.send(JSON.stringify({ type: "ping" })); }
        catch (e) { stopPing(); }
      } else { stopPing(); }
    }, 25000);
  }
  function dropPre() {
    stopPing();
    if (preWs) { try { preWs.close(); } catch (e) { /* 忽略 */ } preWs = null; }
  }

  /* ---------- V6 连接自愈(息屏/切后台 1006 后回前台) ----------
     21:31:29 实证: 手机切后台冻结 JS 杀 WS(err=1006
     ABNORMAL_CLOSURE recv=empty), onclose 只清引用不重建
     ——回前台后 widget 轨永久哑掉(后续无任何 ws_asr_final,
     喊唤醒无反应)。自愈双路:
     1) visibilitychange 回前台: ctx 恢复 + 麦克风流探活
        + 死连接重建(X5 冻结期三杀: WS/audio 图/流)
     2) preWs onclose(前台): 带退避自动重建防池空 */
  var preReconnTimer = null, preReconnTries = 0;
  function schedulePreReconnect(delay) {
    /* eng.on 守卫: 面板打开期 stopWake→dropPre 触发的 onclose
       不重建(麦克风交接优先, 连接不占百炼流) */
    if (!eng.on || !wakeOn() || !authToken()) { return; }
    if (document.visibilityState !== "visible") { return; }
    clearTimeout(preReconnTimer);
    preReconnTimer = setTimeout(function () {
      preReconnTimer = null;
      if (!eng.on || !wakeOn() || !authToken()) { return; }
      if ((preWs && preWs.readyState === 1)
          || (eng.ws && eng.ws.readyState <= 1)) {
        preReconnTries = 0; return; /* 池活/段在建 */
      }
      preconnect();
      /* armed 入池延迟判定: 2s 后仍未活→指数退避再来
         (1s→2s→4s→8s, 5 次封顶防重连风暴) */
      setTimeout(function () {
        if (preWs && preWs.readyState === 1) {
          preReconnTries = 0; return;
        }
        preReconnTries++;
        if (preReconnTries < 5) {
          schedulePreReconnect(1000
            * Math.pow(2, Math.min(preReconnTries - 1, 3)));
        }
      }, 2000);
    }, delay);
  }

  /* 回前台自愈链: 息屏/切后台期间 X5 冻结三杀——WS 断(1006)/
     AudioContext suspended(audio 图停, VAD 失效喊无反应——
     "无任何 ws_asr_final"的直接死因)/麦克风流可能被系统回收
     (track ended 静音)——逐一探活修复 */
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") { return; }
    if (!wakeOn() || !eng.on) { return; }
    /* ① ctx 恢复: 已解锁过的 ctx 回前台多数可直接 resume */
    try {
      if (eng.ctx && eng.ctx.state === "suspended") {
        var p = eng.ctx.resume();
        if (p && p.catch) { p.catch(function () {}); }
      }
    } catch (e) { /* 忽略 */ }
    /* ② 流探活: track 全 ended → 全链重建(重取流+startWake
       +preconnect; lastRebuildAt 置 0 绕 10s 节流——
       回前台场景必须重建) */
    var streamDead = false;
    try {
      var trs = (eng.stream && eng.stream.getTracks()) || [];
      streamDead = !trs.length || trs.every(function (t) {
        return t.readyState === "ended";
      });
    } catch (e) { /* 忽略 */ }
    if (streamDead) {
      lastRebuildAt = 0;
      rebuildWakeStream();
      return;
    }
    /* ③ 连接探活: 池空且无段连接 → 300ms 快速重建 */
    if (!(preWs && preWs.readyState === 1)
        && !(eng.ws && eng.ws.readyState <= 1)) {
      preReconnTries = 0;
      schedulePreReconnect(300);
    }
    /* ①' ctx 仍挂起(X5 回前台不自动恢复): 600ms 后确认
       仍 suspended 再挂交互兜底(防误提示) */
    setTimeout(function () {
      if (eng.ctx && eng.ctx.state === "suspended"
          && eng.on && !ctxResumeHandler) {
        armCtxResume();
      }
    }, 600);
  });

  function preconnect() {
    if (preWs || !wakeOn() || !authToken()) { return; }
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    var ws;
    try {
      ws = new WebSocket(proto + location.host + "/api/xiaozhu/ws/asr");
      ws.binaryType = "arraybuffer";
    } catch (e) { return; }
    ws._xzV2 = true;
    ws.onopen = function () {
      try {
        ws.send(JSON.stringify({
          type: "auth", token: authToken(), ver: 2,
          wakeword: getWord(),
        }));
      } catch (e) { try { ws.close(); } catch (e2) { /* 忽略 */ } }
    };
    ws.onmessage = segOnMsg(ws);
    ws.onclose = function () {
      if (preWs === ws) { preWs = null; stopPing(); }
      if (eng.ws === ws) { teardownSeg(); }
      /* 前台断连自动重建(带退避): 网络抖动/nginx 超时拆连接后
         池空——下一段虽可临时建连, 但 VAD 不起段的哑引擎场景
         需要连接层主动自愈 */
      schedulePreReconnect(1000);
    };
    ws.onerror = function () { /* onclose 兜底 */ };
  }

  /* 段消息统一处理: armed(段中=发 arm / 待命=入池) → ready
     (回补环形缓存+竞态 finish) → partial/final(唤醒匹配) →
     error(续期自愈)——预建与段中连接共用 */
  function segOnMsg(ws) {
    return function (e) {
      if (typeof e.data !== "string") { return; }
      var m;
      try { m = JSON.parse(e.data); } catch (ex) { return; }
      if (m.type === "armed") {
        if (eng.ws === ws) {
          try { ws.send(JSON.stringify({ type: "arm", wakeword: getWord() })); }
          catch (ex) { teardownSeg(); }
        } else if (!preWs) {
          preWs = ws; startPing();  /* 预热完成入池 */
        }
      } else if (m.type === "ready") {
        eng.wsReady = true;
        clearTimeout(eng.armTimer);
        /* V5.2 空转写健康轮二打: 推缓存段音频直接 finish
           (pendingResub 保持到二打 final——中途不置空防
           VAD 起新段双 arm 竞态) */
        if (eng.pendingResub) {
          var a = eng.pendingResub;
          for (var ri = 0; ri + 3200 <= a.length; ri += 3200) {
            var r16 = new Int16Array(3200);
            for (var rk = 0; rk < 3200; rk++) { r16[rk] = a[ri + rk]; }
            try { eng.ws.send(r16.buffer); } catch (er) { teardownSeg(); return; }
          }
          try {
            eng.ws.send(JSON.stringify({ type: "finish" }));
          } catch (er) { /* 忽略 */ }
          return;
        }
        /* 回补环形缓存(唤醒词开头不漏)——按百炼帧约束
           分帧发送(3200 样本=200ms=6.4KB, 勿超 16KB/帧) */
        while (eng.ring.length >= 3200) {
          var i16 = new Int16Array(3200);
          for (var k = 0; k < 3200; k++) { i16[k] = eng.ring[k]; }
          try { eng.ws.send(i16.buffer); } catch (er) { return; }
          eng.ring.splice(0, 3200);
        }
        /* 段已结束(静音先于握手完成竞态): 回补完直接 finish
           要 final——否则服务器一直等音频, 兜底超时拆连接 */
        if (!eng.speaking) {
          try { eng.ws.send(JSON.stringify({ type: "finish" })); }
          catch (er) { /* 忽略 */ }
        }
      } else if (m.type === "partial" && m.text) {
        eng.lastPartial = m.text;
        if (matchWake(m.text)) { onWakeHit(); }
      } else if (m.type === "final") {
        if (eng.pendingResub) { eng.pendingResub = null; }
        var ft = m.text || eng.lastPartial || "";
        if (matchWake(ft)) {
          onWakeHit();
        } else if (ft) {
          /* V5.3 跨段组合: 「你好」(上段) + 「小竹」(本段
             ≤3s) = 「你好小竹」——0.9s 收段切开的自然停顿
             形态(21:44:05「你好。」实锤)不再致命 */
          if (eng.prevCall && Date.now() - eng.prevCall < 3000
              && new RegExp(XZ).test(ft)) {
            eng.prevCall = 0;
            onWakeHit();
            return;
          }
          if (/^(你好|喂|嘿)[。.!！？?\s]*$/.test(ft)) {
            eng.prevCall = Date.now();  /* 呼语挂起待组合 */
          } else { eng.prevCall = 0; }
          /* 未命中唤醒词: 回显转写内容——ASR 实际听到什么可见 */
          diagTip("听到「" + String(ft).slice(0, 24) + "」未含唤醒词", 10);
        } else {
          /* V5.2 空转写健康轮二打: 段能量达标(源 rms≥0.006
             且段长≥1s)=百炼偶发抖动非音频问题——同段缓存
             音频自动重识别一次(用户无感, 每段限一次) */
          var segAvg = (eng.segFrames || 0)
            ? (eng.segRmsSum / eng.segFrames) : 0;
          if (!eng.retried && segAvg >= 0.006
              && eng.segBuf && eng.segBuf.length >= 16000) {
            eng.retried = true;
            var keep = eng.segBuf.slice(0);
            teardownSeg();
            eng.pendingResub = keep;
            beginSegment();
            return;
          }
          /* 空转写提示的 VAD 前置: 本段由 VAD 人声触发(TH_ON 起
             段)——有声音输入但识别不出才提示; 安静无段不提示
             (防骚扰, AHM 文档口径)。哑流场景 VAD 不起段, 由
             AHM 零值检测负责可见化 */
          /* v3.2 连续空转写计数: 实测唤醒失败轮全是极弱能量段
             (源 rms 0.005 级, 软增益 12x 亦不救信噪比; 正常
             音量轮转写完美)——3 连空直接给出正确姿势引导,
             而不是让用户在"没听清"里打转 */
          eng.emptyStreak = (eng.emptyStreak || 0) + 1;
          if (eng.emptyStreak >= 3) {
            diagTip("连续听不清——请靠近手机(30cm 内)用正常说话音量"
              + "喊「小竹、小竹」或「你好小竹」(音量太小识别不了)", 30);
            eng.emptyStreak = 0;
          } else {
            diagTip("没听清——请再喊一声「小竹、小竹」或「你好小竹」", 8);
          }
        }
        teardownSeg();
      } else if (m.type === "error") {
        /* 通道错误可见(token 问题走下方续期自愈) */
        diagTip("识别通道: " + String(m.error || "?").slice(0, 80), 15);
        /* token 可能过期: 单飞刷新一次, 下一段自愈(过期瞬间喊
           一次没反应, 紧接着再喊即恢复) */
        tryRefreshToken();
        teardownSeg();
      }
    };
  }

  /* 人声段开始: token 预检 → 分钟熔断 → 取池/临时建(V2 arm) */
  function beginSegment() {
    /* token 预检: 登出/换设备后 wake 开关残留 → 空 token 开 WS
       只换来 confusing 的「识别通道: 鉴权失败」——预检直接给
       正确指引(省一次 WS 往返); hiStreak 复位防 VAD 连击 */
    if (!authToken()) {
      eng.hiStreak = 0;
      diagTip("请先登录后再唤醒——点小竹球打开面板登录", 15);
      return;
    }
    var now = Date.now();
    eng.segTimes = eng.segTimes.filter(function (t) {
      return now - t < 60000;
    });
    if (eng.segTimes.length >= 10) { /* 噪音/连测熔断: 本分钟段数封顶 */
      eng.hiStreak = 0;
      diagTip("唤醒频率保护——稍候约 1 分钟再试（连接数限额）", 30);
      return;
    }
    eng.segTimes.push(now);
    eng.speaking = true;
    eng.segStart = now;
    eng.loStreak = 0;
    eng.lastPartial = "";
    eng.segBuf = [];       /* V5.2 段音频缓存起档 */
    eng.segRmsSum = 0; eng.segFrames = 0;
    /* 二打段不重置重试权(pendingResub 在身), 正常段重置 */
    eng.retried = !!eng.pendingResub;
    /* V3.5 增益跨段保留(取消段首重置): 双向增益自动回中,
       重置=1 反致段首尖峰未收敛即削(18:13 批 dump 实证);
       强弱源切换由限速跟随(1.6x/帧)自愈 */
    var ws = null;
    if (preWs && preWs.readyState === 1) {
      ws = preWs; preWs = null; stopPing();  /* 池命中转正 */
    } else {
      var proto = location.protocol === "https:" ? "wss://" : "ws://";
      try {
        ws = new WebSocket(proto + location.host + "/api/xiaozhu/ws/asr");
        ws.binaryType = "arraybuffer";
      } catch (e) { teardownSeg(); return; }
      ws.onopen = function () {
        try {
          ws.send(JSON.stringify({
            type: "auth", token: authToken(), ver: 2,
            wakeword: getWord(),
          }));
        } catch (e) { teardownSeg(); }
      };
    }
    ws._xzV2 = true;
    ws.onmessage = segOnMsg(ws);
    ws.onclose = function () {
      clearTimeout(eng.armTimer);
      if (preWs === ws) { preWs = null; stopPing(); }
      if (eng.ws === ws) { teardownSeg(); }
    };
    ws.onerror = function () { /* onclose 兜底 */ };
    eng.ws = ws;
    if (ws.readyState === 1) {
      /* 池连接已 armed: 直接 arm 建百炼流 */
      try { ws.send(JSON.stringify({ type: "arm", wakeword: getWord() })); }
      catch (e) { teardownSeg(); return; }
    }
    /* 建连/arm → ready 8s 守卫: 服务端段内重试一次(2×3s 建连)
       需 8s 容忍; 超时可见化(用户喊了没反应无提示是黑箱) */
    eng.armTimer = setTimeout(function () {
      if (eng.ws === ws && !eng.wsReady) {
        teardownSeg();
        diagTip("识别通道没跟上——请再喊一声", 8);
      }
    }, 8000);
  }

  /* 推流(纯发送器): 从 ring 取 200ms(3200 样本@16k)帧发送。
     v3.3 修复 AHM 版回归——原 segFeed 内部重复 push ring,
     与 onAudioFrame 的 pushRing 形成"段内双写"(帧 A,A,B,B
     卡顿复制流), 百炼必崩(final=None)——唤醒自 AHM 上线起
     全灭的技术根因; 入队唯一入口收敛到 pushRing(含增益) */
  function segFeed() {
    if (!eng.wsReady || eng.pendingResub) { return; }
    while (eng.ring.length >= 3200) {
      var i16 = new Int16Array(3200);
      for (var k = 0; k < 3200; k++) { i16[k] = eng.ring[k]; }
      try { eng.ws.send(i16.buffer); } catch (e) { return; }
      /* V5.2 段音频缓存(与百炼收到的逐字节一致)——
         空转写健康轮二打用 */
      if (eng.speaking) {
        for (var k2 = 0; k2 < 3200; k2++) { eng.segBuf.push(i16[k2]); }
        if (eng.segBuf.length > 96000) { eng.segBuf.splice(0, eng.segBuf.length - 96000); }
      }
      eng.ring.splice(0, 3200);
    }
  }

  /* 人声段结束: finish → 等 final(部分内核立即断)。
     X5 握手慢于段长竞态(真机实证 recv=empty 1006): 安静环境段短,
     静音时 WS 仍 CONNECTING——原版直接拆连接, 鉴权从未发出,
     识别不可能发生。静音只结束采集: 保活连接等握手完成后
     回补环形缓存(唤醒词已在缓存)再识别, 由下方分级超时守卫 */
  function endSegment() {
    eng.speaking = false;
    eng.hiStreak = 0;
    if (eng.ws && eng.ws.readyState === 1) {
      try { eng.ws.send(JSON.stringify({ type: "finish" })); } catch (e) { teardownSeg(); }
    } else if (!eng.ws) {
      teardownSeg();
    }
    setTimeout(function () {
      if (eng.speaking === false && eng.ws) { teardownSeg(); }
    }, (eng.ws && eng.ws.readyState === 1) ? 3000 : 6000);
  }

  function teardownSeg() {
    eng.speaking = false;
    clearTimeout(eng.armTimer);
    var keep = null;
    if (eng.ws && eng.ws._xzV2 && eng.ws.readyState === 1) {
      keep = eng.ws; /* V2 段后保活回池: 服务端已回等-arm 态,
                        下一段发 arm 即用(连测唤醒省 N 次握手);
                        非池连接(竞态 CONNECTING)照旧拆 */
    } else if (eng.ws) {
      try { eng.ws.close(); } catch (e) { /* 忽略 */ }
    }
    eng.ws = null;
    eng.wsReady = false;
    eng.pendingResub = null;
    if (keep) {
      if (!preWs) { preWs = keep; startPing(); }
      else { try { keep.close(); } catch (e) { /* 池已有弃新 */ } }
    }
    /* 保留尾部 1.5s 环形缓存供下段回补 */
    while (eng.ring.length > RING_MAX) { eng.ring.splice(0, eng.ring.length - RING_MAX); }
  }

  /* ---------- 唤醒命中 ---------- */
  /* 唤醒语音应答(用户要求"唤醒后进行播报"): widget 主页面
     AudioContext 播 TTS——面板 iframe 无用户手势 X5 WebAudio
     ctx 挂起, 主页面 ctx 由「开启唤醒」点击手势解锁 ✓;
     回环防线齐备: 播放 start/end postMessage 面板(VAD 静默
     精确跟随实际播放——11:35 实证固定窗对不上开播延迟)+
     面板 wakeGrace 提交丢弃窗+selfUtter 文本层, 三层防御 */
  var wakeReplyCtx = null;
  var wakeReplyAudio = null; /* 应答音频预缓存(五态文档"消除
     首字延迟"): 首次唤醒 fetch+decode 后存 AudioBuffer,
     后续唤醒零网络零合成直接 start——开播延迟 0.5~3s →
     <50ms, 打断窗口同步(面板 VAD 静默跟随)不受影响 */
  function playWakeReply(d) {
    var s = wakeReplyCtx.createBufferSource();
    s.buffer = d;
    s.connect(wakeReplyCtx.destination);
    try {
      fr.contentWindow.postMessage(
        { type: "xz-wake-audio-start" }, "*");
    } catch (e) { /* 面板未开忽略 */ }
    s.onended = function () {
      try {
        fr.contentWindow.postMessage(
          { type: "xz-wake-audio-end" }, "*");
      } catch (e) { /* 忽略 */ }
    };
    setTimeout(function () { /* onended 丢失兜底 */
      try {
        fr.contentWindow.postMessage(
          { type: "xz-wake-audio-end" }, "*");
      } catch (e) { /* 忽略 */ }
    }, 5000);
    s.start();
  }
  function speakWakeReply() {
    try {
      var AC = window.AudioContext
        || window.webkitAudioContext;
      if (!AC) { return; }
      if (!wakeReplyCtx) { wakeReplyCtx = new AC(); }
      if (wakeReplyCtx.state === "suspended") {
        wakeReplyCtx.resume();
      }
      if (wakeReplyAudio) {
        playWakeReply(wakeReplyAudio);
        return;
      }
      fetch("/api/xiaozhu/tts?text="
        + encodeURIComponent("我在，请吩咐"), {
        headers: { Authorization: "Bearer " + authToken() }
      }).then(function (r) {
        if (!r.ok) { throw 0; }
        return r.arrayBuffer();
      }).then(function (b) {
        return wakeReplyCtx.decodeAudioData(b);
      }).then(function (d) {
        wakeReplyAudio = d; /* 预缓存: 后续唤醒秒播 */
        playWakeReply(d);
      }).catch(function () { /* beep 已响, 静默 */ });
    } catch (e) { /* 忽略 */ }
  }

  function onWakeHit() {
    eng.emptyStreak = 0; /* 命中即清连续空计数 */
    teardownSeg();
    wakeBeep();
    speakWakeReply(); /* 「我在，请吩咐」播报(用户要求) */
    openPanel();
    /* 免唤醒窗口打通: widget 唤醒(ws_asr 轨)不写面板会话——
       服务端 wake 窗口(5 分钟)未被点亮, 唤醒后面板说话被打回
       「请以小竹开头」; 通知面板置免唤醒态(下轮自动补
       「小竹，」前缀点亮窗口), 语义与服务端滑动窗一致 */
    try {
      fr.contentWindow.postMessage(
        { type: "xz-panel-wake-hit" }, "*");
    } catch (e) { /* iframe 未就绪忽略 */ }
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
    if (d.type === "xz-mic-released") {
      /* 面板关窗已释放麦克风(v66 listenClose 停流): 本引擎
        旧流对象在面板取流期间被 X5 系统单占 mute, 归还后
        不自愈(13:09:47 实证二次唤醒只识出「小。」)——拆
        旧采集链重取流, 唤醒识别质量恢复 */
      rebuildWakeStream();
      return;
    }
    if (d.type === "xz-panel-maximize") { maximizePanel(); return; }
    if (d.type === "xz-lat" && d.text) {
      /* 78号P1.5: 面板 iframe [LAT] 延迟埋点转发主页面
         console(iframe console.info 部分采集工具不捕获) */
      try { console.info("[LAT-embed] " + d.text); } catch (e) {}
      return;
    }
    if (d.type === "xz-wake-on") {
      /* 面板内开启唤醒: 球缩小常显; 面板正开着(可能录音), X5 麦克风
         独占——不立即重新 getUserMedia, 关面板时 closePanel→
         enableWakeQuiet 自动恢复监听 */
      ballMini(true);
      if (eng.stream) {
        startWake();
      }
      return;
    }
    if (d.type === "xz-wake-off") {
      localStorage.setItem(WAKE_KEY, "off");
      stopWake();
      releaseMic();
      ballMini(false);
      return;
    }
    if (d.type === "xz-wake-word") {
      /* 唤醒词已由语音页写入同源 localStorage, 此处重建匹配器
         (消息仅作即时通知; 词值本身只信 localStorage, 第三方
         伪造 postMessage 无法注入任意正则/拼音序列) */
      wakeMatcher = buildWakeMatcher();
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

  /* ---------- 启动: 已开启唤醒 → 恢复监听(麦克风权限仍在) ----------
     延迟 1.2s: 错开语音页 iframe 初始恢复会话的窗口(其免提启动
     与本引擎 getUserMedia 并发会在 X5 独占冲突) */
  if (wakeOn()) {
    ballMini(true); /* 已开启: 小点常显兜底(唤醒引擎故障时仍有入口) */
    setTimeout(function () { enableWakeQuiet(true); }, 1200);
  } else {
    b.style.display = "";
  }

  /* 恢复流程: 不弹 confirm, 直接试拿麦克风(权限已记住则静默成功);
     loud=false 静默(退避重试中), true 时失败才提示+挂交互兜底 */
  async function enableWakeQuiet(loud) {
    if (!authToken()) {
      if (loud) {
        /* 令牌过期/退出登录: 唤醒暂不可用, 球恢复引导 */
        b.style.display = "";
        showTip("唤醒待命需要登录——登录后点小竹球恢复唤醒");
      }
      return false;
    }
    try {
      eng.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: false },
      });
      startWake();
      return true;
    } catch (e) {
      if (loud) {
        b.style.display = "";
        showTip("麦克风暂不可用（可能被占用）——点一下屏幕任意处即恢复唤醒");
        armRetryOnInteract();
      }
      return false;
    }
  }
  /* 一次性交互重试: 用户任意触摸/点击后重新拿麦克风 */
  function armRetryOnInteract() {
    if (eng.stream || !wakeOn()) { return; }
    var done = false;
    var h = function () {
      if (done || eng.stream || !wakeOn()) { return; }
      done = true;
      document.removeEventListener("touchend", h, true);
      document.removeEventListener("click", h, true);
      b.style.display = "none";
      enableWakeQuiet(true);
    };
    document.addEventListener("touchend", h, true);
    document.addEventListener("click", h, true);
  }
})();
