// 头条号视频 RPA bot 骨架 (2026-10-05 立项, 企业认证审核中)
// 架构镜像 douyin-bot.js 已实证范式: CDP pipe 传输 Chrome + 独立
// toutiao-profile 持久登录态; 与其他 bot 完全隔离。
// 用法: node toutiao-bot.js <config.json>
// config: { action: "probe"|"publish"|"manage", mp4, desc, waitLoginMinutes }
//
// ⚠ 现场校准项(登录态就绪后逐一实证, 标记 [CAL]):
//   1. TT_PUBLISH_URL 头条创作平台视频发布页真实路径(mp.toutiao.com)
//   2. 登录页特征词(头条登录形态: 扫码/验证码登录等)
//   3. 上传 input[type=file] 所在 frame(头条或无 iframe, 直查主文档)
//   4. AI 生成声明控件(字节系与抖音同规, 发布页应有声明开关)
//   5. 发布按钮文案/成功特征(URL 跳转/内容管理页)
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('toutiao_bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// [CAL→实证 2026-10-05 probe2] 头条视频发布页(西爪上传页, 直达有效):
// 「发布视频/点击上传」, input[type=file] 在主文档(无 iframe), 表单上传后渲染
const TT_HOME = 'https://mp.toutiao.com/';
const TT_PUBLISH_URL = process.env.TT_PUBLISH_URL
  || 'https://mp.toutiao.com/profile_v4/xigua/upload-video';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\toutiao-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, toutiao-profile)');

  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(TT_HOME, { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());
  await sleep(6000);

  // ---- 登录保障(轮询特征, 不自动点击; [CAL] 特征词现场校准) ----
  const loginPageLike = (url, text) =>
    /login|passport|sso\.bytedance|sso\.toutiao/.test(url) ||
    /扫码登录|验证码登录|手机号登录|登录头条/.test(text || '');
  const ensureLogin = async (waitMs) => {
    const deadline = Date.now() + waitMs;
    let lastUrl = '';
    while (Date.now() < deadline) {
      const url = page.url();
      let bodyText = '';
      try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 800)); } catch (e) {}
      if (url !== lastUrl) { LOG('url -> ' + url); lastUrl = url; }
      if (!loginPageLike(url, bodyText)) return true;
      await sleep(5000);
    }
    return false;
  };
  const waitLoginMin = Number(CFG.waitLoginMinutes || 5);
  if (!(await ensureLogin(waitLoginMin * 60000))) {
    LOG('LOGIN_TIMEOUT — 请扫码/验证码登录(头条账号)');
    await page.screenshot({ path: 'tt_login_timeout.png' });
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());

  // ---- assist: 人工操作窗口(完善账号信息等), 自动点「去完善」入口,
  //      用户完成后手动关窗=完成信号(横幅消失≠完善完成, 2026-10-05 实证) ----
  if (CFG.action === 'assist') {
    const waitMin = Number(CFG.waitMinutes || 20);
    try {
      const clicked = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button, a, span, div'))
          .filter(el => /^(去完善|立即完善)$/.test((el.innerText || '').trim()) && el.children.length <= 2);
        if (btns.length) { btns[btns.length - 1].click(); return true; }
        return false;
      });
      LOG(clicked ? '已自动点击「去完善/立即完善」入口' : '未找到完善入口 — 请人工从 设置/账号信息 进入');
    } catch (e) { LOG('auto click err: ' + e.message); }
    let closed = false;
    browser.on('disconnected', () => { closed = true; });
    LOG('assist window open (' + waitMin + 'min) — 请完整填写并提交, 完成后直接关闭 Chrome 窗口');
    const deadline = Date.now() + waitMin * 60000;
    while (!closed && Date.now() < deadline) { await sleep(3000); }
    if (closed) {
      LOG('ASSIST_WINDOW_CLOSED — 用户已关窗, 完善操作视为完成, 可跑试发验证');
      process.exit(0);
    }
    LOG('ASSIST_TIMEOUT — 窗口到期(未关窗), 完成状态未知, 以试发为准');
    await browser.close();
    process.exit(7);
  }

  // ---- probe: 现场校准(登录态/发布入口/AI声明控件/内容管理) ----
  if (CFG.action === 'probe') {
    if (CFG.probeUrl) {
      await page.goto(CFG.probeUrl, { waitUntil: 'domcontentloaded' });
      LOG('probe goto ' + CFG.probeUrl);
      await sleep(9000);
    }
    const dump = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a, button'))
        .map(el => (el.innerText || '').trim() + '|' + (el.href || ''))
        .filter(t => /发布|视频|内容管理|作品/.test(t))
        .slice(0, 40);
      const inputs = Array.from(document.querySelectorAll('input, textarea')).slice(0, 30)
        .map(el => el.tagName.toLowerCase() + '|' + (el.type || '') + '|ph=' + (el.placeholder || '').slice(0, 40));
      const buttons = Array.from(document.querySelectorAll('button, [role=button]')).slice(0, 50)
        .map(el => 'btn|' + (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 24) + '|dis=' + !!el.disabled)
        .filter(t => t.length > 8);
      const aiEls = Array.from(document.querySelectorAll('label, span, div, [class*=checkbox], [class*=switch]'))
        .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 60))
        .filter(t => /AI|人工智能|生成声明/.test(t)).slice(0, 12);
      const frames = Array.from(document.querySelectorAll('iframe')).map(f => f.src || '(no src)').slice(0, 8);
      return { url: location.href, links, inputs, buttons, aiEls, frames,
               body: (document.body.innerText || '').slice(0, 1600) };
    });
    LOG('probe url=' + dump.url);
    LOG('publish-ish links:\n  ' + dump.links.join('\n  '));
    LOG('inputs:\n  ' + dump.inputs.join('\n  '));
    LOG('buttons:\n  ' + dump.buttons.join('\n  '));
    LOG('AI-ish els:\n  ' + dump.aiEls.join('\n  '));
    LOG('iframes:\n  ' + dump.frames.join('\n  '));
    LOG('body head:\n' + dump.body.replace(/\n/g, ' / ').slice(0, 800));
    fs.writeFileSync('tt_probe_dump.json', JSON.stringify(dump, null, 2));
    await page.screenshot({ path: 'tt_probe_page.png' });
    LOG('PROBE_DONE (tt_probe_dump.json + tt_probe_page.png)');
    await browser.close();
    return;
  }

  // ---- cleandrafts: 草稿箱批量清理(2026-10-05 试发残留约5条) ----
  // 护栏: ①先切「草稿箱」tab ②已发布作品标题仍在页面则中止 ③封顶8条
  if (CFG.action === 'cleandrafts') {
    await page.goto('https://mp.toutiao.com/profile_v4/manage/content/all',
                    { waitUntil: 'domcontentloaded' });
    await sleep(6000);
    const tabOk = await page.evaluate(() => {
      const t = Array.from(document.querySelectorAll('span, div, a, button'))
        .filter(el => el.children.length === 0 && (el.innerText || '').trim() === '草稿箱');
      if (t.length) { t[t.length - 1].click(); return true; }
      return false;
    });
    LOG('草稿箱 tab click: ' + tabOk);
    await sleep(4000);
    const guard = await page.evaluate(() =>
      !(document.body.innerText || '').includes('寒露至'));
    LOG('published-work-absent guard: ' + guard);
    if (!tabOk || !guard) {
      LOG('ABORT — 草稿箱 tab 未切换或已发布作品仍可见, 不执行删除');
      await browser.close(); process.exit(9);
    }
    let deleted = 0;
    const CAP = Number(CFG.maxDelete || 8);
    for (let round = 0; round < CAP; round++) {
      const item = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll(
          'button, [role=button], span, a, div'))
          .filter(el => el.children.length === 0
                        && /^(删除|删除作品|删除视频|删除草稿)$/.test((el.innerText || '').trim())
                        && el.offsetParent !== null);
        if (!btns.length) return null;
        const b = btns[0];
        b.scrollIntoView({ block: 'center' });
        const row = b.closest('[class*=item],[class*=card],[class*=row],li,tr');
        const title = row ? (row.innerText || '').split('\n').filter(Boolean).slice(0, 3).join('/')
                          : '(unknown)';
        b.click();
        return title;
      });
      if (!item) break;
      LOG('delete draft: [' + String(item).slice(0, 60) + ']');
      await sleep(1500);
      const confirmed = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('button, [role=button], span'))
          .filter(el => el.children.length === 0
                        && /^(确定|删除|确认|确认删除)$/.test((el.innerText || '').trim())
                        && el.offsetParent !== null);
        if (els.length) { els[els.length - 1].click(); return (els[els.length - 1].innerText || '').trim(); }
        return '';
      });
      LOG('confirm: [' + (confirmed || 'no-dialog(auto)') + ']');
      await sleep(2500);
      deleted++;
    }
    const body = await page.evaluate(() => (document.body.innerText || '').slice(0, 800));
    LOG('CLEANDRAFTS_DONE deleted=' + deleted);
    LOG('after-body: ' + body.replace(/\n/g, ' ').slice(0, 300));
    await browser.close();
    process.exit(0);
  }

  // ---- manage: 内容管理列表转储(核发/对账) ----
  if (CFG.action === 'manage') {
    // [CAL] 内容管理真实路径——先走首页链接发现
    const mgUrl = await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll('a'))
        .find(x => /内容管理|作品管理/.test(x.innerText || ''));
      return a ? a.href : '';
    });
    if (mgUrl) { await page.goto(mgUrl, { waitUntil: 'domcontentloaded' }); await sleep(6000); }
    const body = await page.evaluate(() => (document.body.innerText || ''));
    LOG('manage url=' + page.url());
    body.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 80)
      .forEach(s => LOG('  ' + s.slice(0, 100)));
    LOG('MANAGE_DONE');
    await browser.close();
    return;
  }

  // ---- publish: 视频发布 ----
  if (CFG.action !== 'publish') {
    LOG('unknown action: ' + CFG.action);
    await browser.close();
    process.exit(1);
  }
  await page.goto(TT_PUBLISH_URL, { waitUntil: 'domcontentloaded' });
  await sleep(5000);
  LOG('publish page url=' + page.url());

  // 1. 上传 input([CAL] frame 形态: 先主文档后全 frame 兜底)
  let uploadHandle = null;
  for (let i = 0; i < 12 && !uploadHandle; i++) {
    for (const fr of page.frames()) {
      try {
        const h = await fr.$('input[type=file]');
        if (h) { uploadHandle = h; LOG('upload input found, frame=' + fr.url()); break; }
      } catch (e) {}
    }
    if (!uploadHandle) { await sleep(5000); }
  }
  if (!uploadHandle) {
    LOG('UPLOAD_INPUT_TIMEOUT — [CAL] 发布页路径/上传控件形态需校准');
    await page.screenshot({ path: 'tt_err_no_upload.png' });
    await browser.close();
    process.exit(3);
  }
  await uploadHandle.uploadFile(CFG.mp4);
  LOG('uploadFile: ' + CFG.mp4);

  // 2. 等发布表单渲染 → 关引导弹窗(一次性全关, 页面同时多个) → 精确填标题
  //    [实证 probe2] 头条视频发布页无简介框, 仅标题(30字上限, 计数 n/30);
  //    [实证试发2] 「我知道了」弹窗同时多个, 逐个关耗尽重试轮次的教训
  const title = String(CFG.title || CFG.desc || '').slice(0, 30);
  for (let i = 0; i < 8; i++) {
    const n = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button, a, span, div'))
        .filter(el => (el.innerText || '').trim() === '我知道了' && el.children.length <= 2);
      btns.forEach(el => { try { el.click(); } catch (e) {} });
      return btns.length;
    });
    if (!n) break;
    LOG('dismiss 引导弹窗 x' + n);
    await sleep(1200);
  }
  let titleOk = false;
  for (let i = 0; i < 18 && !titleOk; i++) {
    try {
      const r = await page.evaluate((t) => {
        const cands = Array.from(document.querySelectorAll('input, textarea'))
          .filter(el => el.type !== 'file' && el.offsetParent !== null);
        if (!cands.length) return {};
        const box = cands.find(el =>
          /标题/.test(el.placeholder || el.getAttribute('aria-label') || '')) || cands[0];
        box.scrollIntoView({ block: 'center' });
        const proto = box.tagName === 'TEXTAREA'
          ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(box, t);
        box.dispatchEvent(new Event('input', { bubbles: true }));
        box.dispatchEvent(new Event('change', { bubbles: true }));
        return { typed: box.value.length, want: t.length };
      }, title);
      if (r.typed === title.length) {
        titleOk = true; LOG('title filled: [' + title + '] (' + r.typed + '字)');
      } else { await sleep(2500); }
    } catch (e) { LOG('title fill err: ' + e.message); await sleep(2500); }
  }
  if (!titleOk) {
    LOG('TITLE_FILL_FAILED — [CAL] 标题框定位/受控组件写入需人审');
    await page.screenshot({ path: 'tt_err_title.png' });
    await browser.close();
    process.exit(6);
  }

  // 3. AI 生成声明精确点击
  //    [实证 probe2] 「作品声明」是选项组: 取自站外/引用站内/自行拍摄/AI生成/
  //    虚构演绎/故事经历/投资观点——必须叶子精确匹配「AI生成」, 容器含全文误点实证
  try {
    const labeled = await page.evaluate(() => {
      const leaves = Array.from(document.querySelectorAll('span, label, div, li, a'))
        .filter(el => (el.innerText || '').trim() === 'AI生成');
      if (!leaves.length) return '';
      // 取 DOM 最深(最内层)的一个, 避免点到外层容器
      const leaf = leaves.reduce((a, b) =>
        (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_CONTAINED_BY) ? b : a);
      leaf.scrollIntoView({ block: 'center' });
      leaf.click();
      return 'AI生成';
    });
    LOG(labeled ? 'AI 声明已点(叶子精确): [' + labeled + ']'
                : 'WARN AI声明「AI生成」叶子未找到 — [CAL] 待人审(不阻断)');
  } catch (e) { LOG('AI label err: ' + e.message); }

  // 4. 等上传完成(60s 下限铁律) → 发布
  //    [assist 模式 2026-10-05] 表单三件套(上传/标题/AI声明)已实证稳定,
  //    发布按钮合成+CDP 点击均无效且无弹窗(疑似字段级校验/隐藏条件)——
  //    CFG.assistPublish=true 时人工点发布, bot 监听结果(B站 assist 先例)
  for (let i = 0; i < 12; i++) { await sleep(5000); }
  LOG('upload wait done (>=60s), enter publish-btn flow');
  if (CFG.assistPublish) {
    LOG('>>> 请人工: 在窗口中点击「发布」按钮(10 分钟) — 若有红色校验提示请截图/记录 <<<');
    const dl = Date.now() + 10 * 60000;
    let ok = false;
    while (Date.now() < dl) {
      await sleep(5000);
      const url = page.url();
      let bt = '';
      try { bt = await page.evaluate(() => (document.body.innerText || '').slice(0, 600)); } catch (e) {}
      if (!/upload-video|publish/.test(url) || /发布成功/.test(bt)) { ok = true; break; }
    }
    await page.screenshot({ path: 'tt_publish_end.png' });
    if (ok) { LOG('PUBLISH_OK (人工点击) url=' + page.url()); }
    else { LOG('ASSIST_PUBLISH_TIMEOUT — 10 分钟未成功, 请记录页面提示后关窗'); }
    await browser.close();
    process.exit(ok ? 0 : 8);
  }
  const btnState = async () => page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button, [role=button]'));
    const b = btns.find(x => /^(发布|发表)$/.test((x.innerText || '').trim()));
    if (!b) return null;
    b.scrollIntoView({ block: 'center' });
    const r = b.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2,
             disabled: !!b.disabled, cls: (b.className || '').slice(0, 60),
             aria: b.getAttribute('aria-disabled') };
  });
  let btnPt = null;
  for (let i = 0; i < 24 && !btnPt; i++) {
    btnPt = await btnState();
    if (!btnPt) { if (i % 4 === 0) LOG('wait publish-btn poll ' + (i * 5) + 's'); await sleep(5000); }
  }
  if (!btnPt) {
    LOG('PUBLISH_BTN_NOT_FOUND — [CAL] 按钮文案需校准');
    await page.screenshot({ path: 'tt_err_no_btn.png' });
    await browser.close();
    process.exit(4);
  }
  LOG('publish btn state: disabled=' + btnPt.disabled + ' aria=' + btnPt.aria + ' cls=[' + btnPt.cls + ']');
  await page.evaluate(() => {
    const b = Array.from(document.querySelectorAll('button, [role=button]'))
      .find(x => /^(发布|发表)$/.test((x.innerText || '').trim()));
    if (b) b.click();
  });
  LOG('click 发布 (合成) at ' + Math.round(btnPt.x) + ',' + Math.round(btnPt.y));
  await sleep(3000);
  // [诊断] 点击后立即捕获可见弹窗/toast(拦截弹窗/校验提示稍纵即逝)
  const capDialogs = () => page.evaluate(() => {
    const sels = '[class*=modal], [class*=dialog], [class*=Modal], [class*=Dialog], [role=dialog], [class*=toast], [class*=Toast], [class*=popover], [class*=guide]';
    return Array.from(document.querySelectorAll(sels))
      .filter(el => el.offsetParent !== null && (el.innerText || '').trim())
      .map(el => (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 160));
  });
  LOG('post-click dialogs(+3s): ' + JSON.stringify(await capDialogs().catch(() => [])));
  let left = !/upload-video|publish/.test(page.url());
  if (!left) {
    const cdp = await page.createCDPSession();
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: btnPt.x, y: btnPt.y, button: 'none', pointerType: 'mouse' });
    await sleep(150);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    await sleep(90);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    LOG('CDP 真实点击兜底已发');
    await sleep(3000);
    left = !/upload-video|publish/.test(page.url());
  }

  // 5. 成功判定([实证修正] 首试 PUBLISH_OK 假阳性: URL 未离开+表单仍在却被判成功
  //    → 收紧为: URL 离开发布页 或 body 明确含「发布成功」toast)
  let published = left;
  for (let i = 0; i < 20 && !published; i++) {
    const url = page.url();
    let bodyText = '';
    try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 600)); } catch (e) {}
    if (!/upload-video|publish/.test(url) || /发布成功/.test(bodyText)) {
      published = true;
      LOG('PUBLISH_OK url=' + url);
      LOG('after body: ' + bodyText.replace(/\n/g, ' ').slice(0, 300));
    } else { await sleep(3000); }
  }
  await page.screenshot({ path: 'tt_publish_end.png' });
  if (!published) {
    const st = await btnState();
    LOG('PUBLISH_CLICK_INEFFECTIVE — 按钮状态=' + JSON.stringify(st)
      + ' 疑似权限拦截(「请完善账号信息」), 草稿已保全, 请人工完善后重试');
    await browser.close();
    process.exit(5);
  }
  LOG('PUBLISH_RUN_DONE');
  await browser.close();
})();
