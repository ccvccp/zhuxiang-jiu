/**
 * checkout-service.ts · 订单结算服务（Taro 多端版）
 * ============================================================
 * 基于 EnvAdapter.storage 路由到 Taro API（微信/支付宝/H5 多端）
 * 9 阶段事务: 预检→BEGIN→订单创建+发货路由→库存扣减→券核销→
 *   积分扣减→积分入账→分润+5%服务费→支付确认→COMMIT
 * ============================================================
 */

import Taro from '@tarojs/taro';
import EnvAdapter from '@/utils/env-adapter';

const STORAGE_KEY = 'zhuxiang_checkout_db_v1';
const SHIPPING_KEY = 'zhuxiang_shipping_db_v1';
const SERVICE_FEE_RATE = 0.05;
const POINTS_RATE = 0.01;
const POINTS_DEDUCT_MAX_RATE = 0.30;
const EARN_RATE = 0.1;
const LEVEL_BOOST: Record<string, number> = { L1: 1.0, L2: 1.0, L3: 1.02, L4: 1.05, L5: 1.08 };
const PROFIT_SPLIT = { platform: 0.80, hotel: 0.20 };
const FREE_SHIPPING_QTY = 2; // 购买两瓶免运费

interface OrderItem {
  id: number;
  name: string;
  price: number;
  qty: number;
}

interface SubmitParams {
  items: OrderItem[];
  memberId: number;
  memberLevel: string;
  points?: number;
  couponCode?: string;
  paymentMethod?: string;
  region?: string;
}

function readDB() {
  return EnvAdapter.storage.get(STORAGE_KEY) || initMockDB(true);
}

function writeDB(db: any) {
  EnvAdapter.storage.set(STORAGE_KEY, db);
}

function readShippingDB() {
  return EnvAdapter.storage.get(SHIPPING_KEY) || { shipping_claims: [], service_fees: [] };
}

function writeShippingDB(db: any) {
  EnvAdapter.storage.set(SHIPPING_KEY, db);
}

function initMockDB(forceWrite?: boolean) {
  const existing = forceWrite ? null : EnvAdapter.storage.get(STORAGE_KEY);
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
    orders: [] as any[],
    profit_records: [] as any[],
    tx_log: [] as any[],
  };
  writeDB(db);
  return db;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

// 发货方路由(只读)
function resolveShipper(region: string) {
  if (!region) {
    return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region: region || '' };
  }
  const db = readShippingDB();
  const claim = (db.shipping_claims || []).find((c: any) => c.region === region && c.status === 'active');
  if (claim) {
    return { shipper: 'agent', agentId: claim.agent_id, agentName: claim.agent_name, claimId: claim.id, region };
  }
  return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region };
}

// 5%同品分润服务费计提(共享核心,在调用方事务内执行)
function accrueServiceFee(dbRef: any, payload: any) {
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
    id: feeId,
    agent_id: agentId,
    agent_name: o.agentName || '',
    order_no: o.orderNo || null,
    region: o.region || '',
    shipped_qty: o.shippedQty || 0,
    order_amount: round2(orderAmount),
    service_fee: fee,
    service_rate: SERVICE_FEE_RATE,
    settled_as: '同品',
    status: '待发放',
    created_at: new Date().toISOString(),
  };
  dbRef.db.service_fees.push(record);

  // 镜像写入 shipping DB
  try {
    const sdb = readShippingDB();
    sdb.service_fees.push({ ...record });
    writeShippingDB(sdb);
  } catch (e) {
    console.warn('[CheckoutService] 服务费镜像写入失败:', e);
  }

  return { serviceFee: fee, record };
}

// 防重入:连点提交时直接拒绝,避免重复事务(对齐 H5 版 _submitInFlight)
let _submitInFlight = false;

