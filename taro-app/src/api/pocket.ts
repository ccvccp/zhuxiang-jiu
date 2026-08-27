/**
 * 顺手赚钱 API · 对接后端 /api/pocket/*
 * 张贴广告物料(海报/车贴) → 打卡上传 → AI 评估 → 奖励入余额(仅可购物)
 */
import { request } from './request';

// 张贴场景
export type PocketScene = 'hotel' | 'supermarket' | 'taxi_rear' | 'restaurant' | 'community';

/** 场景中文名 */
export const SCENE_NAME: Record<string, string> = {
  hotel: '酒店',
  supermarket: '超市',
  taxi_rear: '车后窗',
  restaurant: '餐馆',
  community: '社区',
};

/** 场景图标 */
export const SCENE_ICON: Record<string, string> = {
  hotel: '🏨',
  supermarket: '🛒',
  taxi_rear: '🚕',
  restaurant: '🍽️',
  community: '🏘️',
};

/** 张贴点位 */
export interface PocketSiteVO {
  siteId: number;
  scene: string;             // hotel/supermarket/taxi_rear/restaurant/community
  posterType: string;        // poster(海报) / sticker(车贴)
  address: string;
  status: string;            // active/removed/invalid
  checkinCount: number;      // 累计有效打卡次数
  consecutiveDays: number;   // 连续打卡天数
  activeDays: number;        // 在贴天数
  durationDays: number;      // 存续奖天数门槛(默认30)
  monthRewardClaimed: boolean;
  monthRewardReady: boolean; // 满月可领
  aiScoreLatest: number;     // 最近 AI 评分
}

/** 我的统计 */
export interface PocketStatsVO {
  activeSiteCount: number;      // 在贴点位数
  totalSiteCount: number;       // 累计点位数
  totalCheckinCount: number;    // 累计有效打卡次数
  totalCheckinReward: number;   // 累计打卡奖励(元)
  monthRewardReadyCount: number;  // 可领存续奖点位数
  monthRewardReadyAmount: number; // 可领存续奖总额(元)
  checkinReward: number;        // 每次打卡奖励(默认2)
  monthRewardPoster: number;    // 海报满月奖(默认20)
  monthRewardSticker: number;   // 车贴满月奖(默认30)
  maxActiveSites: number;       // 在贴点位上限(默认5)
  durationDays: number;         // 存续奖天数门槛(默认30)
}

/** 打卡记录 */
export interface PocketCheckinVO {
  checkinId: number;
  siteId: number;
  photoUrl: string;
  aiScore: number;
  rewardAmount: number;
  createdAt: string;
}

/** 规则说明(公开) */
export interface PocketRulesVO {
  checkinReward: number;
  monthRewardPoster: number;
  monthRewardSticker: number;
  durationDays: number;
  maxActiveSites: number;
  aiScoreThreshold: number;
  scanRewardTip: string;
  rewardNote: string;
}

const mapSite = (s: any): PocketSiteVO => ({
  siteId: Number(s.siteId || 0),
  scene: s.scene || 'hotel',
  posterType: s.posterType || 'poster',
  address: s.address || '',
  status: s.status || 'active',
  checkinCount: Number(s.checkinCount || 0),
  consecutiveDays: Number(s.consecutiveDays || 0),
  activeDays: Number(s.activeDays || 0),
  durationDays: Number(s.durationDays || 30),
  monthRewardClaimed: !!s.monthRewardClaimed,
  monthRewardReady: !!s.monthRewardReady,
  aiScoreLatest: Number(s.aiScoreLatest || 0),
});

export const PocketAPI = {
  /** 我的统计 */
  async stats(): Promise<PocketStatsVO> {
    const res = await request<any>({ url: '/api/pocket/my/stats' });
    return {
      activeSiteCount: res.activeSiteCount || 0,
      totalSiteCount: res.totalSiteCount || 0,
      totalCheckinCount: res.totalCheckinCount || 0,
      totalCheckinReward: res.totalCheckinReward || 0,
      monthRewardReadyCount: res.monthRewardReadyCount || 0,
      monthRewardReadyAmount: res.monthRewardReadyAmount || 0,
      checkinReward: res.checkinReward || 2,
      monthRewardPoster: res.monthRewardPoster || 20,
      monthRewardSticker: res.monthRewardSticker || 30,
      maxActiveSites: res.maxActiveSites || 5,
      durationDays: res.durationDays || 30,
    };
  },

  /** 我的张贴点位列表 */
  async mySites(): Promise<PocketSiteVO[]> {
    const res = await request<any>({ url: '/api/pocket/my/sites' });
    return (res.sites || []).map(mapSite);
  },

  /** 我的打卡记录 */
  async myCheckins(limit = 20): Promise<PocketCheckinVO[]> {
    const res = await request<any>({
      url: `/api/pocket/my/checkins?limit=${limit}`,
    });
    return (res.checkins || []).map((c: any): PocketCheckinVO => ({
      checkinId: Number(c.checkinId || 0),
      siteId: Number(c.siteId || 0),
      photoUrl: c.photoUrl || '',
      aiScore: Number(c.aiScore || 0),
      rewardAmount: Number(c.rewardAmount || 0),
      createdAt: c.createdAt || '',
    }));
  },

  /** 张贴打卡(创建点位+首打卡发奖) */
  async reportSite(scene: PocketScene, address: string, photoUrl: string): Promise<{ success: boolean; site: PocketSiteVO }> {
    const res = await request<any>({
      url: '/api/pocket/site/report',
      method: 'POST',
      data: { scene, address, photoUrl },
    });
    return {
      success: !!res.success,
      site: res.site ? mapSite(res.site) : ({} as PocketSiteVO),
    };
  },

  /** 点位每日打卡 */
  async checkin(siteId: number, photoUrl: string): Promise<{ success: boolean }> {
    const res = await request<any>({
      url: `/api/pocket/site/${siteId}/checkin`,
      method: 'POST',
      data: { photoUrl },
    });
    return { success: !!res.success };
  },

  /** 领取满月存续奖 */
  async claimMonthReward(siteId: number): Promise<{ success: boolean; amount: number }> {
    const res = await request<any>({
      url: `/api/pocket/site/${siteId}/month-reward`,
      method: 'POST',
    });
    return {
      success: !!res.success,
      amount: Number(res.amount || 0),
    };
  },

  /** 撤销张贴 */
  async removeSite(siteId: number): Promise<{ success: boolean }> {
    const res = await request<any>({
      url: `/api/pocket/site/${siteId}/remove`,
      method: 'POST',
    });
    return { success: !!res.success };
  },

  /** 规则说明(公开) */
  async rules(): Promise<PocketRulesVO> {
    const res = await request<any>({ url: '/api/pocket/rules' });
    return {
      checkinReward: res.checkinReward || 2,
      monthRewardPoster: res.monthRewardPoster || 20,
      monthRewardSticker: res.monthRewardSticker || 30,
      durationDays: res.durationDays || 30,
      maxActiveSites: res.maxActiveSites || 5,
      aiScoreThreshold: res.aiScoreThreshold || 60,
      scanRewardTip: res.scanRewardTip || '',
      rewardNote: res.rewardNote || '',
    };
  },
};
