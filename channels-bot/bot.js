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
  await page.goto('https://channels.weixin.qq.com/', { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());

  // ---- 阶段1: 等待扫码登录 (URL 离开 login.html) ----
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
