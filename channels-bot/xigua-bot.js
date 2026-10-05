// 西瓜视频 RPA bot (2026-10-05 立项, 第八平台)
// 架构镜像 toutiao-bot.js 已实证范式(字节系同构): CDP pipe Chrome +
// 独立 xigua-profile 登录态; 复用头条轨全部校准成果:
//   - 标题独立框 React setter 写入(30字级)
//   - 「AI生成」作品声明叶子精确点击
//   - 「我知道了」引导弹窗一次性全关
//   - assist 模式(三件套全自动+发布按钮人工收口, 字节系点击免疫)
// 用法: node xigua-bot.js <config.json>
// config: { action: "probe"|"publish"|"manage"|"assist",
//           mp4, title, desc, assistPublish, probeUrl,
//           waitLoginMinutes, waitMinutes }
//
// ⚠ 现场校准项(登录后实证, [CAL]):
//   1. XG_PUBLISH_URL 西瓜创作平台发布页真实路径(studio.xiguavideo.com)
//   2. 登录形态(字节 SSO——可能有「使用头条号授权」一键登录)
//   3. 表单结构(若与头条 xigua/upload-video 同系统则零校准)
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('xigua_bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// [CAL→实证 2026-10-05 probe2] 西瓜创作平台并入抖音创作服务平台:
// studio.ixigua.com → creator.douyin.com; 发布页=抖音上传页(同系统),
// input[type=file] 主文档, 表单上传后渲染(西瓜同步开关待上传后实证)
const XG_HOME = 'https://studio.ixigua.com/';
const XG_PUBLISH_URL = process.env.XG_PUBLISH_URL
  || 'https://creator.douyin.com/creator-micro/content/upload/video';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\xigua-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, xigua-profile)');

  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(XG_HOME, { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());
  await sleep(6000);

  // ---- 登录保障(字节 SSO 特征; 可能有「使用头条号授权登录」一键入口) ----
  const loginPageLike = (url, text) =>
    /login|passport|sso\.bytedance|sso\.toutiao|sso\.ixigua/.test(url) ||
    /扫码登录|验证码登录|手机号登录|登录西瓜|授权登录/.test(text || '');
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
    LOG('LOGIN_TIMEOUT — 请扫码/授权登录(西瓜视频, 可用头条号一键授权)');
    await page.screenshot({ path: 'xg_login_timeout.png' });
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());

  // ---- assist: 人工操作窗口(关窗=完成信号, toutiao 实证范式) ----
  if (CFG.action === 'assist') {
    const waitMin = Number(CFG.waitMinutes || 20);
    let closed = false;
    browser.on('disconnected', () => { closed = true; });
    LOG('assist window open (' + waitMin + 'min) — 完成后直接关闭 Chrome 窗口');
    const deadline = Date.now() + waitMin * 60000;
    while (!closed && Date.now() < deadline) { await sleep(3000); }
    if (closed) {
      LOG('ASSIST_WINDOW_CLOSED — 用户已关窗, 操作视为完成');
      process.exit(0);
    }
    LOG('ASSIST_TIMEOUT — 窗口到期(未关窗)');
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
      const syncEls = Array.from(document.querySelectorAll('label, span, div, [class*=checkbox], [class*=switch], input'))
        .map(el => (el.innerText || el.getAttribute('aria-label') || el.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 60))
        .filter(t => /西瓜|同步|多平台|分发/.test(t)).slice(0, 12);
      const frames = Array.from(document.querySelectorAll('iframe')).map(f => f.src || '(no src)').slice(0, 8);
      return { url: location.href, links, inputs, buttons, aiEls, syncEls, frames,
               body: (document.body.innerText || '').slice(0, 1600) };
    });
    LOG('probe url=' + dump.url);
    LOG('publish-ish links:\n  ' + dump.links.join('\n  '));
    LOG('inputs:\n  ' + dump.inputs.join('\n  '));
    LOG('buttons:\n  ' + dump.buttons.join('\n  '));
    LOG('AI-ish els:\n  ' + dump.aiEls.join('\n  '));
    LOG('sync-ish els(西瓜/同步):\n  ' + dump.syncEls.join('\n  '));
    LOG('iframes:\n  ' + dump.frames.join('\n  '));
    LOG('body head:\n' + dump.body.replace(/\n/g, ' / ').slice(0, 800));
    fs.writeFileSync('xg_probe_dump.json', JSON.stringify(dump, null, 2));
    await page.screenshot({ path: 'xg_probe_page.png' });
    LOG('PROBE_DONE (xg_probe_dump.json + xg_probe_page.png)');
    await browser.close();
    return;
  }

  // ---- manage: 内容管理列表转储(核发/对账) ----
  if (CFG.action === 'manage') {
    const mgUrl = await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll('a'))
        .find(x => /内容管理|作品管理|稿件管理/.test(x.innerText || ''));
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

  // ---- publish: 视频发布(assistPublish=true 为默认推荐, 字节系点击免疫实证) ----
  if (CFG.action !== 'publish') {
    LOG('unknown action: ' + CFG.action);
    await browser.close();
    process.exit(1);
  }
  await page.goto(XG_PUBLISH_URL, { waitUntil: 'domcontentloaded' });
  await sleep(5000);
  LOG('publish page url=' + page.url());

  // 1. 上传 input(先主文档后全 frame 兜底)
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
    await page.screenshot({ path: 'xg_err_no_upload.png' });
    await browser.close();
    process.exit(3);
  }
  await uploadHandle.uploadFile(CFG.mp4);
  LOG('uploadFile: ' + CFG.mp4);

  // 2. 关引导弹窗(一次性全关) → 精确填标题(React setter)
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
    await page.screenshot({ path: 'xg_err_title.png' });
    await browser.close();
    process.exit(6);
  }

  // 3. AI 生成声明精确点击(叶子精确「AI生成」, 头条同款作品声明选项组)
  try {
    const labeled = await page.evaluate(() => {
      const leaves = Array.from(document.querySelectorAll('span, label, div, li, a'))
        .filter(el => (el.innerText || '').trim() === 'AI生成');
      if (!leaves.length) return '';
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
  for (let i = 0; i < 12; i++) { await sleep(5000); }
  LOG('upload wait done (>=60s), enter publish-btn flow');
  if (CFG.assistPublish !== false) {
    // 默认 assist(字节系发布按钮对合成/CDP 点击免疫——toutiao 2026-10-05 实证)
    LOG('>>> 请人工: 在窗口中点击「发布」按钮(10 分钟) — 若有红色校验提示请截图/记录 <<<');
    const dl = Date.now() + 10 * 60000;
    let ok = false;
    while (Date.now() < dl) {
      await sleep(5000);
      const url = page.url();
      let bt = '';
      try { bt = await page.evaluate(() => (document.body.innerText || '').slice(0, 600)); } catch (e) {}
      if (!/upload|publish/.test(url) || /发布成功/.test(bt)) { ok = true; break; }
    }
    await page.screenshot({ path: 'xg_publish_end.png' });
    if (ok) { LOG('PUBLISH_OK (人工点击) url=' + page.url()); }
    else { LOG('ASSIST_PUBLISH_TIMEOUT — 10 分钟未成功, 请记录页面提示后关窗'); }
    await browser.close();
    process.exit(ok ? 0 : 8);
  }
  // 全自动点击轨(assistPublish=false 时, 保留校准能力)
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
    await page.screenshot({ path: 'xg_err_no_btn.png' });
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
  const capDialogs = () => page.evaluate(() => {
    const sels = '[class*=modal], [class*=dialog], [class*=Modal], [class*=Dialog], [role=dialog], [class*=toast], [class*=Toast], [class*=popover], [class*=guide]';
    return Array.from(document.querySelectorAll(sels))
      .filter(el => el.offsetParent !== null && (el.innerText || '').trim())
      .map(el => (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 160));
  });
  LOG('post-click dialogs(+3s): ' + JSON.stringify(await capDialogs().catch(() => [])));
  let left = !/upload|publish/.test(page.url());
  if (!left) {
    const cdp = await page.createCDPSession();
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: btnPt.x, y: btnPt.y, button: 'none', pointerType: 'mouse' });
    await sleep(150);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    await sleep(90);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    LOG('CDP 真实点击兜底已发');
    await sleep(3000);
    left = !/upload|publish/.test(page.url());
  }
  let published = left;
  for (let i = 0; i < 20 && !published; i++) {
    const url = page.url();
    let bodyText = '';
    try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 600)); } catch (e) {}
    if (!/upload|publish/.test(url) || /发布成功/.test(bodyText)) {
      published = true;
      LOG('PUBLISH_OK url=' + url);
      LOG('after body: ' + bodyText.replace(/\n/g, ' ').slice(0, 300));
    } else { await sleep(3000); }
  }
  await page.screenshot({ path: 'xg_publish_end.png' });
  if (!published) {
    const st = await btnState();
    LOG('PUBLISH_CLICK_INEFFECTIVE — 按钮状态=' + JSON.stringify(st) + ' 草稿已保全');
    await browser.close();
    process.exit(5);
  }
  LOG('PUBLISH_RUN_DONE');
  await browser.close();
})();
