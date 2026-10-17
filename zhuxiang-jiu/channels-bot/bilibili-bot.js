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
    if (CFG.probeUrl) {
      await page.goto(CFG.probeUrl, { waitUntil: 'domcontentloaded' });
      LOG('probe goto ' + CFG.probeUrl);
      await sleep(8000);
    }
    const dump = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a, button'))
        .map(el => (el.innerText || '').trim() + '|' + (el.href || ''))
        .filter(t => /投稿|上传|视频|稿件|内容管理|草稿|删除/.test(t))
        .slice(0, 40);
      const clicks = Array.from(document.querySelectorAll('button, [role=button], span, div, a'))
        .filter(el => el.children.length === 0)
        .map(el => (el.innerText || '').trim())
        .filter(t => /草稿|删除|确定|确认/.test(t) && t.length <= 10)
        .slice(0, 30);
      return { url: location.href, links, clicks,
               body: (document.body.innerText || '').slice(0, 1200) };
    });
    LOG('probe url=' + dump.url);
    LOG('links:\n  ' + dump.links.join('\n  '));
    LOG('clickables(草稿/删除):\n  ' + dump.clicks.join(' | '));
    LOG('body head:\n' + dump.body.replace(/\n/g, ' / ').slice(0, 600));
    fs.writeFileSync('bili_probe_dump.json', JSON.stringify(dump, null, 2));
    LOG('PROBE_DONE (bili_probe_dump.json)');
    await browser.close();
    return;
  }

  // ---- cleandrafts: 草稿箱批量清理(2026-10-05 校准残留) ----
  // 护栏: ①先切「草稿」tab ②已发布作品标题仍在页面则中止(防误删) ③封顶8条
  if (CFG.action === 'cleandrafts') {
    await page.goto('https://member.bilibili.com/platform/upload-manager/article',
                    { waitUntil: 'domcontentloaded' });
    await sleep(6000);
    const tabOk = await page.evaluate(() => {
      const t = Array.from(document.querySelectorAll('span, div, a, button'))
        .filter(el => el.children.length === 0 && (el.innerText || '').trim() === '草稿');
      if (t.length) { t[t.length - 1].click(); return true; }
      return false;
    });
    LOG('草稿 tab click: ' + tabOk);
    await sleep(4000);
    const guard = await page.evaluate(() =>
      !(document.body.innerText || '').includes('活动sv73_492a9c447c99'));
    LOG('published-work-absent guard: ' + guard);
    if (!tabOk || !guard) {
      LOG('ABORT — 草稿 tab 未切换或已发布作品仍可见, 不执行删除');
      await browser.close(); process.exit(9);
    }
    let deleted = 0;
    const CAP = Number(CFG.maxDelete || 8);
    for (let round = 0; round < CAP; round++) {
      const item = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll(
          'button, [role=button], span, a, div'))
          .filter(el => el.children.length === 0
                        && /^(删除|删除作品|删除视频|删除草稿)$/.test((el.innerText || '').trim())
                        && el.offsetParent !== null);
        if (!btns.length) return null;
        const b = btns[0];
        b.scrollIntoView({ block: 'center' });
        const row = b.closest('[class*=item],[class*=card],[class*=row],li,tr');
        const title = row ? (row.innerText || '').split('\n').filter(Boolean).slice(0, 3).join('/')
                          : '(unknown)';
        b.click();
        return title;
      });
      if (!item) break;
      LOG('delete draft: [' + String(item).slice(0, 60) + ']');
      await sleep(1500);
      const confirmed = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('button, [role=button], span'))
          .filter(el => el.children.length === 0
                        && /^(确定|删除|确认|确认删除)$/.test((el.innerText || '').trim())
                        && el.offsetParent !== null);
        if (els.length) { els[els.length - 1].click(); return (els[els.length - 1].innerText || '').trim(); }
        return '';
      });
      LOG('confirm: [' + (confirmed || 'no-dialog(auto)') + ']');
      await sleep(2500);
      deleted++;
    }
    const body = await page.evaluate(() => (document.body.innerText || '').slice(0, 800));
    LOG('CLEANDRAFTS_DONE deleted=' + deleted);
    LOG('after-body: ' + body.replace(/\n/g, ' ').slice(0, 300));
    await browser.close();
    process.exit(0);
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

  // ---- 公共预填(2026-10-05 dumpForm 校准) ----
  // B站表单实证: 标题= input[placeholder*=「稿件标题」], 上传后自动
  // 以文件名预填(「仅空时」守护失效→文件名标题上线教训); 简介是
  // contenteditable(data-placeholder 含「相关信息」) 非 textarea。
  const fillTitleDesc = async (titleText, descText) => {
    const r1 = await page.evaluate((t) => {
      const box = Array.from(document.querySelectorAll('input'))
        .find(x => (x.placeholder || '').includes('稿件标题')
                   && x.getBoundingClientRect().width > 0);
      if (!box) return { err: 'no-title-input' };
      const cur = box.value || '';
      const filenameLike = /sv73_|\.mp4|^\s*$/.test(cur);
      if (cur && !filenameLike && cur !== t) return { skipped: cur };
      box.scrollIntoView({ block: 'center' });
      box.focus();
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set.call(box, t);
      box.dispatchEvent(new Event('input', { bubbles: true }));
      box.dispatchEvent(new Event('change', { bubbles: true }));
      return { typed: box.value };
    }, String(titleText || ''));
    if (r1.typed === (titleText || '')) LOG('title 填写: [' + r1.typed + ']');
    else if (r1.skipped) LOG('标题已有内容(草稿恢复): [' + r1.skipped + '] — 跳过');
    else LOG('WARN 标题框未定位: ' + JSON.stringify(r1));
    const DECL = '（本视频由AI辅助制作）';
    let d = String(descText || '');
    if (d && !/AI|人工智能/.test(d)) d += DECL;
    if (!d) return;
    const dState = await page.evaluate(() => {
      const ce = Array.from(document.querySelectorAll('[contenteditable=true]'))
        .filter(x => x.getBoundingClientRect().width > 0)
        .find(x => /相关信息/.test(x.getAttribute('data-placeholder') || ''));
      return ce ? { has: !!(ce.innerText || '').trim() } : null;
    });
    if (dState && !dState.has) {
      await page.evaluate(() => {
        const ce = Array.from(document.querySelectorAll('[contenteditable=true]'))
          .filter(x => x.getBoundingClientRect().width > 0)
          .find(x => /相关信息/.test(x.getAttribute('data-placeholder') || ''));
        if (ce) { ce.focus(); ce.click(); }
      });
      await page.keyboard.type(d, { delay: 20 });
      LOG('desc 填写(含AIGC软声明): [' + d.slice(0, 30) + '...]');
    } else if (dState) LOG('简介非空(草稿) — 跳过');
    else LOG('WARN 简介框(contenteditable)未定位');
  };

  // ---- assist: 半自动模式(2026-10-05 首发实证) ----
  // bot: 上传+标题/简介预填(含AIGC软声明)+10min 快照监听;
  // 人工: 分区/创作声明/立即投稿 三步(B站必填项弹层结构未完全
  // 校准前的事故安全形态; 成功特征=「恭喜你上传/成为UP主」)
  if (CFG.action === 'assist') {
    await page.goto('https://member.bilibili.com/platform/upload/video/frame/',
                    { waitUntil: 'domcontentloaded' });
    await sleep(6000);
    let uh = null;
    for (let i = 0; i < 12 && !uh; i++) {
      for (const fr of page.frames()) {
        try { const x = await fr.$('input[type=file]'); if (x) { uh = x; break; } } catch (e) {}
      }
      if (!uh) await sleep(5000);
    }
    if (!uh) { LOG('UPLOAD_INPUT_TIMEOUT'); await browser.close(); process.exit(3); }
    await uh.uploadFile(CFG.mp4);
    LOG('uploadFile: ' + CFG.mp4);
    for (let i = 0; i < 36; i++) {
      if (await page.evaluate(() => /请选择分区/.test(document.body.innerText || ''))) break;
      await sleep(5000);
    }
    // dumpForm: 表单取证模式(标题校准 2026-10-05——首条落文件名标题,
    // 旧逻辑回退抓错框实证): 只 dump 不填写, 草稿自然留存
    if (CFG.dumpForm) {
      const dump = await page.evaluate(() => {
        const inputs = Array.from(document.querySelectorAll('input, textarea'))
          .filter(x => x.getBoundingClientRect().width > 0)
          .map(x => ({ tag: x.tagName.toLowerCase(), type: x.type || '',
                       ph: (x.placeholder || '').slice(0, 50),
                       val: (x.value || '').slice(0, 40),
                       cls: (x.className || '').toString().slice(0, 50) }));
        const ces = Array.from(document.querySelectorAll('[contenteditable=true]'))
          .filter(x => x.getBoundingClientRect().width > 0)
          .map(x => ({ tag: 'ce', ph: (x.getAttribute('data-placeholder') || '').slice(0, 50),
                       val: (x.innerText || '').slice(0, 40) }));
        return { url: location.href, inputs, ces };
      });
      LOG('FORM_DUMP url=' + dump.url);
      dump.inputs.forEach((x, i) => LOG('  input[' + i + '] ' + JSON.stringify(x)));
      dump.ces.forEach((x, i) => LOG('  ce[' + i + '] ' + JSON.stringify(x)));
      fs.writeFileSync('bili_form_dump.json', JSON.stringify(dump, null, 2));
      await page.screenshot({ path: 'bili_form_dump.png' });
      LOG('FORM_DUMP_DONE (bili_form_dump.json + bili_form_dump.png) — 草稿留存');
      await browser.close();
      return;
    }
    // 预填(fillTitleDesc: 文件名标题覆盖 + contenteditable 简介)
    await fillTitleDesc(CFG.title, CFG.desc);
    // fillOnly: 校准验证模式(填完核验不发布——同视频已在架防重复)
    if (CFG.fillOnly) {
      const st = await page.evaluate(() => {
        const t = Array.from(document.querySelectorAll('input'))
          .find(x => (x.placeholder || '').includes('稿件标题'));
        const ce = Array.from(document.querySelectorAll('[contenteditable=true]'))
          .find(x => /相关信息/.test(x.getAttribute('data-placeholder') || ''));
        return { title: t ? t.value : null,
                 desc: ce ? (ce.innerText || '').trim().slice(0, 50) : null };
      });
      LOG('FILL_VERIFY: title=[' + st.title + '] desc=[' + st.desc + ']');
      await page.screenshot({ path: 'bili_fill_verify.png' });
      LOG('FILL_ONLY_DONE (bili_fill_verify.png) — 未发布, 草稿留存');
      await browser.close();
      return;
    }
    LOG('>>> 请人工: ①选分区 ②创作声明(AI相关) ③立即投稿 (10 分钟) <<<');
    let done = false;
    for (let i = 0; i < 300 && !done; i++) {
      await sleep(2000);
      done = await page.evaluate(() =>
        /恭喜你上传|成为UP主|投递成功/.test(document.body.innerText || ''));
    }
    if (done) {
      LOG('PUBLISH_OK(人工投稿确认) — 稿件进入转码/审核');
      await page.screenshot({ path: 'bili_assist_ok.png' });
    } else {
      LOG('ASSIST_TIMEOUT — 10 分钟未见投稿成功, 窗口即将关闭(稿件已自动存草稿)');
      await page.screenshot({ path: 'bili_assist_timeout.png' });
    }
    LOG('ASSIST_RUN_DONE');
    await browser.close();
    process.exit(done ? 0 : 5);
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

  // 2. 等投稿表单就绪(B站特有时序: 上传完成后表单才渲染——分区/
  //    标题/简介区随之上屏; 首发实证: 上传后 5s 填写全部落空)
  let formReady = false;
  for (let i = 0; i < 36 && !formReady; i++) {
    formReady = await page.evaluate(() => {
      const t = (document.body.innerText || '');
      const hasTitle = Array.from(document.querySelectorAll('input'))
        .some(x => /标题/.test(x.placeholder || '')
                   && x.getBoundingClientRect().width > 0);
      return /选择分区|所属分区|添加分区/.test(t) || hasTitle;
    });
    if (!formReady) await sleep(5000);
  }
  LOG('form ready: ' + formReady + ' (轮询 ' + (formReady ? '命中' : '超时') + ')');
  if (formReady) {
    try {
      const t = await page.evaluate(() =>
        (document.body.innerText || '').slice(0, 900));
      LOG('form body:\n' + t.replace(/\n/g, ' / ').slice(0, 700));
    } catch (e) {}
  }

  // 3. 分区选择(B站特有硬门槛: 两级; [CAL] 弹层结构)
  //    category 格式 "一级-二级", 如 "美食-美食圈"
  try {
    const parts = String(CFG.category || '生活-美食').split('-');
    let opened = false;
    for (let i = 0; i < 6 && !opened; i++) {
      opened = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('div, span, button'))
          .filter(el => el.children.length === 0
                        && /选择分区|所属分区|添加分区/.test((el.innerText || '').trim()));
        if (els.length) { els[0].scrollIntoView({ block: 'center' }); els[0].click(); return true; }
        return false;
      });
      if (!opened) await sleep(3000);
    }
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

  // 4. 标题+简介(fillTitleDesc 公共函数: dumpForm 校准版)
  await fillTitleDesc(CFG.title, CFG.desc);

  // 6. AI 创作声明勾选(B站正面支持; 首发实证教训: 「试试更多AI
  //    创作工具吧」推广 banner 误命中——匹配收紧: 声明类文案+控件
  //    形态+排除推广词; desc 软声明兜底不变)
  try {
    const labeled = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll(
        'label, div[role=checkbox], [class*=checkbox], [class*=switch], span'));
      const t = els.find(el => {
        const txt = (el.innerText
                     || el.getAttribute('aria-label') || '').trim();
        if (!txt || txt.length > 20 || el.children.length > 1) return false;
        if (/工具|试试|更多|立即使用/.test(txt)) return false;   // 推广排除
        return /(内容由)?AI(生成|创作|制作)|人工智能生成/.test(txt);
      });
      if (t) { t.scrollIntoView({ block: 'center' }); t.click(); return (t.innerText || '').slice(0, 30); }
      return '';
    });
    LOG(labeled ? 'AI 创作声明已点: [' + labeled + ']'
                : 'WARN AI声明控件未找到(收紧匹配后) — desc 软声明兜底');
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
