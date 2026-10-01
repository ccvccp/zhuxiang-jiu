/**
 * 80号·全域会员智能增长 API · 对接后端 /api/growth80/*
 * 分享事件计分(日限防刷) + 今日分享情况
 */
import { request } from './request';

export interface ShareReportVO {
  counted: boolean;       // 本次是否计分
  points?: number;       // 计得分值
  reason?: string;        // 未计分原因(已计/达上限)
  todayCount: number;     // 今日已计次数
}

export interface ShareTodayVO {
  todayCount: number;
  dailyLimit: number;
  remaining: number;      // 今日剩余可计次数
  pointsPerAction: number;
}

export const Growth80API = {
  /** 分享事件上报(itemType: product/promo_code/content) */
  async reportShare(itemType: string, itemId: string): Promise<ShareReportVO> {
    const res = await request<any>({
      url: '/api/growth80/share',
      method: 'POST',
      data: { itemType, itemId },
    });
    return {
      counted: !!res.counted,
      points: Number(res.points ?? 0) || 0,
      reason: res.reason || '',
      todayCount: Number(res.todayCount ?? 0) || 0,
    };
  },

  /** 今日分享计分情况 */
  async shareToday(): Promise<ShareTodayVO> {
    const res = await request<any>({ url: '/api/growth80/share/today' });
    return {
      todayCount: Number(res.todayCount ?? 0) || 0,
      dailyLimit: Number(res.dailyLimit ?? 5) || 0,
      remaining: Number(res.remaining ?? 0) || 0,
      pointsPerAction: Number(res.pointsPerAction ?? 0) || 0,
    };
  },
};
