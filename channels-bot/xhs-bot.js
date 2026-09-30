// 小红书视频笔记 RPA 发布 bot (2026-09-30 立项, 73号平台扩展)
// 架构复用 douyin-bot.js 已实证范式: CDP pipe 传输 Chrome(无 TCP
// 端口, 剥 --enable-automation 拟真) + 独立 xhs-profile 持久登录态。
// 与 channels/douyin 通道完全隔离(独立 profile/日志/产物前缀)。
//
// ⚠️ 发布流 DOM 状态: xhs 创作者中心发布页未实机校准(36号 SOP
// 实证过图文口径, 发布按钮在闭合 shadow DOM)——publish 流按通用
// 结构编写 + 全程 dump/截图兜底, 首次联调按留档校准选择器。
// probe/manage 两 action 已可直接使用(登录态检测/作品列表)。
//
// 用法: node xhs-bot.js <config.json>
// config: { action: "probe"|"publish"|"manage",
//           mp4, title, desc, waitLoginMinutes }
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('xhs_bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const XHS_HOME = 'https://creator.xiaohongshu.com/';
// 视频笔记发布页(probe 阶段现场校准)
const XHS_UPLOAD = 'https://creator.xiaohongshu.com/publish/publish?source=official';
const XHS_MANAGE = 'https://creator.xiaohongshu.com/manage';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\xhs-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, xhs-profile)');

  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(XHS_HOME, { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());
  await sleep(6000);

  // ---- 登录保障(小红书首次须 App 扫码; bot 只轮询特征不自动点击) ----
  const loginPageLike = (url, text) =>
    /\/login|passport\.|security\.xiaohongshu/.test(url) ||
    /扫码登录|登录后即可|扫码体验/.test(text || '');
  const ensureLogin = async (page, waitMs) => {
    const deadline = Date.now() + waitMs;
    let lastUrl = '';
    let announced = false;
    while (Date.now() < deadline) {
      const url = page.url();
      let bodyText = '';
      try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 800)); } catch (e) {}
      if (url !== lastUrl) { LOG('url -> ' + url); lastUrl = url; }
      if (!loginPageLike(url, bodyText)) return true;
      if (!announced) {
        announced = true;
        await page.screenshot({ path: 'xhs_login_1.png' });
        LOG('=== 请在 Chrome 窗口用「小红书 App」扫码登录 (等待 ' + Math.round(waitMs / 60000) + ' 分钟) ===');
      }
      await sleep(3000);
    }
    return !loginPageLike(page.url(), '');
  };

  if (!(await ensureLogin(page, (CFG.waitLoginMinutes || 8) * 60000))) {
    LOG('LOGIN_TIMEOUT: 二维码未被确认, 详见窗口');
    fs.writeFileSync('xhs_login_state.json', JSON.stringify({ ok: false }));
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());
  fs.writeFileSync('xhs_login_state.json', JSON.stringify({ ok: true, url: page.url(), at: Date.now() }));
  await sleep(2000);

  // ---- 通用结构 dump(probe 与 publish 失败留档共用) ----
  const dumpFrames = async (tag) => {
    const out = [];
    for (const fr of page.frames()) {
      try {
        const info = await fr.evaluate(() => {
          const out = { url: location.href.slice(0, 140) };
          out.files = Array.from(document.querySelectorAll('input[type=file]')).map(e => ({ accept: e.accept || '' }));
          out.editables = Array.from(document.querySelectorAll('[contenteditable=true], textarea, input[placeholder]')).map(e => ({
            tag: e.tagName, ph: e.getAttribute('data-placeholder') || e.placeholder || '',
            w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height),
          })).filter(e => e.w > 40 && e.h > 12).slice(0, 20);
          out.buttons = Array.from(document.querySelectorAll('button, [role=button]')).map(e => (e.innerText || '').trim().slice(0, 16)).filter(Boolean).slice(0, 40);
          out.bodySnippet = (document.body.innerText || '').slice(0, 400);
          return out;
        });
        out.push(info);
      } catch (e) { out.push({ url: fr.url().slice(0, 140), err: String(e).slice(0, 100) }); }
    }
    fs.writeFileSync('xhs_probe_' + tag + '.json', JSON.stringify(out, null, 2));
    return out;
  };

  if (CFG.action === 'probe') {
    await page.screenshot({ path: 'xhs_probe_1.png' });
    await dumpFrames('home');
    await page.goto(XHS_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e => LOG('goto upload err: ' + e.message));
    await sleep(6000);
    if (!(await ensureLogin(page, 5 * 60000))) {
      LOG('RELOGIN_TIMEOUT at upload page');
      await browser.close();
      process.exit(2);
    }
    await sleep(4000);
    await page.screenshot({ path: 'xhs_probe_2.png' });
    const up = await dumpFrames('upload');
    for (const f of up) {
      LOG('frame ' + (f.url || '?') + ' files=' + (f.files || []).length + ' editables=' + (f.editables || []).length);
    }
    LOG('PROBE_DONE (xhs_probe_home.json / xhs_probe_upload.json — 发布流选择器校准源)');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'manage') {
    await page.goto(XHS_MANAGE, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(8000);
    if (loginPageLike(page.url(), '')) {
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await page.goto(XHS_MANAGE, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      await sleep(8000);
    }
    await page.screenshot({ path: 'xhs_manage_1.png' });
    const items = await page.evaluate(() =>
      (document.body.innerText || '').slice(0, 1500)).catch(() => '');
    fs.writeFileSync('xhs_manage_items.txt', items);
    LOG('MANAGE_DONE (xhs_manage_items.txt)');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'publish') {
    // ⚠️ DOM 未实机校准(首次联调按 xhs_probe_*.json 留档校准)
    await page.goto(XHS_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e => LOG('goto err: ' + e.message));
    await sleep(6000);
    if (!(await ensureLogin(page, 5 * 60000))) {
      LOG('RELOGIN_TIMEOUT');
      await browser.close();
      process.exit(2);
    }
    await sleep(3000);

    // 1. 文件选择(视频笔记: input[type=file] accept 含 video)
    const fileHandle = await page.evaluateHandle(() => {
      const cands = Array.from(document.querySelectorAll('input[type=file]'));
      const video = cands.find(e => /video|mp4/i.test(e.accept || ''));
      return video || cands[0] || null;
    });
    const fileEl = fileHandle.asElement();
    if (!fileEl) {
      LOG('FILE_INPUT_NOT_FOUND — dump 留档供选择器校准');
      await dumpFrames('publish_nofile');
      await page.screenshot({ path: 'xhs_publish_fail_1.png' });
      await browser.close();
      process.exit(3);
    }
    await fileEl.uploadFile(CFG.mp4);
    LOG('mp4 uploaded: ' + CFG.mp4);
    // 2. 等上传完成特征(通用: 「重新上传/删除/上传成功」类文本)
    let uploaded = false;
    for (let i = 0; i < 40; i++) {
      const txt = await page.evaluate(() => (document.body.innerText || '').slice(0, 1500)).catch(() => '');
      if (/重新上传|上传成功|删除/.test(txt)) { uploaded = true; break; }
      await sleep(3000);
    }
    LOG('upload signal: ' + uploaded);
    if (!uploaded) {
      LOG('UPLOAD_SIGNAL_TIMEOUT — dump 留档');
      await dumpFrames('publish_upload_timeout');
      await page.screenshot({ path: 'xhs_publish_fail_2.png' });
      await browser.close();
      process.exit(4);
    }
    // 3. 填标题/正文/话题(通用编辑器: 宽度过滤+逐个尝试)
    const fillEditor = async (text, preferIdx) => {
      const handles = await page.$$('input[placeholder], [contenteditable=true], textarea');
      const idx = Math.min(preferIdx, handles.length - 1);
      const el = handles[idx];
      if (!el) return false;
      await el.click({ clickCount: 3 }).catch(() => {});
      await page.keyboard.type(String(text || ''), { delay: 30 }).catch(() => {});
      return true;
    };
    if (CFG.title) await fillEditor(CFG.title, 0);
    if (CFG.desc) await fillEditor(CFG.desc, 1);
    await sleep(1500);
    await page.screenshot({ path: 'xhs_publish_1_filled.png' });
    // 4. 点发布(含闭合 shadow DOM 穿透备选——36号 roadmap 实证坑)
    const publishPt = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button, [role=button], div[class*=publish], span[class*=publish]'));
      const hit = btns.find(e => /^发\s*布$|发布笔记/.test((e.innerText || '').trim()));
      if (hit) { const r = hit.getBoundingClientRect(); if (r.width > 0) { hit.scrollIntoView({ block: 'center' }); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; } }
      // shadow 穿透: 遍历所有 shadowRoot 查发布按钮
      const dig = (root) => {
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot) { const r = dig(el.shadowRoot); if (r) return r; }
          if (/^发\s*布$/.test((el.innerText || el.textContent || '').trim().slice(0, 8))) {
            const r = el.getBoundingClientRect();
            if (r.width > 0) { el.scrollIntoView({ block: 'center' }); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }
          }
        }
        return null;
      };
      return dig(document);
    }).catch(() => null);
    if (!publishPt) {
      LOG('PUBLISH_BTN_NOT_FOUND — dump 留档供选择器校准');
      await dumpFrames('publish_nobtn');
      await page.screenshot({ path: 'xhs_publish_fail_3.png' });
      await browser.close();
      process.exit(5);
    }
    LOG('publish btn pt: ' + JSON.stringify(publishPt));
    await page.evaluate((p) => { const hit = document.elementFromPoint(p.x, p.y); if (hit) hit.click(); }, publishPt).catch(() => {});
    await sleep(2500);
    // CDP 真实点击兜底(合成点击无反应时)
    const cdp = await page.createCDPSession();
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: publishPt.x, y: publishPt.y, button: 'none', pointerType: 'mouse' });
    await sleep(150);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: publishPt.x, y: publishPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    await sleep(90);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: publishPt.x, y: publishPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    await sleep(8000);
    // 5. 成功特征(跳管理页/成功提示)
    const finalUrl = page.url();
    const finalText = await page.evaluate(() => (document.body.innerText || '').slice(0, 600)).catch(() => '');
    const ok = /manage|发布成功/.test(finalUrl + finalText);
    await page.screenshot({ path: ok ? 'xhs_publish_ok.png' : 'xhs_publish_after.png' });
    fs.writeFileSync('xhs_publish_result.json', JSON.stringify({
      ok, url: finalUrl, at: Date.now(), note: ok ? '' : 'DOM 未校准——按 xhs_publish_after.png / xhs_probe_*.json 留档校准选择器',
    }, null, 2));
    LOG((ok ? 'PUBLISH_OK' : 'PUBLISH_UNVERIFIED') + ' url=' + finalUrl.slice(0, 100));
    await browser.close();
    process.exit(ok ? 0 : 6);
  }

  LOG('unknown action: ' + CFG.action);
  await browser.close();
  process.exit(1);
})().catch(e => { console.error('FATAL', e); process.exit(9); });
