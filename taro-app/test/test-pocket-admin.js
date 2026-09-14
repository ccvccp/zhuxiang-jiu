/**
 * test-pocket-admin.js · 顺手赚钱管理界面前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-flash-admin.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(顺手赚钱升级):
 *   [API 层 api/pocket.ts]
 *   1. PocketAPI.uploadPhoto 请求体(data_b64 snake_case + fmt 提取)与 URL 返回
 *   2. PocketAdminAPI.listSites URL+admin 头+status 过滤参数
 *   3. PocketAdminAPI.invalidateSite URL+POST+reason 请求体
 *   4. PocketAdminAPI.getSettings 映射(数值化+enabled 布尔化)
 *   5. PocketAdminAPI.updateSettings PUT+字段透传
 *   6. mapAdminSite 字段映射(点位原始字段)
 *   [页面层 pages/pocket-admin]
 *   7. 两页签结构(点位管理/参数配置)
 *   8. 状态筛选(换行平铺)与作废操作
 *   9. 参数配置表单(奖励/阈值/天数)
 *   10. 照片缩略+预览
 *   [用户页 pages/pocket]
 *   11. 打卡照片上传链路(choosePhoto 上传后再提交)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'pocket.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'pocket-admin', 'index.tsx');
const POCKET_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'pocket', 'index.tsx');

const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/hub/media/image') {
    return { success: true, url: '/media/image/20260915-abc123def456.jpg',
             size: 10240, mediaType: 'image' };
  }
  if (url.startsWith('/api/pocket/admin/sites')
      && !opts.method && !/\/sites\/\d+/.test(url)) {
    return { sites: [
      { siteId: 1, memberId: 8, scene: 'hotel', posterType: 'poster',
        address: '某市某路某酒店大堂', photoUrl: '/media/image/a1.jpg',
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
  // 1. uploadPhoto
  const mediaUrl = await PocketAPI.uploadPhoto(
    'data:image/jpeg;base64,/9j/4AAQSkZJRg==');
  const up = requests[requests.length - 1];
  record('uploadPhoto 请求体(data_b64 snake_case+fmt)',
    up.url === '/api/hub/media/image' && up.method === 'POST'
    && up.data.data_b64 === '/9j/4AAQSkZJRg=='
    && up.data.fmt === 'jpg',
    JSON.stringify(up.data));
  record('uploadPhoto 返回静态 URL',
    mediaUrl === '/media/image/20260915-abc123def456.jpg');

  // 2. listSites
  const sites = await PocketAdminAPI.listSites({ status: 'active' });
  const ls = requests[requests.length - 1];
  record('listSites URL+admin 头+状态过滤',
    ls.url === '/api/pocket/admin/sites?status=active'
    && ls.headers['X-Role'] === 'admin');
  record('listSites 字段映射',
    sites.length === 2 && sites[0].siteId === 1 && sites[0].memberId === 8
    && sites[0].photoUrl === '/media/image/a1.jpg'
    && sites[1].status === 'invalid' && sites[1].monthRewardClaimed === true,
    JSON.stringify(sites[0]));

  // 3. invalidateSite
  await PocketAdminAPI.invalidateSite(1, '照片造假');
  const inv = requests[requests.length - 1];
  record('invalidateSite URL+POST+reason',
    inv.url === '/api/pocket/admin/sites/1/invalidate' && inv.method === 'POST'
    && inv.data.reason === '照片造假' && inv.headers['X-Role'] === 'admin');

  // 4. getSettings
  const st = await PocketAdminAPI.getSettings();
  record('getSettings 映射(数值化)',
    st.enabled === true && st.checkinReward === 2
    && st.aiScoreThreshold === 60 && st.minAddressLen === 5
    && typeof st.maxActiveSites === 'number');

  // 5. updateSettings
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
  // 8. 筛选+作废
  record('状态筛选+作废操作',
    pageCode.includes('STATUS_FILTERS') && pageCode.includes('invalidateSite')
    && pageCode.includes('filterActive'));
  // 9. 参数表单
  record('参数配置表单(奖励/阈值/天数)',
    pageCode.includes('checkinReward') && pageCode.includes('aiScoreThreshold')
    && pageCode.includes('durationDays') && pageCode.includes('minAddressLen'));
  // 10. 照片缩略+预览
  record('照片缩略+预览',
    pageCode.includes('photoThumb') && pageCode.includes('previewImage'));

  console.log('[用户页 pocket]');
  const userCode = fs.readFileSync(POCKET_SRC, 'utf-8');
  // 11. 上传链路
  record('打卡照片上传链路(先上传后提交)',
    userCode.includes('uploadPhoto') && userCode.includes("readFile")
    && userCode.includes('getFileSystemManager')
    && userCode.indexOf('PocketAPI.uploadPhoto') < userCode.indexOf('PocketAPI.checkin'));

  console.log('\n通过: ' + PASS + ' / 失败: ' + FAIL + ' / 总计: ' + (PASS + FAIL));
  process.exit(FAIL === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
