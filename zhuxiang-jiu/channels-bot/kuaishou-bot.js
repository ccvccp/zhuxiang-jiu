// 快手视频 RPA bot 骨架 (2026-10-05 立项)
// 架构镜像 douyin-bot/toutiao-bot 已实证范式: CDP pipe 传输 Chrome +
// 独立 kuaishou-profile 持久登录态; 与其他 bot 完全隔离。
// 用法: node kuaishou-bot.js <config.json>
// config: { action: "probe"|"publish"|"manage", mp4, title, desc, waitLoginMinutes }
//
// ⚠ 现场校准项(登录态就绪后逐一实证, 标记 [CAL]):
//   1. KS_PUBLISH_URL 快手创作者平台视频发布页真实路径(cp.kuaishou.com)
//   2. 登录页特征词(快手: 扫码登录/手机号登录等)
//   3. 上传 input[type=file] 所在 frame
//   4. AIGC 声明控件(快手要求 AIGC 内容标识——发布页声明开关)
//   5. 发布按钮文案(发布/发表)与成功特征(URL 跳作品管理/发布成功)
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('kuaishou_bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// 发布页实证(2026-10-05 probe3): header「发布作品」点击 →
// cp.kuaishou.com/article/publish/video, input[type=file]×1 主文档
const KS_HOME = 'https://cp.kuaishou.com/';
const KS_PUBLISH_URL = process.env.KS_PUBLISH_URL
  || 'https://cp.kuaishou.com/article/publish/video';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\kuaishou-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, kuaishou-profile)');

  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(KS_HOME, { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());
  await sleep(6000);

  // ---- 登录保障(轮询特征, 不自动点击; [CAL] 特征词现场校准) ----
  const loginPageLike = (url, text) =>
    /login|passport|id\.kuaishou/.test(url) ||
    /扫码登录|手机号登录|验证码登录|登录快手/.test(text || '');
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
    LOG('LOGIN_TIMEOUT — 请扫码/验证码登录(快手账号)');
    await page.screenshot({ path: 'ks_login_timeout.png' });
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());

  // ---- probe: 现场校准 ----
  if (CFG.action === 'probe') {
    const dump = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a, button'))
        .map(el => (el.innerText || '').trim() + '|' + (el.href || ''))
        .filter(t => /发布|视频|作品|内容管理/.test(t))
        .slice(0, 40);
      return { url: location.href, links, body: (document.body.innerText || '').slice(0, 1200) };
    });
    LOG('probe url=' + dump.url);
    LOG('publish-ish links:\n  ' + dump.links.join('\n  '));
    LOG('body head:\n' + dump.body.replace(/\n/g, ' / ').slice(0, 600));
    fs.writeFileSync('ks_probe_dump.json', JSON.stringify(dump, null, 2));
    LOG('PROBE_DONE (ks_probe_dump.json) — 校准 KS_PUBLISH_URL/登录词/控件选择器');
    await browser.close();
    return;
  }

  // ---- manage: 作品管理列表转储 ----
  if (CFG.action === 'manage') {
    const mgUrl = await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll('a'))
        .find(x => /作品管理|内容管理/.test(x.innerText || ''));
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
  await page.goto(KS_PUBLISH_URL, { waitUntil: 'domcontentloaded' });
  await sleep(5000);
  LOG('publish page url=' + page.url());

  // 1. 上传 input([CAL] frame 形态: 主文档+全 frame 兜底)
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
    await page.screenshot({ path: 'ks_err_no_upload.png' });
    await browser.close();
    process.exit(3);
  }
  await uploadHandle.uploadFile(CFG.mp4);
  LOG('uploadFile: ' + CFG.mp4);

  // 2. 标题 + 描述填写(2026-10-05 快手诊断实证: 标题为独立字段,
  //    缺失扣标题质量分——发布页 标题input + 描述textarea 分离)
  await sleep(4000);
  try {
    const titleOk = await page.evaluate(() => {
      const fields = Array.from(document.querySelectorAll(
        'input, textarea, [contenteditable=true]'))
        .filter(x => {
          const r = x.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        });
      // 优先 placeholder/aria-label 含「标题」; 兜底第一个 input
      const byPh = fields.find(x =>
        /标题/.test(x.placeholder || '')
        || /标题/.test(x.getAttribute('aria-label') || ''));
      const el = byPh
        || fields.find(x => x.tagName === 'INPUT');
      if (!el) return false;
      el.focus();
      return true;
    });
    if (titleOk) {
      await page.keyboard.type(String(CFG.title || ''), { delay: 30 });
      LOG('title typed: [' + String(CFG.title || '').slice(0, 20) + ']');
    } else {
      LOG('WARN 标题输入框未定位 — 待校准');
    }
  } catch (e) { LOG('title type err: ' + e.message); }
  try {
    // AIGC 软声明(2026-10-05 双重实证: Web 发布页无主动声明入口——
    // 人工未找到+bot 滚底扫描未命中; App 端「更多设置」可能有但
    // Web RPA 不可达)——desc 尾部自动补自声明, 已含 AI 字样则不重复
    const DECL = '（本视频由AI辅助制作）';
    let descText = String(CFG.desc || '');
    if (!/AI|人工智能/.test(descText)) descText += DECL;
    const descOk = await page.evaluate(() => {
      const fields = Array.from(document.querySelectorAll(
        'textarea, [contenteditable=true]'));
      const el = fields.find(x => {
        const r = x.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      if (!el) return false;
      el.focus();
      return true;
    });
    if (descOk) {
      await page.keyboard.type(descText, { delay: 30 });
      LOG('desc typed (描述框聚焦后键入, 含 AIGC 软声明)');
    } else {
      await page.keyboard.type(descText, { delay: 30 });
      LOG('desc typed via keyboard (默认焦点兜底, 含 AIGC 软声明)');
    }
  } catch (e) { LOG('desc type err: ' + e.message); }

  // 3. AIGC 声明(2026-10-05 probe4 实证: 发布设置面板在首屏, AIGC
  //    控件在页面底部"更多设置"——先滚到底再扫; 找不到仅告警不阻断)
  try {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(1500);
    const labeled = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll(
        'label, span, div[role=checkbox], [class*=checkbox], [class*=switch], span, div'));
      const t = els.find(el => el.children.length <= 1
        && /AI.{0,4}生成|人工智能|AIGC/.test(
          (el.innerText || el.getAttribute('aria-label') || '')));
      if (t) {
        const target = t.children.length === 0 ? t : t;
        target.scrollIntoView({ block: 'center' });
        target.click();
        return (t.innerText || '').slice(0, 30);
      }
      return '';
    });
    LOG(labeled ? 'AIGC 声明已点: [' + labeled + ']'
                : 'WARN AIGC声明控件未找到(滚底后) — 待人工定位(不阻断)');
    await page.evaluate(() => window.scrollTo(0, 0));
  } catch (e) { LOG('AIGC label err: ' + e.message); }

  // 4. 等上传完成(60s 下限) → 发布按钮(probe4 实证: 为 DIV 叶子
  //    精确文案「发布」, 非 button 标签; 「发布作品/立即发布」为
  //    干扰项须精确匹配; 滚入视口+防双发 guard)
  for (let i = 0; i < 12; i++) { await sleep(5000); }
  LOG('upload wait done (>=60s), enter publish-btn flow');
  const findPubBtn = () => page.evaluate(() => {
    const els = Array.from(document.querySelectorAll(
      'button, [role=button], div, span, a'))
      .filter(el => el.children.length === 0
                    && (el.innerText || '').trim() === '发布');
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.y >= 0 && r.y <= 890) {
        el.scrollIntoView({ block: 'center' });
        const r2 = el.getBoundingClientRect();
        // 灰置判定: opacity/pointer-events 粗筛
        const st = getComputedStyle(el);
        if (st.opacity !== '0' && st.pointerEvents !== 'none') {
          return { x: r2.x + r2.width / 2, y: r2.y + r2.height / 2 };
        }
      }
    }
    return null;
  });
  let clicked = false;
  for (let i = 0; i < 60 && !clicked; i++) {
    try {
      const btnPt = await findPubBtn();
      if (!btnPt) { if (i % 4 === 0) LOG('wait publish-btn poll ' + (i * 5) + 's'); await sleep(5000); continue; }
      const ok = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll(
          'button, [role=button], div, span, a'))
          .filter(el => el.children.length === 0
                        && (el.innerText || '').trim() === '发布');
        for (const el of els) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.y >= 0 && r.y <= 890) { el.click(); return true; }
        }
        return false;
      });
      if (ok) {
        clicked = true;
        LOG('click 发布 (合成) at ' + Math.round(btnPt.x) + ',' + Math.round(btnPt.y));
        await sleep(2500);
        // 防双发 guard(快手形态): 发布生效特征 = 成功文本出现 或
        // URL 离开发布页 或 发布按钮消失; 未证生效才 CDP 兜底
        const gone = await page.evaluate(() => {
          const t = (document.body.innerText || '');
          if (/发布成功|审核中|已提交/.test(t)) return 'success-text';
          const els = Array.from(document.querySelectorAll(
            'button, [role=button], div, span, a'))
            .filter(el => el.children.length === 0
                          && (el.innerText || '').trim() === '发布'
                          && el.getBoundingClientRect().width > 0);
          return els.length ? '' : 'btn-gone';
        });
        const urlLeft = !/publish\/video/.test(page.url());
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
    } catch (e) { LOG('publish click err: ' + e.message); await sleep(5000); }
  }
  if (!clicked) {
    LOG('PUBLISH_BTN_TIMEOUT — [CAL] 按钮文案/就绪条件需校准');
    await page.screenshot({ path: 'ks_err_no_btn.png' });
    await browser.close();
    process.exit(4);
  }

  // 5. 成功特征轮询([CAL]: URL 跳作品管理 / "发布成功/审核")
  let published = false;
  for (let i = 0; i < 30 && !published; i++) {
    const url = page.url();
    let bodyText = '';
    try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 500)); } catch (e) {}
    if ((/manage|content|works/.test(url) && !/publish|upload/.test(url))
        || /发布成功|审核中|已提交/.test(bodyText)) {
      published = true;
      LOG('PUBLISH_OK url=' + url);
      LOG('after body: ' + bodyText.replace(/\n/g, ' ').slice(0, 300));
    } else { await sleep(3000); }
  }
  if (!published) {
    LOG('PUBLISH_UNVERIFIED — 90s 未见成功特征, 截图留档人审(勿重发——先 manage 核对)');
    await page.screenshot({ path: 'ks_unverified.png' });
  }
  await page.screenshot({ path: 'ks_publish_end.png' });
  LOG('PUBLISH_RUN_DONE');
  await browser.close();
})();