export const CheckoutService = {
  resetMock() {
    EnvAdapter.storage.remove(STORAGE_KEY);
    EnvAdapter.storage.remove(SHIPPING_KEY);
    initMockDB(true);
    writeShippingDB({ shipping_claims: [], service_fees: [] });
    return this;
  },

  getMockDB() {
    return readDB();
  },

  getShippingDB() {
    return readShippingDB();
  },

  // 代理商认领区域
  // 注: 同步操作(JS 单线程天然串行),无需锁;跨设备并发由后端
  // shipping_service.claim 的 Redis 锁保证(STORE_MODE=redis 时)
  claim(agentId: number, region: string) {
    const db = readShippingDB();
    if (!Array.isArray(db.shipping_claims)) db.shipping_claims = [];
    const existing = db.shipping_claims.find((c: any) => c.region === region && c.status === 'active');
    if (existing) {
      return { success: false, error: '区域已被认领' };
    }
    // 查找代理商名称
    const checkoutDb = readDB();
    const agentMember = checkoutDb.members.find((m: any) => m.id === agentId);
    const agentName = agentMember ? agentMember.name : '代理商' + agentId;
    const claimId = 'CLAIM' + Date.now();
    db.shipping_claims.push({
      id: claimId,
      agent_id: agentId,
      agent_name: agentName,
      region,
      status: 'active',
      created_at: new Date().toISOString(),
    });
    writeShippingDB(db);
    return { success: true, claimId, agentName };
  },

  async submit(params: SubmitParams): Promise<any> {
    // 防重入:连点提交时直接拒绝,避免重复事务(对齐 H5 版 _submitInFlight)
    if (_submitInFlight) {
      return {
        success: false,
        error: '订单提交进行中,请勿重复点击',
        logs: [{ step: '防重入', msg: '提交被防重入拦截', time: new Date().toISOString() }],
      };
    }
    _submitInFlight = true;

    const log: any[] = [];
    const logger = {
      info: (step: string, msg: string, data?: any) => {
        log.push({ step, msg, data, time: new Date().toISOString() });
        console.log(`[CheckoutService] ${step}: ${msg}`, data || '');
      },
      error: (step: string, msg: string, data?: any) => {
        log.push({ step, msg, data, time: new Date().toISOString(), level: 'error' });
        console.error(`[CheckoutService] ${step}: ${msg}`, data || '');
      },
    };

    // 快照变量提升到 try 块之前(catch 块需要访问以恢复事务)
    let dbSnapshot: any = null;
    let shippingSnapshot: any = null;

    try {
      // ========== 预检 ==========
      logger.info('阶段1-预检', '校验购物车 + 计算价格', {
        itemCount: params.items.length,
        memberLevel: params.memberLevel,
        points: params.points || 0,
        couponCode: params.couponCode || '(无)',
        region: params.region || '(未指定)',
      });

      if (!params.items || params.items.length === 0) {
        throw new Error('购物车为空');
      }

      // 快照(事务 BEGIN)
      dbSnapshot = JSON.parse(JSON.stringify(readDB()));
      shippingSnapshot = JSON.parse(JSON.stringify(readShippingDB()));
      const dbRef = { db: readDB() };
      const shippingDbRef = { db: readShippingDB() };

      dbRef.db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
      logger.info('阶段2-开启事务', 'BEGIN(快照已创建)');

      const ctx: any = {
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

      // 优惠券
      let couponDiscount = 0;
      if (ctx.couponCode) {
        const coupon = dbRef.db.coupons.find((c: any) => c.code === ctx.couponCode);
        if (!coupon || coupon.status !== '未使用') throw new Error('优惠券无效: ' + ctx.couponCode);
        couponDiscount = round2(afterMember * coupon.discount);
        ctx.couponDiscount = couponDiscount;
      }
      let afterCoupon = round2(afterMember - couponDiscount);

      // 积分抵扣
      let pointsDeduct = 0;
      if (ctx.points > 0) {
        pointsDeduct = round2(ctx.points * POINTS_RATE);
        const maxDeduct = round2(afterCoupon * POINTS_DEDUCT_MAX_RATE);
        if (pointsDeduct > maxDeduct) pointsDeduct = maxDeduct;
      }
      const finalAmount = round2(afterCoupon - pointsDeduct);

      // 运费(购买两瓶免运费)
      const totalQty = ctx.items.reduce((s: number, i: OrderItem) => s + i.qty, 0);
      const shipping = totalQty >= FREE_SHIPPING_QTY ? 0 : 12;

      ctx.priceResult = {
        originalTotal, memberDiscount: memberDiscountAmount, couponDiscount,
        pointsDeduct, finalAmount: finalAmount + shipping, shipping,
      };

      // ========== 阶段3: 订单创建 + 发货方路由 ==========
      const shipper = resolveShipper(ctx.region || '');
      ctx.shipperType = shipper.shipper;
      ctx.shipperAgentId = shipper.agentId;
      ctx.shipperAgentName = shipper.agentName;

      dbRef.db.orders.push({
        order_no: ctx.orderNo,
        member_id: ctx.memberId,
        member_level: ctx.memberLevel,
        items: ctx.items.map(i => ({ id: i.id, name: i.name, price: i.price, qty: i.qty })),
        original_total: originalTotal,
        member_discount: memberDiscountAmount,
        coupon_discount: couponDiscount,
        points_deduct: pointsDeduct,
        shipping,
        final_amount: finalAmount + shipping,
        coupon_code: ctx.couponCode || null,
        points_used: ctx.points || 0,
        points_earned: 0,
        payment_method: ctx.paymentMethod,
        ship_region: ctx.region || null,
        shipper_type: ctx.shipperType,
        shipper_agent_id: ctx.shipperAgentId,
        shipper_agent_name: ctx.shipperAgentName,
        status: '待付款',
        created_at: new Date().toISOString(),
      });
      logger.info('阶段3-订单创建', '订单写入 + 发货方路由', {
        orderNo: ctx.orderNo, shipper: ctx.shipperType, agent: ctx.shipperAgentName
      });

      // ========== 阶段4: 库存扣减 ==========
      for (const item of ctx.items) {
        const product = dbRef.db.products.find((p: any) => p.id === item.id);
        if (!product) throw new Error('商品不存在: ' + item.id);
        if (product.stock < item.qty) throw new Error('库存不足: ' + product.name);
        product.stock -= item.qty;
      }
      logger.info('阶段4-库存扣减', '库存已扣减', { totalQty });

      // ========== 阶段5: 优惠券核销 ==========
      if (ctx.couponCode) {
        const coupon = dbRef.db.coupons.find((c: any) => c.code === ctx.couponCode);
        if (coupon) coupon.status = '已使用';
        logger.info('阶段5-优惠券核销', '券状态→已使用', { code: ctx.couponCode });
      }

      // ========== 阶段6: 积分扣减 ==========
      if (ctx.points > 0) {
        const member = dbRef.db.members.find((m: any) => m.id === ctx.memberId);
        if (member) member.points -= ctx.points;
        logger.info('阶段6-积分扣减', `points -= ${ctx.points}`);
      }

      // ========== 阶段7: 积分入账(等级加成) ==========
      const boost = LEVEL_BOOST[ctx.memberLevel] || 1.0;
      const earnedBase = Math.floor((finalAmount + shipping) / 10 * EARN_RATE * 100);
      const earnedPoints = Math.round(earnedBase * boost);
      const member = dbRef.db.members.find((m: any) => m.id === ctx.memberId);
      if (member) member.points += earnedPoints;
      const order = dbRef.db.orders.find((o: any) => o.order_no === ctx.orderNo);
      if (order) order.points_earned = earnedPoints;
      logger.info('阶段7-积分入账', `earned ${earnedPoints} (L${ctx.memberLevel} +${Math.round((boost - 1) * 100)}%)`);

      // ========== 阶段8: 分润计算 + 5%同品分润服务费 ==========
      const platformShare = round2((finalAmount + shipping) * PROFIT_SPLIT.platform);
      const hotelShare = round2((finalAmount + shipping) * PROFIT_SPLIT.hotel);
      let manufacturerServiceFee = 0;
      let feeRecordId: string | null = null;

      if (ctx.shipperType === 'agent') {
        const r = accrueServiceFee(shippingDbRef, {
          agentId: ctx.shipperAgentId,
          agentName: ctx.shipperAgentName,
          region: ctx.region,
          orderNo: ctx.orderNo,
          shippedQty: totalQty,
          orderAmount: finalAmount + shipping,
        });
        manufacturerServiceFee = r.serviceFee;
        feeRecordId = r.record.id;
      }

      dbRef.db.profit_records.push({
        order_no: ctx.orderNo,
        total_amount: finalAmount + shipping,
        platform_share: platformShare,
        hotel_share: hotelShare,
        manufacturer_service_fee: manufacturerServiceFee,
        shipper_type: ctx.shipperType,
        shipper_agent_name: ctx.shipperAgentName,
        split_rule: ctx.shipperType === 'agent'
          ? `厂家直供分润+5%同品分润给${ctx.shipperAgentName}`
          : '无代理商:平台80%+酒店20%',
        created_at: new Date().toISOString(),
      });
      logger.info('阶段8-分润计算', '分润+服务费已记录', {
        platform: platformShare, hotel: hotelShare, mfrFee: manufacturerServiceFee
      });

      // ========== 阶段9: 支付确认 ==========
      if (order) order.status = '已付款';
      logger.info('阶段9-支付确认', '订单状态→已付款');

      // ========== COMMIT ==========
      writeDB(dbRef.db);
      writeShippingDB(shippingDbRef.db);
      dbRef.db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString() });
      // 重新写入包含 tx_log 的 db
      writeDB(dbRef.db);
      logger.info('阶段10-提交事务', 'COMMIT(已持久化)');

      return {
        success: true,
        orderNo: ctx.orderNo,
        data: {
          orderNo: ctx.orderNo,
          finalAmount: finalAmount + shipping,
          shipperType: ctx.shipperType,
          shipperAgentName: ctx.shipperAgentName,
          manufacturerServiceFee,
          pointsEarned: earnedPoints,
          status: '已付款',
        },
        logs: log,
      };
    } catch (e: any) {
      logger.error('回滚', e.message);
      // ROLLBACK: 恢复事务前快照(与 H5 版 rollback 顺序一致)
      if (dbSnapshot) {
        dbSnapshot.tx_log = dbSnapshot.tx_log || [];
        dbSnapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
        writeDB(dbSnapshot);
      }
      if (shippingSnapshot) {
        writeShippingDB(shippingSnapshot);
      }
      return {
        success: false,
        error: e.message,
        orderNo: 'ZX' + Date.now(),
        data: null,
        logs: log,
      };
    } finally {
      // 释放防重入(无论成功/失败/异常都释放,避免后续提交永久阻塞)
      _submitInFlight = false;
    }
  }
};

export default CheckoutService;
