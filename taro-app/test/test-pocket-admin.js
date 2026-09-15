/**
 * test-pocket-admin.js · 顺手赚钱管理界面前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-flash-admin.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(顺手赚钱·现场拍照指纹防刷):
 *   [API 层 api/pocket.ts]
 *   1. 不再上传图片本体(无 uploadPhoto 方法)
 *   2. reportSite/checkin 提交 photoUrl=指纹(sha256:hex64)
 *   3. PocketAdminAPI.listSites URL+admin 头+status 过滤参数
 *   4. PocketAdminAPI.invalidateSite URL+POST+reason 请求体
 *   5. PocketAdminAPI.getSettings 映射(数值化+enabled 布尔化)
 *   6. PocketAdminAPI.updateSettings PUT+字段透传
 *   [页面层 pages/pocket-admin]
 *   7. 两页签结构(点位管理/参数配置)
 *   8. 指纹徽章(点击复制)+状态筛选+作废
 *   [用户页 pages/pocket]
 *   9. 现场拍照(camera only)+指纹链路(dataUrlToSha256)
 *   [行为层 utils/image-reader]
 *   10. dataUrlToSha256 真实哈希(与 Node crypto 对账)
 *   11. H5 路径 originalFileObj→FileReader→dataUrl
 *   12. FSM 桩防护(Promise 无 readFile→受控 reject)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'pocket.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'pocket-admin', 'index.tsx');
const POCKET_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'pocket', 'index.tsx');
const READER_SRC = path.resolve(__dirname, '..', 'src', 'utils', 'image-reader.ts');
const THEME_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'theme-admin', 'index.tsx');

const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/pocket/site/report' && opts.method === 'POST') {
    return { success: true, site: { siteId: 9, scene: opts.data.scene,
      posterType: 'poster', address: opts.data.address,
      photoUrl: opts.data.photoUrl, checkinCount: 1, consecutiveDays: 1,
      status: 'active', monthRewardClaimed: false, aiScoreLatest: 65 } };
  }
  if (/^\/api\/pocket\/site\/\d+\/checkin$/.test(url) && opts.method === 'POST') {
    return { success: true, checkin: { checkinId: 1,
      photoUrl: opts.data.photoUrl, aiScore: 65, rewardAmount: 2 } };
  }
  if (url.startsWith('/api/pocket/admin/sites')
      && !opts.method && !/\/sites\/\d+/.test(url)) {
    return { sites: [
      { siteId: 1, memberId: 8, scene: 'hotel', posterType: 'poster',
        address: '某市某路某酒店大堂',
        photoUrl: 'sha256:' + 'a1'.repeat(32),
        postedAt: '2026-09-10T10:00:00+00:00', lastCheckinAt: '2026-09-14T09:00:00+00:00',
        checkinCount: 5, consecutiveDays: 5, status: 'active',
        monthRewardClaimed: false, aiScoreLatest: 90 },
      { siteId: 2, memberId: 9, scene: 'taxi_rear', posterType: 'sticker',
        address: '某市出租车后窗', photoUrl: '', postedAt: '2026-08-01T10:00:00+00:00',
        lastCheckinAt: '2026-09-01T09:00:00+00:00', checkinCount: 30,
        consecutiveDays: 0, status: 'invalid', monthRewardClaimed: true,
        aiScoreLatest: 75 },
    ] };
  }
  if (/^\/api\/pocket\/admin\/sites\/\d+\/invalidate$/.test(url)) {
    return { success: true, siteId: 1, status: 'invalid',
             reason: opts.data.reason };
  }
  if (url === '/api/pocket/admin/settings' && !opts.method) {
    return { enabled: true, checkinReward: 2, monthRewardPoster: 20,
             monthRewardSticker: 30, maxActiveSites: 5,
             aiScoreThreshold: 60, durationDays: 30, minAddressLen: 5,
             updatedAt: '2026-09-15T06:00:00+00:00' };
  }
  if (url === '/api/pocket/admin/settings' && opts.method === 'PUT') {
    return Object.assign({ enabled: true, checkinReward: 2,
      monthRewardPoster: 20, monthRewardSticker: 30, maxActiveSites: 5,
      aiScoreThreshold: 60, durationDays: 30, minAddressLen: 5,
      updatedAt: '2026-09-15T07:00:00+00:00' }, opts.data);
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = { showToast: () => {} };

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@tarojs/taro') return { default: mockTaro };
  if (request === '@tarojs/components') {
    return new Proxy({}, { get: (t, name) => (name === 'default' ? {} : { default: {} }) });
  }
  if (request.includes('index.module.scss')) return { default: new Proxy({}, { get: () => 'c' }) };
  if (request === '@/components/NavBar') return { default: () => null };
  if (request === 'react') return { useState: (v) => [v, () => {}], useEffect: () => {}, useCallback: (f) => f };
  return origLoad.apply(this, arguments);
};

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pocket-admin-'));
const compileTs = (srcFile, outName) => {
  const code = fs.readFileSync(srcFile, 'utf-8');
  const js = ts.transpileModule(code, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React,
      target: ts.ScriptTarget.ES2019, esModuleInterop: true,
    },
  }).outputText;
  const outFile = path.join(tmpDir, outName);
  fs.writeFileSync(outFile, js);
  return require(outFile);
};

let PASS = 0, FAIL = 0;
const record = (name, ok, detail = '') => {
  if (ok) { PASS++; console.log('  ✓ ' + name); }
  else { FAIL++; console.log('  ✗ ' + name + ' — ' + detail); }
};

(async () => {
  const api = compileTs(API_SRC, 'pocket.js');
  const { PocketAPI, PocketAdminAPI } = api;

  console.log('[API 层 pocket.ts]');
  // 1. 不再上传图片本体(无 uploadPhoto 方法)
  record('无 uploadPhoto(图片本体不上传)',
    typeof PocketAPI.uploadPhoto === 'undefined');

  // 2. reportSite/checkin 提交指纹
  await PocketAPI.reportSite('hotel', 'XX市酒店大堂', 'sha256:' + 'ab'.repeat(32));
  const rp = requests[requests.length - 1];
  record('reportSite 提交 photoUrl=指纹',
    rp.url === '/api/pocket/site/report' && rp.method === 'POST'
    && rp.data.photoUrl === 'sha256:' + 'ab'.repeat(32)
    && rp.data.scene === 'hotel',
    JSON.stringify(rp.data));
  await PocketAPI.checkin(3, 'sha256:' + 'cd'.repeat(32));
  const ck = requests[requests.length - 1];
  record('checkin 提交 photoUrl=指纹',
    ck.url === '/api/pocket/site/3/checkin' && ck.method === 'POST'
    && ck.data.photoUrl === 'sha256:' + 'cd'.repeat(32),
    JSON.stringify(ck.data));

  // 3. listSites
  const sites = await PocketAdminAPI.listSites({ status: 'active' });
  const ls = requests[requests.length - 1];
  record('listSites URL+admin 头+状态过滤',
    ls.url === '/api/pocket/admin/sites?status=active'
    && ls.headers['X-Role'] === 'admin');
  record('listSites 字段映射(指纹透传)',
    sites.length === 2 && sites[0].siteId === 1 && sites[0].memberId === 8
    && sites[0].photoUrl === 'sha256:' + 'a1'.repeat(32)
    && sites[1].status === 'invalid' && sites[1].monthRewardClaimed === true,
    JSON.stringify(sites[0]));

  // 4. invalidateSite
  await PocketAdminAPI.invalidateSite(1, '照片造假');
  const inv = requests[requests.length - 1];
  record('invalidateSite URL+POST+reason',
    inv.url === '/api/pocket/admin/sites/1/invalidate' && inv.method === 'POST'
    && inv.data.reason === '照片造假' && inv.headers['X-Role'] === 'admin');

  // 5. getSettings
  const st = await PocketAdminAPI.getSettings();
  record('getSettings 映射(数值化)',
    st.enabled === true && st.checkinReward === 2
    && st.aiScoreThreshold === 60 && st.minAddressLen === 5
    && typeof st.maxActiveSites === 'number');

  // 6. updateSettings
  const st2 = await PocketAdminAPI.updateSettings({ checkinReward: 3 });
  const su = requests[requests.length - 1];
  record('updateSettings PUT+透传',
    su.method === 'PUT' && su.data.checkinReward === 3
    && st2.checkinReward === 3);

  console.log('[页面层 pocket-admin]');
  const pageCode = fs.readFileSync(PAGE_SRC, 'utf-8');
  // 7. 两页签
  record('两页签结构',
    pageCode.includes('点位管理') && pageCode.includes('参数配置'));
  // 8. 指纹徽章+筛选+作废
  record('指纹徽章(点击复制)+筛选+作废',
    pageCode.includes('hashBadge') && pageCode.includes('copyHash')
    && pageCode.includes('STATUS_FILTERS') && pageCode.includes('invalidateSite')
    && !pageCode.includes('previewImage'));

  console.log('[用户页 pocket]');
  const userCode = fs.readFileSync(POCKET_SRC, 'utf-8');
  const readerCode = fs.readFileSync(READER_SRC, 'utf-8');
  // 9. 现场拍照(H5 getUserMedia 取景 + 小程序 camera only)+指纹链路
  record('H5 摄像头取景(getUserMedia——无文件选择入口)',
    userCode.includes('getUserMedia')
    && userCode.includes('camOpen') && userCode.includes('captureFrame')
    && userCode.includes('pocket-cam-box'));
  record('小程序端 camera only(禁相册)',
    /sourceType:\s*\['camera'\]/.test(userCode)
    && !/sourceType:\s*\['album'/.test(userCode));
  record('拍照→指纹→打卡链路',
    userCode.includes('dataUrlToSha256')
    && userCode.includes('chooseImageAsDataUrl')
    && !userCode.includes('uploadPhoto')
    && !userCode.includes('hub/media/image'));
  record('跨端读取工具(H5 FileReader + 小程序 FSM 双轨)',
    readerCode.includes('originalFileObj')
    && readerCode.includes('readAsDataURL')
    && readerCode.includes('getFileSystemManager')
    && readerCode.includes("typeof fsm?.readFile !== 'function'"));
  record('用户页不再直调 FSM',
    !userCode.includes('Taro.getFileSystemManager'));
  const themeCode = fs.readFileSync(THEME_SRC, 'utf-8');
  record('theme-admin 图标上传仍走跨端工具(不受影响)',
    themeCode.includes('chooseImageAsDataUrl')
    && !themeCode.includes('Taro.getFileSystemManager'));

  console.log('[行为层 utils/image-reader]');
  const readerMod = compileTs(READER_SRC, 'image-reader.js');
  const { chooseImageAsDataUrl, dataUrlToSha256 } = readerMod;

  // 10. dataUrlToSha256 真实哈希(与 Node crypto 对账)
  // (Node 18 全局无 webcrypto——浏览器原生有, 测试以 webcrypto 注入模拟)
  const nodeCrypto = require('crypto');
  if (typeof globalThis.crypto === 'undefined' || !globalThis.crypto?.subtle) {
    globalThis.crypto = nodeCrypto.webcrypto;
  }
  const expected = 'sha256:' + nodeCrypto.createHash('sha256')
    .update(Buffer.from([0, 0, 0])).digest('hex');
  const actual = await dataUrlToSha256('data:image/png;base64,AAAA');
  record('dataUrlToSha256 真实哈希(Node crypto 对账)',
    actual === expected && /^sha256:[0-9a-f]{64}$/.test(actual),
    `${actual} vs ${expected}`);

  // 11. H5 路径: originalFileObj + FileReader
  global.FileReader = class {
    constructor() { this.result = 'data:image/png;base64,AAAA'; }
    readAsDataURL() { setTimeout(() => this.onload && this.onload(), 0); }
    onerror = null; onload = null;
  };
  const h5Res = await chooseImageAsDataUrl({
    tempFilePaths: ['blob:https://zxjiu.com/xyz'],
    tempFiles: [{ path: 'blob:https://zxjiu.com/xyz', size: 100,
                  originalFileObj: { name: 'a.png' } }],
  });
  record('H5 路径(originalFileObj→FileReader→dataUrl)',
    h5Res === 'data:image/png;base64,AAAA', String(h5Res));

  // 12. FSM 桩防护: getFileSystemManager 返回 Promise(H5 生产桩行为)
  // 编译后经 taro_1.default 引用(Module._load 拦截返回 { default: mockTaro })
  const taroPkg = require('@tarojs/taro');
  taroPkg.default.getFileSystemManager = () => Promise.resolve({});
  let rejected = null;
  await chooseImageAsDataUrl({
    tempFilePaths: ['wxfile://tmp/a.jpg'],
    tempFiles: [{ path: 'wxfile://tmp/a.jpg', size: 100 }],
  }).catch(e => { rejected = e; });
  record('FSM 桩防护(Promise 无 readFile→受控 reject)',
    rejected && rejected.name === 'ImageReadError', String(rejected));
  delete taroPkg.default.getFileSystemManager;

  console.log('\n通过: ' + PASS + ' / 失败: ' + FAIL + ' / 总计: ' + (PASS + FAIL));
  process.exit(FAIL === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
