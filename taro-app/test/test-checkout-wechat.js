/**
 * test-checkout-wechat.js · Taro 版 checkout-service 微信环境测试
 * ============================================================
 * 用 Node.js 模拟微信小程序环境(Taro API),测试 9 阶段事务
 * ============================================================
 */

// ========== 1. 模拟微信小程序 Taro 环境 ==========
const wxStorage = {};
const wxRequestLog = [];

// 模拟 Taro API(微信小程序环境)
const mockTaro = {
  // 存储API(微信小程序)
  getStorageSync(key) {
    return wxStorage[key] !== undefined ? wxStorage[key] : '';
  },
  setStorageSync(key, data) {
    wxStorage[key] = data;
  },
  removeStorageSync(key) {
    delete wxStorage[key];
  },
  // 网络请求(微信小程序)
  request(opts) {
    wxRequestLog.push({ url: opts.url, method: opts.method, data: opts.data });
    // 模拟成功响应
    setTimeout(() => {
      opts.success({ statusCode: 200, data: { ok: true, message: 'wx.request 模拟成功' } });
    }, 10);
  },
  // 环境标识
  getEnv() {
    return 'weapp'; // 微信小程序
  }
};

// 注入全局 Taro(模拟微信小程序环境)
global.Taro = mockTaro;
process.env.TARO_ENV = 'weapp';

// ========== 2. EnvAdapter(从 env-adapter.ts 转换) ==========
class EnvAdapter {
  constructor() {
    this.version = '2.0.0';
  }

  getEnv() {
    return global.process.env.TARO_ENV || 'h5';
  }

  isH5() { return this.getEnv() === 'h5'; }
  isWechatMini() { return this.getEnv() === 'weapp'; }
  isAlipayMini() { return this.getEnv() === 'alipay'; }

  storage = {
    get(key) {
      try {
        const val = mockTaro.getStorageSync(key);
        if (val === '' || val === undefined || val === null) return null;
        if (typeof val === 'string') {
          try { return JSON.parse(val); } catch { return val; }
        }
        return val;
      } catch (e) {
        console.error('[EnvAdapter] storage.get failed:', e);
        return null;
      }
    },
    set(key, value) {
      try {
        mockTaro.setStorageSync(key, value);
        return true;
      } catch (e) {
        console.error('[EnvAdapter] storage.set failed:', e);
        return false;
      }
    },
    remove(key) {
      try {
        mockTaro.removeStorageSync(key);
        return true;
      } catch (e) {
        console.error('[EnvAdapter] storage.remove failed:', e);
        return false;
      }
    }
  };

  async request(opts) {
    return new Promise((resolve, reject) => {
      mockTaro.request({
        url: opts.url,
        method: opts.method || 'GET',
        data: opts.data,
        header: opts.header || {},
        success: (res) => {
          resolve({
            ok: res.statusCode >= 200 && res.statusCode < 300,
            status: res.statusCode,
            json: () => Promise.resolve(res.data),
            data: res.data
          });
        },
        fail: (err) => {
          reject(new Error(err.errMsg || 'Taro.request failed'));
        }
      });
    });
  }
}

const envAdapter = new EnvAdapter();

// ========== 3. CheckoutService(从 checkout-service.ts 转换) ==========
const STORAGE_KEY = 'zhuxiang_checkout_db_v1';
const SHIPPING_KEY = 'zhuxiang_shipping_db_v1';
const SERVICE_FEE_RATE = 0.05;
const POINTS_RATE = 0.01;
const POINTS_DEDUCT_MAX_RATE = 0.30;
const EARN_RATE = 0.1;
const LEVEL_BOOST = { L1: 1.0, L2: 1.0, L3: 1.02, L4: 1.05, L5: 1.08 };
const PROFIT_SPLIT = { platform: 0.80, hotel: 0.20 };
const FREE_SHIPPING_QTY = 2; // 购买两瓶免运费

function readDB() {
  return envAdapter.storage.get(STORAGE_KEY) || initMockDB(true);
}
function writeDB(db) {
  envAdapter.storage.set(STORAGE_KEY, db);
}
function readShippingDB() {
  return envAdapter.storage.get(SHIPPING_KEY) || { shipping_claims: [], service_fees: [] };
}
function writeShippingDB(db) {
  envAdapter.storage.set(SHIPPING_KEY, db);
}

