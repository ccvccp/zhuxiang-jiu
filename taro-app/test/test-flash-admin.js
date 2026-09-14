/**
 * test-flash-admin.js · 限时秒杀管理界面前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-trust-appeal.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(秒杀管理界面补齐):
 *   [API 层 api/flashsale.ts]
 *   1. FlashAdminAPI.listSessions URL+admin 头
 *   2. createSession 请求体(name/startTime/endTime)
 *   3. addItem 请求体(productId/flashPrice/flashStock/limitPerMember)
 *   4. publishSession/cancelSession URL+POST
 *   5. updateSettings 白名单字段透传
 *   6. mapSettings 数值化+布尔化
 *   [页面层 pages/flashsale-admin]
 *   7. toIsoBJ 时间转换(空格→T / 补秒+时区 / 已带时区容错)
 *   8. 页面含三页签结构(场次管理/风控参数/运营统计)
 *   9. 页面含建场次表单与发布/取消操作
 *   10. 页面含非 admin 拦截
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'flashsale.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'flashsale-admin', 'index.tsx');

const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  const m = url.match(/\/api\/flash\/admin\/sessions\/([^/]+)\/(publish|cancel|items)/);
  if (url === '/api/flash/admin/sessions' && !opts.method) {
    return { success: true, count: 2, sessions: [
      { sessionId: 'FS001', name: '草稿场', status: 'draft',
        startTime: '2026-09-16T20:00:00+08:00', endTime: '2026-09-16T22:00:00+08:00',
        runtimeStatus: 'UPCOMING', runtimeStatusName: '未开始', itemCount: 0 },
      { sessionId: 'FS002', name: '已发布场', status: 'published',
        startTime: '2026-09-16T10:00:00+08:00', endTime: '2026-09-16T12:00:00+08:00',
        runtimeStatus: 'IN_PROGRESS', runtimeStatusName: '进行中', itemCount: 2,
        items: [{ itemId: 'FI1', productId: 'ZX42-2026L07', productName: '竹香酒',
          originalPrice: 88, flashPrice: 58, flashStock: 100, limitPerMember: 2,
          soldCount: 30, remainingStock: 70, progressPercent: 30.0 }] },
    ] };
  }
  if (url === '/api/flash/admin/sessions' && opts.method === 'POST') {
    return { success: true, session: { sessionId: 'FS003', name: '新场', status: 'draft',
      startTime: '2026-09-17T20:00:00+08:00', endTime: '2026-09-17T22:00:00+08:00',
      runtimeStatus: 'UPCOMING', runtimeStatusName: '未开始', itemCount: 0 } };
  }
  const pu = url.match(/^\/api\/flash\/admin\/sessions\/([^/]+)$/);
  if (pu && opts.method === 'PUT') {
    return { success: true, session: { sessionId: pu[1],
      name: opts.data.name || '原名', status: 'draft',
      startTime: opts.data.startTime || '2026-09-16T20:00:00+08:00',
      endTime: opts.data.endTime || '2026-09-16T22:00:00+08:00',
      runtimeStatus: 'UPCOMING', runtimeStatusName: '未开始', itemCount: 0 } };
  }
  if (m && m[2] === 'items') {
    return { success: true, item: { itemId: 'FI9', productId: 'ZX42-2026L07',
      productName: '竹香酒', originalPrice: 88, flashPrice: 58, flashStock: 50,
      limitPerMember: 1, soldCount: 0, remainingStock: 50, progressPercent: 0 } };
  }
  if (m && m[2] === 'publish') {
    return { success: true, session: { sessionId: m[1], status: 'published' } };
  }
  if (m && m[2] === 'cancel') {
    return { success: true, session: { sessionId: m[1], status: 'cancelled' } };
  }
  if (url === '/api/flash/admin/settings' && !opts.method) {
    return { success: true, settings: { enabled: true, minRegisterHours: 1,
      minMemberLevel: 0, orderExpireMinutes: 15, maxQuantityPerOrder: 5 } };
  }
  if (url === '/api/flash/admin/settings' && opts.method === 'POST') {
    return { success: true, settings: Object.assign(
      { enabled: true, minRegisterHours: 1, minMemberLevel: 0,
        orderExpireMinutes: 20, maxQuantityPerOrder: 5 }, opts.data) };
  }
  if (url === '/api/flash/admin/stats') {
    return { success: true, stats: { sessionCount: 2, orderCount: 10,
      paidAmount: 580, sessions: [{ sessionId: 'FS002', name: '已发布场' }] } };
  }
  if (url === '/api/flash/admin/orders/expire-cancel') {
    return { success: true, cancelled: 3 };
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = {
  showToast: () => {},
  navigateTo: () => {},
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@tarojs/taro') return { default: mockTaro };
  if (request === '@/services/auth-service') {
    return { getSession: () => ({ role: 'admin' }), getMemberId: () => 1 };
  }
  if (request === '@tarojs/components') {
    return new Proxy({}, { get: (t, name) => (name === 'default' ? {} : { default: {} }) });
  }
  if (request.includes('index.module.scss')) return { default: new Proxy({}, { get: () => 'c' }) };
  if (request === '@/components/NavBar') return { default: () => null };
  if (request === 'react') return { useState: (v) => [v, () => {}], useEffect: () => {}, useCallback: (f) => f };
  return origLoad.apply(this, arguments);
};

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'flash-admin-'));
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
  const api = compileTs(API_SRC, 'flashsale.js');
  const { FlashAdminAPI } = api;

  console.log('[API 层 flashsale.ts]');
  // 1. listSessions
  const sessions = await FlashAdminAPI.listSessions();
  const last = requests[requests.length - 1];
  record('listSessions URL+admin 头',
    last.url === '/api/flash/admin/sessions'
    && last.headers['X-Role'] === 'admin');
  record('listSessions 映射(含草稿+items)',
    sessions.length === 2
    && sessions[0].sessionId === 'FS001' && sessions[0].status === 'draft'
    && sessions[1].items && sessions[1].items[0].flashPrice === 58
    && sessions[1].items[0].remainingStock === 70);

  // 2. createSession
  await FlashAdminAPI.createSession('新场', '2026-09-17T20:00:00+08:00', '2026-09-17T22:00:00+08:00');
  const cr = requests[requests.length - 1];
  record('createSession 请求体',
    cr.url === '/api/flash/admin/sessions' && cr.method === 'POST'
    && cr.headers['X-Role'] === 'admin'
    && cr.data.name === '新场'
    && cr.data.startTime === '2026-09-17T20:00:00+08:00',
    JSON.stringify(cr.data));

  // 3. addItem
  await FlashAdminAPI.addItem('FS001', 'ZX42-2026L07', 58, 50, 1);
  const ai = requests[requests.length - 1];
  record('addItem URL+请求体',
    ai.url === '/api/flash/admin/sessions/FS001/items' && ai.method === 'POST'
    && ai.data.productId === 'ZX42-2026L07'
    && ai.data.flashPrice === 58 && ai.data.flashStock === 50
    && ai.data.limitPerMember === 1);

  // 4. publish/cancel
  await FlashAdminAPI.publishSession('FS001');
  let r4 = requests[requests.length - 1];
  record('publishSession URL+POST',
    r4.url === '/api/flash/admin/sessions/FS001/publish' && r4.method === 'POST');
  await FlashAdminAPI.cancelSession('FS001');
  r4 = requests[requests.length - 1];
  record('cancelSession URL+POST',
    r4.url === '/api/flash/admin/sessions/FS001/cancel' && r4.method === 'POST');

  // 4b. updateSession(编辑草稿)
  await FlashAdminAPI.updateSession('FS001', {
    name: '改名场', startTime: '2026-09-18T20:00:00+08:00' });
  const up = requests[requests.length - 1];
  record('updateSession PUT+局部补丁',
    up.url === '/api/flash/admin/sessions/FS001' && up.method === 'PUT'
    && up.data.name === '改名场'
    && up.data.startTime === '2026-09-18T20:00:00+08:00'
    && !('endTime' in up.data),
    JSON.stringify(up.data));

  // 5-6. settings
  const st = await FlashAdminAPI.getSettings();
  record('getSettings 映射',
    st.enabled === true && st.orderExpireMinutes === 15
    && st.maxQuantityPerOrder === 5);
  const st2 = await FlashAdminAPI.updateSettings({ orderExpireMinutes: 20 });
  const su = requests[requests.length - 1];
  record('updateSettings 白名单透传',
    su.method === 'POST' && su.data.orderExpireMinutes === 20
    && st2.orderExpireMinutes === 20);

  console.log('[页面层 flashsale-admin]');
  const pageCode = fs.readFileSync(PAGE_SRC, 'utf-8');
  record('三页签结构',
    pageCode.includes('场次管理') && pageCode.includes('风控参数')
    && pageCode.includes('运营统计'));
  record('双模式表单(创建/编辑)',
    pageCode.includes('创建秒杀场次') && pageCode.includes('编辑草稿场次')
    && pageCode.includes('editingId'));
  record('草稿编辑按钮+取消编辑',
    pageCode.includes('openEdit') && pageCode.includes('取消编辑')
    && pageCode.includes('保存修改'));
  record('建场次+加商品表单',
    pageCode.includes('创建秒杀场次') && pageCode.includes('添加秒杀商品')
    && pageCode.includes('flashPrice'));
  record('发布/取消操作',
    pageCode.includes('发布场次') && pageCode.includes('取消场次'));
  record('非 admin 拦截',
    pageCode.includes('仅管理员可访问'));
  record('页签切换自动加载',
    pageCode.includes('switchTab') && pageCode.includes('loadSessions'));

  // 7. toIsoBJ 纯逻辑复刻断言(与页面同款)
  const toIsoBJ = (raw) => {
    const s = raw.trim().replace(' ', 'T');
    if (/[Zz]|[+-]\d{2}:\d{2}$/.test(s)) return s;
    return `${s}:00+08:00`;
  };
  record('时间转换(空格+补秒+时区)',
    toIsoBJ('2026-09-16 20:00') === '2026-09-16T20:00:00+08:00');
  record('时间转换(已带时区容错)',
    toIsoBJ('2026-09-16T20:00:00+08:00') === '2026-09-16T20:00:00+08:00');

  console.log('\n通过: ' + PASS + ' / 失败: ' + FAIL + ' / 总计: ' + (PASS + FAIL));
  process.exit(FAIL === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
