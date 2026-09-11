/**
 * 36号·AI智能推广模块 API 客户端
 * ============================================================
 * 对接后端 promo_routes.py(31 端点, 管理端 X-Role: admin)
 *
 * 六大子系统(设计文档 §2):
 *   热点雷达 → 蹭点决策 → 受众匹配 → Agent内容工厂
 *   → 三审合规 → 发布调度 → 效果回流
 *
 * 注意: 与 promotion.ts(推广码/分销, /api/promotion/*)是两个模块,
 *       本文件对接 /api/promo/*。
 */
import { request } from './request';

// ============================================================
// 字典
// ============================================================

/** 热点源平台 */
export const HOTSPOT_PLATFORM_NAME: Record<string, string> = {
  baidu: '百度热搜', douyin: '抖音热榜', weibo: '微博热搜',
  zhihu: '知乎热榜', xiaohongshu: '小红书',
};

/** 发布平台 */
export const PUBLISH_PLATFORM_NAME: Record<string, string> = {
  douyin: '抖音', xiaohongshu: '小红书', wechat_moments: '视频号',
  weibo: '微博', wechat_channels: '百家号',
};

/** 热点状态 */
export const HOTSPOT_STATUS_NAME: Record<string, string> = {
  active: '待裁决', engaged: '已跟进', passed: '已放弃', discarded: '风险否决',
};

/** 内容状态 */
export const CONTENT_STATUS_NAME: Record<string, string> = {
  pending: '待审核', approved: '已通过', rejected: '已拒绝',
  queued: '已入队', published: '已发布',
};

/** 决策档位 */
export const DECISION_NAME: Record<string, string> = {
  auto_engage: '自动跟进', manual_queue: '人工裁决', pass: '放弃',
};

/** 通道模式 */
export const CHANNEL_MODE_NAME: Record<string, string> = {
  mock: '模拟轨', real: '真实轨', mock_fallback: '降级模拟',
};

export const hotspotPlatformName = (p: string): string =>
  HOTSPOT_PLATFORM_NAME[p] || p;
export const publishPlatformName = (p: string): string =>
  PUBLISH_PLATFORM_NAME[p] || p;
export const hotspotStatusName = (s: string): string =>
  HOTSPOT_STATUS_NAME[s] || s;
export const contentStatusName = (s: string): string =>
  CONTENT_STATUS_NAME[s] || s;
export const decisionName = (d: string): string => DECISION_NAME[d] || d;
export const channelModeName = (m: string): string =>
  CHANNEL_MODE_NAME[m] || m;

// ============================================================
// VO 定义
// ============================================================

/** 热点事件 */
export interface HotspotVO {
  hotspotId: number;
  platform: string;
  title: string;
  summary?: string;
  heat?: number;
  score: number;
  status: string;
  scoreDetail?: Record<string, number>;
  brandHits?: string[];
  riskFlags?: string[];
  createdAt?: string;
}

/** 后端热点 → VO(字符串数字数值化, 对齐 xinzhi 范式) */
function toHotspot(h: any): HotspotVO {
  return {
    hotspotId: Number(h.hotspotId || 0),
    platform: String(h.platform || ''),
    title: String(h.title || ''),
    summary: h.summary || '',
    heat: h.heat != null ? Number(h.heat) : undefined,
    score: Number(h.score || 0),
    status: String(h.status || ''),
    scoreDetail: h.scoreDetail || {},
    brandHits: h.brandHits || [],
    riskFlags: h.riskFlags || [],
    createdAt: h.createdAt || '',
  };
}

/** 扫描结果 */
export interface ScanResultVO {
  scanned: number;
  new: number;
  discarded: number;
  decisions: {
    hotspotId: number; decision: string; reason: string;
  }[];
  hotspots: HotspotVO[];
}

/** 决策记录 */
export interface DecisionVO {
  decisionId: number;
  hotspotId: number;
  hotspotTitle?: string;
  platform?: string;
  score?: number;
  decision: string;
  reason: string;
  decided?: boolean;
  note?: string;
  createdAt?: string;
}