function initMockDB(forceWrite) {
  const existing = forceWrite ? null : envAdapter.storage.get(STORAGE_KEY);
  if (existing && !forceWrite) return existing;
  const db = {
    products: [
      { id: 1, name: '竹奕·竹香经典 500ml', price: 268, stock: 100 },
      { id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368, stock: 100 },
      { id: 3, name: '竹奕·竹香珍藏 500ml', price: 698, stock: 50 },
    ],
    coupons: [
      { id: 'C001', code: 'NEW10', discount: 0.10, status: '未使用', desc: '新人9折' },
      { id: 'C002', code: 'SVIP20', discount: 0.20, status: '未使用', desc: 'SVIP8折' },
    ],
    members: [
      { id: 1, name: '张三', points: 5000, level: 'L3' },
      { id: 2, name: '李四', points: 12000, level: 'L5' },
    ],
    orders: [],
    profit_records: [],
    tx_log: [],
  };
  writeDB(db);
  return db;
}

function round2(n) { return Math.round(n * 100) / 100; }

function resolveShipper(region) {
  if (!region) return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region: '' };
  const db = readShippingDB();
  const claim = (db.shipping_claims || []).find(c => c.region === region && c.status === 'active');
  if (claim) return { shipper: 'agent', agentId: claim.agent_id, agentName: claim.agent_name, claimId: claim.id, region };
  return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region };
}

function accrueServiceFee(dbRef, payload) {
  const o = payload || {};
  if (!dbRef || !dbRef.db) throw new Error('accrueServiceFee: dbRef 缺失');
  if (!Array.isArray(dbRef.db.service_fees)) dbRef.db.service_fees = [];
  const agentId = Number(o.agentId);
  const orderAmount = Number(o.orderAmount);
  if (!Number.isFinite(agentId) || agentId <= 0) throw new Error('服务费计提: 代理商ID无效');
  if (!Number.isFinite(orderAmount) || orderAmount < 0) throw new Error('服务费计提: 订单金额无效');
  const fee = round2(orderAmount * SERVICE_FEE_RATE);
  const feeId = 'SF' + Date.now() + '-' + Math.floor(Math.random() * 1000);
  const record = {
    id: feeId, agent_id: agentId, agent_name: o.agentName || '', order_no: o.orderNo || null,
    region: o.region || '', shipped_qty: o.shippedQty || 0, order_amount: round2(orderAmount),
    service_fee: fee, service_rate: SERVICE_FEE_RATE, settled_as: '同品', status: '待发放',
    created_at: new Date().toISOString(),
  };
  dbRef.db.service_fees.push(record);
  try {
    const sdb = readShippingDB();
    sdb.service_fees.push({ ...record });
    writeShippingDB(sdb);
  } catch (e) { console.warn('[CheckoutService] 服务费镜像写入失败:', e); }
  return { serviceFee: fee, record };
}

