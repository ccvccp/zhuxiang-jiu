/**
 * 营销/活动 API · 对接后端 /api/activity/* 与 /api/groupbuy/*
 * ============================================================
 * - 活动列表(公开): /api/activity/list
 * - 团购阶梯折扣(公开): /api/groupbuy/tiers
 * - 热销推荐(公开): /api/product/hot (复用 ProductAPI.hot)
 */
import { request } from './request';

// 活动类型
export interface ActivityVO {
  id: string;
  name: string;
  type: string;            // promotion/lottery/competition/arena/...
  status: string;          // registering/ongoing/ended/cancelled
  description?: string;
  startTime?: string;
  endTime?: string;
  budget?: number;
}

// 团购阶梯
export interface GroupBuyTier {
  tier: string;            // T1/T2/T3/T4
  name: string;            // "T1(8折)"
  minAmount: number;
  maxAmount: number | null;
  discount: number;        // 0.8
  discountRate: string;    // "19% off"
}

// 团购规则
export interface GroupBuyRules {
  minAmount: number;
  minAmountWedding: number;
  minAmountCustom: number;
  minQuantity: number;
  maxAmount: number;
  annualLimit: number;
  monthlyFreqLimit: number;
}

export const PromotionAPI = {
  /** 活动列表(可按状态/类型筛选) */
  async activities(params?: {
    status?: string;
    type?: string;
    limit?: number;
  }): Promise<ActivityVO[]> {
    const query: string[] = [];
    if (params?.status) query.push(`status=${params.status}`);
    if (params?.type) query.push(`type=${params.type}`);
    query.push(`limit=${params?.limit || 10}`);
    const qs = `?${query.join('&')}`;

    const res = await request<any>({ url: `/api/activity/list${qs}` });
    const list = res.data || [];
    return list.map((a: any): ActivityVO => ({
      id: String(a.activity_id || a.id || ''),
      name: a.name || '活动',
      type: a.type || 'promotion',
      status: a.status || 'ongoing',
      description: a.description,
      startTime: a.start_time || a.startTime,
      endTime: a.end_time || a.endTime,
      budget: a.budget,
    }));
  },

  /** 团购阶梯折扣表(公开) */
  async groupBuyTiers(): Promise<{ tiers: GroupBuyTier[]; rules: GroupBuyRules | null }> {
    const res = await request<any>({ url: '/api/groupbuy/tiers' });
    return {
      tiers: (res.data?.tiers || []).map((t: any): GroupBuyTier => ({
        tier: t.tier,
        name: t.name,
        minAmount: t.minAmount,
        maxAmount: t.maxAmount,
        discount: t.discount,
        discountRate: t.discountRate,
      })),
      rules: res.data?.rules || null,
    };
  },
};
