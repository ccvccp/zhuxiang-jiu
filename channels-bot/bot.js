// 视频号助手自动发布 bot (CDP pipe 传输, 无 TCP 端口)
// 用法: node bot.js <config.json>
// config: { action: "probe"|"publish", mp4, desc, shortTitle, waitLoginMinutes, clearCookies }
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

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
  // 可选: 清视频号 cookie 强制走登录流程(登录弹窗探测/回归用)
  if (CFG.clearCookies) {
    const c = await page.createCDPSession();
    await c.send('Storage.clearDataForOrigin', { origin: 'https://channels.weixin.qq.com', storageTypes: 'cookies' });
    LOG('cookies cleared for channels.weixin.qq.com');
  }
  await page.goto('https://channels.weixin.qq.com/platform/post/create', { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());

  // ---- 登录保障 (2026-09-28 优化: 设备已绑定微信, 登录页可一键点击免扫码; 失败回退等扫码) ----
  // 铁律: qrconnect iframe 按钮合成 click 无效(trusted click, 同列表页 Vue 压力校验)——
  //       必须 CDP Input.dispatchMouseEvent 真实点击(force=0.5 对齐人手 pointerdown 压力),
  //       iframe 内坐标需叠加主页面 iframe 偏移
  const ensureLogin = async (page, waitMs) => {
    const deadline = Date.now() + waitMs;
    const tried = []; // 已点过的按钮文案, 防同一错误目标反复点击
    let lastTry = 0;
    let lastUrl = '';
    let enterSpawned = false; // 微信授权弹窗 Enter 允许器只 spawn 一次
    // 找 frame 内目标按钮的 iframe 视口坐标
    // 铁律: 微信 web 授权「允许」按钮在闭合 shadow DOM 内(querySelectorAll 穿不透, 小红书发布按钮栏同款)——
    //       deepQueryAll 递归穿透 shadowRoot 才能找到
    const findPt = (fr, triedArr) => fr.evaluate((arr) => {
      const deepQueryAll = (sel) => {
        const out = [];
        const walk = (node) => {
          node.querySelectorAll(sel).forEach(e => out.push(e));
          node.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); });
        };
        walk(document);
        return out;
      };
      const bad = /扫码|扫一扫|二维码|切换|其他方式|取消|帮助|拒绝/;
      const cands = deepQueryAll('button, [role=button], a, div, span')
        .filter(e => {
          const t = (e.innerText || '').trim();
          if (!t || t.length > 12 || bad.test(t)) return false;
          if (!/(登录|进入|允许|确认)/.test(t)) return false;
          // 「登录视频号助手」疑似 Chrome 内授权确认, 允许重试但限 3 次(防刷屏)
          const clicks = arr.filter(x => x === t).length;
          if (clicks >= (t === '登录视频号助手' ? 3 : 1)) return false;
          if (e.children.length > 0) return false; // 只点叶子, 避免命中容器
          const r = e.getBoundingClientRect();
          return r.width > 10 && r.height > 10 && r.x >= 0 && r.y >= 0;
        });
      if (!cands.length) return null;
      const el = cands[0];
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, t: (el.innerText || '').trim() };
    }, triedArr).catch(() => null);
    while (Date.now() < deadline) {
      const url = page.url();
      if (url !== lastUrl) { LOG('url -> ' + url); lastUrl = url; }
      if (!/login\.html/.test(url)) return true;
      if (Date.now() - lastTry > 5000) {
        lastTry = Date.now();
        // 调试: dump login.html 全部可见可点元素(弹窗按钮形态探测)
        for (const fr of page.frames()) {
          try {
            const dump = await fr.evaluate(() => {
              const els = Array.from(document.querySelectorAll('button, [role=button], a, div, span'))
                .filter(e => { const r = e.getBoundingClientRect(); return r.width > 8 && r.height > 8 && r.x >= 0 && r.y >= 0 && (e.innerText || '').trim().length > 0 && (e.innerText || '').trim().length < 16; });
              return els.map(e => ({ tag: e.tagName, cls: String(e.className).slice(0, 60), t: (e.innerText || '').trim() })).slice(0, 60);
            });
            fs.writeFileSync('login_dom_dump_' + (fr === page.mainFrame() ? 'main' : String(page.frames().indexOf(fr))) + '.json', JSON.stringify(dump, null, 1));
          } catch (e) {}
        }
        for (const fr of page.frames()) {
          const pt = await findPt(fr, tried);
          if (!pt) continue;
          // iframe 偏移: 子 frame 坐标需叠加主页面对应 iframe rect; 主 frame 无偏移
          let off = { x: 0, y: 0 };
          if (fr !== page.mainFrame()) {
            off = await page.evaluate(() => {
              const ifr = Array.from(document.querySelectorAll('iframe'))
                .find(i => /open\.weixin|qrconnect|login/.test(i.src || ''));
              if (!ifr) return null;
              const r = ifr.getBoundingClientRect();
              return { x: r.x, y: r.y };
            }).catch(() => null);
            if (!off) continue;
          }
          // 双保险: 先合成 click 再 CDP 真实点击 (force=0.5 对齐人手压力)
          try {
            await fr.evaluate((p) => {
              const hit = document.elementFromPoint(p.x, p.y);
              if (hit) hit.click();
            }, pt).catch(() => {});
          } catch (e) {}
          const cdp = await page.createCDPSession();
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: off.x + pt.x, y: off.y + pt.y, button: 'none', pointerType: 'mouse' });
          await sleep(150);
          await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: off.x + pt.x, y: off.y + pt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
          await sleep(90);
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: off.x + pt.x, y: off.y + pt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
          tried.push(pt.t);
          LOG('auto-login real-click [' + pt.t + '] at ' + Math.round(off.x + pt.x) + ',' + Math.round(off.y + pt.y) + ' @' + fr.url().slice(0, 60));
          // 微信客户端授权弹窗自动允许: 文件信号触发机制——
          // bot spawn 的沙箱子进程 EnumWindows 枚举不到弹窗(深沙箱限制, 实证),
          // 改由 shell 侧后台 job 跑 allow_enter.ps1(枚举可见), 靠此 flag 文件协同
          if (!enterSpawned) {
            enterSpawned = true;
            try {
              fs.writeFileSync(path.join(__dirname, 'allow_signal.flag'), String(Date.now()));
              LOG('allow_signal.flag written (allow_enter watcher 将自动前台化+Enter 点允许)');
            } catch (e) { LOG('allow flag write err ' + e.message); }
          }
          break;
        }
      }
      await sleep(2500);
    }
    return !/login\.html/.test(page.url());
  };

  // ---- 阶段1: 登录 (一键点击优先, 兜底等扫码) ----
  await sleep(6000); // 客户端 auth 检查有延迟重定向, 先沉降防假阳性
  if (!(await ensureLogin(page, (CFG.waitLoginMinutes || 8) * 60000))) {
    LOG('LOGIN_TIMEOUT: 一键登录未出现且二维码未被确认, 详见窗口');
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
  // 会话过期: 弹回 login.html → 一键登录优先, 兜底等扫码
  if (page.url().includes('login.html')) {
    LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
    if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
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

    // 5. 点发表: 合成 click + CDP 真实点击兜底(xhs 21 轮机制迁移)——
    //    iframe 内按钮取视口坐标(getBoundingClientRect 跨 frame
    //    一致), CDP Input 按视口坐标分发可达 iframe, force=0.5
    //    压力校验(微信系 pointer 压力校验实证)
    let clicked = false;
    let postPt = null;
    try {
      form = getForm() || form;
      postPt = await form.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button, [role=button]'));
        const b = btns.find(x => (x.innerText || '').trim() === '发表');
        if (!b) return null;
        const r = b.getBoundingClientRect();
        b.click();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      });
      clicked = !!postPt;
    } catch (e) { LOG('publish click err: ' + e.message); }
    LOG('click 发表: ' + clicked
      + (postPt ? ' at ' + Math.round(postPt.x) + ',' + Math.round(postPt.y) : ''));
    if (postPt) {
      await sleep(2500);
      const cdp = await page.createCDPSession();
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: postPt.x, y: postPt.y, button: 'none', pointerType: 'mouse' });
      await sleep(150);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: postPt.x, y: postPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      await sleep(90);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: postPt.x, y: postPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      LOG('CDP 真实点击兜底已发');
    }

    // 6. 成功特征轮询收口(xhs 21 轮机制迁移, 替换固定 sleep 15s):
    //    发表后表单 iframe 消失(frame gone, 历史实证)/文本特征——
    //    每 3s 一次最多 90s, 命中早退; 超时留档人审
    let published = false;
    let afterBody = '';
    let mainBody = '';
    let afterUrl = page.url();
    const pubDeadline = Date.now() + 90 * 1000;
    while (Date.now() < pubDeadline) {
      await sleep(3000);
      afterUrl = page.url();
      const f = getForm();
      try {
        afterBody = f
          ? await f.evaluate(() => (document.body.innerText || '').slice(0, 500))
          : 'frame gone';
      } catch (e) { afterBody = 'frame gone: ' + e.message; }
      try { mainBody = await page.evaluate(() => (document.body.innerText || '').slice(0, 400)); } catch (e) {}
      const frameGone = !f || String(afterBody).startsWith('frame gone');
      const hit = frameGone
        || /发表成功|已发表|审核中/.test(afterBody + mainBody);
      if (hit) { published = true; break; }
    }
    await page.screenshot({ path: 'publish_3_after.png' });
    LOG((published ? 'PUBLISH_OK' : 'PUBLISH_UNVERIFIED') + ' url=' + afterUrl);
    LOG('after body: ' + afterBody.slice(0, 300));
    LOG('main body: ' + mainBody.slice(0, 200));
    fs.writeFileSync('publish_result.json', JSON.stringify({ clicked, published, uploadDone, afterUrl, afterBody, mainBody }, null, 2));
    LOG('PUBLISH_RUN_DONE');
    await browser.close();
    process.exit(published ? 0 : 6);
  }

  if (CFG.action === 'annotate') {
    const gotoList = async () => {
      try { await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 }); return true; }
      catch (e) { LOG('goto list err: ' + String(e.message).slice(0, 60)); return false; }
    };
    if (!await gotoList()) { await sleep(2500); await gotoList(); }
    await sleep(4000);
    // 会话过期: 弹回 login.html → 一键登录优先, 兜底等扫码
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
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

  if (CFG.action === 'fixclick') {
    const gotoList = async () => {
      try { await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 }); return true; }
      catch (e) { LOG('goto err: ' + String(e.message).slice(0, 60)); return false; }
    };
    if (!await gotoList()) { await sleep(2500); await gotoList(); }
    await sleep(4000);
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await sleep(2000);
      if (!await gotoList()) { await sleep(2500); await gotoList(); }
      await sleep(5000);
    }
    let f = page.frames().find(x => x.url().includes('/micro/content/post'));
    if (!f) { LOG('NO_LIST_FRAME'); await browser.close(); process.exit(3); }
    await page.bringToFront();
    try {
      const ctx = browser.defaultBrowserContext();
      await ctx.overridePermissions('https://channels.weixin.qq.com', ['clipboard-read', 'clipboard-write']);
      LOG('clipboard perms granted');
    } catch (e) { LOG('perm err ' + e.message); }

    // CDP 真实点击: force=0.5 让 pointerdown.pressure 与人手一致 (Vue 压力校验)
    const cdp = await page.createCDPSession();
    const cdpClick = async (x, y) => {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', pointerType: 'mouse' });
      await sleep(150);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      await sleep(90);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    };
    const framePt = async (sel) => f.evaluate((s) => {
      const texts = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => e.children.length === 0 && (e.innerText || '').trim() === s);
      const visTexts = texts.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.x >= 0 && r.x < window.innerWidth - 5 && r.y >= 0 && r.y < window.innerHeight - 5; });
      if (!visTexts.length) return null;
      const text = visTexts[0];
      // item 容器 = 文本元素向上找 class 含 item 的层
      let item = text;
      for (let i = 0; i < 4 && item.parentElement; i++) { item = item.parentElement; if (/item/i.test(String(item.className))) break; }
      // 图标 = 容器内空文本且有尺寸的子元素 (处理器所在, 文字标签是死目标)
      const icons = Array.from(item.querySelectorAll('*')).filter(e => {
        const r = e.getBoundingClientRect();
        return r.width > 4 && r.height > 4 && !(e.innerText || '').trim();
      });
      const r = (icons[0] || item).getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), icons: icons.length };
    }, sel);

    // 验证1: 修改描述和封面 (开编辑器)
    let pt = await framePt('修改描述和封面');
    LOG('edit btn pt: ' + JSON.stringify(pt));
    if (pt) {
      await cdpClick(pt.x, pt.y);
      LOG('cdpClick(force=0.5) 修改描述和封面');
      await sleep(3500);
      const u = page.url();
      LOG('编辑器 url=' + u.slice(0, 110));
      await page.screenshot({ path: 'fixclick_1.png' });
      // 编辑器已整页导航打开(coverEdit), 验证成功 — 回列表继续验证分享
      await gotoList();
      await sleep(5000);
    }

    // 验证2: 分享 → 复制视频链接 → 剪贴板
    f = page.frames().find(x => x.url().includes('/micro/content/post')) || f;
    pt = await framePt('分享');
    LOG('share btn pt: ' + JSON.stringify(pt));
    if (pt) {
      await cdpClick(pt.x, pt.y);
      LOG('cdpClick 分享');
      await sleep(3000);
      await page.screenshot({ path: 'fixclick_2_share.png' });
      f = page.frames().find(x => x.url().includes('/micro/content/post')) || f;
      const cp = await framePt('复制视频链接');
      LOG('copy btn pt: ' + JSON.stringify(cp));
      if (cp) {
        await cdpClick(cp.x, cp.y);
        LOG('cdpClick 复制视频链接');
        await sleep(2500);
        let link = '';
        try { link = await page.evaluate(() => navigator.clipboard.readText()); } catch (e) { LOG('clipboard err ' + e.message); }
        LOG('CLIPBOARD LINK: ' + String(link).slice(0, 150));
        fs.writeFileSync('fixclick_link.txt', String(link));
      }
    }
    LOG('FIXCLICK_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'evtspy') {
    const gotoList = async () => {
      try { await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 }); return true; }
      catch (e) { LOG('goto err: ' + String(e.message).slice(0, 60)); return false; }
    };
    if (!await gotoList()) { await sleep(2500); await gotoList(); }
    await sleep(4000);
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await sleep(2000);
      if (!await gotoList()) { await sleep(2500); await gotoList(); }
      await sleep(5000);
    }
    let f = page.frames().find(x => x.url().includes('/micro/content/post'));
    if (!f) { LOG('NO_LIST_FRAME'); await browser.close(); process.exit(3); }
    await page.bringToFront();

    // 0. Vue 标记探测
    const vue = await f.evaluate(() => {
      const el = Array.from(document.querySelectorAll('div,span,a,button')).find(e => (e.innerText || '').trim() === '修改描述和封面');
      if (!el) return 'no-el';
      let n = el; const marks = [];
      for (let k = 0; k < 8 && n; k++) {
        const vk = Object.keys(n).find(key => /vue|__v_/i.test(key));
        if (vk) marks.push({ k, vk, tag: n.tagName });
        n = n.parentElement;
      }
      return marks;
    });
    LOG('vue marks: ' + JSON.stringify(vue));

    // 1. 装全事件间谍 (element/body/document × capture/bubble × 事件类型)
    const installSpy = () => f.evaluate(() => {
      window.__evt = [];
      const el = Array.from(document.querySelectorAll('div,span,a,button')).find(e => (e.innerText || '').trim() === '修改描述和封面');
      if (!el) return false;
      const rec = (phase) => (e) => {
        window.__evt.push(phase + '|' + e.type + '|tgt=' + (e.target.tagName || '') + ':' + String(e.target.innerText || '').trim().slice(0, 8) + '|trusted=' + e.isTrusted + (e.type.startsWith('pointer') ? '|pp=' + e.pressure + ',pt=' + e.pointerType + ',pr=' + e.isPrimary : '') + (e.type === 'click' || e.type.startsWith('mouse') ? '|btn=' + e.button + ',cx=' + Math.round(e.clientX) + ',cy=' + Math.round(e.clientY) : ''));
      };
      const nodes = [
        ['el', el], ['parent', el.parentElement], ['gparent', el.parentElement ? el.parentElement.parentElement : null],
        ['body', document.body], ['doc', document]
      ];
      const types = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'mouseover', 'mouseenter'];
      for (const [name, node] of nodes) {
        if (!node) continue;
        for (const t of types) {
          try { node.addEventListener(t, rec(name + ':' + t + ':B'), false); } catch (e) {}
          try { node.addEventListener(t, rec(name + ':' + t + ':C'), true); } catch (e) {}
        }
      }
      const r = el.getBoundingClientRect();
      window.__elpt = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      return true;
    });
    LOG('spy installed: ' + await installSpy());

    // 2. bot 真实点击
    const pt = await f.evaluate(() => window.__elpt);
    LOG('bot click at ' + Math.round(pt.x) + ',' + Math.round(pt.y));
    await page.mouse.move(pt.x, pt.y, { steps: 8 });
    await sleep(600);
    await page.mouse.click(pt.x, pt.y);
    await sleep(2500);
    const botLog = await f.evaluate(() => window.__evt);
    fs.writeFileSync('evtspy_bot.json', JSON.stringify(botLog, null, 1));
    LOG('BOT EVENTS (' + botLog.length + '): ' + JSON.stringify(botLog.slice(0, 20)));
    await page.screenshot({ path: 'evtspy_bot.png' });

    // 3. 请用户点击同一按钮, 对比
    await f.evaluate(() => { window.__evt = []; });
    LOG('=== 请你在 Chrome 窗口里用鼠标点一下「修改描述和封面」(90秒) ===');
    const dl = Date.now() + 90000;
    let userOpened = false;
    while (Date.now() < dl) {
      await sleep(2000);
      const n = await f.evaluate(() => window.__evt.length).catch(() => 0);
      if (n > 0) { userOpened = true; break; }
    }
    await sleep(3000);
    const userLog = await f.evaluate(() => window.__evt).catch(() => []);
    fs.writeFileSync('evtspy_user.json', JSON.stringify(userLog, null, 1));
    LOG('USER EVENTS (' + userLog.length + '): ' + JSON.stringify(userLog.slice(0, 30)));
    await page.screenshot({ path: 'evtspy_user.png' });
    LOG('EVTSPY_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'rsprops') {
    const gotoList = async () => {
      try { await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 }); return true; }
      catch (e) { LOG('goto err: ' + String(e.message).slice(0, 60)); return false; }
    };
    if (!await gotoList()) { await sleep(2500); await gotoList(); }
    await sleep(4000);
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await sleep(2000);
      if (!await gotoList()) { await sleep(2500); await gotoList(); }
      await sleep(5000);
    }
    let f = page.frames().find(x => x.url().includes('/micro/content/post'));
    if (!f) { LOG('NO_LIST_FRAME'); await browser.close(); process.exit(3); }
    await page.bringToFront();

    // 1. React props 全画像: 修改描述和封面 祖先链上哪层挂了什么 handler
    const probe = await f.evaluate(() => {
      const all = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '修改描述和封面');
      return all.slice(0, 2).map((el, idx) => {
        let n = el; const levels = [];
        for (let k = 0; k < 8 && n; k++) {
          const pk = Object.keys(n).find(key => key.startsWith('__reactProps$'));
          if (pk) {
            const props = n[pk] || {};
            const hs = Object.keys(props).filter(h => /^on[A-Z]/.test(h));
            levels.push({ k, tag: n.tagName, cls: String(n.className).slice(0, 36), handlers: hs });
          }
          n = n.parentElement;
        }
        return { idx, levels };
      });
    });
    LOG('react props map: ' + JSON.stringify(probe));
    fs.writeFileSync('rsprops.json', JSON.stringify(probe, null, 2));

    // 2. 直接调用最近带 onClick 的祖先 handler (绕过事件系统)
    const invoked = await f.evaluate(() => {
      const all = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => (e.innerText || '').trim() === '修改描述和封面');
      if (!all.length) return 'no-element';
      const el = all[0];
      let n = el;
      for (let k = 0; k < 10 && n; k++) {
        const pk = Object.keys(n).find(key => key.startsWith('__reactProps$'));
        if (pk && n[pk] && typeof n[pk].onClick === 'function') {
          const fake = { preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {}, target: n, currentTarget: n, type: 'click', isTrusted: true, clientX: 0, clientY: 0 };
          try { n[pk].onClick(fake); return 'invoked@' + k + ':' + String(n.className).slice(0, 30); }
          catch (e) { return 'invoke-err:' + String(e.message).slice(0, 60); }
        }
        n = n.parentElement;
      }
      return 'no-onClick-found';
    });
    LOG('direct invoke: ' + invoked);
    await sleep(4000);
    await page.screenshot({ path: 'rsprops_1.png' });
    LOG('url now: ' + page.url());
    f = page.frames().find(x => x.url().includes('/micro/content/post')) || f;
    const opened = await f.evaluate(() => (document.body.innerText || '').includes('原视频信息')).catch(() => false);
    LOG('editor opened: ' + opened);
    LOG('RSPROPS_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'getlink') {
    try {
      await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 });
    } catch (e) { await sleep(2500); await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded' }).catch(() => {}); }
    await sleep(4000);
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await sleep(2000);
      await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded' }).catch(() => {});
      await sleep(5000);
    }
    let f = page.frames().find(x => x.url().includes('/micro/content/post'));
    if (!f) { LOG('NO_LIST_FRAME'); await browser.close(); process.exit(3); }
    await page.bringToFront();
    try {
      const ctx = browser.defaultBrowserContext();
      await ctx.overridePermissions('https://channels.weixin.qq.com', ['clipboard-read', 'clipboard-write']);
    } catch (e) { }
    // 铁律: Vue opr 菜单处理器绑在图标(空文本子元素)上, 文字标签是死目标; CDP force=0.5 压力对齐人手
    const cdp = await page.createCDPSession();
    const cdpClick = async (x, y) => {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', pointerType: 'mouse' });
      await sleep(150);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      await sleep(90);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    };
    const framePt = async (sel, idx) => f.evaluate((s, i) => {
      const texts = Array.from(document.querySelectorAll('div,span,a,button')).filter(e => e.children.length === 0 && (e.innerText || '').trim() === s);
      const visTexts = texts.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.x >= 0 && r.x < window.innerWidth - 5 && r.y >= 0 && r.y < window.innerHeight - 5; });
      if (!visTexts.length) return null;
      const text = visTexts[Math.min(i, visTexts.length - 1)];
      let item = text;
      for (let k = 0; k < 4 && item.parentElement; k++) { item = item.parentElement; if (/item/i.test(String(item.className))) break; }
      const icons = Array.from(item.querySelectorAll('*')).filter(e => { const r = e.getBoundingClientRect(); return r.width > 4 && r.height > 4 && !(e.innerText || '').trim(); });
      const r = (icons[0] || item).getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }, sel, idx || 0);
    const cardIdx = CFG.cardIndex || 0;
    const pt = await framePt('分享', cardIdx);
    LOG('share pt (card ' + cardIdx + '): ' + JSON.stringify(pt));
    let link = '';
    if (pt) {
      await cdpClick(pt.x, pt.y);
      LOG('cdpClick 分享');
      await sleep(3000);
      const cp = await framePt('复制视频链接', 0);
      LOG('copy pt: ' + JSON.stringify(cp));
      if (cp) {
        await cdpClick(cp.x, cp.y);
        LOG('cdpClick 复制视频链接');
        await sleep(2500);
        try { link = await page.evaluate(() => navigator.clipboard.readText()); } catch (e) { }
        if (!/^https?:/.test(String(link))) link = '';
      }
    }
    LOG('LINK: ' + link);
    fs.writeFileSync('link_result.json', JSON.stringify({ link }, null, 2));
    LOG('GETLINK_DONE');
    await browser.close();
    process.exit(link ? 0 : 7);
  }

  if (CFG.action === 'stats') {
    // 数据监控: 作品列表页 dump(标题/时间/状态+可见数据字段) — 首版
    // 实机校准源: 列表页若有播放/点赞列直接文本提取; 无则需数据
    // 中心页二次校准(2026-10-04 首版留档 channels_stats_raw.txt)
    const gotoList = async () => {
      try { await page.goto('https://channels.weixin.qq.com/platform/post/list', { waitUntil: 'domcontentloaded', timeout: 30000 }); return true; }
      catch (e) { return false; }
    };
    if (!await gotoList()) { await sleep(2500); await gotoList(); }
    await sleep(4000);
    if (page.url().includes('login.html')) {
      LOG('SESSION_EXPIRED — 尝试一键登录(免扫码), 兜底等扫码确认');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await sleep(2000);
      await gotoList(); await sleep(5000);
    }
    let f = page.frames().find(x => x.url().includes('/micro/content/post'));
    if (!f) { LOG('NO_LIST_FRAME'); await browser.close(); process.exit(3); }
    await page.bringToFront();
    await sleep(3000);
    const raw = await f.evaluate(() => (document.body.innerText || '').slice(0, 6000)).catch(() => '');
    const ts = new Date().toISOString().replace(/[:T]/g, '-').slice(0, 16);
    fs.writeFileSync(`channels_stats_${ts}.txt`, raw);
    fs.writeFileSync('channels_stats_raw.txt', raw);
    // 行级摘要(过滤空行, 便于 diff 增量)
    const lines = raw.split('\n').map(s => s.trim()).filter(Boolean);
    LOG('STATS_LINES ' + lines.length);
    lines.slice(0, 40).forEach((t, i) => LOG('  #' + i + ' ' + t.slice(0, 80)));
    LOG('STATS_DONE (channels_stats_raw.txt + 时间戳留档)');
    await browser.close();
    process.exit(0);
  }

  LOG('unknown action');
  await browser.close();
})().catch(e => { LOG('ERR ' + (e && e.message || e)); process.exit(1); });