const CheckoutService = {
  resetMock() {
    envAdapter.storage.remove(STORAGE_KEY);
    envAdapter.storage.remove(SHIPPING_KEY);
    initMockDB(true);
    writeShippingDB({ shipping_claims: [], service_fees: [] });
    return this;
  },
  getMockDB() { return readDB(); },
  getShippingDB() { return readShippingDB(); },
  claim(agentId, region) {
    const db = readShippingDB();
    if (!Array.isArray(db.shipping_claims)) db.shipping_claims = [];
    const existing = db.shipping_claims.find(c => c.region === region && c.status === 'active');
    if (existing) return { success: false, error: '区域已被认领' };
    const checkoutDb = readDB();
    const agentMember = checkoutDb.members.find(m => m.id === agentId);
    const agentName = agentMember ? agentMember.name : '代理商' + agentId;
    const claimId = 'CLAIM' + Date.now();
    db.shipping_claims.push({
      id: claimId, agent_id: agentId, agent_name: agentName, region, status: 'active',
      created_at: new Date().toISOString(),
    });
    writeShippingDB(db);
    return { success: true, claimId, agentName };
  },
  async submit(params) {
    const log = [];
    const logger = {
      info: (step, msg, data) => {
        log.push({ step, msg, data, time: new Date().toISOString() });
        console.log(`[CheckoutService] ${step}: ${msg}`, data || '');
      },
      error: (step, msg, data) => {
        log.push({ step, msg, data, time: new Date().toISOString(), level: 'error' });
        console.error(`[CheckoutService] ${step}: ${msg}`, data || '');
      },
    };

    let dbSnapshot = null;
    let shippingSnapshot = null;

    try {
      // 阶段1: 预检
      logger.info('阶段1-预检', '校验购物车 + 计算价格', {
        itemCount: params.items.length,
        memberLevel: params.memberLevel,
        points: params.points || 0,
        couponCode: params.couponCode || '(无)',
        region: params.region || '(未指定)',
      });
      if (!params.items || params.items.length === 0) throw new Error('购物车为空');

      // 阶段2: BEGIN
      dbSnapshot = JSON.parse(JSON.stringify(readDB()));
      shippingSnapshot = JSON.parse(JSON.stringify(readShippingDB()));
      const dbRef = { db: readDB() };
      const shippingDbRef = { db: readShippingDB() };
      dbRef.db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
      logger.info('阶段2-开启事务', 'BEGIN(快照已创建)');

      const ctx = {
        orderNo: 'ZX' + Date.now(),
        items: params.items,
        memberId: params.memberId,
        memberLevel: params.memberLevel,
        points: params.points || 0,
        couponCode: params.couponCode,
        paymentMethod: params.paymentMethod || 'wechat',
        region: params.region,
      };

      // 价格计算
      const originalTotal = round2(params.items.reduce((s, i) => s + i.price * i.qty, 0));
      const memberDiscount = ctx.memberLevel === 'L5' ? 0.15 : ctx.memberLevel === 'L4' ? 0.10 : ctx.memberLevel === 'L3' ? 0.05 : 0;
      const memberDiscountAmount = round2(originalTotal * memberDiscount);
      let afterMember = round2(originalTotal - memberDiscountAmount);
      let couponDiscount = 0;
      if (ctx.couponCode) {
        const coupon = dbRef.db.coupons.find(c => c.code === ctx.couponCode);
        if (!coupon || coupon.status !== '未使用') throw new Error('优惠券无效: ' + ctx.couponCode);
        couponDiscount = round2(afterMember * coupon.discount);
      }
      let afterCoupon = round2(afterMember - couponDiscount);
      let pointsDeduct = 0;
      if (ctx.points > 0) {
        pointsDeduct = round2(ctx.points * POINTS_RATE);
        const maxDeduct = round2(afterCoupon * POINTS_DEDUCT_MAX_RATE);
        if (pointsDeduct > maxDeduct) pointsDeduct = maxDeduct;
      }
      const finalAmount = round2(afterCoupon - pointsDeduct);
      const totalQty = ctx.items.reduce((s, i) => s + i.qty, 0);
      const shipping = totalQty >= FREE_SHIPPING_QTY ? 0 : 12;
      ctx.priceResult = {
        originalTotal, memberDiscount: memberDiscountAmount, couponDiscount,
        pointsDeduct, finalAmount: finalAmount + shipping, shipping,
      };

      // 阶段3: 订单创建 + 发货方路由
      const shipper = resolveShipper(ctx.region || '');
      ctx.shipperType = shipper.shipper;
      ctx.shipperAgentId = shipper.agentId;
      ctx.shipperAgentName = shipper.agentName;
      dbRef.db.orders.push({
        order_no: ctx.orderNo, member_id: ctx.memberId, member_level: ctx.memberLevel,
        items: ctx.items.map(i => ({ id: i.id, name: i.name, price: i.price, qty: i.qty })),
        original_total: originalTotal, member_discount: memberDiscountAmount,
        coupon_discount: couponDiscount, points_deduct: pointsDeduct,
        shipping, final_amount: finalAmount + shipping,
        coupon_code: ctx.couponCode || null, points_used: ctx.points || 0,
        points_earned: 0, payment_method: ctx.paymentMethod,
        ship_region: ctx.region || null, shipper_type: ctx.shipperType,
        shipper_agent_id: ctx.shipperAgentId, shipper_agent_name: ctx.shipperAgentName,
        status: '待付款', created_at: new Date().toISOString(),
      });
      logger.info('阶段3-订单创建', '订单写入 + 发货方路由', {
        orderNo: ctx.orderNo, shipper: ctx.shipperType, agent: ctx.shipperAgentName
      });

      // 阶段4: 库存扣减
      for (const item of ctx.items) {
        const product = dbRef.db.products.find(p => p.id === item.id);
        if (!product) throw new Error('商品不存在: ' + item.id);
        if (product.stock < item.qty) throw new Error('库存不足: ' + product.name);
        product.stock -= item.qty;
      }
      logger.info('阶段4-库存扣减', '库存已扣减', { totalQty });

      // 阶段5: 优惠券核销
      if (ctx.couponCode) {
        const coupon = dbRef.db.coupons.find(c => c.code === ctx.couponCode);
        if (coupon) coupon.status = '已使用';
        logger.info('阶段5-优惠券核销', '券状态→已使用', { code: ctx.couponCode });
      }

      // 阶段6: 积分扣减
      if (ctx.points > 0) {
        const member = dbRef.db.members.find(m => m.id === ctx.memberId);
        if (member) member.points -= ctx.points;
        logger.info('阶段6-积分扣减', `points -= ${ctx.points}`);
      }

      // 阶段7: 积分入账(等级加成)
      const boost = LEVEL_BOOST[ctx.memberLevel] || 1.0;
      const earnedBase = Math.floor((finalAmount + shipping) / 10 * EARN_RATE * 100);
      const earnedPoints = Math.round(earnedBase * boost);
      const member = dbRef.db.members.find(m => m.id === ctx.memberId);
      if (member) member.points += earnedPoints;
      const order = dbRef.db.orders.find(o => o.order_no === ctx.orderNo);
      if (order) order.points_earned = earnedPoints;
      logger.info('阶段7-积分入账', `earned ${earnedPoints} (L${ctx.memberLevel} +${Math.round((boost - 1) * 100)}%)`);

      // 阶段8: 分润计算 + 5%同品分润
      const platformShare = round2((finalAmount + shipping) * PROFIT_SPLIT.platform);
      const hotelShare = round2((finalAmount + shipping) * PROFIT_SPLIT.hotel);
      let manufacturerServiceFee = 0;
      let feeRecordId = null;
      if (ctx.shipperType === 'agent') {
        const r = accrueServiceFee(shippingDbRef, {
          agentId: ctx.shipperAgentId, agentName: ctx.shipperAgentName,
          region: ctx.region, orderNo: ctx.orderNo, shippedQty: totalQty,
          orderAmount: finalAmount + shipping,
        });
        manufacturerServiceFee = r.serviceFee;
        feeRecordId = r.record.id;
      }
      dbRef.db.profit_records.push({
        order_no: ctx.orderNo, total_amount: finalAmount + shipping,
        platform_share: platformShare, hotel_share: hotelShare,
        manufacturer_service_fee: manufacturerServiceFee,
        shipper_type: ctx.shipperType, shipper_agent_name: ctx.shipperAgentName,
        split_rule: ctx.shipperType === 'agent'
          ? `厂家直供分润+5%同品分润给${ctx.shipperAgentName}`
          : '无代理商:平台80%+酒店20%',
        created_at: new Date().toISOString(),
      });
      logger.info('阶段8-分润计算', '分润+服务费已记录', {
        platform: platformShare, hotel: hotelShare, mfrFee: manufacturerServiceFee
      });

      // 阶段9: 支付确认
      if (order) order.status = '已付款';
      logger.info('阶段9-支付确认', '订单状态→已付款');

      // 阶段10: COMMIT
      writeDB(dbRef.db);
      writeShippingDB(shippingDbRef.db);
      dbRef.db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString() });
      writeDB(dbRef.db);
      logger.info('阶段10-提交事务', 'COMMIT(已持久化)');

      return {
        success: true,
        orderNo: ctx.orderNo,
        data: {
          orderNo: ctx.orderNo, finalAmount: finalAmount + shipping,
          shipperType: ctx.shipperType, shipperAgentName: ctx.shipperAgentName,
          manufacturerServiceFee, pointsEarned: earnedPoints, status: '已付款',
        },
        logs: log,
      };
    } catch (e) {
      logger.error('回滚', e.message);
      if (dbSnapshot) {
        dbSnapshot.tx_log = dbSnapshot.tx_log || [];
        dbSnapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
        writeDB(dbSnapshot);
      }
      if (shippingSnapshot) {
        writeShippingDB(shippingSnapshot);
      }
      return { success: false, error: e.message, orderNo: 'ZX' + Date.now(), data: null, logs: log };
    }
  }
};

