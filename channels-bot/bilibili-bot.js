// B站(哔哩哔哩)视频 RPA bot 骨架 (2026-10-05 立项)
// 架构镜像 douyin/kuaishou 实证范式: CDP pipe + 独立 bilibili-profile。
// 用法: node bilibili-bot.js <config.json>
// config: { action: "probe"|"publish"|"manage", mp4, title, desc,
//           category: "一级-二级" (如 "生活-美食", B站投稿必选分区),
//           waitLoginMinutes }
//
// ⚠ 现场校准项(登录态就绪后逐一实证, 标记 [CAL]):
//   1. BILI_UPLOAD_URL 投稿页真实路径(member.bilibili.com 创作中心)
//   2. 登录特征(passport.bilibili.com / 扫码登录)
//   3. 分区选择弹层结构(两级: 一级→二级)——B站特有硬门槛
//   4. 「AI创作声明/内容由AI生成」勾选控件(B站正面支持, 优先级高)
//   5. 立即投稿按钮文案与成功特征(稿件投递成功/投稿成功)
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('bilibili_bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// [CAL] 创作中心入口; 投稿页路径待 probe 修正
const BILI_HOME = 'https://member.bilibili.com/platform/home';
const BILI_UPLOAD_URL = process.env.BILI_UPLOAD_URL
  || 'https://member.bilibili.com/platform/upload/video/frame/';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\bilibili-profile',
    headless: false, pipe: true, ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, bilibili-profile)');
  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(BILI_HOME, { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());
  await sleep(6000);

  // ---- 登录保障([CAL] 特征词) ----
  const loginPageLike = (url, text) =>
    /passport\.bilibili|\/login/.test(url) ||
    /扫码登录|登录B站|登录哔哩|短信登录|密码登录/.test(text || '');
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
    LOG('LOGIN_TIMEOUT — 请在弹出的窗口扫码登录 B站账号');
    await page.screenshot({ path: 'bili_login_timeout.png' });
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());

  // ---- probe ----
  if (CFG.action === 'probe') {
    const dump = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a, button'))
        .map(el => (el.innerText || '').trim() + '|' + (el.href || ''))
        .filter(t => /投稿|上传|视频|稿件|内容管理/.test(t))
        .slice(0, 40);
      return { url: location.href, links,
               body: (document.body.innerText || '').slice(0, 1200) };
    });
    LOG('probe url=' + dump.url);
    LOG('links:\n  ' + dump.links.join('\n  '));
    LOG('body head:\n' + dump.body.replace(/\n/g, ' / ').slice(0, 600));
    fs.writeFileSync('bili_probe_dump.json', JSON.stringify(dump, null, 2));
    LOG('PROBE_DONE (bili_probe_dump.json)');
    await browser.close();
    return;
  }

  // ---- manage: 稿件管理转储 ----
  if (CFG.action === 'manage') {
    const mgUrl = await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll('a'))
        .find(x => /稿件管理|内容管理/.test(x.innerText || ''));
      return a ? a.href : 'https://member.bilibili.com/platform/upload-manager/article';
    });
    await page.goto(mgUrl, { waitUntil: 'domcontentloaded' });
    await sleep(6000);
    const body = await page.evaluate(() => (document.body.innerText || ''));
    LOG('manage url=' + page.url());
    body.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 80)
      .forEach(s => LOG('  ' + s.slice(0, 100)));
    LOG('MANAGE_DONE');
    await browser.close();
    return;
  }

  if (CFG.action !== 'publish') {
    LOG('unknown action: ' + CFG.action);
    await browser.close();
    process.exit(1);
  }

  // ---- publish ----
  await page.goto(BILI_UPLOAD_URL, { waitUntil: 'domcontentloaded' });
  await sleep(6000);
  LOG('publish page url=' + page.url());

  // 1. 上传 input(全 frame 兜底)
  let uploadHandle = null;
  for (let i = 0; i < 12 && !uploadHandle; i++) {
    for (const fr of page.frames()) {
      try {
        const h = await fr.$('input[type=file]');
        if (h) { uploadHandle = h; LOG('upload input found, frame=' + fr.url()); break; }
      } catch (e) {}
    }
    if (!uploadHandle) await sleep(5000);
  }
  if (!uploadHandle) {
    LOG('UPLOAD_INPUT_TIMEOUT — [CAL] 投稿页路径/上传控件需校准');
    await page.screenshot({ path: 'bili_err_no_upload.png' });
    await browser.close();
    process.exit(3);
  }
  await uploadHandle.uploadFile(CFG.mp4);
  LOG('uploadFile: ' + CFG.mp4);
  await sleep(5000);

  // 2. 分区选择(B站特有硬门槛: 两级; [CAL] 弹层结构)
  //    category 格式 "一级-二级", 如 "生活-美食"
  try {
    const parts = String(CFG.category || '生活-美食').split('-');
    const opened = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div, span, button'))
        .filter(el => el.children.length === 0
                      && /选择分区|点击选择分区/.test((el.innerText || '').trim()));
      if (els.length) { els[0].scrollIntoView({ block: 'center' }); els[0].click(); return true; }
      return false;
    });
    LOG('分区选择器点击: ' + opened);
    if (opened) {
      await sleep(2000);
      await page.evaluate((lv1) => {
        const els = Array.from(document.querySelectorAll('div, span, li, a'))
          .filter(el => el.children.length === 0
                        && (el.innerText || '').trim() === lv1);
        if (els.length) { els[0].click(); }
      }, parts[0]);
      await sleep(1500);
      await page.evaluate((lv2) => {
        const els = Array.from(document.querySelectorAll('div, span, li, a'))
          .filter(el => el.children.length === 0
                        && (el.innerText || '').trim() === lv2);
        if (els.length) { els[els.length - 1].click(); }
      }, parts[1] || '');
      LOG('分区已点: ' + parts.join(' → ') + ' ([CAL] 弹层结构待核)');
    }
  } catch (e) { LOG('category err: ' + e.message); }

  // 3. 标题(input, placeholder 含「标题」优先)
  try {
    const ok = await page.evaluate(() => {
      const fields = Array.from(document.querySelectorAll('input, textarea'))
        .filter(x => x.getBoundingClientRect().width > 0);
      const el = fields.find(x => /标题/.test(x.placeholder || ''))
        || fields.find(x => x.tagName === 'INPUT');
      if (!el) return false;
      el.focus();
      return true;
    });
    if (ok) { await page.keyboard.type(String(CFG.title || ''), { delay: 30 }); LOG('title typed'); }
    else LOG('WARN 标题框未定位');
  } catch (e) { LOG('title err: ' + e.message); }

  // 4. 简介(textarea; 尾部带 AIGC 软声明兜底)
  try {
    const DECL = '（本视频由AI辅助制作）';
    let descText = String(CFG.desc || '');
    if (!/AI|人工智能/.test(descText)) descText += DECL;
    const ok = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('textarea'))
        .filter(x => x.getBoundingClientRect().width > 0);
      if (!els.length) return false;
      els[els.length - 1].focus();
      return true;
    });
    if (ok) { await page.keyboard.type(descText, { delay: 30 }); LOG('desc typed(含AIGC软声明兜底)'); }
    else LOG('WARN 简介框未定位(desc 跳过)');
  } catch (e) { LOG('desc err: ' + e.message); }

  // 5. AI 创作声明勾选(B站正面支持; [CAL] 控件文案)
  try {
    const labeled = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll(
        'label, span, div[role=checkbox], [class*=checkbox], [class*=switch]'));
      const t = els.find(el => el.children.length <= 1
        && /AI.{0,6}(生成|创作|制作)|人工智能/.test(
          (el.innerText || el.getAttribute('aria-label') || '')));
      if (t) { t.scrollIntoView({ block: 'center' }); t.click(); return (t.innerText || '').slice(0, 30); }
      return '';
    });
    LOG(labeled ? 'AI 创作声明已点: [' + labeled + ']'
                : 'WARN AI声明控件未找到 — [CAL] 待校准(已有 desc 软声明兜底)');
  } catch (e) { LOG('AI label err: ' + e.message); }

  // 6. 等上传完成(60s 下限) → 立即投稿(滚入视口+防双发 guard)
  for (let i = 0; i < 12; i++) await sleep(5000);
  LOG('upload wait done (>=60s), enter submit-btn flow');
  let clicked = false;
  for (let i = 0; i < 60 && !clicked; i++) {
    try {
      const btnPt = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll(
          'button, [role=button], div, span, a'))
          .filter(el => el.children.length === 0
                        && /^(立即投稿|投稿|发布)$/.test((el.innerText || '').trim()));
        for (const el of els) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.y >= 0 && r.y <= 890) {
            el.scrollIntoView({ block: 'center' });
            const r2 = el.getBoundingClientRect();
            const st = getComputedStyle(el);
            if (st.opacity !== '0' && st.pointerEvents !== 'none') {
              return { x: r2.x + r2.width / 2, y: r2.y + r2.height / 2 };
            }
          }
        }
        return null;
      });
      if (!btnPt) { if (i % 4 === 0) LOG('wait submit-btn poll ' + (i * 5) + 's'); await sleep(5000); continue; }
      const ok = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll(
          'button, [role=button], div, span, a'))
          .filter(el => el.children.length === 0
                        && /^(立即投稿|投稿|发布)$/.test((el.innerText || '').trim()));
        for (const el of els) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.y >= 0 && r.y <= 890) { el.click(); return true; }
        }
        return false;
      });
      if (ok) {
        clicked = true;
        LOG('click 投稿 (合成) at ' + Math.round(btnPt.x) + ',' + Math.round(btnPt.y));
        await sleep(2500);
        const gone = await page.evaluate(() => {
          const t = (document.body.innerText || '');
          if (/投递成功|投稿成功|稿件投递/.test(t)) return 'success-text';
          return '';
        });
        const urlLeft = !/upload/.test(page.url());
        if (gone || urlLeft) {
          LOG('合成点击已生效(' + (gone || 'url-left') + ') — 跳过 CDP 兜底防双发');
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
    } catch (e) { LOG('submit click err: ' + e.message); await sleep(5000); }
  }
  if (!clicked) {
    LOG('SUBMIT_BTN_TIMEOUT — [CAL] 按钮文案/分区缺失致灰?需校准');
    await page.screenshot({ path: 'bili_err_no_btn.png' });
    await browser.close();
    process.exit(4);
  }

  // 7. 成功特征轮询([CAL]: 稿件投递成功/URL 稿件管理)
  let published = false;
  for (let i = 0; i < 30 && !published; i++) {
    const url = page.url();
    let bodyText = '';
    try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 500)); } catch (e) {}
    if ((/upload-manager|manage/.test(url) && !/upload\/video/.test(url))
        || /投递成功|投稿成功|审核中/.test(bodyText)) {
      published = true;
      LOG('PUBLISH_OK url=' + url);
      LOG('after body: ' + bodyText.replace(/\n/g, ' ').slice(0, 300));
    } else await sleep(3000);
  }
  if (!published) {
    LOG('PUBLISH_UNVERIFIED — 截图留档人审(勿重发——先 manage 核对)');
    await page.screenshot({ path: 'bili_unverified.png' });
  }
  await page.screenshot({ path: 'bili_publish_end.png' });
  LOG('PUBLISH_RUN_DONE');
  await browser.close();
})();