/** 生成内容(Agent 产物) */
export interface PromoContentVO {
  contentId: number;
  hotspotId: number;
  platform: string;
  title: string;
  body?: string;
  hashtags?: string[];
  status: string;
  complianceScore?: number;
  contentGroupId?: number;
  shortCode?: string;
  agentTrace?: string[];
  authorityRefs?: string[];
  provenanceViolations?: string[];
  createdAt?: string;
}

/** 发布队列条目 */
export interface PublishQueueVO {
  contentId: number;
  platform: string;
  title?: string;
  scheduledAt?: string;
  inWindow?: boolean;
  windowHint?: string;
  status?: string;
}

/** 发布回执 */
export interface PublishReceiptVO {
  contentId: number;
  platform: string;
  publishedAt?: string;
  receipt?: {
    mode: string; publishId?: string; exposureEstimate?: number;
    error?: string;
  };
}

/** 归因报表总览 */
export interface PromoOverviewVO {
  hotspots: { total: number; engaged: number; passed: number; pendingManual: number };
  contents: { total: number; pending: number; published: number; rejected: number };
  attribution: { clicks: number; registered: number; ordered: number; gmv: number };
  dailyCap: { used: number; limit: number };
}

/** 平台维度报表行 */
export interface PlatformReportVO {
  platform: string;
  published: number;
  clicks: number;
  registered: number;
  ordered: number;
  gmv: number;
}

/** 通道状态 */
export interface ChannelVO {
  platform: string;
  mode: string;
  effectiveMode: string;
  keyConfigured: boolean;
  authStyle?: string;
  endpoint?: string;
}

/** SEO 推送记录 */
export interface SeoPushVO {
  pushId?: number;
  status: string;
  urls?: string[];
  message?: string;
  pushedAt?: string;
}

/** 受众画像 */
export interface AudienceProfileVO {
  platform: string;
  audience?: string;
  tone?: string;
  format?: string;
  scenes?: string[];
  productTones?: string[];
}

// ============================================================
// 管理端请求头(X-Role: admin, 对齐 blogger.ts 范式)
// ============================================================

function adminHeaders(): Record<string, string> {
  return { 'X-Role': 'admin' };
}

// ============================================================
// API 客户端
// ============================================================

