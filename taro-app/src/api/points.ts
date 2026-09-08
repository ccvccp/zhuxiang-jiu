/**
 * 积分 API · 对接后端 /api/points/*
 * 签到后端化(连续签到+宝箱奖励+幂等防重由后端保证)
 */
import { request } from './request';

export interface SigninResultVO {
  signDate: string;
  continuousDays: number;
  pointsEarned: number;
  isBonus: boolean;      // 宝箱日(第7/14/21天)
  bonusPoints: number;
}

export interface PointsAccountVO {
  totalPoints: number;
  frozenPoints: number;
  totalEarned: number;
  totalSpent: number;
  expiringPoints: number;  // 30 日内将过期
}

export interface PointsLogVO {
  id: number;
  type: string;          // earn | spend
  source: string;
  points: number;
  balance: number;       // 变动后余额
  refDesc: string;
  createdAt: string;
}

export const PointsAPI = {
  /** 每日签到(幂等: 重复签到后端 409) */
  async signin(userId: number): Promise<SigninResultVO> {
    const res = await request<any>({
      url: '/api/points/signin',
      method: 'POST',
      data: { userId },
    });
    return {
      signDate: res.signDate || '',
      continuousDays: res.continuousDays || 1,
      pointsEarned: res.pointsEarned || 0,
      isBonus: Number(res.isBonus) === 1,
      bonusPoints: res.bonusPoints || 0,
    };
  },

  /** 签到记录(判定今日是否已签到) */
  async signinRecords(userId: number, limit = 30): Promise<SigninResultVO[]> {
    const res = await request<any>({
      url: `/api/points/signin/${userId}?limit=${limit}`,
    });
    return (res.data || []).map((r: any) => ({
      signDate: r.signDate || r.sign_date || '',
      continuousDays: r.continuousDays || r.continuous_days || 0,
      pointsEarned: r.pointsEarned || r.points_earned || 0,
      isBonus: Number(r.isBonus ?? r.is_bonus) === 1,
      bonusPoints: r.bonusPoints || r.bonus_points || 0,
    }));
  },

  /** 积分账户(不存在自动创建) */
  async account(userId: number): Promise<PointsAccountVO> {
    const res = await request<any>({ url: `/api/points/account/${userId}` });
    return {
      totalPoints: res.totalPoints || 0,
      frozenPoints: res.frozenPoints || 0,
      totalEarned: res.totalEarned || 0,
      totalSpent: res.totalSpent || 0,
      expiringPoints: res.expiringPoints || 0,
    };
  },

  /** 积分流水 */
  async logs(userId: number, limit = 50): Promise<PointsLogVO[]> {
    const res = await request<any>({
      url: `/api/points/logs/${userId}?limit=${limit}`,
    });
    const list = res.logs || res.data || [];
    return list.map((l: any) => ({
      id: l.id,
      type: l.type || 'earn',
      source: l.source || '',
      points: l.points || 0,
      balance: l.balance || 0,
      refDesc: l.refDesc || l.ref_desc || '',
      createdAt: l.createdAt || l.created_at || '',
    }));
  },
};
