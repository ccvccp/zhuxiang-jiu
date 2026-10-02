// 抖音视频 RPA 发布 bot (2026-09-28 立项)
// 架构复用视频号 bot.js 已实证范式: CDP pipe 传输 Chrome(无 TCP 端口,
// 剥 --enable-automation 拟真) + 独立 douyin-profile 持久登录态。
// 与视频号 bot 完全隔离(独立 profile/日志/产物前缀), 视频号链路零改动。
// 用法: node douyin-bot.js <config.json>
// config: { action: "probe"|"publish", mp4, desc, waitLoginMinutes }
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CFG = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const LOG = (m) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${m}`;
  console.log(line);
  fs.appendFileSync('douyin_bot.log', line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const DOUYIN_HOME = 'https://creator.douyin.com/';
// 视频发布页(creator-micro 前缀; probe 阶段现场校准)
const DOUYIN_UPLOAD = 'https://creator.douyin.com/creator-micro/content/upload/video';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\douyin-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome launched (pipe mode, douyin-profile)');

  const page = (await browser.pages())[0] || await browser.newPage();
  // 视口铁律(同 bot.js): --window-size 可能不生效, 不设视口操作列会挤出视口
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(DOUYIN_HOME, { waitUntil: 'domcontentloaded' });
  LOG('opened url=' + page.url());
  await sleep(6000); // 登录重定向有延迟, 先沉降防假阳性

  // ---- 登录保障 ----
  // 与视频号不同: 抖音无"一键登录", 首次必须抖音 App 扫码
  // (douyin-profile 持久化后后续免扫); bot 只轮询登录特征, 不自动点击
  const loginPageLike = (url, text) =>
    /\/login|passport\.|sso\.douyin/.test(url) ||
    /扫码登录|扫码进入|登录抖音|验证码登录|密码登录|我是创作者|我是MCN机构/.test(text || '');
  const ensureLogin = async (page, waitMs) => {
    const deadline = Date.now() + waitMs;
    let lastUrl = '';
    let announced = false;
    let dumped = false;
    while (Date.now() < deadline) {
      const url = page.url();
      let bodyText = '';
      try { bodyText = await page.evaluate(() => (document.body.innerText || '').slice(0, 800)); } catch (e) {}
      if (url !== lastUrl) { LOG('url -> ' + url); lastUrl = url; }
      if (!loginPageLike(url, bodyText)) return true;
      if (!dumped) {
        dumped = true;
        // 首次遭遇登录页: dump 结构留档(扫码 iframe 形态分析)
        for (const fr of page.frames()) {
          try {
            const info = await fr.evaluate(() => ({
              url: location.href.slice(0, 140),
              imgs: Array.from(document.querySelectorAll('img')).map(i => ({
                src: (i.src || '').slice(0, 100),
                w: Math.round(i.getBoundingClientRect().width), h: Math.round(i.getBoundingClientRect().height),
              })).slice(0, 8),
              text: (document.body.innerText || '').slice(0, 300),
            }));
            fs.writeFileSync('douyin_login_dump_' + (fr === page.mainFrame() ? 'main' : String(page.frames().indexOf(fr))) + '.json', JSON.stringify(info, null, 1));
          } catch (e) {}
        }
        await page.screenshot({ path: 'douyin_login_1.png' });
        LOG('登录页 dump 留档 (douyin_login_dump_*.json / douyin_login_1.png)');
      }
      if (!announced) {
        announced = true;
        LOG('=== 请在 Chrome 窗口用「抖音 App」扫码登录 (等待 ' + Math.round(waitMs / 60000) + ' 分钟) ===');
      }
      await sleep(3000);
    }
    return !loginPageLike(page.url(), '');
  };

  if (!(await ensureLogin(page, (CFG.waitLoginMinutes || 8) * 60000))) {
    LOG('LOGIN_TIMEOUT: 二维码未被确认, 详见窗口');
    fs.writeFileSync('douyin_login_state.json', JSON.stringify({ ok: false }));
    await browser.close();
    process.exit(2);
  }
  LOG('LOGGED_IN url=' + page.url());
  fs.writeFileSync('douyin_login_state.json', JSON.stringify({ ok: true, url: page.url(), at: Date.now() }));
  await sleep(2000);

  // ---- 通用结构 dump (probe 与 publish 失败留档共用) ----
  const dumpFrames = async (tag) => {
    const out = [];
    for (const fr of page.frames()) {
      try {
        const info = await fr.evaluate(() => {
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
          const out = { url: location.href.slice(0, 140) };
          out.files = Array.from(document.querySelectorAll('input[type=file]')).map(e => ({ path: path(e), accept: e.accept || '' }));
          out.editables = Array.from(document.querySelectorAll('[contenteditable=true], textarea')).map(e => ({
            tag: e.tagName, path: path(e),
            ph: e.getAttribute('data-placeholder') || e.placeholder || '',
            w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height),
          }));
          out.buttons = Array.from(document.querySelectorAll('button, [role=button]')).map(e => (e.innerText || '').trim().slice(0, 16)).filter(Boolean).slice(0, 40);
          out.bodySnippet = (document.body.innerText || '').slice(0, 400);
          return out;
        });
        out.push(info);
      } catch (e) { out.push({ url: fr.url().slice(0, 140), err: String(e).slice(0, 100) }); }
    }
    fs.writeFileSync('douyin_probe_' + tag + '.json', JSON.stringify(out, null, 2));
    return out;
  };

  if (CFG.action === 'probe') {
    await page.screenshot({ path: 'douyin_probe_1.png' });
    const home = await dumpFrames('home');
    LOG('home frames=' + home.length);
    await page.goto(DOUYIN_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e => LOG('goto upload err: ' + e.message));
    await sleep(6000);
    // 进发布页可能又触发登录(会话校验), 二次保障
    if (!(await ensureLogin(page, 5 * 60000))) {
      LOG('RELOGIN_TIMEOUT at upload page');
      await browser.close();
      process.exit(2);
    }
    await page.goto(DOUYIN_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(6000);
    await page.screenshot({ path: 'douyin_probe_2.png' });
    const up = await dumpFrames('upload');
    for (const f of up) {
      LOG('frame ' + (f.url || '?') + ' files=' + (f.files || []).length + ' editables=' + (f.editables || []).length + ' buttons=' + JSON.stringify((f.buttons || []).slice(0, 12)));
    }
    LOG('PROBE_DONE (douyin_probe_home.json / douyin_probe_upload.json)');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'manage') {
    // 内容管理页验证: dump 作品列表前几条(标题/日期/状态), 发布结果核对用
    await page.goto('https://creator.douyin.com/creator-micro/content/manage', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(8000);
    if (loginPageLike(page.url(), '')) {
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await page.goto('https://creator.douyin.com/creator-micro/content/manage', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      await sleep(8000);
    }
    await page.screenshot({ path: 'douyin_manage_1.png' });
    const items = await page.evaluate(() => {
      // 作品卡片: 从含"编辑作品"操作按钮的容器向上归组, 提取标题/日期/状态
      const cards = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText || '').trim() === '编辑作品');
      return cards.slice(0, 6).map(btn => {
        let card = btn;
        for (let i = 0; i < 12 && card.parentElement; i++) { card = card.parentElement; const r = card.getBoundingClientRect(); if (r.width > 400 && r.height > 60 && (card.innerText || '').includes('已发布') || (card.innerText || '').includes('审核')) { if ((card.innerText || '').match(/\d{4}年/)) break; } }
        const txt = (card.innerText || '').slice(0, 200).replace(/\n/g, ' | ');
        return txt;
      });
    }).catch(e => ['eval err: ' + e.message]);
    LOG('manage items: ');
    items.forEach((t, i) => LOG('  #' + i + ' ' + t.slice(0, 150)));
    fs.writeFileSync('douyin_manage_items.json', JSON.stringify(items, null, 2));
    LOG('MANAGE_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'edit') {
    // 编辑已发作品描述 (2026-09-29: 修复 #37 微博闭合井号被抖音编辑器吞成悬空井号)
    // 定位 CFG.matchText 特征卡片 → 编辑作品 → 全选清空重填描述 → 保存
    await page.goto('https://creator.douyin.com/creator-micro/content/manage', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(8000);
    if (loginPageLike(page.url(), '')) {
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await page.goto('https://creator.douyin.com/creator-micro/content/manage', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      await sleep(8000);
    }
    await page.bringToFront();
    // 1. 定位目标卡片「编辑作品」(从 CFG.matchText 特征文本向上归组找卡片)
    const pt = await page.evaluate((mt) => {
      const leaf = Array.from(document.querySelectorAll('*')).find(e => e.children.length === 0 && (e.innerText || '').includes(mt));
      if (!leaf) return null;
      let card = leaf;
      for (let i = 0; i < 20 && card.parentElement; i++) { card = card.parentElement; if ((card.innerText || '').includes('编辑作品')) break; }
      if (!card || !(card.innerText || '').includes('编辑作品')) return null;
      const btn = Array.from(card.querySelectorAll('*')).find(e => (e.innerText || '').trim() === '编辑作品');
      if (!btn) return null;
      const r = btn.getBoundingClientRect();
      if (r.width === 0) return null;
      btn.scrollIntoView({ block: 'center' });
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }, CFG.matchText).catch(() => null);
    if (!pt) { LOG('EDIT_TARGET_NOT_FOUND'); await page.screenshot({ path: 'douyin_edit_0_notfound.png' }); await browser.close(); process.exit(3); }
    LOG('edit btn pt: ' + JSON.stringify(pt));
    // 2. 点「编辑作品」: 合成 click 优先, 无反应兜底 CDP 真实点击(force=0.5)
    const editorOpen = () => page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('[contenteditable=true], textarea'));
      return els.some(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 20; });
    }).catch(() => false);
    await page.evaluate((p) => { const hit = document.elementFromPoint(p.x, p.y); if (hit) hit.click(); }, pt).catch(() => {});
    await sleep(2500);
    let opened = await editorOpen();
    if (!opened) {
      LOG('合成 click 无反应 — CDP 真实点击(force=0.5)');
      const cdp = await page.createCDPSession();
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pt.x, y: pt.y, button: 'none', pointerType: 'mouse' });
      await sleep(150);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pt.x, y: pt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      await sleep(90);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pt.x, y: pt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
      await sleep(2500);
      opened = await editorOpen();
    }
    LOG('editor open: ' + opened + ' url=' + page.url().slice(0, 100));
    await page.screenshot({ path: 'douyin_edit_1_open.png' });
    if (!opened) { LOG('EDITOR_NOT_OPENED'); await dumpFrames('edit_not_open'); await browser.close(); process.exit(5); }
    // 3. 定位含旧文案的描述编辑器 → 全选清空 → 重填
    let edH = null;
    try {
      edH = await page.evaluateHandle((mt) => {
        const els = Array.from(document.querySelectorAll('[contenteditable=true], textarea'));
        const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 20; });
        return vis.find(e => (e.innerText || '').includes(mt)) || vis[0] || null;
      }, CFG.matchText);
      const ok = await edH.evaluate(el => !!(el && el.tagName)).catch(() => false);
      if (!ok) edH = null;
    } catch (e) {}
    if (!edH) { LOG('NO_DESC_EDITOR_IN_EDIT'); await dumpFrames('edit_no_desc'); await browser.close(); process.exit(5); }
    await edH.evaluate(el => el.focus()).catch(() => {});
    // puppeteer 不支持 press('Control+A') 组合写法 → 拆 down/press/up
    await page.keyboard.down('Control');
    await page.keyboard.press('KeyA');
    await page.keyboard.up('Control');
    await page.keyboard.press('Delete');
    await sleep(300);
    await page.keyboard.type(CFG.desc, { delay: 20 });
    LOG('desc retyped: ' + CFG.desc.slice(0, 40) + '...');
    await sleep(1000);
    await page.screenshot({ path: 'douyin_edit_2_filled.png' });
    // 4. 保存: 先收尾干扰弹层(话题联想/预览提示), dump 按钮诊断, 宽松匹配发布
    // (铁律: 不能含"作品"匹配——防误点左上角「作品发布」入口按钮)
    await page.keyboard.press('Escape').catch(() => {});
    await page.evaluate(() => {
      const b = Array.from(document.querySelectorAll('button, [role=button], div, span')).find(e => (e.innerText || '').trim() === '我知道了');
      if (b) b.click();
    }).catch(() => {});
    await sleep(800);
    const btnDump = await page.evaluate(() => Array.from(document.querySelectorAll('button')).map(b => ({ t: (b.innerText || '').trim().slice(0, 12), d: !!b.disabled })).filter(x => x.t).slice(0, 30)).catch(() => []);
    LOG('btn dump: ' + JSON.stringify(btnDump));
    let saved = false;
    for (let round = 0; round < 3 && !saved; round++) {
      const r = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button, [role=button]'));
        const b = btns.find(x => {
          const t = (x.innerText || '').trim();
          // 编辑页保存按钮实证为「提交修改」(2026-09-29 btn dump)
          return ['发布', '保存', '确认', '完成', '提交修改'].includes(t) && !x.disabled;
        });
        if (!b) return { clicked: false };
        b.click();
        return { clicked: true, t: (b.innerText || '').trim() };
      }).catch(() => ({ clicked: false }));
      LOG('save round ' + round + ': ' + JSON.stringify(r));
      await sleep(4000);
      if (r.clicked) saved = true;
    }
    await page.screenshot({ path: 'douyin_edit_3_saved.png' });
    const editAfter = { url: page.url() };
    try { editAfter.body = await page.evaluate(() => (document.body.innerText || '').slice(0, 400)); } catch (e) {}
    LOG('after url=' + editAfter.url);
    fs.writeFileSync('douyin_edit_result.json', JSON.stringify({ ok: saved, desc: CFG.desc, afterUrl: editAfter.url, afterBody: editAfter.body }, null, 2));
    LOG('EDIT_DONE');
    await browser.close();
    process.exit(0);
  }

  if (CFG.action === 'publish') {
    await page.goto(DOUYIN_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(6000);
    if (loginPageLike(page.url(), '')) {
      LOG('SESSION_EXPIRED — 等扫码');
      if (!(await ensureLogin(page, 10 * 60000))) { LOG('RELOGIN_TIMEOUT'); await browser.close(); process.exit(4); }
      await page.goto(DOUYIN_UPLOAD, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      await sleep(6000);
    }

    // 1. 找上传 input (frames 遍历, 同 bot.js findUpload 范式)
    const findUpload = async () => {
      for (const f of page.frames()) {
        try {
          const h = await f.evaluateHandle(() => document.querySelector('input[type=file]'));
          const ok = await h.evaluate((el) => !!(el && el.tagName)).catch(() => false);
          if (ok) return { f, h: h.asElement() };
        } catch (e) {}
      }
      return null;
    };
    let upload = null;
    for (let i = 0; i < 15; i++) {
      upload = await findUpload();
      if (upload) break;
      LOG('poll ' + (i * 2) + 's frames=' + page.frames().length + ' no file input yet');
      await sleep(2000);
    }
    if (!upload) {
      LOG('NO_FILE_INPUT after 30s');
      await page.screenshot({ path: 'douyin_err_no_input.png' });
      await dumpFrames('err_no_input');
      await browser.close();
      process.exit(3);
    }
    LOG('upload input found, frame=' + upload.f.url().slice(0, 80));
    await upload.h.uploadFile(CFG.mp4);
    LOG('uploadFile: ' + CFG.mp4);

    // 2. 填作品描述 (抖音描述表单在上传中即并行渲染——轮询首个可见编辑器)
    // 铁律(2026-09-28 实证): 不能用"删除"字样判上传完成(上传中就显示);
    // 就绪真值唯一 = 发布按钮 enabled(上传+处理完成才亮), 误判会点空/漏发
    let descFilled = !CFG.desc;
    for (let i = 0; i < 24 && !descFilled; i++) {
      try {
        const descPick = await page.evaluateHandle(() => {
          const els = Array.from(document.querySelectorAll('[contenteditable=true], textarea'));
          const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 20; });
          const byPh = vis.find(e => ((e.getAttribute('data-placeholder') || '') + (e.getAttribute('placeholder') || '')).match(/描述|标题|介绍|想法/));
          return byPh || vis[0] || null;
        });
        const descOk = await descPick.evaluate(el => !!(el && el.tagName)).catch(() => false);
        if (descOk) {
          await descPick.evaluate(el => el.focus()).catch(e => LOG('desc focus err ' + e.message));
          await page.keyboard.type(CFG.desc, { delay: 20 });
          descFilled = true;
          LOG('desc typed via keyboard (上传中并行填写)');
        } else if (i % 4 === 0) LOG('wait desc editor poll ' + (i * 3) + 's');
      } catch (e) { LOG('desc step err: ' + e.message); }
      if (!descFilled) await sleep(3000);
    }
    if (!descFilled) { LOG('NO_DESC_EDITOR after 72s'); await dumpFrames('no_desc'); }
    await sleep(1500);
    await page.screenshot({ path: 'douyin_publish_1_filled.png' });

    // 3.0 上传完成等待(2026-10-02 三轮发布失败实证修正): 「按钮
    //     enabled=上传+处理完成」铁律已失效(页面改版——enabled 上传
    //     中即可见, 10 秒点发布静默无效, manage 作品列表实证零新增)
    //     ——固定 60s 处理下限 + 每 15s 记录页面文本(校准素材),
    //     期满再进按钮流
    const upStart = Date.now();
    while (Date.now() - upStart < 60000) {
      const el = Math.round((Date.now() - upStart) / 1000);
      let txt = '';
      try { txt = await page.evaluate(() => (document.body.innerText || '').slice(0, 120)); } catch (e) {}
      if (el % 15 === 0) LOG('wait upload/process ' + el + 's body=[' + txt.slice(0, 60).replace(/\n/g, ' ') + ']');
      await sleep(5000);
    }
    LOG('upload wait done (>=60s), enter publish-btn flow');

    // 3. 等发布按钮就绪并点击 (enabled = 上传+处理完成唯一真值; 最多等 5 分钟)
    let clicked = false;
    for (let i = 0; i < 60 && !clicked; i++) {
      try {
        const btnPt = await page.evaluate(() => {
          const btns = Array.from(document.querySelectorAll('button, [role=button]'));
          const b = btns.find(x => (x.innerText || '').trim() === '发布' && !x.disabled);
          if (!b) return null;
          // 2026-10-02 视口外点击无效实证修正: 按钮 rect y=1378 >
          // 视口 900(表单长页底部), 合成 b.click() 与 CDP 坐标点击
          // 全部打空(xhs-bot 21 轮同款坑——先 scrollIntoView 滚入
          // 视口再取新坐标; 滚后 rect 立即更新)
          b.scrollIntoView({ block: 'center' });
          const r = b.getBoundingClientRect();
          if (!(r.width > 0) || r.y < 0 || r.y > 890) return null;
          return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        });
        if (!btnPt) {
          if (i % 4 === 0) LOG('wait publish-btn enabled poll ' + (i * 5) + 's');
          await sleep(5000);
          continue;
        }
        const ok = await page.evaluate(() => {
          const btns = Array.from(document.querySelectorAll('button, [role=button]'));
          const b = btns.find(x => (x.innerText || '').trim() === '发布' && !x.disabled);
          if (!b) return false;
          b.click();
          return true;
        });
        if (ok) {
          clicked = true;
          LOG('click 发布 (合成, 按钮 enabled) at ' + Math.round(btnPt.x) + ',' + Math.round(btnPt.y));
          // CDP 真实点击兜底(xhs 21 轮机制迁移: 合成 click 可能被
          // 平台拦, CDP Input 为输入层真实事件 isTrusted=true,
          // force=0.5 压力校验——视频号/抖音列表页同款已实证)
          await sleep(2500);
          const cdp = await page.createCDPSession();
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: btnPt.x, y: btnPt.y, button: 'none', pointerType: 'mouse' });
          await sleep(150);
          await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
          await sleep(90);
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: btnPt.x, y: btnPt.y, button: 'left', clickCount: 1, force: 0.5, pointerType: 'mouse' });
          LOG('CDP 真实点击兜底已发');
        }
      } catch (e) { LOG('publish click err: ' + e.message); await sleep(5000); }
    }
    if (!clicked) {
      LOG('PUBLISH_BTN_TIMEOUT — 发布按钮 5 分钟未就绪');
      await dumpFrames('no_publish_btn');
      await page.screenshot({ path: 'douyin_err_no_btn.png' });
    }

    // 5. 成功特征轮询收口(xhs 21 轮机制迁移, 替换固定 sleep 15s):
    //    URL 离开 upload/video 跳内容管理页 / 文本含 发布成功/审核中
    //    —— 每 3s 一次最多 90s, 命中早退; 超时留档人审(特征实证校准项)
    let published = false;
    let afterUrl = page.url();
    let afterBody = '';
    const pubDeadline = Date.now() + 90 * 1000;
    while (Date.now() < pubDeadline) {
      await sleep(3000);
      afterUrl = page.url();
      try { afterBody = await page.evaluate(() => (document.body.innerText || '').slice(0, 500)); } catch (e) {}
      const leftUpload = /upload\/video/.test(afterUrl);
      // 2026-10-02 误判实证修正: content/post/video 是发布表单页
      // (点早/无效点击的复现形态), 旧特征「离开 upload 即成功」把它
      // 当成功——排除之; 成功真值 = 内容管理页或成功 toast 文本
      const hit = (!leftUpload && !/post\/video/.test(afterUrl)
                   && /creator\.douyin\.com/.test(afterUrl))
        || /发布成功|审核中|已成功发布/.test(afterBody);
      if (hit) { published = true; break; }
    }
    await page.screenshot({ path: 'douyin_publish_3_after.png' });
    LOG((published ? 'PUBLISH_OK' : 'PUBLISH_UNVERIFIED') + ' url=' + afterUrl);
    LOG('after body: ' + afterBody.slice(0, 300));
    fs.writeFileSync('douyin_publish_result.json', JSON.stringify({ clicked, descFilled, published, afterUrl, afterBody, mp4: CFG.mp4 }, null, 2));
    LOG('PUBLISH_RUN_DONE');
    await browser.close();
    process.exit(published ? 0 : 6);
  }

  LOG('unknown action');
  await browser.close();
})().catch(e => { LOG('ERR ' + (e && e.message || e)); process.exit(1); });
