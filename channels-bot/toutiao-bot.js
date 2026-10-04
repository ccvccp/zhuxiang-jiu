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

// [CAL] 头条创作平台入口——首页确定性高, 发布页路径待 probe 现场修正
const TT_HOME = 'https://mp.toutiao.com/';
const TT_PUBLISH_URL = process.env.TT_PUBLISH_URL || TT_HOME;

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

  // ---- probe: 现场校准(登录态/发布入口/AI声明控件/内容管理) ----
  if (CFG.action === 'probe') {
    const dump = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a, button'))
        .map(el => (el.innerText || '').trim() + '|' + (el.href || ''))
        .filter(t => /发布|视频|内容管理|作品/.test(t))
        .slice(0, 40);
      return { url: location.href, links, body: (document.body.innerText || '').slice(0, 1200) };
    });
    LOG('probe url=' + dump.url);
    LOG('publish-ish links:\n  ' + dump.links.join('\n  '));
    LOG('body head:\n' + dump.body.replace(/\n/g, ' / ').slice(0, 600));
    fs.writeFileSync('tt_probe_dump.json', JSON.stringify(dump, null, 2));
    LOG('PROBE_DONE (tt_probe_dump.json) — 校准 TT_PUBLISH_URL/登录词/控件选择器');
    await browser.close();
    return;
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

  // 2. 描述并行填写(上传中键入, douyin 同款)
  await sleep(4000);
  try {
    await page.keyboard.type(String(CFG.desc || ''), { delay: 30 });
    LOG('desc typed via keyboard (上传中并行填写)');
  } catch (e) { LOG('desc type err: ' + e.message); }

  // 3. AI 生成声明([CAL] 控件形态: 开关/复选; 字节系同规, 找不到仅告警不阻断)
  try {
    const labeled = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll(
        'label, span, div[role=checkbox], [class*=checkbox], [class*=switch]'));
      const t = els.find(el => /AI.{0,4}生成|人工智能生成/.test(
        (el.innerText || el.getAttribute('aria-label') || '')));
      if (t) { t.click(); return (t.innerText || '').slice(0, 30); }
      return '';
    });
    LOG(labeled ? 'AI 声明已点: [' + labeled + ']'
                : 'WARN AI声明控件未找到 — [CAL] 待现场校准(不阻断)');
  } catch (e) { LOG('AI label err: ' + e.message); }

  // 4. 等上传完成(60s 下限铁律) → 发布按钮(滚入视口+防双发 guard)
  for (let i = 0; i < 12; i++) { await sleep(5000); }
  LOG('upload wait done (>=60s), enter publish-btn flow');
  let clicked = false;
  for (let i = 0; i < 60 && !clicked; i++) {
    try {
      const btnPt = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button, [role=button]'));
        const b = btns.find(x => /^(发布|发表)$/.test((x.innerText || '').trim()) && !x.disabled);
        if (!b) return null;
        b.scrollIntoView({ block: 'center' });               // 视口外打空修正
        const r = b.getBoundingClientRect();
        if (!(r.width > 0) || r.y < 0 || r.y > 890) return null;
        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      });
      if (!btnPt) { if (i % 4 === 0) LOG('wait publish-btn poll ' + (i * 5) + 's'); await sleep(5000); continue; }
      const ok = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button, [role=button]'));
        const b = btns.find(x => /^(发布|发表)$/.test((x.innerText || '').trim()) && !x.disabled);
        if (!b) return false;
        b.click();
        return true;
      });
      if (ok) {
        clicked = true;
        LOG('click 发布 (合成) at ' + Math.round(btnPt.x) + ',' + Math.round(btnPt.y));
        await sleep(2500);
        // 防双发 guard(douyin 2026-10-04 实证修正同款): 合成点击已生效
        // (URL 离开发布页)则跳过 CDP 兜底
        const left = !/publish|upload/.test(page.url());
        if (left) {
          LOG('合成点击已生效(URL 已离开发布页) — 跳过 CDP 兜底防双发');
        } else {
          const cdp = await page.createCDPSession();
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: btnPt.x, y: btnPt.y, button: 'none', pointerType: 'mouse' });
          await sleep(150);
          await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
          await sleep(90);
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
          LOG('CDP 真实点击兜底已发');
        }
      }
    } catch (e) { LOG('publish click err: ' + e.message); await sleep(5000); }
  }
  if (!clicked) {
    LOG('PUBLISH_BTN_TIMEOUT — [CAL] 按钮文案/就绪条件需校准');
    await page.screenshot({ path: 'tt_err_no_btn.png' });
    await browser.close();
    process.exit(4);
  }

  // 5. 成功特征轮询([CAL]: URL 跳内容管理 / 文本"发布成功")
  let published = false;
  for (let i = 0; i < 30 && !published; i++) {
    const url = page.url();
    let bodyText = '';
    try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 500)); } catch (e) {}
    if ((/manage|content/.test(url) && !/publish|upload/.test(url))
        || /发布成功|审核中/.test(bodyText)) {
      published = true;
      LOG('PUBLISH_OK url=' + url);
      LOG('after body: ' + bodyText.replace(/\n/g, ' ').slice(0, 300));
    } else { await sleep(3000); }
  }
  if (!published) {
    LOG('PUBLISH_UNVERIFIED — 90s 未见成功特征, 截图留档人审(勿重发——先 manage 核对)');
    await page.screenshot({ path: 'tt_unverified.png' });
  }
  await page.screenshot({ path: 'tt_publish_end.png' });
  LOG('PUBLISH_RUN_DONE');
  await browser.close();
})();
