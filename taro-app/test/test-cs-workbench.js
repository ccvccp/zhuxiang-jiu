/**
 * test-cs-workbench.js · 客服工作台 + 聊天实时升级 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-av62.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖:
 *   [API 层 api/cs-chat.ts + api/chat.ts]
 *   1. 字典-排队三态(waiting/transferring/human_chatting)
 *   2. 方法完备(7 端点: queue/accept/reply/messages/close/stats/mode)
 *   3. URL-排队 query(status) + admin 头注入(X-Role + Bearer)
 *   4. URL-增量轮询(messages since_message_id 拼接)
 *   5. 用户端增量 API(messagesSince/markRead —— P1 轮询配套)
 *   [页面层 pages/cs-workbench/index.tsx]
 *   6. 四页签结构(排队/我的会话/聊天窗/统计)
 *   7. hero 卡口径(3s 实时/敏感词同口径/AI 灰度不影响人工侧)
 *   8. 排队页流(chips + 会话行 + 接入按钮)
 *   9. 接入流(accept POST → 切聊天窗)
 *   10. 聊天窗渲染(消息行/客服侧右对齐)
 *   11. 客服回复流(reply POST + 服务端重载)
 *   12. 统计页流(总会话/AI解决率/满意度 + 灰度态)
 *   13. 渲染确定性(两次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'cs-chat.ts');
const CHAT_API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'chat.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'cs-workbench', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const MOCK_SESSIONS = [
  { sessionId: 'CS9001', userId: 1001, sessionType: 'presale',
    status: 'human_chatting', aiConfidence: 0.3, satisfaction: 0,
    unresolvedCount: 2, createdAt: '2026-09-14T10:00:00', endedAt: null },
  { sessionId: 'CS9002', userId: 1002, sessionType: 'aftersale',
    status: 'human_chatting', aiConfidence: 0.4, satisfaction: 0,
    unresolvedCount: 1, createdAt: '2026-09-14T11:00:00', endedAt: null },
];
const MOCK_MESSAGES = [
  { id: 5, sessionId: 'CS9001', senderType: 'system', senderId: 0,
    messageType: 'text', content: '您好, 欢迎咨询竹香酒官方客服',
    aiConfidence: null, createdAt: '2026-09-14T10:00:05' },
  { id: 6, sessionId: 'CS9001', senderType: 'user', senderId: 1001,
    messageType: 'text', content: '退款怎么办',
    aiConfidence: null, createdAt: '2026-09-14T10:01:00' },
  { id: 8, sessionId: 'CS9001', senderType: 'customer_service', senderId: 1,
    messageType: 'text', content: '已接入, 请提供订单号',
    aiConfidence: null, createdAt: '2026-09-14T10:02:00' },
];
const MOCK_STATS = {
  totalSessions: 12, statusDistribution: { ai_chatting: 5, human_chatting: 3, ended: 4 },
  aiResolutionRate: 0.5, avgSatisfaction: 4.5,
};
const MOCK_MODE = { mode: 'off', source: 'env', paused: false,
  override: '', envMode: 'off' };

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  const method = (opts.method || 'GET').toUpperCase();
  if (url.startsWith('/api/chat/cs/queue')) {
    return { success: true, data: MOCK_SESSIONS, count: MOCK_SESSIONS.length };
  }
  if (/\/api\/chat\/cs\/sessions\/(.+)\/accept$/.test(url) && method === 'POST') {
    return { success: true, data: { sessionId: 'CS9001',
      customerServiceId: 1, acceptedAt: '2026-09-14T10:01:30',
      alreadyAssigned: false } };
  }
  if (/\/api\/chat\/cs\/sessions\/(.+)\/reply$/.test(url) && method === 'POST') {
    return { success: true, data: { sessionId: 'CS9001',
      messageId: 9, customerServiceId: 1 } };
  }
  if (/\/api\/chat\/sessions\/(.+)\/messages/.test(url)) {
    if (url.includes('since_message_id=6')) {
      return { success: true, data: [MOCK_MESSAGES[2]], count: 1 };
    }
    return { success: true, data: MOCK_MESSAGES, count: MOCK_MESSAGES.length };
  }
  if (/\/api\/chat\/sessions\/(.+)\/close$/.test(url) && method === 'POST') {
    return { success: true, data: { sessionId: 'CS9001', status: 'ended' } };
  }
  if (url === '/api/chat/stats') return { success: true, data: MOCK_STATS };
  if (url === '/api/chat/mode') return { success: true, data: MOCK_MODE };
  return { success: true, data: {} };
};

// 极简 React hooks 运行时(对齐 test-av62.js)
function miniReact() {
  let hookIdx = 0;
  let renderFn = null;
  let renderProps = null;
  const states = [];
  const dirty = { v: false };
  const setStateAt = (i) => (v) => {
    states[i] = typeof v === 'function' ? v(states[i]) : v;
    dirty.v = true;
  };
  const effects = [];
  const api = {
    useState: (init) => {
      const i = hookIdx++;
      if (!(i in states)) states[i] = init;
      return [states[i], setStateAt(i)];
    },
    useEffect: (fn) => { effects.push(fn); },
    useCallback: (fn) => fn,
    useRef: (init) => ({ current: init }),
    createElement,
  };
  api.__test = {
    effects, states, dirty,
    setRenderer(fn, props) { renderFn = fn; renderProps = props; },
    rerender() { hookIdx = 0; return renderFn(renderProps); },
    resetIdx() { hookIdx = 0; },
  };
  api.default = api;
  return Object.assign(Object.create(null), {
    __esModule: true, default: api, ...api,
  });
}

async function renderFlushed(Comp, react, maxRounds = 8) {
  const t = react.__test || react.default.__test;
  const settle = () => new Promise(r => setTimeout(r, 20));
  let out = null;
  for (let round = 0; round < maxRounds; round++) {
    t.resetIdx();
    t.effects.length = 0;
    out = Comp({});
    t.setRenderer(Comp, {});
    const fns = t.effects.splice(0);
    for (const fn of fns) {
      try { await fn(); } catch (_) { /* 忽略 */ }
    }
    await settle();
    if (!t.dirty.v) break;
    t.dirty.v = false;
  }
  return out;
}

