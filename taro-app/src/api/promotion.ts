/**
 * 营销/推广 API · 对接后端 /api/activity/* /api/groupbuy/* /api/promotion/*
 * ============================================================
 * - 活动列表(公开): /api/activity/list
 * - 团购阶梯折扣(公开): /api/groupbuy/tiers
 * - 推广统计/团队/奖励/推广码: /api/promotion/*
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

// 活动类型
export interface ActivityVO {
  id: string;
  activityNo?: string;
  name: string;
  type: string;            // promotion/lottery/competition/arena/...
  status: string;          // registering/ongoing/ended/cancelled
  description?: string;
  startTime?: string;
  endTime?: string;
  budget?: number;
  registrationCount?: number;
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

// ============================================================
// 推广码矩阵模块 /api/promotion/*
// ============================================================

/** 推广统计 */
export interface PromotionStatsVO {
  directCount: number;          // 直推人数
  qualifiedSubCount: number;    // 达标下线数(各自推广数≥阈值)
  level1Threshold: number;      // 一级奖励人数阈值
  level1RewardAmount: number;   // 一级奖励金额(元/轮)
  level2SubPromoterCount: number; // 二级达标所需下线数
  level2SubThreshold: number;   // 每个下线需推广人数
  wineMinPrice: number;         // 奖励酒最低价
  rewardBalance: number;        // 奖励余额(仅可购本站产品)
  wineQualifyAvailable: number; // 可领酒资格数
  walletRewardCycles: number;   // 钱包奖励轮次
}

/** 团队成员 */
export interface TeamMemberVO {
  inviteeMemberId: string;
  nickname: string;
  channel: string;
  subCount: number;   // 该下线的推广数
  boundAt: string;
}

/** 推广码 */
export interface PromoCodeVO {
  code: string;
  channel: string;
  boundCount: number;
}

/** 奖励记录 */
export interface RewardVO {
  rewardType: string;   // wallet / wine_qualify
  amount: number;
  status: string;       // issued / used
  detail: string;
  createdAt: string;
}

export const PromoAPI = {
  /** 我的推广统计 */
  async stats(): Promise<PromotionStatsVO> {
    const res = await request<any>({ url: '/api/promotion/my/stats' });
    return {
      directCount: res.directCount || 0,
      qualifiedSubCount: res.qualifiedSubCount || 0,
      level1Threshold: res.level1Threshold || 100,
      level1RewardAmount: res.level1RewardAmount || 0,
      level2SubPromoterCount: res.level2SubPromoterCount || 0,
      level2SubThreshold: res.level2SubThreshold || 0,
      wineMinPrice: res.wineMinPrice || 0,
      rewardBalance: res.rewardBalance || 0,
      wineQualifyAvailable: res.wineQualifyAvailable || 0,
      walletRewardCycles: res.walletRewardCycles || 0,
    };
  },

  /** 领取推广码(同渠道幂等) */
  async claimCode(channel = 'wechat_miniprogram'): Promise<{ code: string; shareTip: string; reclaimed: boolean }> {
    const res = await request<any>({
      url: '/api/promotion/code/claim',
      method: 'POST',
      data: { channel },
    });
    return {
      code: res.code,
      shareTip: res.shareTip || '',
      reclaimed: !!res.reclaimed,
    };
  },

  /** 我的推广码列表 */
  async myCodes(): Promise<PromoCodeVO[]> {
    const res = await request<any>({ url: '/api/promotion/my/codes' });
    return (res.codes || []).map((c: any): PromoCodeVO => ({
      code: c.code,
      channel: c.channel,
      boundCount: c.boundCount || 0,
    }));
  },

  /** 我的团队 */
  async myTeam(): Promise<TeamMemberVO[]> {
    const res = await request<any>({ url: '/api/promotion/my/team' });
    return (res.team || []).map((t: any): TeamMemberVO => ({
      inviteeMemberId: String(t.inviteeMemberId),
      nickname: t.nickname || '酒友',
      channel: t.channel || '',
      subCount: t.subCount || 0,
      boundAt: t.boundAt || '',
    }));
  },

  /** 我的奖励记录 */
  async myRewards(): Promise<RewardVO[]> {
    const res = await request<any>({ url: '/api/promotion/my/rewards' });
    return (res.rewards || []).map((r: any): RewardVO => ({
      rewardType: r.rewardType || r.reward_type || '',
      amount: r.amount || 0,
      status: r.status || 'issued',
      detail: r.detail || '',
      createdAt: r.createdAt || r.created_at || '',
    }));
  },
};

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
      activityNo: a.activityNo || a.activity_no,
      name: a.name || '活动',
      type: a.type || 'promotion',
      status: a.status || 'ongoing',
      description: a.description,
      startTime: a.startTime || a.start_time,
      endTime: a.endTime || a.end_time,
      budget: a.budget,
      registrationCount: a.registrationCount || 0,
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

// ============================================================
// 活动中心模块 /api/activity/* (用户端)
// ============================================================

/** 活动统计 */
export interface ActivityStatsVO {
  activityId: string;
  name: string;
  status: string;
  registrationCount: number;
  cancelledCount: number;
  startTime?: string;
  endTime?: string;
}

export const ActivityAPI = {
  /** 活动详情 */
  async detail(activityId: string): Promise<ActivityVO> {
    const res = await request<any>({ url: `/api/activity/${activityId}` });
    const a = res.data || {};
    return {
      id: String(a.id || activityId),
      activityNo: a.activityNo || a.activity_no,
      name: a.name || '活动',
      type: a.type || 'promotion',
      status: a.status || 'ongoing',
      description: a.description,
      startTime: a.startTime || a.start_time,
      endTime: a.endTime || a.end_time,
      budget: a.budget,
      registrationCount: a.registrationCount || 0,
    };
  },

  /** 活动统计(报名数等) */
  async stats(activityId: string): Promise<ActivityStatsVO> {
    const res = await request<any>({ url: `/api/activity/stats/${activityId}` });
    const d = res.data || {};
    return {
      activityId: String(d.activityId || activityId),
      name: d.name || '',
      status: d.status || '',
      registrationCount: d.registrationCount || 0,
      cancelledCount: d.cancelledCount || 0,
      startTime: d.startTime,
      endTime: d.endTime,
    };
  },

  /** 活动报名(幂等防重) */
  async register(activityId: string): Promise<void> {
    await request<any>({
      url: '/api/activity/register',
      method: 'POST',
      data: { activityId: Number(activityId), userId: Number(getMemberId()) },
    });
  },

  /** 取消报名(报名中/进行中可取消) */
  async cancelRegister(activityId: string): Promise<void> {
    await request<any>({
      url: '/api/activity/cancel',
      method: 'POST',
      data: { activityId: Number(activityId), userId: Number(getMemberId()) },
    });
  },
};
