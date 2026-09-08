/**
 * 团购 API · 对接后端 /api/groupbuy/*
 * 阶梯价试算 → 提交团购申请(SVIP) → 我的团购(取消)
 * 注: promotion.ts 的 groupBuyTiers 保留只读阶梯展示, 本文件承载交易闭环
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

export interface GroupBuyProductVO {
  productId: string;
  productName: string;
  spec: string;
  price: number;
}

export interface CalcResultVO {
  originalTotal: number;
  tier: string;
  discount: number;
  groupPrice: number;
  savedAmount: number;
  meetsThreshold: boolean;
  items: Array<{
    productId: string; productName: string; productSpec: string;
    quantity: number; originalPrice: number; groupPrice?: number;
  }>;
  suggestions: string[];
}

export interface GroupBuyOrderVO {
  orderNo: string;
  groupType: string;
  groupPrice: number;
  status: string;
  statusName: string;
  createdAt: string;
}

// 会员等级 "L5" → int 5(团购申请要求 userLevel: int)
export function levelToInt(level: string | undefined | null): number {
  const m = String(level || '').match(/L?(\d+)/i);
  return m ? Number(m[1]) : 1;
}

export const GroupBuyAPI = {
  /** 可团购产品列表(需登录) */
  async products(): Promise<GroupBuyProductVO[]> {
    const res = await request<any>({ url: '/api/groupbuy/products' });
    const d = res.data || res;
    const list = d.products || [];
    return list.map((p: any) => ({
      productId: p.productId || '',
      productName: p.name || p.productName || '',
      spec: `${p.alcohol ?? ''}° ${p.volume || ''}`.trim(),
      price: p.price ?? p.originalPrice ?? 0,
    }));
  },

  /** 阶梯价试算 */
  async calculate(items: Array<{ productId: string; quantity: number }>): Promise<CalcResultVO> {
    const res = await request<any>({
      url: '/api/groupbuy/calculate',
      method: 'POST',
      data: { items },
    });
    const d = res.data || res;
    return {
      originalTotal: d.originalTotal ?? d.original_total ?? 0,
      tier: d.tier || '',
      discount: d.discount ?? 1,
      groupPrice: d.groupPrice ?? d.group_price ?? 0,
      savedAmount: d.savedAmount ?? d.saved_amount ?? 0,
      meetsThreshold: Boolean(d.meetsThreshold ?? d.meets_threshold ?? true),
      items: d.items || [],
      // 后端 suggestions 为对象数组(阶梯/差额/预估加购量), 归一化为可读文案
      suggestions: (d.suggestions || []).map((s: any) =>
        typeof s === 'string'
          ? s
          : `还差 ¥${s.diffAmount ?? 0} 升至 ${s.tier || ''} 阶梯(${Math.round((1 - (s.discount ?? 1)) * 100)}% off), 预计加购 ${s.estimatedExtraQuantity ?? 0} 瓶`
      ),
    };
  },

  /** 提交团购申请(userId/userLevel 从会话推导) */
  async applyWithLevel(params: {
    userLevel: number;
    groupType: string;
    items: Array<{ productId: string; quantity: number }>;
    purpose?: string;
  }): Promise<{ orderNo: string; groupPrice: number }> {
    const res = await request<any>({
      url: '/api/groupbuy/apply',
      method: 'POST',
      data: {
        userId: Number(getMemberId()),
        userLevel: params.userLevel,
        groupType: params.groupType,
        items: params.items,
        purpose: params.purpose || '',
      },
    });
    const d = res.data || res;
    return { orderNo: d.orderNo || d.order_no || '', groupPrice: d.groupPrice ?? 0 };
  },

  /** 我的团购订单列表 */
  async myOrders(status?: string): Promise<GroupBuyOrderVO[]> {
    const qs = status ? `?status=${status}` : '';
    const res = await request<any>({ url: `/api/groupbuy/list${qs}` });
    const d = res.data || res;
    return (d.orders || []).map((o: any) => ({
      orderNo: o.orderNo || o.order_no || '',
      groupType: o.groupType || o.group_type || '',
      groupPrice: o.groupPrice ?? o.group_price ?? 0,
      status: o.status || '',
      statusName: o.statusName || o.status_name || '',
      createdAt: o.createdAt || o.created_at || '',
    }));
  },

  /** 取消团购申请(仅活跃状态) */
  async cancel(orderNo: string, reason = ''): Promise<any> {
    return await request<any>({
      url: `/api/groupbuy/${orderNo}/cancel`,
      method: 'PUT',
      data: { userId: Number(getMemberId()), reason },
    });
  },
};