export const PromoAPI = {

  // ---------- 热点雷达 ----------

  /** 手动触发热点扫描(5平台模拟源+评分+风险否决+去重+自动决策) */
  async radarScan(): Promise<ScanResultVO> {
    const res = await request<any>({
      url: '/api/promo/radar/scan',
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
    return res.data;
  },

  /** 热点列表(评分降序) */
  async hotspots(params?: {
    status?: string; platform?: string; minScore?: number;
  }): Promise<HotspotVO[]> {
    const q: string[] = [];
    if (params?.status) q.push(`status=${params.status}`);
    if (params?.platform) q.push(`platform=${params.platform}`);
    if (params?.minScore) q.push(`minScore=${params.minScore}`);
    const qs = q.length ? `?${q.join('&')}` : '';
    const res = await request<any>({
      url: `/api/promo/radar/hotspots${qs}`, headers: adminHeaders(),
    });
    return (res.data || []).map(toHotspot);
  },

  /** 热点详情(评分分项/品牌命中/风险标记) */
  async hotspotDetail(hotspotId: number): Promise<HotspotVO> {
    const res = await request<any>({
      url: `/api/promo/radar/hotspots/${hotspotId}`, headers: adminHeaders(),
    });
    return res.data;
  },

  // ---------- 蹭点决策 ----------

  /** 决策列表(审计留痕) */
  async decisions(params?: { pendingOnly?: boolean }): Promise<DecisionVO[]> {
    const qs = params?.pendingOnly ? '?pendingOnly=true' : '';
    const res = await request<any>({
      url: `/api/promo/decisions${qs}`, headers: adminHeaders(),
    });
    return res.data || [];
  },

  /** 人工裁决(跟进/放弃) */
  async decide(hotspotId: number, engage: boolean, note = ''): Promise<DecisionVO> {
    const res = await request<any>({
      url: `/api/promo/decisions/${hotspotId}/decide`,
      method: 'POST',
      headers: adminHeaders(),
      data: { engage, note },
    });
    return res.data;
  },

  // ---------- 内容工厂 ----------

  /** Agent 一源多态生成(四步链+三级降级+合规预审+短码) */
  async generate(params: {
    hotspotId: number; platforms: string[];
  }): Promise<PromoContentVO[]> {
    const res = await request<any>({
      url: '/api/promo/contents/generate',
      method: 'POST',
      headers: adminHeaders(),
      data: { hotspotId: params.hotspotId, platforms: params.platforms },
    });
    return res.data || [];
  },

  /** 内容列表 */
  async contents(params?: {
    platform?: string; status?: string; hotspotId?: number; groupId?: number;
  }): Promise<PromoContentVO[]> {
    const q: string[] = [];
    if (params?.platform) q.push(`platform=${params.platform}`);
    if (params?.status) q.push(`status=${params.status}`);
    if (params?.hotspotId) q.push(`hotspotId=${params.hotspotId}`);
    if (params?.groupId) q.push(`groupId=${params.groupId}`);
    const qs = q.length ? `?${q.join('&')}` : '';
    const res = await request<any>({
      url: `/api/promo/contents${qs}`, headers: adminHeaders(),
    });
    return res.data || [];
  },

  /** 内容详情(agentTrace/合规报告/短码映射) */
  async contentDetail(contentId: number): Promise<PromoContentVO> {
    const res = await request<any>({
      url: `/api/promo/contents/${contentId}`, headers: adminHeaders(),
    });
    return res.data;
  },

  /** 人工审核(三审 HITL) */
  async review(contentId: number, approved: boolean): Promise<PromoContentVO> {
    const res = await request<any>({
      url: `/api/promo/contents/${contentId}/review`,
      method: 'POST',
      headers: adminHeaders(),
      data: { approved, reviewer: 'admin' },
    });
    return res.data;
  },

  // ---------- 发布调度 ----------

  /** 入发布队列(黄金时段+单日上限) */
  async publish(contentId: number, publishAt = ''): Promise<PublishQueueVO> {
    const res = await request<any>({
      url: `/api/promo/contents/${contentId}/publish`,
      method: 'POST',
      headers: adminHeaders(),
      data: { publishAt },
    });
    return res.data;
  },

  /** 发布队列与黄金时段窗口状态 */
  async publishQueue(): Promise<PublishQueueVO[]> {
    const res = await request<any>({
      url: '/api/promo/publish/queue', headers: adminHeaders(),
    });
    return res.data || [];
  },

  /** 处理到期发布(出队+回执) */
  async processPublish(): Promise<PublishReceiptVO[]> {
    const res = await request<any>({
      url: '/api/promo/publish/process',
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
    return res.data || [];
  },

  // ---------- 受众画像 ----------

  /** 平台画像列表(首次自动初始化种子) */
  async audienceProfiles(): Promise<AudienceProfileVO[]> {
    const res = await request<any>({
      url: '/api/promo/audience/profiles', headers: adminHeaders(),
    });
    return res.data || [];
  },

  // ---------- 通道与 SEO ----------

  /** 五平台通道状态(三态: mock/real/mock_fallback) */
  async channelsStatus(): Promise<ChannelVO[]> {
    const res = await request<any>({
      url: '/api/promo/channels/status', headers: adminHeaders(),
    });
    return res.data || [];
  },

  /** 百度 SEO 推送记录 */
  async seoPushes(): Promise<SeoPushVO[]> {
    const res = await request<any>({
      url: '/api/promo/seo/pushes', headers: adminHeaders(),
    });
    return res.data || [];
  },

  // ---------- 报表 ----------

  /** 全景总览(热点/内容/归因/日限) */
  async reportOverview(): Promise<PromoOverviewVO> {
    const res = await request<any>({
      url: '/api/promo/report/overview', headers: adminHeaders(),
    });
    return res.data;
  },

  /** 平台维度报表 */
  async reportPlatform(): Promise<PlatformReportVO[]> {
    const res = await request<any>({
      url: '/api/promo/report/platform', headers: adminHeaders(),
    });
    return res.data || [];
  },
};
