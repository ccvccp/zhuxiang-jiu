/**
 * 信值兑换 API · 对接后端 /api/xx64/*（64号模块会员面）
 * 积分→信值兑换 / 兑换下单锁值支付 / 最优支付组合 / 规则解释 / 风险画像 / 申诉
 *
 * 注: 鉴权口径 X-Role: member; trustId 为信值档案 ID(本项目以会员 ID 同值建档)
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';
import Taro from '@tarojs/taro';

/** 订单九态 */
export const ORDER_STATUS_NAME: Record<string, string> = {
  reserved: '已锁值',
  paid: '已支付',
  cancelled: '已取消',
  refunded: '已退款',
  disputed: '申诉中',
};

/** 会员 ID(建档与查询默认身份) */
export function myTrustId(): number {
  return Number(getMemberId()) || 0;
}

/** 本地缓存的信值档案 ID(建档后由服务端分配, 跨会话持久) */
const TRUST_ID_KEY = 'trust_id_cache';
export function getCachedTrustId(): number | null {
  const v = Taro.getStorageSync(TRUST_ID_KEY) as string | number;
  const n = Number(v);
  return v && n > 0 ? n : null;
}
export function setCachedTrustId(trustId: number): void {
  Taro.setStorageSync(TRUST_ID_KEY, String(trustId));
}

export interface Xx64OrderVO {
  orderId: number;
  buyerId: number;
  sellerId: number;
  trustId: number;
  price: number;
  product: string;
  status: string;
  createdAt: string;
}

export interface PointsPreviewVO {
  rate: string;
  pendingValue: number;    // 冻结观察中信值
  creditedValue: number;   // 已入账信值
  note: string;
}

export interface QuotaVO {
  balance: number;
  singleQuota: number;      // 单次 20% 基准
  windowUsed: number;
  cumulativeQuota: number; // 窗口 40% 基准
  windowRemaining: number;
  windowDays: number;
}

export interface PlanVO {
  price: number;
  balance: number;
  planA: {
    label: string;
    feasible: boolean;
    trustValue: number;
    cash: number;
    saving: number;
    gap: number;
    gapPoints: number;
  };
  planB: {
    label: string;
    cash: number;
  };
}

function memberHeaders(): Record<string, string> {
  return { 'X-Role': 'member' };
}

