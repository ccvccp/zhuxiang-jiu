// 抓取抖音创作者后台作品列表(标题+日期+作品链接) — 回执登记用
// 用法: node dy_fetch_urls.js
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const LOG = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\douyin-profile',
    headless: false, pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN'],
  });
  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto('https://creator.douyin.com/creator-micro/content/manage', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
  await sleep(15000);
  // 诊断: dump 页面文本状态
  const diag = await page.evaluate(() => (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 600));
  LOG('page text: ' + diag);
  // 尝试点「作品」tab(部分会话默认落在别的 tab)
  try {
    await page.evaluate(() => {
      const tab = Array.from(document.querySelectorAll('*')).find(e => (e.innerText || '').trim() === '作品' && e.offsetParent);
      if (tab) tab.click();
    });
    await sleep(5000);
  } catch (e) {}
  let listReady = false;
  try {
    await page.waitForSelector('a[href*="/video/"]', { timeout: 30000 });
    listReady = true;
  } catch (e) { LOG('video-link selector timeout'); }
  if (!listReady) {
    try { await page.waitForFunction(() => (document.body.innerText || '').includes('编辑作品'), { timeout: 15000 }); listReady = true; } catch (e) { LOG('edit-btn wait timeout'); }
  }
  await sleep(3000);
  // 滚动加载更多卡片
  for (let i = 0; i < 4; i++) { await page.evaluate(() => window.scrollBy(0, 900)).catch(() => {}); await sleep(1200); }

  if (!listReady) { await page.screenshot({ path: 'douyin_fetch_diag.png', fullPage: false }); LOG('diag screenshot saved'); }
  const items = await page.evaluate(() => {
    const out = [];
    // 作品卡片: 含「编辑作品」按钮的容器
    const editBtns = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText || '').trim() === '编辑作品');
    for (const btn of editBtns) {
      let card = btn;
      for (let i = 0; i < 25 && card.parentElement; i++) { card = card.parentElement; if (card.querySelector('a[href*="/video/"]')) break; }
      const links = card.querySelectorAll('a[href*="/video/"]');
      const href = links.length ? links[0].href : '';
      const text = (card.innerText || '').replace(/\s+/g, ' ').slice(0, 200);
      out.push({ href, text });
    }
    return out;
  });
  LOG('items: ' + items.length);
  fs.writeFileSync('douyin_video_urls.json', JSON.stringify(items, null, 2));
  for (const it of items) LOG((it.href || '(no-link)') + '  ::  ' + it.text.slice(0, 90));
  await browser.close();
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
