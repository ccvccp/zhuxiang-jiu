// douyin 登录专用(2026-10-02): ensureLogin 误判实证后的一次性登录窗口
// —— puppeteer pipe 同款启动(bot 历史稳定形态, 手动 Start-Process
//    Chrome 反复闪退实证), 打开 creator.douyin.com 后无条件等 8 分钟,
//    页面文本轮询登录特征(登录页: 扫码登录|验证码登录|密码登录;
//    后台: 内容管理|数据中心|创作服务——不用 URL 判定, 误判根因),
//    LOGIN_OK 后关浏览器(登录态已持久进 douyin-profile)
const puppeteer = require('puppeteer-core');
const LOG = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    userDataDir: 'd:\\网站架构设计\\channels-bot\\douyin-profile',
    headless: false,
    pipe: true,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--window-size=1280,900', '--no-first-run',
           '--no-default-browser-check', '--lang=zh-CN'],
  });
  LOG('chrome up (pipe mode, douyin-profile)');
  const page = (await browser.pages())[0] || await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto('https://creator.douyin.com/',
                 { waitUntil: 'domcontentloaded' });
  LOG('opened, waiting for QR login (up to 8 min) ...');
  const deadline = Date.now() + 8 * 60000;
  let ok = false;
  while (Date.now() < deadline) {
    await sleep(10000);
    try {
      const txt = await page.evaluate(
        () => (document.body.innerText || '').slice(0, 1500));
      const isLoginPage = /扫码登录|验证码登录|密码登录/.test(txt);
      const isBackend = /内容管理|数据中心|创作服务|作品发布/.test(txt);
      ok = !isLoginPage && isBackend;
      LOG('poll loginPage=' + isLoginPage + ' backend=' + isBackend
          + (ok ? ' => LOGIN_OK' : ''));
    } catch (e) { LOG('poll err: ' + e.message); }
    if (ok) break;
  }
  await sleep(5000);
  await browser.close().catch(() => {});
  LOG(ok ? 'LOGIN_CONFIRMED_AND_CLOSED' : 'TIMEOUT_NOT_LOGGED_IN');
  process.exit(ok ? 0 : 1);
})();