export const Xx64API = {
  /** 自助建档(45号: person 个人 / org 企业; 证件号只留摘要; 返回 trustId) */
  async createTrustRole(params: {
    role: 'person' | 'org';
    name: string;
    idNumber: string;
  }): Promise<{ trustId: number; tier: string }> {
    const res = await request<any>({
      url: '/api/trust/roles',
      method: 'POST',
      data: {
        role: params.role,
        name: params.name,
        idNumber: params.idNumber,
      },
    });
    const d = res.data || res;
    const trustId = Number(d.trustId ?? d.trust_id ?? 0);
    if (trustId > 0) setCachedTrustId(trustId);
    return { trustId, tier: d.tier || '' };
  },

  /** 查询信值档案视图(404 = 未建档) */
  async trustProfile(trustId: number): Promise<any> {
    return await request<any>({ url: `/api/trust/roles/${trustId}` });
  },

  /** 换算预览(100:1 + 冻结/已入账统计; 观测面不受开关影响) */
  async pointsPreview(trustId: number, neededTrust?: number): Promise<PointsPreviewVO> {
    const qs = neededTrust != null ? `?trust_id=${trustId}&needed_trust=${neededTrust}` : `?trust_id=${trustId}`;
    const res = await request<any>({
      url: `/api/xx64/points/preview${qs}`,
      headers: memberHeaders(),
    });
    const d = res.data || res;
    return {
      rate: d.rate || '1 信值 = 100 积分',
      pendingValue: d.pendingValue ?? 0,
      creditedValue: d.creditedValue ?? 0,
      note: d.note || '',
    };
  },

  /** 积分→信值兑换(决策面: XX64_MODE off 时 409) */
  async exchangePoints(points: number): Promise<any> {
    const uid = myTrustId();
    return await request<any>({
      url: '/api/xx64/points/exchange',
      method: 'POST',
      headers: memberHeaders(),
      data: { userId: uid, trustId: uid, points },
    });
  },

  /** 创建兑换订单+锁值(决策面 off 409) */
  async createOrder(params: {
    sellerId: number;
    price: number;
    product: string;
    useTrust?: boolean;
  }): Promise<Xx64OrderVO> {
    const uid = myTrustId();
    const res = await request<any>({
      url: '/api/xx64/orders',
      method: 'POST',
      headers: memberHeaders(),
      data: {
        buyerId: uid,
        sellerId: params.sellerId,
        trustId: uid,
        price: params.price,
        product: params.product,
        useTrust: params.useTrust ?? true,
      },
    });
    const d = res.data || res;
    return {
      orderId: d.orderId ?? d.order_id ?? 0,
      buyerId: d.buyerId ?? d.buyer_id ?? uid,
      sellerId: d.sellerId ?? d.seller_id ?? 0,
      trustId: d.trustId ?? d.trust_id ?? uid,
      price: d.price ?? 0,
      product: d.product || '',
      status: d.status || '',
      createdAt: d.createdAt || d.created_at || '',
    };
  },

  /** 订单支付(买扣卖增原子转移; 决策面 off 409) */
  async payOrder(orderId: number): Promise<any> {
    return await request<any>({
      url: `/api/xx64/orders/${orderId}/pay`,
      method: 'POST',
      headers: memberHeaders(),
      data: { paidBy: 'member' },
    });
  },

  /** 订单取消(解锁信值; 决策面 off 409) */
  async cancelOrder(orderId: number): Promise<any> {
    return await request<any>({
      url: `/api/xx64/orders/${orderId}/cancel`,
      method: 'POST',
      headers: memberHeaders(),
      data: { cancelledBy: 'member' },
    });
  },

  /** 订单详情(观测面) */
  async orderDetail(orderId: number): Promise<Xx64OrderVO> {
    const res = await request<any>({
      url: `/api/xx64/orders/${orderId}`,
      headers: memberHeaders(),
    });
    const d = res.data || res;
    return {
      orderId: d.orderId ?? d.order_id ?? orderId,
      buyerId: d.buyerId ?? 0,
      sellerId: d.sellerId ?? 0,
      trustId: d.trustId ?? 0,
      price: d.price ?? 0,
      product: d.product || '',
      status: d.status || '',
      createdAt: d.createdAt || d.created_at || '',
    };
  },

  /** 最优支付组合(planA 信值30%+现金70% / planB 纯现金; 观测面) */
  async paymentPlan(trustId: number, price: number, discountValue = 0): Promise<PlanVO> {
    const res = await request<any>({
      url: `/api/xx64/plan?trust_id=${trustId}&price=${price}&discount_value=${discountValue}`,
      headers: memberHeaders(),
    });
    const d = res.data || res;
    return {
      price: d.price ?? price,
      balance: d.balance ?? 0,
      planA: {
        label: d.planA?.label || '信值支付(30%信值+70%现金)',
        feasible: Boolean(d.planA?.feasible),
        trustValue: d.planA?.trustValue ?? 0,
        cash: d.planA?.cash ?? 0,
        saving: d.planA?.saving ?? 0,
        gap: d.planA?.gap ?? 0,
        gapPoints: d.planA?.gapPoints ?? 0,
      },
      planB: {
        label: d.planB?.label || '优惠活动(纯现金)',
        cash: d.planB?.cash ?? price,
      },
    };
  },

  /** 订单规则解释("为什么这样算" R1-R6; 观测面) */
  async explainOrder(orderId: number): Promise<any> {
    return await request<any>({
      url: `/api/xx64/orders/${orderId}/explain`,
      headers: memberHeaders(),
    });
  },

  /** 用户风险画像(当前风险分+tier+命中事件; 观测面) */
  async riskStatus(trustId: number): Promise<any> {
    return await request<any>({
      url: `/api/xx64/risk/status?trust_id=${trustId}`,
      headers: memberHeaders(),
    });
  },

  /** 限额状态(单次 20%/窗口 40% 基准; 观测面) */
  async quota(trustId: number): Promise<QuotaVO> {
    const res = await request<any>({
      url: `/api/xx64/quota?trust_id=${trustId}`,
      headers: memberHeaders(),
    });
    const d = res.data || res;
    return {
      balance: d.balance ?? 0,
      singleQuota: d.singleQuota ?? 0,
      windowUsed: d.windowUsed ?? 0,
      cumulativeQuota: d.cumulativeQuota ?? 0,
      windowRemaining: d.windowRemaining ?? 0,
      windowDays: d.windowDays ?? 30,
    };
  },

  /** 提交申诉(不受开关影响) */
  async appeal(orderId: number, reason: string): Promise<any> {
    return await request<any>({
      url: '/api/xx64/appeals',
      method: 'POST',
      headers: memberHeaders(),
      data: { orderId, reason, submittedBy: 'member' },
    });
  },

  /** 信值余额视图(45号: 可用/冻结/兑换上限; 档案不存在返回 null) */
  async trustBalance(trustId: number): Promise<{
    trustId: number;
    available: number;
    frozen: number;
    dailyCap: number;
    monthlyCap: number;
  } | null> {
    try {
      const res = await request<any>({ url: `/api/trust/balance/${trustId}` });
      const d = res.data || res;
      return {
        trustId: d.trustId ?? trustId,
        available: d.available ?? 0,
        frozen: d.frozen ?? 0,
        dailyCap: d.redeemLimits?.dailyCap ?? 0,
        monthlyCap: d.redeemLimits?.monthlyCap ?? 0,
      };
    } catch (_) {
      return null;
    }
  },
};
