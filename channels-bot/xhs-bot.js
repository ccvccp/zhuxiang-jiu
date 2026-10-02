// 小红书视频笔记 RPA 发布 bot (2026-09-30 立项, 73号平台扩展)
// 架构复用 douyin-bot.js 已实证范式: CDP pipe 传输 Chrome(无 TCP
// 端口, 剥 --enable-automation 拟真) + 独立 xhs-profile 持久登录态。
// 与 channels/douyin 通道完全隔离(独立 profile/日志/产物前缀)。
//
// ⚠️ 发布流联调实证(2026-09-30, 20 轮全自动闭环定稿):
//   · probe / manage / draft / publish 四 action 实证可用;
//     publish 全自动闭环已打通(上传+填写+转码等待+CDP 穿透
//     定位+双击+成功检测, 无人介入)
//   · 操作栏(暂存离开/发布)在 closed shadow DOM——DOM 遍历
//     不可达, 走 CDP getFlattenedDocument(pierce) 穿透定位
//   · 转码窗口期: 视频上传完成≠可发布, 「检测为高清视频」
//     绿标=转码完成特征(前置等待), 未完成时点发布会被静默
//     转存草稿(draft 流为草稿恢复路径)
//   · 「定时发布」是开关(更多设置区, 1h~14d), 默认 OFF——
//     点「发布」= 立即发布, 全自动无需碰定时开关
//   · 发布成功特征: URL 带 published=true / 跳 manage
//   · manage: 旧 /manage 与 /new/manage 均 404——须首页侧边栏
//     「笔记管理」菜单导航进入(点击式)
//
// 用法: node xhs-bot.js <config.json>
// config: { action: "probe"|"publish"|"manage"|"draft",
//           mp4, title, desc, waitLoginMinutes, manualWaitMinutes }
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
// 笔记管理页(联调第 19 轮实证: 旧 /manage 已 404, 改版规律 /new/*)
const XHS_MANAGE = 'https://creator.xiaohongshu.com/new/manage';

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
    // 联调第 19 轮实证: 旧 /manage 与 /new/manage 均 404——改版后
    // 须从首页侧边栏「笔记管理」菜单导航进入(点击式, 非 URL 直达)
    const clickManageMenu = () => page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('a, [role=menuitem], li, span, div'));
      const hit = els.find(e => (e.innerText || '').trim() === '笔记管理');
      if (hit) hit.click();
      return !!hit;
    }).catch((e) => { LOG('click 笔记管理 err: ' + e.message); return false; });
    if (!(await clickManageMenu())) {
      LOG('MANAGE_MENU_NOT_FOUND — dump 留档');
      await dumpFrames('manage_nomenu');
    }
    await sleep(8000);
    if (loginPageLike(page.url(), '')) {
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await clickManageMenu();
      await sleep(8000);
    }
    await page.screenshot({ path: 'xhs_manage_1.png' });
    const items = await page.evaluate(() =>
      (document.body.innerText || '').slice(0, 2000)).catch(() => '');
    fs.writeFileSync('xhs_manage_items.txt', items);
    LOG('MANAGE_DONE url=' + page.url().slice(0, 100) + ' (xhs_manage_items.txt)');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'drafts_clean') {
    // 草稿箱清理(2026-09-30 21 轮联调, 三模式):
    //   · cleanMode:'list'(默认) dump 清单; 加 manualWaitMinutes
    //     则停住人工删除(备用路径)
    //   · cleanMode:'delete' 已修复(第 21 轮根因实证): UI 删除
    //     handler 被 xhs 安全盾拦截(CDP 点按钮中心触发
    //     shield/webprofile 风控调用后 handler 链中止, 44 轮
    //     坐标点击 + dispatchEvent 全序列均无效)——改走
    //     IndexedDB draft-database-v1 直接删记录绕过 UI 层,
    //     一次删除列表归零; match 空=全删, 非空=按标题过滤
    //   · cleanMode:'diag' 诊断模式(listeners/Network/存储
    //     结构三路取证——21 轮根因定位即用此模式)
    //   · 顶部「草稿箱(N)」入口计数是快照不实时刷新, 以弹窗内
    //     列表 DOM 为准
    await page.goto(XHS_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(5000);
    if (!(await ensureLogin(page, 5 * 60000))) {
      LOG('RELOGIN_TIMEOUT');
      await browser.close();
      process.exit(2);
    }
    // 点「草稿箱(N)」入口(发布页顶部, 与红「发布笔记」同条线)——
    // 取文本匹配的最深叶子点击(外层容器不触发导航, 12:28 轮实证)
    let opened = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div, button, span, a'))
        .filter((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
      const el = els[els.length - 1];
      if (el) { el.click(); return true; }
      return false;
    }).catch(() => false);
    LOG('drafts entry clicked: ' + opened);
    // 等草稿列表特征(body 含「保存于」), 未出现则重试点击(最多 5 轮)
    let listReady = false;
    for (let i = 0; i < 5 && !listReady; i++) {
      await sleep(3000);
      const bodyHas = await page.evaluate(() =>
        /保存于/.test((document.body.innerText || '').slice(0, 3000))
      ).catch(() => false);
      if (bodyHas) { listReady = true; break; }
      opened = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('div, button, span, a'))
          .filter((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
        const el = els[els.length - 1];
        if (el) { el.click(); return true; }
        return false;
      }).catch(() => false);
      LOG('drafts list not ready, re-click: ' + opened);
    }
    LOG('drafts list ready: ' + listReady);
    await page.screenshot({ path: 'xhs_drafts_1.png' });
    // dump 草稿列表条目: 标题 + 各按钮形态(探测删除入口)
    const list = await page.evaluate(() => {
      const items = [];
      const cards = Array.from(document.querySelectorAll('*'))
        .filter((e) => {
          const r = e.getBoundingClientRect();
          return r.width > 200 && r.height > 60 && r.x > 250 && r.y > 60;
        });
      // 以可见文本含「编辑」的叶子向上取卡片级容器
      for (const c of cards) {
        const txt = (c.innerText || '').trim();
        if (!txt || txt.length > 300) continue;
        if (/编辑/.test(txt)) {
          items.push({
            cls: String(c.className).slice(0, 50),
            text: txt.slice(0, 120).replace(/\n/g, ' | '),
            buttons: Array.from(c.querySelectorAll('button, [role=button], span, div'))
              .map((b) => (b.innerText || '').trim().slice(0, 10))
              .filter(Boolean).slice(0, 10),
          });
        }
      }
      return items.slice(0, 40);
    }).catch((e) => [{ err: String(e) }]);
    fs.writeFileSync('xhs_drafts_list.json', JSON.stringify(list, null, 2));
    LOG('DRAFTS_LISTED ' + list.length + ' 条 (xhs_drafts_list.json / xhs_drafts_1.png)');
    if (CFG.manualWaitMinutes) {
      // 人工观察模式: 停住等人工在草稿箱弹窗手动操作(删除点击
      // 行为实证), 每 5s 轮询计数留痕
      LOG('=== 请人工在 Chrome 草稿箱弹窗手动点「删除」(观察行为) ===');
      const dl = Date.now() + CFG.manualWaitMinutes * 60000;
      while (Date.now() < dl) {
        await sleep(5000);
        const cnt = await page.evaluate(() => {
          const el = Array.from(document.querySelectorAll('*'))
            .find((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
          return el ? el.innerText.trim() : '弹窗内(n/a)';
        }).catch(() => 'n/a');
        LOG('drafts count: ' + cnt);
      }
      await page.screenshot({ path: 'xhs_drafts_manual_after.png' });
      await browser.close();
      process.exit(0);
    }
    if (CFG.cleanMode === 'diag') {
      // 诊断模式: 探索 CDP 删除钮失效根因——三路证据一次收齐:
      //   ① DOMDebugger.getEventListeners 列删除钮 SPAN 父链各层
      //      绑定的 handler 事件类型(handler 挂在哪层/click 还是
      //      pointerdown?)——CDP dispatchMouseEvent 若只触发部分
      //      序列而 handler 绑了未触发的事件类型即失效
      //   ② Network 监听: CDP 点击后是否发出删除 XHR(有请求=
      //      handler 触发了、UI 层问题; 无请求=handler 未触发)
      //   ③ 点击前后弹窗 DOM 状态对照
      const cdp2 = await page.createCDPSession();
      await cdp2.send('Runtime.enable').catch(() => {});
      await cdp2.send('Network.enable').catch(() => {});
      const netLog = [];
      cdp2.on('Network.requestWillBeSent', (r) => {
        netLog.push({
          url: String(r.request.url).slice(0, 140),
          method: r.request.method, at: Date.now(),
        });
      });
      // 定位删除钮 SPAN + 父链(存 window.__chain 供 objectId 取用)
      const located = await cdp2.send('Runtime.evaluate', {
        expression: `(() => {
          const cards = Array.from(document.querySelectorAll('*'))
            .filter(e => /draft-item/.test(String(e.className)));
          const card = cards[0];
          const dels = card ? Array.from(card.querySelectorAll('*'))
            .filter(e => (e.innerText||'').trim() === '删除'
              && e.children.length === 0) : [];
          const span = dels[dels.length-1];
          if (!span) return false;
          window.__chain = [];
          let cur = span;
          for (let i = 0; i < 7 && cur; i++) {
            window.__chain.push(cur);
            cur = cur.parentElement;
          }
          return true;
        })()`,
        returnByValue: true,
      }).catch(() => null);
      LOG('diag located: ' + JSON.stringify(located && located.result));
      // ① 各层 listeners
      const diag = [];
      for (let i = 0; i < 7; i++) {
        try {
          const obj = await cdp2.send('Runtime.evaluate', {
            expression: `window.__chain && window.__chain[${i}]`,
          });
          if (!obj.result || !obj.result.objectId) break;
          const info = await cdp2.send('Runtime.evaluate', {
            expression: `window.__chain[${i}].tagName + ' | '
              + String(window.__chain[${i}].className).slice(0, 50)`,
            returnByValue: true,
          });
          const ls = await cdp2.send('DOMDebugger.getEventListeners', {
            objectId: obj.result.objectId,
          }).catch(() => ({ listeners: [] }));
          diag.push({
            el: info.result && info.result.value,
            listeners: (ls.listeners || []).map((l) => l.type),
          });
        } catch (e) { diag.push({ err: String(e).slice(0, 80) }); break; }
      }
      // document/window 层(事件委托可能绑根)
      for (const expr of ['document', 'window']) {
        try {
          const obj = await cdp2.send('Runtime.evaluate', { expression: expr });
          if (!obj.result || !obj.result.objectId) continue;
          const ls = await cdp2.send('DOMDebugger.getEventListeners', {
            objectId: obj.result.objectId,
          }).catch(() => ({ listeners: [] }));
          diag.push({
            el: expr,
            listeners: (ls.listeners || []).map((l) => l.type),
          });
        } catch (e) {}
      }
      fs.writeFileSync('xhs_del_diag.json', JSON.stringify(diag, null, 2));
      LOG('LISTENERS_DIAG: ' + JSON.stringify(diag).slice(0, 400));
      // ② CDP 点击 + Network 观察
      const pt = await page.evaluate(async () => {
        const span = window.__chain && window.__chain[0];
        if (!span) return null;
        span.scrollIntoView({ block: 'center' });
        await new Promise((r) => setTimeout(r, 300));
        const r = span.getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      }).catch(() => null);
      LOG('diag click at ' + JSON.stringify(pt) + ' — net=' + netLog.length);
      if (pt) {
        await cdp2.send('Input.dispatchMouseEvent',
          { type: 'mouseMoved', x: pt.x, y: pt.y, button: 'none', pointerType: 'mouse' });
        await sleep(150);
        await cdp2.send('Input.dispatchMouseEvent',
          { type: 'mousePressed', x: pt.x, y: pt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
        await sleep(100);
        await cdp2.send('Input.dispatchMouseEvent',
          { type: 'mouseReleased', x: pt.x, y: pt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      }
      await sleep(2500);
      fs.writeFileSync('xhs_del_net.json', JSON.stringify(netLog, null, 2));
      LOG('NET_AFTER_CLICK: ' + netLog.length + ' 请求 (xhs_del_net.json)');
      await page.screenshot({ path: 'xhs_del_diag_after.png' });
      const cnt = await page.evaluate(() => {
        const el = Array.from(document.querySelectorAll('*'))
          .find((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
        return el ? el.innerText.trim() : 'n/a';
      }).catch(() => 'n/a');
      LOG('count after diag click: ' + cnt);
      // 对照实验 1: DIV.btn 自身 rect + 中心 elementFromPoint
      // (SPAN 文本中心命中≠按钮中心命中, 排查命中错位)
      const btnInfo = await page.evaluate(() => {
        const btn = window.__chain && window.__chain[1]; // DIV.btn
        if (!btn) return null;
        const r = btn.getBoundingClientRect();
        const cx = Math.round(r.x + r.width / 2);
        const cy = Math.round(r.y + r.height / 2);
        const hit = document.elementFromPoint(cx, cy);
        return {
          rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
          center: { x: cx, y: cy },
          hitAtCenter: hit ? {
            tag: hit.tagName,
            cls: String(hit.className).slice(0, 40),
            inBtn: btn.contains(hit),
          } : null,
        };
      }).catch(() => null);
      LOG('BTN_INFO: ' + JSON.stringify(btnInfo));
      // 对照实验 2: CDP 点 DIV.btn 中心
      if (btnInfo && btnInfo.center) {
        const bc = btnInfo.center;
        await cdp2.send('Input.dispatchMouseEvent',
          { type: 'mouseMoved', x: bc.x, y: bc.y, button: 'none', pointerType: 'mouse' });
        await sleep(150);
        await cdp2.send('Input.dispatchMouseEvent',
          { type: 'mousePressed', x: bc.x, y: bc.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
        await sleep(100);
        await cdp2.send('Input.dispatchMouseEvent',
          { type: 'mouseReleased', x: bc.x, y: bc.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
        await sleep(2500);
        LOG('NET_AFTER_BTN_CLICK: ' + netLog.length);
        const cnt2 = await page.evaluate(() => {
          const el = Array.from(document.querySelectorAll('*'))
            .find((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
          return el ? el.innerText.trim() : 'n/a';
        }).catch(() => 'n/a');
        LOG('count after btn-center click: ' + cnt2);
      }
      // 对照实验 3: 页面内 dispatchEvent 完整 Pointer/Mouse 序列
      // (el.click() 只发孤 click; 组件库常需完整序列)
      const dispatchResult = await page.evaluate(() => {
        const btn = window.__chain && window.__chain[1];
        if (!btn) return null;
        const r = btn.getBoundingClientRect();
        const cx = r.x + r.width / 2;
        const cy = r.y + r.height / 2;
        const opts = { bubbles: true, cancelable: true, composed: true, clientX: cx, clientY: cy, button: 0, buttons: 1, pointerId: 1, isPrimary: true, pressure: 0.5, pointerType: 'mouse' };
        try {
          btn.dispatchEvent(new PointerEvent('pointerover', opts));
          btn.dispatchEvent(new PointerEvent('pointerenter', { ...opts, bubbles: false }));
          btn.dispatchEvent(new PointerEvent('pointerdown', opts));
          btn.dispatchEvent(new MouseEvent('mousedown', opts));
          btn.dispatchEvent(new PointerEvent('pointerup', opts));
          btn.dispatchEvent(new MouseEvent('mouseup', opts));
          btn.dispatchEvent(new MouseEvent('click', opts));
          return 'dispatched 7 events';
        } catch (e) { return 'err: ' + String(e).slice(0, 60); }
      }).catch(() => null);
      LOG('DISPATCH_SEQ: ' + dispatchResult);
      await sleep(2500);
      LOG('NET_AFTER_SEQ: ' + netLog.length + ' (总数, 含前置)');
      const reqUrls = netLog.map((n) => n.method + ' ' + n.url.slice(0, 80));
      fs.writeFileSync('xhs_del_net.json',
        JSON.stringify(reqUrls, null, 2));
      const cnt3 = await page.evaluate(() => {
        const el = Array.from(document.querySelectorAll('*'))
          .find((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
        return el ? el.innerText.trim() : 'n/a';
      }).catch(() => 'n/a');
      LOG('count after dispatch-seq: ' + cnt3);
      // 对照实验 4: 草稿本地存储探测(弹窗明示"草稿仅保存在当前
      // 浏览器本地"——删除可能是纯本地 IndexedDB/localStorage 操作,
      // UI handler 被安全盾拦截时可直接操作存储绕过)
      const storageInfo = await page.evaluate(async () => {
        const out = { idb: [], ls: [] };
        try {
          const dbs = await indexedDB.databases();
          for (const db of (dbs || []).slice(0, 10)) {
            out.idb.push({ name: db.name, version: db.version });
          }
        } catch (e) { out.idbErr = String(e).slice(0, 60); }
        try {
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            const v = String(localStorage.getItem(k) || '');
            out.ls.push({
              key: String(k).slice(0, 60),
              size: v.length,
              head: v.slice(0, 80),
            });
          }
        } catch (e) { out.lsErr = String(e).slice(0, 60); }
        return out;
      }).catch((e) => ({ err: String(e) }));
      fs.writeFileSync('xhs_del_storage.json',
        JSON.stringify(storageInfo, null, 2));
      LOG('STORAGE: ' + JSON.stringify(storageInfo).slice(0, 500));
      // 对照实验 5: draft-database-v1 深挖(store/记录结构——
      // 本地删除的实现依据: 绕过 UI handler 与安全盾直接删记录)
      const dbInfo = await page.evaluate(() => new Promise((resolve) => {
        const req = indexedDB.open('draft-database-v1');
        req.onsuccess = () => {
          const db = req.result;
          const stores = Array.from(db.objectStoreNames);
          const out = { stores: [] };
          let pending = stores.length;
          if (!pending) { db.close(); return resolve(out); }
          for (const name of stores) {
            try {
              const tx = db.transaction(name, 'readonly');
              const st = tx.objectStore(name);
              const info = { name, keyPath: st.keyPath, count: 0, samples: [] };
              out.stores.push(info);
              const c = st.count();
              c.onsuccess = () => {
                info.count = c.result;
                const cur = st.openCursor();
                let n = 0;
                cur.onsuccess = () => {
                  const cursor = cur.result;
                  if (cursor && n < 2) {
                    info.samples.push({
                      key: String(cursor.key).slice(0, 50),
                      head: JSON.stringify(cursor.value).slice(0, 160),
                    });
                    n++;
                    cursor.continue();
                  } else {
                    if (--pending === 0) { db.close(); resolve(out); }
                  }
                };
                cur.onerror = () => { if (--pending === 0) resolve(out); };
              };
              c.onerror = () => { if (--pending === 0) resolve(out); };
            } catch (e) {
              out.stores.push({ name, err: String(e).slice(0, 50) });
              if (--pending === 0) resolve(out);
            }
          }
        };
        req.onerror = () => resolve({ err: String(req.error) });
      })).catch((e) => ({ err: String(e) }));
      fs.writeFileSync('xhs_del_db.json', JSON.stringify(dbInfo, null, 2));
      LOG('DB_DUMP: ' + JSON.stringify(dbInfo).slice(0, 700));
      await page.screenshot({ path: 'xhs_del_diag_final.png' });
      await browser.close();
      process.exit(0);
    }
    if (CFG.cleanMode === 'delete') {
      // 第 21 轮根因修复(diag 三路实证): UI 删除 handler 绑在
      // DIV.btn(click), CDP 点击按钮中心触发安全盾风控调用
      // (as.xiaohongshu.com/api/sec/v1/shield/webprofile)后
      // handler 链中止, 删除操作(本地)未执行——44 轮坐标点击
      // 与 dispatchEvent 全序列均绕不过。改走 IndexedDB 直接
      // 删记录, 彻底绕过 UI 层与安全盾。
      // 结构(12:58 diag 实证): draft-database-v1 四 store
      // (article/audio/image/video-draft), keyPath=draftId;
      // match 为空=全删, 非空=只删 JSON 含 match 的记录。
      // 逻辑抽至 xhs_draft_db.js(与 node:test 单测共用同一份
      // 函数体, 测试不漂移)
      const { deleteDraftsInPage } = require('./xhs_draft_db');
      const matchTitle = String(CFG.match || '');
      const delResult = await page.evaluate(
        deleteDraftsInPage, matchTitle
      ).catch((e) => ({ err: String(e) }));
      LOG('DB_DELETE: ' + JSON.stringify(delResult).slice(0, 400));
      // 弹窗内验证(顶部入口计数是快照不可靠, 以重开弹窗列表为准)
      await sleep(2000);
      await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
      await sleep(5000);
      await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('div, button, span, a'))
          .filter((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
        const el = els[els.length - 1];
        if (el) el.click();
      }).catch(() => {});
      await sleep(3500);
      await page.screenshot({ path: 'xhs_drafts_after.png' });
      const afterCount = await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('*'))
          .filter((e) => /draft-item/.test(String(e.className)));
        return items.length;
      }).catch(() => -1);
      LOG('draft items visible after db-clean: ' + afterCount);
    }
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'draft') {
    // 联调第 19 轮: 草稿恢复流——18 轮实证根因: 上传完成≠可发布,
    // xhs 视频需服务端转码, 转码窗口期点「发布」会被静默转存草稿。
    // 此 action 打开首页停住: 人工从左侧「草稿箱」进编辑页点发布
    // (此时转码已完成), bot 轮询成功特征收口(同一 tab 内操作)
    LOG('=== 请人工操作: 左侧「草稿箱」→ 草稿「编辑」→ 点红「发布」 ===');
    let ok = false;
    let finalUrl = page.url();
    const deadline = Date.now() + (CFG.manualWaitMinutes || 10) * 60000;
    while (Date.now() < deadline) {
      await sleep(3000);
      finalUrl = page.url();
      const t = await page.evaluate(
        () => (document.body.innerText || '').slice(0, 600)
      ).catch(() => '');
      if (/发布成功|正在发布|审核中|定时成功|定时发布成功/.test(t) || /\/manage|published=true/.test(finalUrl)) {
        ok = true;
        break;
      }
    }
    await page.screenshot({ path: ok ? 'xhs_publish_ok.png' : 'xhs_draft_after.png' });
    fs.writeFileSync('xhs_publish_result.json', JSON.stringify({
      ok, url: finalUrl, at: Date.now(),
      note: ok ? 'draft-recovery publish' : 'draft 流人工操作未完成',
    }, null, 2));
    LOG((ok ? 'PUBLISH_OK' : 'PUBLISH_UNVERIFIED') + ' url=' + finalUrl.slice(0, 100));
    await browser.close();
    process.exit(ok ? 0 : 6);
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
    // 关闭话题推荐下拉浮层(联调实证 2026-09-30: 正文输入后
    // 「#xx」话题建议浮层 z-index 高, 会盖住底部操作栏致
    // elementFromPoint 命中浮层——ESC 失焦关闭后再定位)
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(800);
    await page.screenshot({ path: 'xhs_publish_1_filled.png' });
    // 底部操作栏裁剪放大(联调: 红色「发布」按钮坐标定位源)
    await page.screenshot({
      path: 'xhs_publish_bottom.png',
      clip: { x: 800, y: 700, width: 480, height: 200 },
    });
    // 3.5 转码完成等待(第 20 轮实证): 上传完成≠可发布——19 轮
    //     实证转码窗口期点「发布」被静默转存草稿; 「检测为高清
    //     视频」绿标 = 服务端转码完成特征, 出现后才允许点发布
    //     2026-10-02 校准(本次发布实证): 绿标仅 ≥1080P 片出现——
    //     720x1280 口径片永远显示「视频分辨率较低」黄色警告而非
    //     绿标(截图 xhs_publish_1_filled.png 实证), 5 分钟轮询必然
    //     false; 将分辨率警告纳入完成特征(平台已给出分析建议 =
    //     处理完毕信号)。720 标准片在 5 分钟窗口后自动点发布安全
    //     (published=true + 笔记管理已发布实证); 非标分辨率片仍走
    //     19 轮转存草稿风险面——根治出片升 1080P 见 dh_batch 注释
    let transcoded = false;
    for (let i = 0; i < 60; i++) {
      const txt = await page.evaluate(
        () => (document.body.innerText || '').slice(0, 2000)
      ).catch(() => '');
      if (/检测为高清视频|高清视频|视频分辨率较低|分辨率较低/.test(txt)) { transcoded = true; break; }
      await sleep(5000);
    }
    LOG('transcode signal(高清绿标/分辨率警告): ' + transcoded);
    // 4. 点发布(第 20 轮全自动闭环定稿): 四大实证——
    //    · 底部操作栏(暂存离开/发布)整个在 closed shadow DOM 内:
    //      querySelectorAll/getComputedStyle 全不可达——16 轮
    //      elementFromPoint 文本扫描 + 4 轮 DOM 文本/颜色遍历
    //      全 miss 的终极根因(截图可见而 DOM 不可达)
    //    · 修法: CDP DOM.getFlattenedDocument(pierce) ——DevTools
    //      官方穿透协议: text node「发布」→ 父按钮 getBoxModel
    //      → 视口坐标(本轮实证 (700,855) 命中)
    //    · 先 scrollIntoView 定时开关(常规 DOM 可达)带操作栏入
    //      视口; 「定时发布」是开关非按钮(1h~14d), 默认 OFF,
    //      点「发布」= 立即发布(开关不碰)
    //    · 成功特征: URL 带 published=true(20:20 实跑实证, CDP
    //      双击生效笔记已发布; 此前正则缺此模式致成功未识别)
    let publishPt = null;
    for (let attempt = 0; attempt < 2 && !publishPt; attempt++) {
      // 穿透通道(第 20 轮终因实证): 操作栏(暂存离开/发布)整个
      // 在 closed shadow DOM 内——querySelectorAll/getComputedStyle
      // 均不可达, 截图可见而 DOM 全遍历 miss; CDP
      // DOM.getFlattenedDocument(pierce) 是 DevTools 官方穿透
      // 通道: text node「发布」→ 父按钮 getBoxModel → 视口坐标
      try {
        // 先滚到「定时发布」开关(常规 DOM 可达, 操作栏紧邻其后)
        // 使 shadow 内操作栏入视口(boxModel 视口外则点击无效)
        await page.evaluate(() => {
          const el = document.querySelector('.post-time-wrapper')
            || document.querySelector('.custom-switch-wrapper');
          if (el) el.scrollIntoView({ block: 'center' });
        }).catch(() => {});
        await sleep(800);
        const cdp = await page.createCDPSession();
        await cdp.send('DOM.enable');
        const { nodes } = await cdp.send('DOM.getFlattenedDocument',
          { depth: -1, pierce: true });
        // saveDraft 模式目标「暂存离开」(同 shadow 操作栏, 造草稿
        // 供 diag/delete 诊断流), 默认「发布」
        const pierceTarget = CFG.saveDraft ? '暂存离开' : '发布';
        const hits = nodes.filter((n) =>
          n.nodeType === 3
          && (n.nodeValue || '').trim() === pierceTarget);
        for (const t of hits) {
          const parent = nodes.find((n) => n.nodeId === t.parentId);
          if (!parent) continue;
          const bm = await cdp.send('DOM.getBoxModel',
            { nodeId: parent.nodeId }).catch(() => null);
          if (!bm || !bm.model) continue;
          const c = bm.model.content;
          const x = (c[0] + c[4]) / 2;
          const y = (c[1] + c[5]) / 2;
          // x>300 排侧边栏「发布笔记」; 视口内校验
          if (x > 300 && y > 0 && y < 890) {
            publishPt = {
              x: Math.round(x), y: Math.round(y),
              label: pierceTarget + '(cdp-pierce)',
            };
            break;
          }
        }
        await cdp.detach();
      } catch (e) { LOG('cdp pierce err: ' + e.message); }
      if (!publishPt) await sleep(2000);
    }
    if (!publishPt) {
      // 联调第 18 轮定稿: 坐标兜底乱点已废弃——实证曾误触
      // 重置类操作致表单清空回上传初始态(红「上传视频」入口)。
      // 人工模式 miss 即停, 交人工点「发布」; DPR 留证仅作缩放排查
      const dpr = await page.evaluate(
        () => window.devicePixelRatio).catch(() => 0);
      LOG('scan miss — DPR=' + dpr
        + ' 跳过自动点击, 人机协作等待人工点「发布」');
    } else {
      LOG('publish btn pt: ' + JSON.stringify(publishPt));
      // 滚动后再拍底部裁剪验证(按钮入视口)
      await page.screenshot({
        path: 'xhs_publish_bottom.png',
        clip: { x: 800, y: 680, width: 480, height: 220 },
      });
      // 合成点击(仅真实命中时)
      await page.evaluate((p) => { const hit = document.elementFromPoint(p.x, p.y); if (hit) hit.click(); }, publishPt).catch(() => {});
      await sleep(2500);
      // CDP 真实点击兜底(合成点击无反应时)
      const cdp = await page.createCDPSession();
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: publishPt.x, y: publishPt.y, button: 'none', pointerType: 'mouse' });
      await sleep(150);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: publishPt.x, y: publishPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      await sleep(90);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: publishPt.x, y: publishPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
    }
    // 4.6 saveDraft 模式收口: 暂存离开后页面回上传初始页,
    //     草稿箱计数+1 留证(造草稿供 diag/delete 诊断流)
    if (CFG.saveDraft) {
      await sleep(3000);
      const cnt = await page.evaluate(() => {
        const el = Array.from(document.querySelectorAll('*'))
          .find((e) => /^草稿箱\(\d+\)$/.test((e.innerText || '').trim()));
        return el ? el.innerText.trim() : 'n/a';
      }).catch(() => 'n/a');
      await page.screenshot({ path: 'xhs_savedraft_after.png' });
      fs.writeFileSync('xhs_publish_result.json', JSON.stringify({
        ok: !!publishPt, saveDraft: true, count: cnt, at: Date.now(),
      }, null, 2));
      LOG('SAVE_DRAFT_DONE count=' + cnt);
      await browser.close();
      process.exit(publishPt ? 0 : 5);
    }
    // 4.5 dry-run 模式(第 20 轮): 全自动发布前的无副作用验证——
    //     上传+填写+等转码+扫描全链真实执行, 命中「发布」钮即停
    //     (不点击不发布), 留档命中坐标与截图供人审
    if (CFG.probeSchedule) {
      // 底部区域全量取证(y>500): 无论命中与否, dump 全部元素
      // tag/位置/背景/定位/文本——发布钮 DOM 形态一次看全
      const areaDump = await page.evaluate(() =>
        Array.from(document.querySelectorAll('div, button, span, a'))
          .map((e) => {
            const r = e.getBoundingClientRect();
            if (r.y < 500 || r.width < 30 || r.height < 14) return null;
            const s = getComputedStyle(e);
            return {
              tag: e.tagName,
              cls: String(e.className).slice(0, 50),
              x: Math.round(r.x), y: Math.round(r.y),
              w: Math.round(r.width), h: Math.round(r.height),
              pos: s.position,
              bg: (s.backgroundColor || '').slice(0, 40),
              bgImg: (s.backgroundImage || '').slice(0, 70),
              text: (e.innerText || '').trim().slice(0, 10),
            };
          }).filter(Boolean).slice(-60)
      ).catch((e) => [{ err: String(e) }]);
      fs.writeFileSync('xhs_area_dump.json',
        JSON.stringify(areaDump, null, 2));
      await page.screenshot({ path: 'xhs_probe_hit.png' });
      fs.writeFileSync('xhs_probe_hit.json', JSON.stringify({
        hit: publishPt,
        transcoded,
        at: Date.now(),
      }, null, 2));
      LOG('DRYRUN_DONE hit=' + JSON.stringify(publishPt)
        + ' transcoded=' + transcoded
        + ' — 未点击未发布 (xhs_probe_hit.png/json + xhs_area_dump.json)');
      await browser.close();
      process.exit(0);
    }
    await sleep(8000);
    // 5. 人工确认等待(联调第 17 轮定稿: 自动化定位 16 轮未果——
    //    切人机协作, 对齐 36号"对话内 browser agent"SOP 原始
    //    语义: bot 上传+填写后停住, 人工点「发布」, bot 检测
    //    URL 跳转/成功提示后继续)
    let ok = false;
    let finalUrl = page.url();
    const MANUAL_WAIT = (CFG.manualWaitMinutes || 6) * 60000;
    LOG('=== 请在 Chrome 窗口人工点击红色「发布」按钮 (等待 '
      + Math.round(MANUAL_WAIT / 60000) + ' 分钟) ===');
    const manualDeadline = Date.now() + MANUAL_WAIT;
    while (Date.now() < manualDeadline) {
      await sleep(3000);
      finalUrl = page.url();
      const t = await page.evaluate(
        () => (document.body.innerText || '').slice(0, 600)
      ).catch(() => '');
      if (/manage|发布成功|正在发布|published=true/.test(finalUrl + t)) {
        ok = true;
        break;
      }
    }
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
