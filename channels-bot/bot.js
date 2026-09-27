// 视频号助手自动发布 bot (CDP pipe 传输, 无 TCP 端口)
// 用法: node bot.js <config.json>
// config: { action: "probe"|"publish", mp4, desc, shortTitle, waitLoginMinutes }
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\chrome-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, no tcp port)');

  const page = (await browser.pages())[0] || await browser.newPage();
  // 关键: --window-size 可能不生效(实证 800x600), 强制视口 1280x900 否则列表操作列(x~1090)在视口外
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto('https://channels.weixin.qq.com/platform/post/create', { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());

  // ---- 阶段1: 等待扫码登录 (URL 离开 login.html) ----
  await sleep(6000); // 客户端 auth 检查有延迟重定向, 先沉降防假阳性
  const deadline = Date.now() + (CFG.waitLoginMinutes || 8) * 60000;
  let logged = false;
  let lastUrl = '';
  while (Date.now() < deadline) {
    const url = page.url();
    if (url !== lastUrl) { LOG('url -> ' + url); lastUrl = url; }
    if (!/login\.html/.test(url)) { logged = true; break; }
    await sleep(3000);
  }
  if (!logged) {
    LOG('LOGIN_TIMEOUT: 二维码未被确认(或仍在循环), 详见窗口');
    fs.writeFileSync('login_state.json', JSON.stringify({ ok: false }));
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());
  fs.writeFileSync('login_state.json', JSON.stringify({ ok: true, url: page.url(), at: Date.now() }));

  await sleep(3000);

  // ---- 阶段2: 进发布页 ----
  await page.goto('https://channels.weixin.qq.com/platform/post/create', { waitUntil: 'domcontentloaded' });
  await sleep(8000);
  LOG('publish page url=' + page.url());
  // 会话过期: 弹回 login.html → 等扫码
  if (page.url().includes('login.html')) {
    LOG('SESSION_EXPIRED — 请扫码');
    const dl = Date.now() + 10 * 60000;
    while (Date.now() < dl && page.url().includes('login.html')) await sleep(3000);
    if (page.url().includes('login.html')) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
    LOG('re-logged in');
    await page.goto('https://channels.weixin.qq.com/platform/post/create', { waitUntil: 'domcontentloaded' });
    await sleep(6000);
    LOG('publish page url(2)=' + page.url());
  }

  const dumpStructure = () => page.evaluate(() => {
    const path = (el) => {
      const parts = [];
      let n = el;
      while (n && n.nodeType === 1 && parts.length < 6) {
        let s = n.tagName.toLowerCase();
        if (n.id) { s = '#' + n.id; parts.unshift(s); break; }
        const cls = (n.className && typeof n.className === 'string') ? n.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
        if (cls) s += '.' + cls;
        parts.unshift(s);
        n = n.parentElement;
      }
      return parts.join(' > ');
    };
    const out = { files: [], textareas: [], editables: [], buttons: [], inputs: [], dialogs: [] };
    document.querySelectorAll('input[type=file]').forEach(e => out.files.push(path(e)));
    document.querySelectorAll('textarea').forEach(e => out.textareas.push({ path: path(e), ph: e.placeholder || '' }));
    document.querySelectorAll('[contenteditable=true]').forEach(e => out.editables.push({ path: path(e), text: (e.innerText || '').slice(0, 30) }));
    document.querySelectorAll('button').forEach(e => {
      const t = (e.innerText || '').trim().slice(0, 20);
      if (t) out.buttons.push({ path: path(e), text: t });
    });
    document.querySelectorAll('input:not([type=file])').forEach(e => out.inputs.push({ path: path(e), type: e.type, ph: e.placeholder || '' }));
    document.querySelectorAll('.weui-desktop-dialog__wrp').forEach(d => out.dialogs.push((d.innerText || '').slice(0, 300)));
    out.url = location.href;
    out.bodyText = (document.body.innerText || '').slice(0, 600);
    return out;
  });

  if (CFG.action === 'probe') {
    await page.screenshot({ path: 'probe_1.png' });
    LOG('shot probe_1.png');
    const framesInfo = [];
    for (const frame of page.frames()) {
      try {
        const info = await frame.evaluate(() => {
          const path = (el) => {
            const parts = [];
            let n = el;
            while (n && n.nodeType === 1 && parts.length < 6) {
              let s = n.tagName.toLowerCase();
              if (n.id) { s = '#' + n.id; parts.unshift(s); break; }
              const cls = (n.className && typeof n.className === 'string') ? n.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
              if (cls) s += '.' + cls;
              parts.unshift(s);
              n = n.parentElement;
            }
            return parts.join(' > ');
          };
          const q = (s) => document.querySelectorAll(s).length;
          const out = {
            url: location.href.slice(0, 140),
            counts: { files: q('input[type=file]'), textareas: q('textarea'), editables: q('[contenteditable=true]'), inputs: q('input:not([type=file])') },
            hasUploadZone: (document.body.innerText || '').includes('上传时长8小时内'),
          };
          if (out.hasUploadZone) {
            out.filePaths = Array.from(document.querySelectorAll('input[type=file]')).map(e => path(e));
            out.textareaPaths = Array.from(document.querySelectorAll('textarea')).map(e => ({ path: path(e), ph: e.placeholder || '' }));
            out.editablePaths = Array.from(document.querySelectorAll('[contenteditable=true]')).map(e => ({ path: path(e), ph: e.getAttribute('data-placeholder') || '' }));
            out.inputPaths = Array.from(document.querySelectorAll('input:not([type=file])')).map(e => ({ path: path(e), type: e.type, ph: e.placeholder || '' }));
            out.actions = Array.from(document.querySelectorAll('button, [role=button]')).map(e => (e.innerText || '').trim().slice(0, 20)).filter(Boolean).slice(0, 30);
            out.bodySnippet = (document.body.innerText || '').slice(0, 500);
          }
          return out;
        });
        framesInfo.push(info);
      } catch (e) {
        framesInfo.push({ url: frame.url().slice(0, 140), err: String(e).slice(0, 120) });
      }
    }
    fs.writeFileSync('probe_frames.json', JSON.stringify(framesInfo, null, 2));
    for (const f of framesInfo) {
      LOG('frame ' + (f.url || '?') + ' zone=' + f.hasUploadZone + ' counts=' + JSON.stringify(f.counts || {}));
    }
    LOG('PROBE_DONE (probe_frames.json)');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'publish') {
    const getForm = () => page.frames().find(f => f.url().includes('/micro/content/post/create'));
    let form = getForm();
    if (!form) { LOG('NO_FORM_FRAME'); await browser.close(); process.exit(3); }
    LOG('form frame ok');

    // 1. 上传视频 (frame.$ 在该 iframe 缓存旧 document, 一律 evaluateHandle 取元素)
    const qh = async (sel) => {
      const f = getForm();
      if (!f) return null;
      try {
        const h = await f.evaluateHandle((s) => document.querySelector(s), sel);
        const ok = await h.evaluate((el) => !!(el && el.tagName)).catch(() => false);
        if (ok) return h.asElement();
      } catch (e) {}
      return null;
    };
    const findUpload = async () => {
      for (const f of page.frames()) {
        try {
          const h = await f.evaluateHandle(() => document.querySelector('input[type=file]'));
          const ok = await h.evaluate((el) => !!(el && el.tagName)).catch(() => false);
          if (ok) return { f, h: h.asElement() };
        } catch (e) { /* frame 可能失效, 跳过 */ }
      }
      return null;
    };
    let upload = null;
    for (let i = 0; i < 15; i++) {
      upload = await findUpload();
      if (upload) break;
      const counts = [];
      for (const f of page.frames()) {
        try { counts.push(await f.evaluate(() => document.querySelectorAll('input[type=file]').length)); }
        catch (e) { counts.push(-1); }
      }
      LOG('poll ' + (i * 2) + 's frames=' + page.frames().length + ' fileInputs=' + JSON.stringify(counts));
      await sleep(2000);
    }
    if (!upload) { LOG('NO_FILE_INPUT after 30s'); await page.screenshot({ path: 'err_no_input.png' }); await browser.close(); process.exit(3); }
    LOG('upload input found, frame=' + upload.f.url().slice(0, 80));
    await upload.h.uploadFile(CFG.mp4);
    LOG('uploadFile: ' + CFG.mp4);

    // 2. 等上传+处理完成
    let uploadDone = false;
    for (let i = 0; i < 24; i++) {
      await sleep(5000);
      form = getForm() || form;
      const st = await form.evaluate(() => ({
        re: (document.body.innerText || '').includes('重新上传'),
        del: (document.body.innerText || '').includes('删除'),
      }));
      if (i % 2 === 0) LOG('upload poll ' + (i * 5) + 's re=' + st.re + ' del=' + st.del);
      if (st.re || st.del) { uploadDone = true; break; }
    }
    LOG('uploadDone=' + uploadDone);
    await page.screenshot({ path: 'publish_1_uploaded.png' });

    // 3. 填视频描述 (in-page 定位可见编辑器 → focus → keyboard.type)
    try {
      const f3 = getForm();
      const descPick = await f3.evaluateHandle(() => {
        const els = Array.from(document.querySelectorAll('[contenteditable], [data-placeholder]'));
        const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 20; });
        const byPh = vis.find(e => ((e.getAttribute('data-placeholder') || '') + (e.getAttribute('placeholder') || '')).includes('描述'));
        return byPh || vis[0] || null;
      });
      const descOk = await descPick.evaluate(el => !!(el && el.tagName)).catch(() => false);
      if (descOk) {
        await descPick.evaluate(el => el.focus()).catch(e => LOG('desc focus err ' + e.message));
        await page.keyboard.type(CFG.desc, { delay: 20 });
        LOG('desc typed via keyboard');
      } else {
        LOG('NO_DESC_EDITOR');
        const cands = await f3.evaluate(() => Array.from(document.querySelectorAll('[contenteditable], [data-placeholder], textarea')).map(e => ({ tag: e.tagName, ce: e.getAttribute('contenteditable') || '', ph: e.getAttribute('data-placeholder') || e.getAttribute('placeholder') || '', cls: String(e.className).slice(0, 50), w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height) })).slice(0, 12));
        LOG('candidates: ' + JSON.stringify(cands));
      }
    } catch (e) { LOG('desc step err: ' + e.message); }

    // 4. 短标题 (focus + keyboard.type)
    if (CFG.shortTitle) {
      try {
        const stHandle = await qh('.short-title-wrap input');
        if (stHandle) {
          await stHandle.evaluate(el => { el.focus(); el.select(); }).catch(() => {});
          await page.keyboard.type(CFG.shortTitle, { delay: 20 });
          LOG('shortTitle typed via keyboard');
        } else LOG('NO_SHORT_TITLE_INPUT');
      } catch (e) { LOG('shortTitle step err: ' + e.message); }
    }

    // 4.5 视频标注 (AI 声明合规项)
    try {
      form = getForm() || form;
      const opened = await form.evaluate(() => {
        const leaves = Array.from(document.querySelectorAll('div,span'));
        const t = leaves.find(e => e.children.length === 0 && (e.innerText || '').trim() === '选择视频标注');
        if (t) { t.click(); return true; }
        return false;
      });
      LOG('open 视频标注: ' + opened);
      if (opened) {
        await sleep(2000);
        const opts = await form.evaluate(() => {
          const items = Array.from(document.querySelectorAll('li, [class*=option], [class*=item]'));
          const texts = items.map(e => (e.innerText || '').trim()).filter(t => t && t.length < 40);
          return Array.from(new Set(texts)).slice(0, 25);
        });
        LOG('标注 options: ' + JSON.stringify(opts));
        const picked = await form.evaluate(() => {
          const items = Array.from(document.querySelectorAll('li, [class*=option]'));
          const ai = items.find(e => e.children.length === 0 && (e.innerText || '').trim() === '含AI生成内容');
          if (ai) { ai.click(); return '含AI生成内容'; }
          return '';
        });
        LOG('标注 picked: [' + picked + ']');
        await sleep(1000);
      }
    } catch (e) { LOG('标注 err: ' + e.message); }

    await sleep(1500);
    await page.screenshot({ path: 'publish_2_filled.png' });

    // 5. 点发表
    let clicked = false;
    try {
      form = getForm() || form;
      clicked = await form.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button, [role=button]'));
        const b = btns.find(x => (x.innerText || '').trim() === '发表');
        if (b) { b.click(); return true; }
        return false;
      });
    } catch (e) { LOG('publish click err: ' + e.message); }
    LOG('click 发表: ' + clicked);

    // 6. 等结果
    await sleep(15000);
    await page.screenshot({ path: 'publish_3_after.png' });
    form = getForm() || form;
    let afterBody = '';
    try {
      afterBody = await form.evaluate(() => (document.body.innerText || '').slice(0, 500));
    } catch (e) { afterBody = 'frame gone: ' + e.message; }
    const afterUrl = page.url();
    LOG('after url=' + afterUrl);
    LOG('after body: ' + afterBody.slice(0, 300));
    const mainBody = await page.evaluate(() => (document.body.innerText || '').slice(0, 400));
    LOG('main body: ' + mainBody.slice(0, 200));
    fs.writeFileSync('publish_result.json', JSON.stringify({ clicked, uploadDone, afterUrl, afterBody, mainBody }, null, 2));
    LOG('PUBLISH_RUN_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'annotate') {
    const gotoList = async () => {
      try { await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 }); return true; }
      catch (e) { LOG('goto list err: ' + String(e.message).slice(0, 60)); return false; }
    };
    if (!await gotoList()) { await sleep(2500); await gotoList(); }
    await sleep(4000);
    // 会话过期: 弹回 login.html → 等用户扫码 (最长10分钟)
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — Chrome 窗口已出二维码, 请扫码');
      const dl = Date.now() + 10 * 60000;
      while (Date.now() < dl && page.url().includes('login.html')) await sleep(3000);
      if (page.url().includes('login.html')) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      LOG('re-logged in');
      await sleep(2000);
      await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded' });
      await sleep(5000);
    }
    let f = page.frames().find(x => x.url().includes('/micro/content/post'));
    if (!f) { LOG('NO_LIST_FRAME'); await browser.close(); process.exit(3); }
    await page.bringToFront();

    // 1. 找第一张卡的「修改描述和封面」(可见实例) → scrollIntoView 滚入 → 命中验证 → 合成点击
    const findBtn = () => f.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '修改描述和封面');
      const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
      if (!vis.length) return null;
      const el = vis[0];
      const r = el.getBoundingClientRect();
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      const hit = document.elementFromPoint(cx, cy);
      return { cx, cy, inView: !!hit, hitTag: hit ? hit.tagName : null, hitTxt: hit ? (hit.innerText || '').trim().slice(0, 24) : '' };
    });
    let btn = await findBtn();
    LOG('edit btn initial: ' + JSON.stringify(btn));
    if (!btn) { LOG('NO_EDIT_BTN'); await page.screenshot({ path: 'anno_0.png' }); await browser.close(); process.exit(3); }
    if (!btn.inView) {
      await f.evaluate(() => {
        const els = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '修改描述和封面');
        const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
        if (vis.length) vis[0].scrollIntoView({ block: 'center', inline: 'center' });
      });
      await sleep(800);
      btn = await findBtn();
      LOG('edit btn after scroll: ' + JSON.stringify(btn));
    }
    await page.screenshot({ path: 'anno_0_list.png' });

    // 2. 终版: 重进列表恢复自然滚动 → 读视口 → hover 可见卡片区 → 重测按钮 → 点击
    await gotoList();
    await sleep(5000);
    f = page.frames().find(x => x.url().includes('/micro/content/post')) || f;
    const ifrRect2 = await page.evaluate(() => {
      const ifr = document.querySelector('iframe');
      if (!ifr) return { x: 0, y: 0 };
      const r = ifr.getBoundingClientRect();
      return { x: r.x, y: r.y };
    });
    const vp = await f.evaluate(() => ({ iw: window.innerWidth, ih: window.innerHeight }));
    LOG('viewport: ' + JSON.stringify(vp) + ' iframe: ' + JSON.stringify(ifrRect2));
    // 自然状态下按钮位置 + 卡片行可见区
    const natural = await f.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '修改描述和封面');
      const r0 = els.length ? els[0].getBoundingClientRect() : null;
      const leaf = Array.from(document.querySelectorAll('div,span')).find(e => e.children.length === 0 && (e.innerText || '').startsWith('中秋团圆宴白酒清单火了'));
      let card = leaf;
      for (let i = 0; i < 6 && card && card.parentElement; i++) { card = card.parentElement; const r = card.getBoundingClientRect(); if (r.width > 300 && r.height > 50) break; }
      const rc = card ? card.getBoundingClientRect() : null;
      return { btn: r0 ? { x: Math.round(r0.x), y: Math.round(r0.y), w: Math.round(r0.width), h: Math.round(r0.height) } : null, card: rc ? { x: Math.round(rc.x), y: Math.round(rc.y), w: Math.round(rc.width), h: Math.round(rc.height) } : null };
    });
    LOG('natural state: ' + JSON.stringify(natural));
    // hover 卡片行可见部分中心 (x 取 min(card中心, iw-100) 保证在视口内)
    if (natural.card) {
      const hovX = Math.min(natural.card.x + natural.card.w / 2, vp.iw - 80);
      const hovY = natural.card.y + natural.card.h / 2;
      await page.mouse.move(ifrRect2.x + hovX, ifrRect2.y + hovY, { steps: 6 });
      LOG('hover card at ' + Math.round(ifrRect2.x + hovX) + ',' + Math.round(ifrRect2.y + hovY));
      await sleep(1800);
      const hovers = await f.evaluate(() => Array.from(document.querySelectorAll(':hover')).slice(-6).map(e => String(e.className).slice(0, 40)));
      LOG(':hover chain: ' + JSON.stringify(hovers));
      await page.screenshot({ path: 'anno_0d_hovered.png' });
      LOG('shot anno_0d_hovered.png');
    }
    let dlgOpen = false;
    let allPages = [];
    // 重测按钮 (hover 后可能移位/浮现)
    const target = await f.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '修改描述和封面');
      const inVp = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.x >= 0 && r.x < window.innerWidth - 5 && r.y >= 0 && r.y < window.innerHeight - 5; });
      if (!inVp.length) return null;
      const el = inVp[0];
      const r = el.getBoundingClientRect();
      el.style.outline = '3px solid red';
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    });
    LOG('click target (post-hover): ' + JSON.stringify(target));
    if (!dlgOpen && target) {
      // 自动点击打不开 → 混合模式: 请用户在 Chrome 窗口亲手点, bot 等编辑器出现
      LOG('AUTO_CLICK_FAILED — 请在 Chrome 窗口中 hover 第一条视频并点「修改描述和封面」(bot 等待最多5分钟)');
      const wdl = Date.now() + 5 * 60000;
      while (Date.now() < wdl) {
        await sleep(2500);
        // 导航竞态守卫: 帧列表瞬时为空时跳过本轮
        const frames = page.frames();
        if (!frames.length) continue;
        f = frames.find(x => x.url().includes('/micro/content/post')) || frames[frames.length - 1];
        let ok = false;
        try { ok = await f.evaluate(() => (document.body.innerText || '').includes('原视频信息')); } catch (e) { continue; }
        if (ok) { dlgOpen = true; LOG('editor opened by user (dialog)'); break; }
        const u = page.url();
        if (/edit/i.test(u)) { LOG('editor page: ' + u.slice(0, 110)); break; }
      }
      if (!dlgOpen && !/edit/i.test(page.url())) { LOG('USER_TIMEOUT'); await browser.close(); process.exit(6); }
      await sleep(3500);
      f = (page.frames().find(x => x.url().includes('/micro/content/post')) || f);
      LOG('editor frame: ' + f.url().slice(0, 110));
    }
    await page.screenshot({ path: 'anno_1_editor.png' });
    LOG('shot anno_1_editor.png');
    const txt = await f.evaluate(() => (document.body.innerText || '').slice(0, 2500)).catch(() => '');
    LOG('editor text: ' + txt.slice(0, 900));
    fs.writeFileSync('annotate_editor.txt', txt);
    if (!dlgOpen) {
      LOG('EDITOR_NOT_OPENED');
      await browser.close();
      process.exit(5);
    }

    // 3. 点「选择视频标注」开下拉 (带挂载重试)
    const findAnno = () => f.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div,span'));
      const t = els.find(e => e.children.length === 0 && (e.innerText || '').trim() === '选择视频标注');
      if (!t) return null;
      const r = t.getBoundingClientRect();
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      if (!document.elementFromPoint(cx, cy)) { t.scrollIntoView({ block: 'center', inline: 'center' }); }
      return { cx, cy };
    });
    let annoFound = await findAnno();
    if (!annoFound) { await sleep(4000); annoFound = await findAnno(); }
    LOG('选择视频标注: ' + JSON.stringify(annoFound));
    if (annoFound) {
      await f.evaluate((cx, cy) => {
        let el = document.elementFromPoint(cx, cy);
        if (el) el.click();
      }, annoFound.cx, annoFound.cy);
      LOG('clicked 选择视频标注');
      await sleep(2000);
      await page.screenshot({ path: 'anno_2_options.png' });
      const opts = await f.evaluate(() => {
        return Array.from(document.querySelectorAll('li, [class*=option], [class*=item]')).map(e => (e.innerText || '').trim()).filter(t => t && t.length < 40);
      });
      LOG('options: ' + JSON.stringify(Array.from(new Set(opts)).slice(0, 25)));
      // 选 含AI生成内容 (leaf 精确匹配)
      const picked = await f.evaluate(() => {
        const items = Array.from(document.querySelectorAll('li, [class*=option], [class*=item]'));
        const ai = items.find(e => e.children.length === 0 && (e.innerText || '').trim() === '含AI生成内容');
        if (ai) { ai.scrollIntoView({ block: 'center' }); const r = ai.getBoundingClientRect(); const cx = r.x + r.width / 2, cy = r.y + r.height / 2; const hit = document.elementFromPoint(cx, cy); if (hit) { hit.click(); return 'clicked:' + (hit.innerText || '').trim().slice(0, 10); } return 'nohit'; }
        return 'notfound';
      });
      LOG('picked: ' + picked);
      await sleep(1500);
      await page.screenshot({ path: 'anno_3_picked.png' });
      // 保存: 找确定/保存类按钮
      const saveBtns = await f.evaluate(() => {
        return Array.from(document.querySelectorAll('button')).map(b => (b.innerText || '').trim()).filter(t => t && /确定|保存|确认/.test(t)).slice(0, 8);
      });
      LOG('save candidates: ' + JSON.stringify(saveBtns));
      const saved = await f.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button')).filter(b => /^(确定|保存|确认)$/.test((b.innerText || '').trim()));
        if (!btns.length) return false;
        const b = btns[btns.length - 1];
        const r = b.getBoundingClientRect();
        const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
        const hit = document.elementFromPoint(cx, cy);
        if (hit) { hit.click(); return true; }
        b.click(); return true;
      });
      LOG('clicked save: ' + saved);
      await sleep(4000);
      await page.screenshot({ path: 'anno_4_saved.png' });
      const finalTxt = await f.evaluate(() => (document.body.innerText || '').slice(0, 600)).catch(() => '');
      LOG('final text: ' + finalTxt.slice(0, 300));
      fs.writeFileSync('annotate_result.txt', finalTxt);
    } else {
      LOG('NO_ANNOTATION_ENTRY');
    }
    LOG('ANNOTATE_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'getlink') {
    const captured = [];
    page.on('response', async (r) => {
      try {
        const u = r.url();
        if (u.includes('cgi-bin') || u.includes('finder')) {
          const body = await r.text().catch(() => '');
          if (body) captured.push({ u: u.slice(0, 150), body: body.slice(0, 150000) });
        }
      } catch (e) {}
    });
    await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded' });
    await sleep(6000);
    try {
      const ctx = browser.defaultBrowserContext();
      await ctx.overridePermissions('https://channels.weixin.qq.com', ['clipboard-read', 'clipboard-write']);
      LOG('clipboard perms granted');
    } catch (e) { LOG('perm err ' + e.message); }
    const f = page.frames().find(x => x.url().includes('/micro/content/post')) || page.frames()[page.frames().length - 1];
    LOG('list frame: ' + f.url().slice(0, 80));
    const info = await f.evaluate(() => {
      const cards = Array.from(document.querySelectorAll('[class*=post-item], [class*=post-list] > *, li'));
      const texts = Array.from(document.querySelectorAll('div,span,a,button')).map(e => (e.innerText || '').trim()).filter(t => t && t.length < 15);
      return { cards: cards.length, uniqTexts: Array.from(new Set(texts)).slice(0, 40) };
    });
    LOG('list info cards=' + info.cards);
    LOG('texts: ' + JSON.stringify(info.uniqTexts));
    await page.bringToFront();
    let link = '';
    let shared = false;
    // 真实鼠标: 移到卡片中心(CSS :hover 需真实事件) → 菜单出现 → 真实点击复制视频链接
    try {
      const ifrRect = await page.evaluate(() => {
        const ifr = document.querySelector('iframe');
        if (!ifr) return null;
        const r = ifr.getBoundingClientRect();
        return { x: r.x, y: r.y };
      });
      const cardRect = await f.evaluate(() => {
        const leaf = Array.from(document.querySelectorAll('div,span')).find(e => e.children.length === 0 && (e.innerText || '').startsWith('中秋团圆宴白酒清单火了'));
        if (!leaf) return null;
        let card = leaf;
        for (let i = 0; i < 6 && card.parentElement; i++) {
          card = card.parentElement;
          const r = card.getBoundingClientRect();
          if (r.width > 200 && r.height > 50) break;
        }
        const r = card.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      });
      LOG('iframe: ' + JSON.stringify(ifrRect) + ' card: ' + JSON.stringify(cardRect));
      if (ifrRect && cardRect) {
        // 全局找「可见的」分享按钮 (第一张卡的操作排最前) → 真实点击开对话框
        const shareRect = await f.evaluate(() => {
          const cands = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => e.children.length === 0 && (e.innerText || '').trim() === '分享');
          const vis = cands.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
          if (!vis.length) return null;
          const r = vis[0].getBoundingClientRect();
          return { x: r.x, y: r.y, w: r.width, h: r.height };
        });
        LOG('share btn rect: ' + JSON.stringify(shareRect));
        if (shareRect) {
          shared = true;
          const sx = ifrRect.x + shareRect.x + shareRect.w / 2;
          const sy = ifrRect.y + shareRect.y + shareRect.h / 2;
          await page.mouse.move(sx, sy, { steps: 3 });
          await sleep(200);
          await page.mouse.click(sx, sy);
          LOG('real click 分享 at ' + Math.round(sx) + ',' + Math.round(sy));
          await sleep(4000);
          await page.screenshot({ path: 'share_open.png' });
          LOG('shot share_open.png');
          // 对话框内找可见的 复制视频链接 → 真实点击
          const copyRect = await f.evaluate(() => {
            const els = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '复制视频链接');
            const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            if (!vis.length) return null;
            const r = vis[0].getBoundingClientRect();
            return { x: r.x, y: r.y, w: r.width, h: r.height };
          });
          LOG('copy btn rect: ' + JSON.stringify(copyRect));
          if (copyRect) {
            const cx = ifrRect.x + copyRect.x + copyRect.w / 2;
            const cy = ifrRect.y + copyRect.y + copyRect.h / 2;
            await page.mouse.move(cx, cy, { steps: 3 });
            await sleep(200);
            await page.mouse.click(cx, cy);
            LOG('real click 复制视频链接 at ' + Math.round(cx) + ',' + Math.round(cy));
            await sleep(2500);
            try { link = await page.evaluate(() => navigator.clipboard.readText()); } catch (e) { }
            LOG('clipboard: ' + String(link).slice(0, 150));
            if (!/^https?:/.test(String(link))) link = '';
          }
        }
      }
    } catch (e) { LOG('mouse flow err: ' + e.message); }
    // 2) QR 兜底 (排除转圈图标, 含 canvas)
    if (!link) {
      await sleep(4000);
      const qr = await f.evaluate(() => {
        const SPINNER = 'iVBORw0KGgoAAAANSUhEUgAAAKAAAACgCAMAAAC8EZcf';
        const imgs = Array.from(document.images).filter(i => i.src && i.src.startsWith('data:image/png;base64,') && !i.src.includes(SPINNER) && i.width >= 100);
        if (imgs.length) return imgs[0].src;
        const cvs = Array.from(document.querySelectorAll('canvas')).filter(c => c.width >= 100 && c.height >= 100);
        if (cvs.length) { try { return cvs[0].toDataURL('image/png'); } catch (e) { } }
        return '';
      });
      LOG('qr fallback length: ' + qr.length);
      if (qr) {
        const buf = Buffer.from(qr.split(',')[1], 'base64');
        fs.writeFileSync('qr.png', buf);
        LOG('qr.png saved ' + buf.length + ' bytes');
        try {
          const { Jimp } = require('jimp');
          const jsQR = require('jsqr');
          const image = await Jimp.read(buf);
          const res = jsQR(image.bitmap.data, image.bitmap.width, image.bitmap.height);
          if (res && res.data) { link = res.data; LOG('decoded link: ' + link); }
          else LOG('qr decode failed (jsqr)');
        } catch (e) { LOG('jsqr err: ' + e.message); }
      }
    }
    // 3) 网络捕获全量正则 + 落盘
    if (!link) {
      const bodies = captured.map(c => c.body).join('\n');
      const m = bodies.match(/https?:\/\/[^"'\\\s]*sph[^"'\\\s]*/) || bodies.match(/https?:\\?\/\\?\/[^"'\\\s]*sph[^"'\\\s]*/);
      if (m) { link = m[0].replace(/\\\//g, '/'); LOG('link from network: ' + link); }
    }
    fs.writeFileSync('captured_full.json', JSON.stringify(captured));
    LOG('captured ' + captured.length + ' responses (captured_full.json)');
    fs.writeFileSync('link_result.json', JSON.stringify({ shared, link: String(link) }, null, 2));
    LOG('GETLINK_DONE');
    await browser.close();
    process.exit(0);
  }

  LOG('unknown action');
  await browser.close();
})().catch(e => { LOG('ERR ' + (e && e.message || e)); process.exit(1); });
