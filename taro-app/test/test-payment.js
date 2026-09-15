/**
 * test-payment.js · 支付拉起模块前端单元测试(P1-3)
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-points.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(api/payment.ts):
 *   1. mock 渠道: startPay 即 paid → completePay 直返(含 dispatch 透传)
 *   2. real·jsapi(weapp): 五元组 requestPayment 成功 → 轮询 paid
 *   3. real·jsapi(weapp): requestPayment 取消 → 关单(close) → cancelled
 *   4. real·H5 h5Url: 落 storage pending + window.location.href 跳转
 *   5. real·扫码 codeUrl: onQrCode 回调 → 轮询 paid
 *   6. real·超时: 一直 paying → timeout
 *   7. real·终态失败: getPay=failed → paid:false
 *   8. closePay: POST /close + reason
 *   9. resumePendingPay: 无挂起 null / paid 清除 / closed 清除
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'payment.ts');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];
// 支付单状态机: payNo → startPay 响应/getPay 响应(测试中动态改写)
const payState = {};
let requestPaymentBehavior = 'resolve';   // resolve | reject
let lastToast = null;
const storage = {};
const win = { location: { href: '' } };   // H5 window 桩

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  let m = url.match(/^\/api\/payment\/([^/]+)\/start$/);
  if (m && opts.method === 'POST') {
    const st = payState[m[1]] || {};
    return { payNo: m[1], status: st.startStatus || 'paying',
      statusName: st.startStatus || 'paying', channelMode: 'real',
      dispatch: st.dispatch, payParams: st.payParams || {
        channel: st.channel || 'wechat', method: 'h5',
        actualAmount: st.amount || 100, expireTime: '' } };
  }
  m = url.match(/^\/api\/payment\/([^/]+)\/close$/);
  if (m && opts.method === 'POST') {
    payState[m[1]] = { ...(payState[m[1]] || {}), status: 'closed' };
    return { success: true, payNo: m[1], status: 'closed' };
  }
  m = url.match(/^\/api\/payment\/([^/]+)$/);
  if (m) {
    const st = payState[m[1]] || {};
    return { payNo: m[1], orderId: st.orderId || 'WD-1',
      orderType: 'wallet_deposit', status: st.status || 'paying',
      statusName: st.status || '支付中', actualAmount: st.amount || 100,
      expireTime: '', dispatchError: '' };
  }
  return { success: true };
};

const mockTaro = {
  showToast: (o) => { lastToast = o; },
  setStorageSync: (k, v) => { storage[k] = v; },
  getStorageSync: (k) => storage[k] ?? '',
  removeStorageSync: (k) => { delete storage[k]; },
  requestPayment: () => requestPaymentBehavior === 'resolve'
    ? Promise.resolve('ok') : Promise.reject(new Error('requestPayment:fail cancel')),
  useDidShow: () => {},
};

const MOCKS = {
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, ...mockTaro,
  }),
};
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) {
    return MOCKS[request];
  }
  if (request === './request' || request.endsWith('/api/request')) {
    return { request: mockRequest };
  }
  return origLoad.apply(this, arguments);
};

// H5 window 桩(payment.ts 跳转分支判定 typeof window !== 'undefined')
global.window = win;

const compiled = ts.transpileModule(fs.readFileSync(API_SRC, 'utf-8'), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2019,
    esModuleInterop: true,
  },
  fileName: API_SRC,
});
const out = path.join(os.tmpdir(), `payment-api-${process.pid}.js`);
fs.writeFileSync(out, compiled.outputText);
const mod = require(out);
const { PaymentAPI, completePay, invokePayment, resumePendingPay } = mod;

// ============================================================
// 2. 断言
// ============================================================
let PASS = 0, FAIL = 0;
const RESULTS = [];
function check(name, cond, detail = '') {
  if (cond) { PASS++; RESULTS.push(`  [PASS] ${name}`); }
  else { FAIL++; RESULTS.push(`[FAIL] ${name} ${detail}`); }
}
const settle = (ms = 30) => new Promise(r => setTimeout(r, ms));

async function main() {
  // ---------- 1. mock 渠道: startPay 即 paid ----------
  payState['PAY_M1'] = { startStatus: 'paid', dispatch: { granted: true,
    business: { action: 'purchased' } } };
  let r = await completePay('PAY_M1');
  check('mock 渠道: 即时 paid+dispatch 透传',
    r.paid === true && r.status === 'paid'
    && r.dispatch?.business?.action === 'purchased', JSON.stringify(r));

  // ---------- 2. real·jsapi(weapp) 成功 ----------
  process.env.TARO_ENV = 'weapp';
  requestPaymentBehavior = 'resolve';
  payState['PAY_J1'] = { startStatus: 'paying', status: 'paying',
    payParams: { channel: 'wechat', method: 'jsapi', appId: 'wxAPP',
      timeStamp: '1', nonceStr: 'n', package: 'prepay_id=x',
      signType: 'RSA', paySign: 'sig==' } };
  const p51 = completePay('PAY_J1');
  await settle(50);
  payState['PAY_J1'].status = 'paid';    // 模拟渠道回调落账
  r = await p51;
  check('jsapi: requestPayment 成功 → 轮询 paid',
    r.paid === true && r.status === 'paid', JSON.stringify(r));

  // ---------- 3. real·jsapi 取消 → close ----------
  requestPaymentBehavior = 'reject';
  payState['PAY_J2'] = { startStatus: 'paying', status: 'paying',
    payParams: { channel: 'wechat', method: 'jsapi', timeStamp: '1',
      nonceStr: 'n', package: 'p', signType: 'RSA', paySign: 's' } };
  r = await completePay('PAY_J2');
  await settle(50);
  const closeReq = requests.find(q => (q.url || '').includes('PAY_J2/close'));
  check('jsapi: 取消 → cancelled + 关单',
    r.paid === false && r.status === 'cancelled'
    && closeReq && closeReq.data.reason === 'USER_CANCEL',
    JSON.stringify(r));

  // ---------- 4. real·H5 h5Url 跳转 ----------
  process.env.TARO_ENV = 'h5';
  requestPaymentBehavior = 'resolve';
  payState['PAY_H1'] = { startStatus: 'paying', status: 'paying',
    payParams: { channel: 'wechat', method: 'h5',
      h5Url: 'https://wx.tenpay.com/h5pay1' } };
  // completePay 会跳转并持续轮询——只验证拉起副作用(不 await 全程)
  const p54 = completePay('PAY_H1', { timeoutMs: 10 });
  await settle(50);
  check('H5 h5: 跳转 h5Url + storage 落挂起单',
    win.location.href === 'https://wx.tenpay.com/h5pay1'
    && storage.pending_pay_no === 'PAY_H1',
    `href=${win.location.href} pending=${storage.pending_pay_no}`);
  await p54;   // 等轮询超时退出(timeoutMs=10 → 一次 2s sleep)

  // ---------- 5. real·扫码 codeUrl ----------
  let qrGot = '';
  payState['PAY_Q1'] = { startStatus: 'paying', status: 'paying',
    payParams: { channel: 'wechat', method: 'native',
      codeUrl: 'weixin://wxpay/bizpayurl?pr=abc' } };
  const p55 = completePay('PAY_Q1', {
    onQrCode: code => { qrGot = code; },
    timeoutMs: 60000,
  });
  await settle(50);
  payState['PAY_Q1'].status = 'paid';
  r = await p55;
  check('扫码: onQrCode 回调 + 轮询 paid',
    qrGot === 'weixin://wxpay/bizpayurl?pr=abc' && r.paid === true,
    `qr=${qrGot} r=${JSON.stringify(r)}`);

  // ---------- 6. real·超时 ----------
  payState['PAY_T1'] = { startStatus: 'paying', status: 'paying',
    payParams: { channel: 'wechat', method: 'native', codeUrl: 'c' } };
  r = await completePay('PAY_T1', { timeoutMs: 10 });
  check('超时: 一直 paying → timeout',
    r.paid === false && r.status === 'timeout', JSON.stringify(r));

  // ---------- 7. real·终态失败 ----------
  payState['PAY_F1'] = { startStatus: 'paying', status: 'failed',
    payParams: { channel: 'alipay', method: 'wap', payUrl: 'https://m.alipay.com/x' } };
  // payUrl 跳转会设置 location——改用扫码方式避免跳转, 直接验终态
  payState['PAY_F1'].payParams = { channel: 'alipay', method: 'qr',
    qrCode: 'https://qr.alipay.com/xy' };
  r = await completePay('PAY_F1');
  check('终态失败: getPay=failed → paid:false',
    r.paid === false && r.status === 'failed', JSON.stringify(r));

  // ---------- 8. closePay ----------
  requests.length = 0;
  await PaymentAPI.closePay('PAY_C1', 'USER_CANCEL');
  const cr = requests.find(q => (q.url || '').includes('PAY_C1/close'));
  check('closePay: POST /close + reason',
    cr && cr.method === 'POST' && cr.data.reason === 'USER_CANCEL');

  // ---------- 9. resumePendingPay ----------
  storage.pending_pay_no = '';
  r = await resumePendingPay();
  check('恢复: 无挂起 → null', r === null);

  storage.pending_pay_no = 'PAY_R1';
  payState['PAY_R1'] = { status: 'paid' };
  r = await resumePendingPay();
  check('恢复: paid → 清除挂起',
    r?.paid === true && !storage.pending_pay_no);

  storage.pending_pay_no = 'PAY_R2';
  payState['PAY_R2'] = { status: 'closed' };
  r = await resumePendingPay();
  check('恢复: closed → 清除挂起',
    r?.paid === false && r?.status === 'closed'
    && !storage.pending_pay_no);

  console.log(RESULTS.join('\n'));
  console.log('-'.repeat(64));
  console.log(`通过 ${PASS} 项 / 失败 ${FAIL} 项`);
  process.exit(FAIL === 0 ? 0 : 1);
}

main();
