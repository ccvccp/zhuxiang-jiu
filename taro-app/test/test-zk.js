/**
 * test-zk.js · 智客·AI智能会员大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-zy.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 zk.ts]
 *   1. 字典完整性(等级五档/问答五域/裁决三态/风险三级/
 *      反馈六对象/备忘两主题/唤醒三级)
 *   2. 映射函数(未知回落原值)
 *   3. 方法完备性(20 端点全覆盖)
 *   4. 响应映射(status/overview/qa/health/portrait)
 *   5. 列表数组防御(portraits 非数组→[])
 *   6. 预警映射(churn-scan 红黄绿/churns/ltv/sandbox)
 *   7. 运营映射(benefit-match/points 单会员与全量/wakeup/wakeups)
 *   8. 进化映射(feedback/feedbacks/params/detect/memo/memos)
 *   9. 请求头注入(X-Role: admin + Bearer)
 *   10. URL/方法/请求体正确性(qa/sandbox/feedback/memo POST 体)
 *   [页面层 pages/zk/index.tsx]
 *   11. 四页签结构(洞察/预警/运营/进化)
 *   12. 标题与副标题(确定性引擎 · 建议书模式 · 不涉信值域)
 *   13. 总览统计卡(会员量/等级分布/消费/积分)
 *   14. 问答交互 / 单会员洞察交互(健康度五维 /100 + RFM)
 *   15. 流失扫描交互(红黄绿 + 三信号)
 *   16. 唤醒建议书交互(永不自动发送 + 三级三要素)
 *   17. 参数安全阀 / 三检测器 / 备忘录交互
 *   18. 信值五维不出现(诚信/互助/专业/活跃成长)
 *   19. 组件确定性(同输入多次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'zk.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'zk', 'index.tsx');

// ============================================================
// 1. Mock 上下文(响应结构镜像后端 zk_routes.py / zk_*_service.py)
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  const method = (opts.method || 'GET').toUpperCase();
  if (url === '/api/member-ai/status') {
    return { success: true, data: {
      service: '智客·AI智能会员大模型',
      phase: 'P0-P3 全量上线',
      capabilities: ['NL问答', '健康度五维', 'RFM画像', '三信号流失预警',
        'LTV预测', '等级沙盘', '权益匹配', '积分运营', '沉睡唤醒',
        '反馈学习', '三检测器', '决策备忘'],
      rules: ['全部确定性规则引擎(LLM 禁入判定链)',
        '数字 100% 来自织物查询层',
        '建议书模式: 运营策略永不自动执行',
        '推理链留痕: 评分/预测带 formula'],
      tables: ['zk_churns', 'zk_feedbacks', 'zk_healths', 'zk_memos',
        'zk_params', 'zk_portraits', 'zk_sandboxes', 'zk_wakeups'],
      status: 'ok' } };
  }
  if (url === '/api/member-ai/overview') {
    return { success: true, data: {
      memberTotal: 120,
      levelDistribution: { 1: 58, 2: 34, 3: 18, 4: 7, 5: 3 },
      statusDistribution: { active: 116, disabled: 4 },
      orderTotal: 410, validOrderTotal: 396,
      totalConsume: 286400.5, avgConsume: 723.23,
      pointsTotal: 512340,
      registrationSeries: [
        { date: '2026-08-01', count: 12 },
        { date: '2026-08-02', count: 18 }],
      consumeScope: '有效订单实付额(PAID/SHIPPED/RECEIVED/COMPLETED, '
        + 'priceDetail.actualAmount 聚合)',
      aggregatedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/qa' && method === 'POST') {
    return { success: true, data: {
      domain: 'member', intent: '会员量查询',
      answer: '当前会员总量 120 名(正常 116 / 禁用 4); 累计订单 410 单, '
        + '其中有效订单 396 单。',
      dataSnapshot: { memberTotal: 120, orderTotal: 410,
        validOrderTotal: 396 },
      reasoning: '意图路由→会员量域; 取数织物总览(会员 120 名/'
        + '有效订单 396 单); 数字全部来自查询层' } };
  }
  if (url === '/api/member-ai/health/7') {
    return { success: true, data: {
      memberId: 7, nickname: '竹韵客', level: 3, levelName: '竹林会员',
      dimensions: [
        { dimKey: 'activity', dim: '活跃度', raw: 3, score: 82.6,
          explain: '近30天有效消费 2 单+登录 1 次' },
        { dimKey: 'spending', dim: '消费力', raw: 1.35, score: 88.1,
          explain: '月均消费 ¥680.5 / 等级应达 ¥502.5(L3 保级额/12)' },
        { dimKey: 'growth', dim: '等级成长', raw: 303.9, score: 76.4,
          explain: '成长值斜率 303.9/日(累计 3100 / 10.2 天)' },
        { dimKey: 'points', dim: '积分活力', raw: 502.0, score: 73.1,
          explain: '月均积分流量 502 竹叶(订单获得+抵扣)' },
        { dimKey: 'lifecycle', dim: '生命周期', raw: 10.2, score: 35.9,
          explain: '注册 10.2 个月' }],
      totalScore: 71.2, grade: 'B 良好',
      weights: { activity: 0.25, spending: 0.25, growth: 0.15,
        points: 0.15, lifecycle: 0.2 },
      formula: '各维 0-100: 100/(1+exp(-k(x-x0))); 总分=Σ维度分×权重',
      computedAt: '2026-09-12T08:00:00' } };
  }
  // 非数组响应 → 前端列表防御须回落 []
  if (url === '/api/member-ai/portraits') {
    return { success: true, data: { oops: true } };
  }
  if (url === '/api/member-ai/portrait/7') {
    return { success: true, data: {
      memberId: 7, nickname: '竹韵客', level: 3,
      rfm: { recencyDays: 5.2, recencyScore: 5, frequency30d: 2,
        frequencyScore: 3, monthlyConsume: 680.5, monetaryScore: 3 },
      segmentLabel: '重要保持会员',
      suggestedAction: '消费力待提升: 组合购/满赠提客单',
      formula: 'R 档: ≤7天→5/≤30→4/≤90→3/≤180→2/其余→1; '
        + 'F 档: ≥5单→5/≥3→4/≥2→3/≥1→2/0→1; '
        + 'M 档: 月均≥1000→5/≥500→4/≥200→3/≥50→2/其余→1',
      computedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/churn-scan') {
    return { success: true, data: {
      scanned: 120,
      riskCounts: { red: 6, yellow: 19, green: 95 },
      items: [
        { memberId: 12, nickname: '沉睡客甲', level: 2,
          signals: {
            loginGap: { daysSinceLogin: 95.1, cohortAvg: 30.2, value: 1 },
            consumeDecay: { recent30: 0, prev30: 420.5, value: 1 },
            levelSlide: { growth: 380, threshold: 500, value: 0 } },
          churnScore: 0.8, riskLevel: 'red', riskLevelName: '红色高预警',
          computedAt: '2026-09-12T08:00:00' },
        { memberId: 21, nickname: '犹豫客乙', level: 3,
          signals: {
            loginGap: { daysSinceLogin: 45.0, cohortAvg: 30.2, value: 0.49 },
            consumeDecay: { recent30: 120, prev30: 380, value: 0.68 },
            levelSlide: { growth: 2500, threshold: 3000, value: 1 } },
          churnScore: 0.56, riskLevel: 'yellow', riskLevelName: '黄色中预警',
          computedAt: '2026-09-12T08:00:00' }],
      formula: 'score = 0.4×登录拉长 + 0.4×消费衰减 + 0.2×等级下滑; '
        + '红≥0.7 / 黄≥0.4 / 绿其余(确定性, 无 LLM)',
      scannedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/churns?limit=50' || url === '/api/member-ai/churns') {
    return { success: true, data: [
      { memberId: 12, nickname: '沉睡客甲', level: 2, churnScore: 0.8,
        riskLevel: 'red', riskLevelName: '红色高预警',
        computedAt: '2026-09-12T08:00:00' },
      { memberId: 21, nickname: '犹豫客乙', level: 3, churnScore: 0.56,
        riskLevel: 'yellow', riskLevelName: '黄色中预警',
        computedAt: '2026-09-12T08:00:00' }] };
  }
  if (url === '/api/member-ai/ltv/7') {
    return { success: true, data: {
      memberId: 7, nickname: '竹韵客', level: 3, levelName: '竹林会员',
      basis: { monthlyAvgConsume: 680.5, historyMonths: 10.2,
        ordersIn90d: 4 },
      factors: { levelWeight: 0.5, activeWeight: 0.83, retainFactor: 0.6,
        horizonMonths: 12 },
      ltv: 2035.5,
      formula: 'LTV = 月均消费 ¥680.5 × 等级权重 0.5 × 活跃权重 0.83 × '
        + '留存系数 0.6(可学习) × 12 个月 = ¥2035.5',
      assumptions: [
        '等级留存权重按当前等级取值(升级/降级会改变 LTV)',
        '活跃权重以近 90 天有效单量/6 为上限归一',
        'ltvRetainFactor 为全局校准参数(默认 0.6, '
        + 'P3 反馈闭环在 [0.4, 0.8] 内学习)',
        '预期存续 12 个月(行业口径假设, 非个体预测)'],
      predictedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/sandbox' && method === 'POST') {
    return { success: true, data: {
      sandboxId: 5, memberId: 7, nickname: '竹韵客',
      assumption: { consumeDelta: 0.2, growthDelta: 500,
        horizonMonths: 12 },
      current: { level: 3, levelName: '竹林会员', growth: 3100,
        levelByGrowth: 3, monthlyAvgConsume: 680.5, threshold: 3000 },
      projected: { futureMonthlyConsume: 816.6, future12mConsume: 9799.2,
        growthGain: 9799, newGrowth: 13399, newLevel: 5,
        newLevelName: '竹海 SVIP', keepRequirement: 9999,
        keepVerdict: '保级', nextLevel: null, gapToNextLevel: 0,
        direction: '升级' },
      formula: '未来月消费 = 月均 ¥680.5 × (1+0.2); 新成长值 = '
        + '3100 + 9799 + 500 = 13399(每元 1 成长值)',
      disposition: '推演为建议书; 任何等级/权益调整须管理员确认',
      simulatedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/benefit-match/7') {
    return { success: true, data: {
      memberId: 7, nickname: '竹韵客', level: 3, levelName: '竹林会员',
      rfmLabel: '重要保持会员',
      benefits: ['生日礼: ¥100 生日券', '专属折扣 92 折',
        '免邮阈值 ¥59', '新品优先购'],
      matchRule: '等级基础档 L3 × RFM 分层「重要保持会员」修正'
        + '(无追加)(确定性规则表)',
      disposition: '策略为建议书; 执行须管理员确认',
      matchedAt: '2026-09-12T08:00:00' } };
  }
  if (url.startsWith('/api/member-ai/points-analysis')) {
    if (url.includes('memberId=')) {
      return { success: true, data: {
        scope: 'member', memberId: 7, nickname: '竹韵客',
        depositBalance: 2350, totalEarned: 5120,
        earnRatePerMonth: 501.96,
        orderUsedPoints: 1800, orderConsumedPoints: 5120,
        redeemTendency: 0.26, expiringSoon: 300,
        expiryRiskNote: '300 竹叶 30 天内到期, 建议优先引导兑换',
        suggestion: '兑换倾向低: 建议满减+积分抵扣组合引导',
        disposition: '策略为建议书; 执行须管理员确认',
        analyzedAt: '2026-09-12T08:00:00' } };
    }
    return { success: true, data: {
      scope: 'all', memberTotal: 120, legacyPointsTotal: 512340,
      orderUsedPointsTotal: 96000, orderConsumedPointsTotal: 410000,
      redeemTendency: 0.19, membersWithExpiringRisk: 23,
      suggestion: '全量会员兑换倾向 19%; 23 名会员 30 天内有积分到期, '
        + '建议定向推送兑换提醒(须管理员确认后执行)',
      disposition: '策略为建议书; 执行须管理员确认',
      analyzedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/wakeup-suggest') {
    return { success: true, data: {
      sleepingTotal: 2,
      tierCounts: { deep: 1, medium: 1, light: 0 },
      suggestions: [
        { suggestionId: 3, memberId: 12, nickname: '沉睡客甲', level: 2,
          levelName: '竹叶会员', sleepDays: 150.2, activityScore: 12.5,
          tier: 'deep', tierName: '深度沉睡', channel: '短信+站内信',
          timing: '工作日 12:00-13:00',
          benefit: '大额回归券 ¥50 + 全单免邮',
          reason: '活跃分 12.5(<40) / 距上次消费 150.2 天; 分级依据: '
            + '无消费≥120 天或活跃分<20',
          disposition: '建议书; 永不自动发送, 须管理员确认',
          generatedAt: '2026-09-12T08:00:00' },
        { suggestionId: 4, memberId: 21, nickname: '犹豫客乙', level: 3,
          levelName: '竹林会员', sleepDays: 92.0, activityScore: 33.0,
          tier: 'medium', tierName: '中度沉睡', channel: '站内信+短信',
          timing: '周末 20:00-21:00',
          benefit: '专属折扣 9 折 + ¥20 券',
          reason: '活跃分 33(<40) / 距上次消费 92 天; 分级依据: 无消费≥90 天',
          disposition: '建议书; 永不自动发送, 须管理员确认',
          generatedAt: '2026-09-12T08:00:00' }],
      formula: '沉睡 = 活跃维<40 或 60 天无有效消费; '
        + '深度=无消费≥120 天或活跃<20, 中度=≥90 天, 轻度=其余(确定性阈值)',
      disposition: '建议书; 触达永不自动发送, 须管理员确认后由消息模块执行',
      generatedAt: '2026-09-12T08:00:00' } };
  }
  if (url.startsWith('/api/member-ai/wakeups')) {
    return { success: true, data: [
      { suggestionId: 3, memberId: 12, nickname: '沉睡客甲', tier: 'deep',
        tierName: '深度沉睡', channel: '短信+站内信',
        timing: '工作日 12:00-13:00',
        benefit: '大额回归券 ¥50 + 全单免邮',
        disposition: '建议书; 永不自动发送, 须管理员确认',
        generatedAt: '2026-09-12T08:00:00' }] };
  }
  if (url === '/api/member-ai/feedback' && method === 'POST') {
    return { success: true, data: {
      feedbackId: 9, targetType: 'ltv', verdict: 'adopted', note: '准',
      paramBefore: 0.6, paramAfter: 0.63,
      learningNote: 'ltvRetainFactor 0.6 ×1.05 → 0.63'
        + '(clamp [0.4, 0.8] 安全阀内)',
      negativeSample: null,
      feedbackAt: '2026-09-12T08:00:00' } };
  }
  if (url.startsWith('/api/member-ai/feedbacks')) {
    return { success: true, data: [
      { feedbackId: 9, targetType: 'ltv', verdict: 'adopted', note: '',
        paramBefore: 0.6, paramAfter: 0.63,
        learningNote: 'ltvRetainFactor 0.6 ×1.05 → 0.63',
        feedbackAt: '2026-09-12T08:00:00' },
      { feedbackId: 8, targetType: 'wakeup', verdict: 'rejected', note: '',
        paramBefore: 0.66, paramAfter: 0.59,
        learningNote: 'ltvRetainFactor 0.66 ×0.9 → 0.59',
        feedbackAt: '2026-09-11T08:00:00' }] };
  }
  if (url === '/api/member-ai/params') {
    return { success: true, data: {
      ltvRetainFactor: 0.63, clampRange: [0.4, 0.8],
      defaultLtvRetainFactor: 0.6,
      updatedAt: '2026-09-12T08:00:00',
      note: '参数仅由反馈闭环学习(clamp 安全阀), 不经人工直改' } };
  }
  if (url === '/api/member-ai/detect') {
    return { success: true, data: {
      alerts: [
        { detector: '消费尖峰', domain: '消费', date: '2026-09-11',
          current: 25800.5, historyAvg: 9200.3, unit: '元',
          rule: '当前 > μ+3σ(μ≥5)' }],
      seriesMeta: { consumeDays: 42, pointsDays: 42, registerDays: 60 },
      formula: 'spike: 当前>μ+3σ 且 μ≥5; drop: 当前<μ−3σ 且 |当前|≥20; '
        + 'surge: 当前≥20 且 ≥均值×3(μ/σ 为历史日序列均值/标准差, 确定性)',
      disposition: '检测为观测告警; 处置动作须管理员确认',
      detectedAt: '2026-09-12T08:00:00' } };
  }
  if (url === '/api/member-ai/memo' && method === 'POST') {
    return { success: true, data: {
      memoId: 4, topic: 'member_day', topicName: '会员日活动',
      notes: 'Q4 促活',
      disposition: '备忘为建议书; 决策须管理层确认后方可执行',
      createdAt: '2026-09-12T08:00:00',
      title: '每月 8 日会员日: 消费力分层满减方案',
      body: '当前会员 120 名(正常 116), 有效单平均消费 ¥723.23; '
        + '建议采用「满 500-100 / 满 1000-250 双梯度满减+赠品鉴小样」; '
        + '目标提升复购频次与客单价(数字全部来自织物总览)。',
      assumptions: [
        '满减梯度按当前平均消费分档(确定性规则)',
        '目标基线为活动后 30 天复购率, 需活动后复盘校验'],
      formula: '平均消费 ¥723.23 → ≥500 高档满减模板' } };
  }
  if (url.startsWith('/api/member-ai/memos')) {
    return { success: true, data: [
      { memoId: 3, topic: 'member_day', topicName: '会员日活动', notes: '',
        title: '每月 8 日会员日: 消费力分层满减方案',
        createdAt: '2026-09-12T08:00:00' },
      { memoId: 2, topic: 'level_threshold', topicName: '等级门槛调整',
        notes: '', title: '等级门槛审视: L3+ 渗透率观测',
        createdAt: '2026-09-11T08:00:00' }] };
  }
  return { success: true, data: {} };
};

// 极简 React hooks 运行时(仅支持 useState/useEffect/useCallback 同步路径)
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
    createElement,
  };
  api.__test = {
    effects, states, dirty,
    setRenderer(fn, props) { renderFn = fn; renderProps = props; },
    rerender() {
      hookIdx = 0;
      return renderFn(renderProps);
    },
    resetIdx() { hookIdx = 0; },
  };
  api.default = api;
  const mod = Object.assign(Object.create(null), {
    __esModule: true,
    default: api,
    ...api,
  });
  return mod;
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
      try { await fn(); } catch (_) { /* effect 内部失败忽略 */ }
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
  showToast: () => {}, showModal: () => {}, navigateTo: () => {},
};
function createElement(type, props, ...children) {
  return { type, props: props || {}, children: children.flat().filter(c => c != null && c !== false && c !== true) };
}
const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input', Textarea: 'Textarea',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, useDidShow: () => {},
  }),
  './request': { request: mockRequest },
  './index.module.scss': mockStyles,
  '@/services/auth-service': {
    getSession: () => ({ accessToken: 'tok' }),
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
let apiMod = null;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/zk') return apiMod;
  return origLoad.apply(this, arguments);
};