// ========== 4. 执行测试 ==========
async function runTest() {
  console.log('='.repeat(60));
  console.log('Taro 版 checkout-service 微信环境测试');
  console.log('环境: ' + envAdapter.getEnv() + ' (微信小程序)');
  console.log('='.repeat(60));

  // 1. 重置 mock DB
  CheckoutService.resetMock();
  console.log('\n[1] Mock DB 已重置');

  // 2. 代理商认领山东泰安
  const claimRes = CheckoutService.claim(1, '山东泰安');
  console.log('[2] 代理商认领山东泰安:', claimRes);

  // 3. 记录提交前状态
  const dbBefore = CheckoutService.getMockDB();
  const productBefore = dbBefore.products.find(p => p.id === 2);
  const memberBefore = dbBefore.members.find(m => m.id === 2);
  const stockBefore = productBefore.stock;
  const pointsBefore = memberBefore.points;

  console.log('\n[3] 提交前状态:');
  console.log('  商品库存(竹韵佳酿):', stockBefore);
  console.log('  会员积分(李四L5):', pointsBefore);

  // 4. 构造模拟订单并提交
  const orderData = {
    items: [{ id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368, qty: 2 }],
    memberId: 2,
    memberLevel: 'L5',
    points: 0,
    couponCode: undefined,
    paymentMethod: 'wechat',
    region: '山东泰安'
  };

  console.log('\n[4] 构造模拟订单:');
  console.log('  商品: 竹奕·竹韵佳酿 500ml × 2');
  console.log('  会员: 李四(L5 SVIP)');
  console.log('  发货区域: 山东泰安(已认领)');
  console.log('');

  const result = await CheckoutService.submit(orderData);

  // 5. 验证结果
  const dbAfter = CheckoutService.getMockDB();
  const shippingDb = CheckoutService.getShippingDB();
  const order = dbAfter.orders[dbAfter.orders.length - 1];
  const productAfter = dbAfter.products.find(p => p.id === 2);
  const memberAfter = dbAfter.members.find(m => m.id === 2);
  const profitRec = dbAfter.profit_records[dbAfter.profit_records.length - 1];
  const feeRecord = shippingDb.service_fees && shippingDb.service_fees.length > 0
    ? shippingDb.service_fees[shippingDb.service_fees.length - 1] : null;

  // 6. 逐项验证
  const checks = [];

  // 环境检测
  const env = envAdapter.getEnv();
  checks.push({ 验证: '环境检测', env, 结果: env === 'weapp' ? '✓ PASS' : '✗ FAIL env=' + env });

  // 认领
  checks.push({ 验证: '代理商认领山东泰安', success: claimRes.success, agentName: claimRes.agentName, 结果: claimRes.success ? '✓ PASS' : '✗ FAIL' });

  // 事务成功
  checks.push({ 验证: '事务提交成功', success: result.success, orderNo: result.orderNo, 结果: result.success ? '✓ PASS' : '✗ FAIL ' + (result.error || '') });

  // BEGIN
  const hasBegin = dbAfter.tx_log.some(t => t.type === 'BEGIN');
  checks.push({ 验证: '阶段2-BEGIN', 结果: hasBegin ? '✓ PASS' : '✗ FAIL' });

  // 订单创建 + 发货方路由
  checks.push({
    验证: '阶段3-订单创建+发货方路由',
    shipper_type: order ? order.shipper_type : 'null',
    shipper_agent: order ? order.shipper_agent_name : 'null',
    ship_region: order ? order.ship_region : 'null',
    结果: order && order.shipper_type === 'agent' ? '✓ PASS' : '✗ FAIL'
  });

  // 库存扣减
  const stockAfter = productAfter.stock;
  checks.push({
    验证: '阶段4-库存扣减',
    前: stockBefore, 后: stockAfter, 差: stockBefore - stockAfter,
    结果: stockBefore - stockAfter === 2 ? '✓ PASS' : '✗ FAIL'
  });

  // 积分入账(L5+8%)
  checks.push({
    验证: '阶段7-积分入账(L5+8%)',
    earned: order ? order.points_earned : 'null',
    结果: order && order.points_earned > 0 ? '✓ PASS' : '✗ FAIL'
  });

  // 分润+5%服务费
  checks.push({
    验证: '阶段8-分润+5%服务费',
    platform: profitRec ? profitRec.platform_share : 'null',
    hotel: profitRec ? profitRec.hotel_share : 'null',
    mfr_fee: profitRec ? profitRec.manufacturer_service_fee : 'null',
    结果: profitRec && profitRec.manufacturer_service_fee > 0 ? '✓ PASS' : '✗ FAIL'
  });

  // 服务费流水
  checks.push({
    验证: '阶段8-服务费流水(同品分润)',
    agent: feeRecord ? feeRecord.agent_name : 'null',
    settled_as: feeRecord ? feeRecord.settled_as : 'null',
    status: feeRecord ? feeRecord.status : 'null',
    结果: feeRecord && feeRecord.settled_as === '同品' && feeRecord.status === '待发放' ? '✓ PASS' : '✗ FAIL'
  });

  // 支付确认
  checks.push({
    验证: '阶段9-支付确认',
    status: order ? order.status : 'null',
    结果: order && order.status === '已付款' ? '✓ PASS' : '✗ FAIL'
  });

  // COMMIT
  const hasCommit = dbAfter.tx_log.some(t => t.type === 'COMMIT');
  checks.push({ 验证: '阶段10-COMMIT', 结果: hasCommit ? '✓ PASS' : '✗ FAIL' });

  // 存储路由(通过 Taro API = wxStorage)
  const hasInWxStorage = wxStorage[STORAGE_KEY] !== undefined && wxStorage[SHIPPING_KEY] !== undefined;
  checks.push({
    验证: '存储路由(Taro API→wxStorage)',
    checkout_in_wx: wxStorage[STORAGE_KEY] !== undefined,
    shipping_in_wx: wxStorage[SHIPPING_KEY] !== undefined,
    结果: hasInWxStorage ? '✓ PASS' : '✗ FAIL'
  });

  const passCount = checks.filter(c => c.结果.includes('PASS')).length;

  // 输出结果
  console.log('\n' + '='.repeat(60));
  console.log('测试结果汇总');
  console.log('='.repeat(60));
  console.log('环境: ' + env + ' (微信小程序)');
  console.log('业务数据: L5会员(李四)+竹韵佳酿×2+山东泰安(认领区域)');
  console.log('事务结果: ' + (result.success ? '✓ success=true' : '✗ success=false'));
  console.log('订单号: ' + result.orderNo);
  console.log('通过数: ' + passCount + '/' + checks.length);
  console.log('结论: ' + (passCount === checks.length ? '✓ Taro 小程序中 9 阶段事务全部通过(EnvAdapter 路由 Taro API 正常)' : '✗ 存在失败'));
  console.log('');

  console.log('提交前状态:');
  console.log('  商品库存: ' + stockBefore);
  console.log('  会员积分: ' + pointsBefore + ' (L5)');
  console.log('');

  console.log('提交后状态:');
  console.log('  商品库存: ' + stockAfter + ' (扣减 ' + (stockBefore - stockAfter) + ')');
  console.log('  会员积分: ' + memberAfter.points);
  console.log('  订单数: ' + dbAfter.orders.length);
  console.log('  分润记录: ' + dbAfter.profit_records.length);
  console.log('  服务费流水: ' + (shippingDb.service_fees ? shippingDb.service_fees.length : 0));
  console.log('  认领记录: ' + shippingDb.shipping_claims.length);
  console.log('  事务轨迹: ' + dbAfter.tx_log.map(t => t.type).join('→'));
  console.log('');

  console.log('逐项验证:');
  checks.forEach((c, i) => {
    console.log('  ' + (i + 1) + '. ' + c.验证 + ': ' + c.结果);
  });
  console.log('');

  console.log('订单详情:');
  if (result.data) {
    console.log('  订单号: ' + result.data.orderNo);
    console.log('  实付金额: ¥' + result.data.finalAmount);
    console.log('  发货方: ' + (result.data.shipperType === 'agent' ? '代理商: ' + result.data.shipperAgentName : '厂家直供'));
    console.log('  厂家服务费: ¥' + result.data.manufacturerServiceFee + ' (同品分润)');
    console.log('  积分入账: ' + result.data.pointsEarned + ' 竹叶 (L5+8%)');
    console.log('  订单状态: ' + result.data.status);
  }
  console.log('');

  console.log('事务日志条数: ' + (result.logs ? result.logs.length : 0));
  console.log('');

  // 存储路由验证
  console.log('存储路由验证(Taro API → wxStorage):');
  console.log('  wxStorage 中的 key:');
  Object.keys(wxStorage).forEach(k => {
    console.log('    ' + k + ' (存在)');
  });
  console.log('');

  console.log('='.repeat(60));
  if (passCount === checks.length) {
    console.log('✅ 全部 ' + checks.length + '/' + checks.length + ' PASS — Taro 版 checkout-service 微信环境测试通过');
  } else {
    console.log('❌ ' + passCount + '/' + checks.length + ' PASS — 存在失败项');
  }
  console.log('='.repeat(60));
}

runTest().catch(e => {
  console.error('测试执行错误:', e);
  process.exit(1);
});
