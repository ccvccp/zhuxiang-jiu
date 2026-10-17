// 视频号助手登录循环取证: 连接 9222 专用 Chrome, 抓登录页 console/网络 90 秒
const puppeteer = require('puppeteer-core');

(async () => {
  const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222' });
  const pages = await browser.pages();
  let page = pages.find(p => p.url().includes('channels.weixin'));
  if (!page) {
    page = await browser.newPage();
    await page.goto('https://channels.weixin.qq.com/login.html', { waitUntil: 'domcontentloaded' });
  }
  console.log('URL:', page.url());
  console.log('UA:', await page.evaluate(() => navigator.userAgent));

  page.on('console', m => {
    const t = m.text() || '';
    if (/send data|login|auth|socket|qrcode|qr_code|wxcode/i.test(t)) {
      console.log('[console]', m.type(), t.slice(0, 400));
    }
  });
  page.on('response', r => {
    const u = r.url();
    if (/login|long\.|qrcode|auth|jslogin|scan/i.test(u)) {
      console.log('[resp]', r.status(), u.slice(0, 160));
    }
  });
  page.on('requestfailed', r => {
    const u = r.url();
    if (/login|long\.|qrcode|auth/i.test(u)) {
      console.log('[fail]', r.failure() && r.failure().errorText, u.slice(0, 160));
    }
  });

  // QR 刷新信号: 每 5 秒报当前页面上二维码相关元素计数
  const started = Date.now();
  while (Date.now() - started < 90000) {
    await new Promise(r => setTimeout(r, 5000));
    try {
      const state = await page.evaluate(() => ({
        url: location.href,
        imgs: document.querySelectorAll('img').length,
        canvas: document.querySelectorAll('canvas').length,
        iframes: document.querySelectorAll('iframe').length,
      }));
      console.log('[tick]', JSON.stringify(state));
    } catch (e) {
      console.log('[tick] nav:', String(e).slice(0, 80));
    }
  }
  await browser.disconnect();
  console.log('CAPTURE_DONE');
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