// ============================================================
// 2. 编译加载
// ============================================================
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
  const out = path.join(os.tmpdir(), `zk-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

apiMod = compileLoad(API_SRC, 'api');
const ZkAPI = apiMod.ZkAPI;

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
// 全树文本拼接(JSX 表达式子节点在 JSON 中不连续, 须拼接后匹配)
const flatOf = (el) => textOf(el);
const findBtn = (el, text, cls) =>
  findAll(el, n => n.props.className === cls
    && textOf(n).includes(text))[0];
const inputBy = (el, placeholder) =>
  findAll(el, n => n.type === 'Input'
    && (n.props.placeholder || '') === placeholder)[0];

(async () => {
  console.log('='.repeat(60));
  console.log('智客·AI智能会员大模型 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-等级五档', Object.keys(apiMod.LEVEL_NAME).length === 5
    && apiMod.LEVEL_NAME[3] === '竹林会员'
    && apiMod.LEVEL_NAME[5] === '竹海 SVIP');
  record('字典-问答五域', Object.keys(apiMod.QA_DOMAIN_NAME).length === 5
    && apiMod.QA_DOMAIN_NAME.member === '会员量'
    && apiMod.QA_DOMAIN_NAME.churn === '流失预警');
  record('字典-裁决三态', Object.keys(apiMod.VERDICT_NAME).length === 3
    && apiMod.VERDICT_NAME.adopted === '采纳'
    && apiMod.VERDICT_NAME.rejected === '拒绝');
  record('字典-风险三级', Object.keys(apiMod.RISK_LEVEL_NAME).length === 3
    && apiMod.RISK_LEVEL_NAME.red === '红色高预警');
  record('字典-反馈目标六类', Object.keys(apiMod.FEEDBACK_TARGET_NAME).length === 6
    && apiMod.FEEDBACK_TARGET_NAME.benefit_match === '权益匹配'
    && apiMod.FEEDBACK_TARGET_NAME.sandbox === '等级沙盘');
  record('字典-备忘两主题', Object.keys(apiMod.MEMO_TOPIC_NAME).length === 2
    && apiMod.MEMO_TOPIC_NAME.level_threshold === '等级门槛调整');
  record('字典-唤醒三级', Object.keys(apiMod.WAKEUP_TIER_NAME).length === 3
    && apiMod.WAKEUP_TIER_NAME.deep === '深度沉睡');

  // ---------- [2] 映射回落 ----------
  record('映射-未知回落', apiMod.levelName(9) === 'L9'
    && apiMod.qaDomainName('x') === 'x'
    && apiMod.verdictName('y') === 'y'
    && apiMod.riskLevelName('z') === 'z'
    && apiMod.memoTopicName('w') === 'w'
    && apiMod.wakeupTierName('v') === 'v'
    && apiMod.feedbackTargetName('u') === 'u');

  // ---------- [3] 方法完备性(20 端点) ----------
  const METHODS = ['status', 'overview', 'qa', 'health', 'portrait',
    'portraits', 'churnScan', 'churns', 'ltv', 'sandbox', 'benefitMatch',
    'pointsAnalysis', 'wakeupSuggest', 'wakeups', 'feedback', 'feedbacks',
    'params', 'detect', 'memo', 'memos'];
  record('API-方法20个完备', METHODS.length === 20
    && METHODS.every(m => typeof ZkAPI[m] === 'function'));

  // ---------- [4] P0 洞察映射 ----------
  const st = await ZkAPI.status();
  record('API-status', st.service === '智客·AI智能会员大模型'
    && st.capabilities.length === 12 && st.status === 'ok');

  const ov = await ZkAPI.overview();
  record('API-overview', ov.memberTotal === 120
    && ov.levelDistribution[3] === 18
    && ov.validOrderTotal === 396
    && ov.pointsTotal === 512340
    && ov.statusDistribution.active === 116);

  const qa = await ZkAPI.qa('现在有多少会员');
  record('API-智能问答', qa.domain === 'member'
    && qa.answer.includes('120')
    && qa.reasoning.includes('查询层'));

  const h = await ZkAPI.health(7);
  record('API-健康度五维', h.dimensions.length === 5
    && h.dimensions[0].dimKey === 'activity'
    && h.dimensions[0].score === 82.6
    && h.totalScore === 71.2
    && h.grade === 'B 良好');

  const p = await ZkAPI.portrait(7);
  record('API-RFM画像', p.segmentLabel === '重要保持会员'
    && p.rfm.recencyScore === 5
    && p.rfm.frequencyScore === 3
    && p.rfm.monetaryScore === 3
    && p.suggestedAction.includes('客单'));

  const ps = await ZkAPI.portraits();
  record('API-portraits数组防御', Array.isArray(ps) && ps.length === 0);

  // ---------- [5] P1 预警映射 ----------
  const cs = await ZkAPI.churnScan();
  record('API-流失扫描', cs.scanned === 120
    && cs.riskCounts.red === 6 && cs.riskCounts.yellow === 19
    && cs.items.length === 2
    && cs.items[0].riskLevel === 'red'
    && cs.items[0].signals.loginGap.value === 1
    && cs.items[0].signals.consumeDecay.value === 1);

  const ch = await ZkAPI.churns(50);
  record('API-流失列表', Array.isArray(ch) && ch.length === 2
    && ch[0].churnScore >= ch[1].churnScore
    && ch[1].riskLevelName === '黄色中预警');

  const lv = await ZkAPI.ltv(7);
  record('API-LTV预测', lv.ltv === 2035.5
    && lv.factors.retainFactor === 0.6
    && lv.factors.levelWeight === 0.5
    && lv.assumptions.length === 4
    && lv.formula.includes('LTV'));

  const sb = await ZkAPI.sandbox(
    { memberId: 7, consumeDelta: 0.2, growthDelta: 500 });
  record('API-等级沙盘', sb.projected.newLevel === 5
    && sb.projected.direction === '升级'
    && sb.projected.keepVerdict === '保级'
    && sb.disposition.includes('建议书'));

  // ---------- [6] P2 运营映射 ----------
  const bm = await ZkAPI.benefitMatch(7);
  record('API-权益匹配', bm.level === 3
    && bm.rfmLabel === '重要保持会员'
    && bm.benefits.length === 4
    && bm.matchRule.includes('确定性规则表'));

  const pm = await ZkAPI.pointsAnalysis(7);
  record('API-积分单会员', pm.scope === 'member'
    && pm.depositBalance === 2350
    && pm.earnRatePerMonth === 501.96
    && pm.redeemTendency === 0.26
    && pm.expiringSoon === 300);

  const pa = await ZkAPI.pointsAnalysis();
  record('API-积分全量', pa.scope === 'all'
    && pa.memberTotal === 120
    && pa.membersWithExpiringRisk === 23
    && pa.disposition.includes('管理员确认'));

  const ws = await ZkAPI.wakeupSuggest();
  record('API-唤醒建议书', ws.sleepingTotal === 2
    && ws.tierCounts.deep === 1 && ws.tierCounts.medium === 1
    && ws.suggestions[0].tier === 'deep'
    && ws.suggestions[0].channel === '短信+站内信'
    && ws.suggestions[1].tier === 'medium'
    && ws.disposition.includes('永不自动发送'));

  const wk = await ZkAPI.wakeups(50);
  record('API-唤醒列表', Array.isArray(wk) && wk.length === 1
    && wk[0].tierName === '深度沉睡');

  // ---------- [7] P3 进化映射 ----------
  const fb = await ZkAPI.feedback('ltv', 'adopted', '准');
  record('API-反馈闭环', fb.feedbackId === 9
    && fb.paramBefore === 0.6 && fb.paramAfter === 0.63
    && fb.learningNote.includes('clamp'));

  const fbs = await ZkAPI.feedbacks(50);
  record('API-反馈列表', Array.isArray(fbs) && fbs.length === 2
    && fbs[0].verdict === 'adopted'
    && fbs[1].verdict === 'rejected');

  const pv = await ZkAPI.params();
  record('API-参数视图', pv.ltvRetainFactor === 0.63
    && pv.clampRange[0] === 0.4 && pv.clampRange[1] === 0.8
    && pv.defaultLtvRetainFactor === 0.6);

  const dt = await ZkAPI.detect();
  record('API-三检测器', dt.alerts.length === 1
    && dt.alerts[0].detector === '消费尖峰'
    && dt.seriesMeta.consumeDays === 42
    && dt.formula.includes('spike'));

  const mm = await ZkAPI.memo('member_day', 'Q4 促活');
  record('API-决策备忘', mm.topicName === '会员日活动'
    && mm.title.includes('会员日')
    && mm.assumptions.length === 2
    && mm.disposition.includes('管理层确认'));

  const ms = await ZkAPI.memos(50);
  record('API-备忘列表', Array.isArray(ms) && ms.length === 2
    && ms[0].memoId === 3
    && ms[1].topicName === '等级门槛调整');

  // ---------- [8] 请求头注入 ----------
  requests.length = 0;
  await ZkAPI.status();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  // ---------- [9] URL/方法/请求体 ----------
  requests.length = 0;
  await ZkAPI.qa('现在有多少会员');
  await ZkAPI.sandbox({ memberId: 7, consumeDelta: 0.2, growthDelta: 500 });
  await ZkAPI.feedback('ltv', 'adopted', '准');
  await ZkAPI.memo('member_day', 'Q4 促活');
  await ZkAPI.health(7);
  await ZkAPI.ltv(7);
  await ZkAPI.benefitMatch(7);
  await ZkAPI.pointsAnalysis(7);
  await ZkAPI.pointsAnalysis();
  await ZkAPI.churns(50);
  const reqOf = (u) => requests.find(r => r.url === u);
  record('API-URL与请求体',
    reqOf('/api/member-ai/qa') !== undefined
    && reqOf('/api/member-ai/qa').method === 'POST'
    && reqOf('/api/member-ai/qa').data.text === '现在有多少会员'
    && reqOf('/api/member-ai/sandbox').method === 'POST'
    && reqOf('/api/member-ai/sandbox').data.memberId === 7
    && reqOf('/api/member-ai/sandbox').data.consumeDelta === 0.2
    && reqOf('/api/member-ai/sandbox').data.growthDelta === 500
    && reqOf('/api/member-ai/feedback').data.targetType === 'ltv'
    && reqOf('/api/member-ai/feedback').data.verdict === 'adopted'
    && reqOf('/api/member-ai/feedback').data.note === '准'
    && reqOf('/api/member-ai/memo').data.topic === 'member_day'
    && reqOf('/api/member-ai/memo').data.notes === 'Q4 促活'
    && reqOf('/api/member-ai/health/7') !== undefined
    && !reqOf('/api/member-ai/health/7').method
    && reqOf('/api/member-ai/ltv/7') !== undefined
    && reqOf('/api/member-ai/benefit-match/7') !== undefined
    && reqOf('/api/member-ai/points-analysis?memberId=7') !== undefined
    && reqOf('/api/member-ai/points-analysis') !== undefined
    && reqOf('/api/member-ai/churns?limit=50') !== undefined);

  // ---------- [10] 页面层 ----------
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, {
    default: reactForPage,
  });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const t0 = reactForPage.__test || reactForPage.default.__test;
  const el = await renderFlushed(pageMod.default, reactForPage);

  // [10.1] 四页签(激活页签 className 为 'tab tabActive')
  const tabs = findAll(el, n =>
    (n.props.className || '').split(' ').includes('tab'));
  record('页面-四页签', tabs.length === 4
    && ['洞察', '预警', '运营', '进化'].every((t, i) => textOf(tabs[i]) === t));

  // [10.2] 标题与副标题
  const heroTitle = findAll(el, n => n.props.className === 'heroTitle')[0];
  const heroSub = findAll(el, n => n.props.className === 'heroSub')[0];
  record('页面-标题与副标题',
    textOf(heroTitle) === '智客·AI智能会员大模型'
    && textOf(heroSub).includes('确定性引擎')
    && textOf(heroSub).includes('建议书模式')
    && textOf(heroSub).includes('不涉信值域'));

  // [10.3] 总览统计卡(挂载自动加载)
  const statGrid = findAll(el, n => n.props.className === 'statGrid')[0];
  record('页面-总览统计卡', statGrid !== undefined
    && textOf(statGrid).includes('120')
    && textOf(statGrid).includes('512340')
    && textOf(statGrid).includes('会员总量')
    && flatOf(el).includes('L3 竹林会员')
    && flatOf(el).includes('18 名'));

  // [10.4] 问答交互(默认问题 → 会员量域)
  let elNow = el;
  let qaBtn = findBtn(elNow, '问答', 'qaBtn');
  await qaBtn.props.onClick();
  elNow = await renderFlushed(pageMod.default, reactForPage);
  record('页面-问答交互', flatOf(elNow).includes('会员量·会员量查询')
    && flatOf(elNow).includes('当前会员总量 120 名')
    && flatOf(elNow).includes('数字全部来自查询层'));

  // [10.5] 单会员洞察交互(输入 7 → 健康度五维 + RFM)
  const midInput = inputBy(elNow, '会员 ID');
  await midInput.props.onInput({ detail: { value: '7' } });
  elNow = await renderFlushed(pageMod.default, reactForPage);
  const queryBtn = findBtn(elNow, '查询', 'qaBtn');
  await queryBtn.props.onClick();
  elNow = await renderFlushed(pageMod.default, reactForPage);
  record('页面-健康度与RFM交互', flatOf(elNow).includes('82.6/100')
    && flatOf(elNow).includes('71.2/100')
    && flatOf(elNow).includes('B 良好')
    && flatOf(elNow).includes('重要保持会员')
    && flatOf(elNow).includes('5 档'));

  // [10.6] 预警页签(表单结构)
  t0.states[0] = 'alert';
  t0.dirty.v = true;
  let elTab = await renderFlushed(pageMod.default, reactForPage);
  record('页面-预警表单', inputBy(elTab, '会员 ID') !== undefined
    && inputBy(elTab, '月消费变动%(10=+10%)') !== undefined
    && inputBy(elTab, '成长值加成') !== undefined
    && findBtn(elTab, '预测', 'qaBtn') !== undefined
    && findBtn(elTab, '推演', 'qaBtn') !== undefined);

  // [10.7] 流失扫描交互(红黄绿 + 三信号)
  await findBtn(elTab, '全量扫描', 'runBtn').props.onClick();
  elTab = await renderFlushed(pageMod.default, reactForPage);
  record('页面-流失扫描交互', flatOf(elTab).includes('红色高预警')
    && flatOf(elTab).includes('黄色中预警')
    && flatOf(elTab).includes('80%')
    && flatOf(elTab).includes('三信号: 登录拉长 1')
    && flatOf(elTab).includes('0.4×登录拉长'));

  // [10.8] 运营页签(永不自动发送红线 + 表单)
  t0.states[0] = 'ops';
  t0.dirty.v = true;
  elTab = await renderFlushed(pageMod.default, reactForPage);
  const neverSend = findAll(elTab,
    n => n.props.className === 'neverSend')[0];
  record('页面-运营页签与红线', neverSend !== undefined
    && textOf(neverSend).includes('永不自动发送')
    && inputBy(elTab, '会员 ID(留空查全量)') !== undefined
    && findBtn(elTab, '匹配', 'qaBtn') !== undefined
    && findBtn(elTab, '分析', 'qaBtn') !== undefined);

  // [10.9] 唤醒建议书交互(三级 + 三要素 + 建议书标签)
  await findBtn(elTab, '生成唤醒建议书', 'runBtn').props.onClick();
  elTab = await renderFlushed(pageMod.default, reactForPage);
  const dispTags = findAll(elTab, n => n.props.className === 'dispTag');
  record('页面-唤醒建议书交互', flatOf(elTab).includes('深度沉睡')
    && flatOf(elTab).includes('中度沉睡')
    && flatOf(elTab).includes('短信+站内信')
    && flatOf(elTab).includes('工作日 12:00-13:00')
    && flatOf(elTab).includes('大额回归券 ¥50 + 全单免邮')
    && flatOf(elTab).includes('12.5/100')
    && dispTags.length >= 1
    && dispTags.every(t => textOf(t).includes('建议书')));

  // [10.10] 进化页签(参数/反馈/检测器/备忘录结构)
  t0.states[0] = 'evo';
  t0.dirty.v = true;
  elTab = await renderFlushed(pageMod.default, reactForPage);
  record('页面-进化页签结构', flatOf(elTab).includes('可学习参数(安全阀)')
    && flatOf(elTab).includes('反馈闭环(三裁决驱动参数学习)')
    && flatOf(elTab).includes('三检测器(消费尖峰/积分骤降/注册激增)')
    && flatOf(elTab).includes('决策备忘录')
    && flatOf(elTab).includes('LTV 预测')
    && flatOf(elTab).includes('权益匹配'));

  // [10.11] 参数安全阀交互
  await findBtn(elTab, '查看当前参数', 'runBtn').props.onClick();
  elTab = await renderFlushed(pageMod.default, reactForPage);
  record('页面-参数安全阀交互', flatOf(elTab).includes('ltvRetainFactor 0.63')
    && flatOf(elTab).includes('[0.4, 0.8]')
    && flatOf(elTab).includes('0.6'));

  // [10.12] 三检测器交互
  await findBtn(elTab, '运行检测扫描', 'runBtn').props.onClick();
  elTab = await renderFlushed(pageMod.default, reactForPage);
  record('页面-三检测器交互', flatOf(elTab).includes('消费尖峰')
    && flatOf(elTab).includes('25800.5元')
    && flatOf(elTab).includes('μ+3σ'));

  // [10.13] 备忘录交互(默认主题 member_day + 历史列表)
  const genBtn = findAll(elTab, n => n.props.className === 'qaBtn'
    && textOf(n) === '生成')[0];
  await genBtn.props.onClick();
  elTab = await renderFlushed(pageMod.default, reactForPage);
  record('页面-备忘录交互', flatOf(elTab).includes('会员日活动')
    && flatOf(elTab).includes('满 500-100')
    && flatOf(elTab).includes('满减梯度按当前平均消费分档')
    && flatOf(elTab).includes('备忘录留痕(最近 2 份)')
    && flatOf(elTab).includes('#3 会员日活动')
    && flatOf(elTab).includes('#2 等级门槛调整'));

  // [10.14] 信值五维不出现(边界红线)
  const flatAll = flatOf(el) + flatOf(elNow) + flatOf(elTab);
  record('页面-信值五维不出现', !flatAll.includes('诚信')
    && !flatAll.includes('互助')
    && !flatAll.includes('专业')
    && !flatAll.includes('活跃成长'));

  // [10.15] 组件确定性(同输入两次渲染结构一致)
  const reactForPage2 = miniReact();
  Object.assign(MOCKS.react, reactForPage2, {
    default: reactForPage2,
  });
  const el2 = await renderFlushed(pageMod.default, reactForPage2);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'statCell')
      .map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'statCell')
      .map(textOf))
    && JSON.stringify(findAll(el, n => n.props.className === 'tab')
      .map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'tab')
      .map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  const fail = results.length - pass;
  console.log('-'.repeat(60));
  console.log(`总计: ${pass} 通过 / ${fail} 失败`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