const mockStyles = {
  __esModule: true,
  default: new Proxy({}, { get: (_, k) => String(k) }),
};
const mockTaro = {
  showToast: () => {},
  showModal: async () => ({ confirm: true }),
};

function createElement(type, props, ...children) {
  return { type, props: props || {},
    children: children.flat().filter(c => c != null && c !== false && c !== true) };
}

function compileLoad(srcPath, label) {
  const source = fs.readFileSync(srcPath, 'utf-8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.React,
      target: ts.ScriptTarget.ES2019,
      esModuleInterop: true,
    },
    fileName: srcPath,
  });
  const out = path.join(os.tmpdir(), `cswork-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

// 轮询定时器 stub(页面 useEffect 内 setInterval 不真正挂起进程)
const realSetInterval = global.setInterval;
const realClearInterval = global.clearInterval;
global.setInterval = () => 0;
global.clearInterval = () => {};

const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, ...mockTaro,
    useDidShow: () => {}, useDidHide: () => {},
  }),
  './index.module.scss': mockStyles,
  './request': { request: mockRequest },
  '@/services/auth-service': {
    __esModule: true,
    getSession: () => ({ memberId: '1', phone: '13800000001',
      nickname: '测试客服', role: 'admin', accessToken: 'tk-cs' }),
    getMemberId: () => '1',
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request === '@/api/cs-chat') return apiMod;
  if (request === './chat') return chatApiMod;
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  return origLoad.apply(this, arguments);
};

// 先编译 chat.ts(cs-chat.ts 的 './chat' 运行时依赖), 再编译 cs-chat.ts
const chatApiMod = compileLoad(CHAT_API_SRC, 'chat-api');
const ChatAPI = chatApiMod.ChatAPI;
const apiMod = compileLoad(API_SRC, 'api');
const CsChatAPI = apiMod.CsChatAPI;
const pageMod = compileLoad(PAGE_SRC, 'page');

// ============================================================
// 3. 断言工具
// ============================================================
const results = [];
const record = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`  ${ok ? '✓' : '✗'} ${name}${ok ? '' : ` — ${detail}`}`);
};
function findAll(node, predicate, acc = []) {
  if (!node || typeof node !== 'object') return acc;
  if (predicate(node)) acc.push(node);
  for (const child of node.children || []) {
    if (child && typeof child === 'object') findAll(child, predicate, acc);
  }
  return acc;
}
const textOf = (node) => {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (node && typeof node === 'object') return (node.children || []).map(textOf).join('');
  return '';
};

(async () => {
  console.log('='.repeat(60));
  console.log('客服工作台 + 聊天实时升级 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1-5] API 层 ----------
  record('字典-排队三态', Object.keys(apiMod.QUEUE_STATUS_NAME).length === 3
    && apiMod.QUEUE_STATUS_NAME.waiting === '排队等待'
    && apiMod.QUEUE_STATUS_NAME.transferring === '转接中'
    && apiMod.QUEUE_STATUS_NAME.human_chatting === '人工服务中');
  record('API-方法7个', ['queue', 'accept', 'reply', 'messages',
    'close', 'stats', 'mode'].every(m => typeof CsChatAPI[m] === 'function'));

  requests.length = 0;
  await CsChatAPI.queue('human_chatting');
  await CsChatAPI.accept('CS9001');
  await CsChatAPI.reply('CS9001', '您好');
  await CsChatAPI.messages('CS9001', 6);
  await CsChatAPI.messages('CS9001', 0);
  await CsChatAPI.close('CS9001');
  await CsChatAPI.stats();
  await CsChatAPI.mode();
  const exact = (u) => requests.find(r => r.url === u);
  record('URL-排队query+admin头',
    !!exact('/api/chat/cs/queue?status=human_chatting&limit=100')
    && exact('/api/chat/cs/queue?status=human_chatting&limit=100').headers['X-Role'] === 'admin'
    && exact('/api/chat/cs/queue?status=human_chatting&limit=100').headers.Authorization === 'Bearer tk-cs');
  record('URL-增量轮询拼接',
    !!exact('/api/chat/sessions/CS9001/messages?limit=100&since_message_id=6')
    && !!exact('/api/chat/sessions/CS9001/messages?limit=100'));

  // 用户端增量 API(P1 轮询配套)
  requests.length = 0;
  await ChatAPI.messagesSince('CS9001', 6);
  await ChatAPI.markRead('CS9001');
  record('用户端增量API', requests.some(r =>
      r.url === '/api/chat/sessions/CS9001/messages?limit=100&since_message_id=6')
    && requests.some(r => r.url === '/api/chat/sessions/CS9001/read'
      && (r.method || 'GET').toUpperCase() === 'POST'));

  // ---------- [6-13] 页面层 ----------
  const reactP = miniReact();
  Object.assign(MOCKS.react, reactP, { default: reactP });
  const el = await renderFlushed(pageMod.default, reactP);
  const flat = JSON.stringify(el);
  const waitTick = (ms) => new Promise(r => setTimeout(r, ms));
  const clickBtn = (root, label) => {
    const t = findAll(root, n => typeof n.props.onClick === 'function'
      && textOf(n) === label)[0];
    if (t) t.props.onClick();
    return !!t;
  };

  record('页面-四页签', flat.includes('排队') && flat.includes('我的会话')
    && flat.includes('聊天窗') && flat.includes('统计'));
  const heroEl = findAll(el, n => n.props.className === 'heroCard');
  record('页面-hero口径', heroEl.length === 1
    && textOf(heroEl[0]).includes('人工客服工作台')
    && textOf(heroEl[0]).includes('3s')
    && textOf(heroEl[0]).includes('敏感词同口径'));

  // 排队页流: 会话行 + 接入按钮
  const rows = findAll(el, n => n.props.className === 'sessionRow');
  const acceptBtns = findAll(el, n => textOf(n) === '接入');
  record('页面-排队行渲染', rows.length === 2
    && textOf(el).includes('CS9001') && textOf(el).includes('会员 1001')
    && textOf(el).includes('人工服务中')
    && acceptBtns.length === 2, `rows=${rows.length}`);

  // 接入流: 点接入 → accept POST → 切聊天窗
  requests.length = 0;
  acceptBtns[0].props.onClick();
  await waitTick(60);
  const elChat = reactP.__test.rerender();
  record('页面-接入流切聊天窗', requests.some(r =>
      /\/api\/chat\/cs\/sessions\/CS9001\/accept$/.test(r.url)
      && (r.method || 'GET').toUpperCase() === 'POST')
    && requests.some(r => r.url.includes('/api/chat/sessions/CS9001/messages'))
    && textOf(elChat).includes('CS9001'), `reqs=${requests.map(r => r.url).join('|')}`);

  // 聊天窗渲染: 消息行 + 客服侧右对齐 + 输入行
  const msgRows = findAll(elChat, n => typeof n.props.className === 'string'
    && n.props.className.trim().split(' ').includes('msgRow'));
  const mineRows = findAll(elChat, n => n.props.className === 'msgRow msgMine');
  const inputs = findAll(elChat, n => n.type === 'Input');
  const sendBtn = findAll(elChat, n => textOf(n) === '发送');
  const closeBtn = findAll(elChat, n => textOf(n) === '结束');
  record('页面-聊天窗渲染', msgRows.length === 3 && mineRows.length === 1
    && textOf(elChat).includes('已接入, 请提供订单号')
    && inputs.length === 1 && sendBtn.length === 1 && closeBtn.length === 1,
    `rows=${msgRows.length} mine=${mineRows.length}`);

  // 客服回复流: 发送 → reply POST + 服务端重载
  const inputEl = inputs[0];
  inputEl.props.onInput({ detail: { value: '请提供订单号' } });
  await waitTick(30);
  requests.length = 0;
  const elBeforeSend = reactP.__test.rerender();
  const sendBtnNow = findAll(elBeforeSend, n => textOf(n) === '发送')[0];
  sendBtnNow.props.onClick();
  await waitTick(80);
  record('页面-客服回复流', requests.some(r =>
      /\/api\/chat\/cs\/sessions\/CS9001\/reply$/.test(r.url)
      && (r.method || 'GET').toUpperCase() === 'POST'
      && r.data && r.data.customerServiceId === 1
      && r.data.content === '请提供订单号'),
    `reqs=${requests.map(r => r.url).join('|')}`);

  // 统计页流
  const elAfterReply = reactP.__test.rerender();
  clickBtn(elAfterReply, '统计');
  await waitTick(30);
  const elStatsTab = reactP.__test.rerender();
  requests.length = 0;
  clickBtn(elStatsTab, '刷新统计');
  await waitTick(60);
  const elStats = reactP.__test.rerender();
  const statsText = textOf(elStats);
  record('页面-统计页流', statsText.includes('12')
    && statsText.includes('50.0%')
    && statsText.includes('4.5')
    && statsText.includes('AI 对话中:5')
    && statsText.includes('mode=off'), `text=${statsText.slice(0, 80)}`);

  // 渲染确定性(两次渲染 JSON 一致)
  const el1 = reactP.__test.rerender();
  const el2 = reactP.__test.rerender();
  record('页面-渲染确定性', JSON.stringify(el1) === JSON.stringify(el2));

  // ---------- 汇总 ----------
  console.log('------------------------------------------------------------');
  const pass = results.filter(r => r.ok).length;
  console.log(`通过: ${pass} / ${results.length}`);
  console.log('============================================================');

  global.setInterval = realSetInterval;
  global.clearInterval = realClearInterval;
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => {
  console.error('测试运行失败:', e);
  global.setInterval = realSetInterval;
  global.clearInterval = realClearInterval;
  process.exit(1);
});
