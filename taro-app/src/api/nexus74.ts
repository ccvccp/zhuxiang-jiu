/**
 * 74号·NexusFlow 内容发布引擎 API · 发布工作台消费面
 * 后端: /api/nexus74/*(X-Role: admin, 内容发布敏感域)
 * 口径: 观测面(rules/sources/publications/quota/metrics/retro)常开;
 *       决策面(adapt/publish/retry)受 NEXUSFLOW74_MODE 门控;
 *       B 档人工回执不受 MODE(数据诚实)。
 * 标签: platformName/intentLabel/verdictLabel 均由后端提供——前端零字典。
 */
import { request } from './request';

/** 素材源 */
export interface SourceVO {
  sourceId: number;
  title: string;
  body: string;
  intent: string;
  intentLabel: string;
  keywords: string[];
  hasImage: boolean;
  hasVideo: boolean;
  createdAt: string;
}

/** 适配版本 */
export interface AdaptationVO {
  adaptationId: number;
  sourceId: number;
  platform: string;
  platformName: string;
  personaState: string;
  personaStateLabel: string;
  title: string;
  summary: string;
  complianceState: string;
}

/** 发布记录 */
export interface PublicationVO {
  publicationId: number;
  sourceId: number;
  adaptationId: number;
  platform: string;
  platformName: string;
  intentLabel: string;
  adapterTier: string;
  mode: string;
  autoPublished: boolean;
  retryCount: number;
  needsReview: boolean;
  complianceState: string;
  externalId: string;
  createdAt: string;
  package: { title?: string; summary?: string };
}

/** 合规检查结果 */
export interface ComplianceVO {
  state: string;
  stateLabel: string;
  platform: string;
  hits: Array<{ ruleId?: number; content?: string; pattern?: string }>;
  boundaryMatched: string[];
  safeHarborApplied: boolean;
  isLiquorContent: boolean;
  warningPresent: boolean;
  fixable: boolean;
  fixAction: string;
  note: string;
}

/** 平台配额 */
export interface QuotaVO {
  mode: string;
  beijingHour: number;
  silence: { enabled: boolean; hours: number[]; active: boolean };
  platforms: Array<{
    platform: string; platformName: string; adapterTier: string;
    todayPublished: number; cap: number; remaining: number;
  }>;
}

/** 指标汇总 */
export interface MetricsSummaryVO {
  mode: string;
  global: { publications: number; published: number; withMetrics: number; avgEngagement: number };
  auditDistribution: Record<string, number>;
  platforms: Array<{
    platform: string; platformName: string; published: number;
    totalRead: number; totalLike: number; totalComment: number; totalShare: number;
    avgEngagement: number;
  }>;
}

/** 复盘记录 */
export interface RetroVO {
  retroId: number;
  scope: string;
  platformName: string;
  intentLabel: string;
  verdictLabel: string;
  advices: Array<{ code: string; text: string }>;
  engagementRate: number;
}

/** 模型状态 */
export interface NexusModelStatusVO {
  mode: string;
  kill: boolean;
  immunity: { status: string; frozenAt: string };
  platformCount: number;
  redlines: string[];
}

/** 发布平台清单(封闭六平台) */
export const PUBLISH_PLATFORMS = [
  { key: 'wechat_mp', label: '微信公众号' },
  { key: 'douyin', label: '抖音' },
  { key: 'xiaohongshu', label: '小红书' },
  { key: 'zhihu', label: '知乎' },
  { key: 'bilibili', label: 'B 站' },
  { key: 'toutiao', label: '今日头条' },
];

/** 意图清单 */
export const SOURCE_INTENTS = [
  { key: 'news', label: '资讯' },
  { key: 'tutorial', label: '教程' },
  { key: 'seeding', label: '种草' },
  { key: 'opinion', label: '观点' },
];

export const NexusAPI = {
  /** 模型状态(模式/免疫/红线) */
  async modelStatus(): Promise<NexusModelStatusVO> {
    const res = await request<any>({ url: '/api/nexus74/model/status' });
    const d = (res && (res.data || res)) || {};
    return {
      mode: String(d.mode || ''),
      kill: Boolean(d.kill),
      immunity: {
        status: String((d.immunity || {}).status || ''),
        frozenAt: String((d.immunity || {}).frozenAt || ''),
      },
      platformCount: Number(d.platformCount ?? 0),
      redlines: d.redlines || [],
    };
  },

  /** 平台配额(当日发布配额+静默窗) */
  async quotaStatus(): Promise<QuotaVO | null> {
    try {
      const res = await request<any>({ url: '/api/nexus74/quota/status' });
      const d = (res && (res.data || res)) || {};
      return {
        mode: String(d.mode || ''),
        beijingHour: Number(d.beijingHour ?? 0),
        silence: {
          enabled: Boolean((d.silence || {}).enabled),
          hours: (d.silence || {}).hours || [],
          active: Boolean((d.silence || {}).active),
        },
        platforms: (d.platforms || []).map((p: any) => ({
          platform: String(p.platform || ''),
          platformName: String(p.platformName || ''),
          adapterTier: String(p.adapterTier || ''),
          todayPublished: Number(p.todayPublished ?? 0),
          cap: Number(p.cap ?? 0),
          remaining: Number(p.remaining ?? 0),
        })),
      };
    } catch (_) {
      return null;
    }
  },

  /** 指标汇总 */
  async metricsSummary(): Promise<MetricsSummaryVO | null> {
    try {
      const res = await request<any>({ url: '/api/nexus74/metrics/summary' });
      const d = (res && (res.data || res)) || {};
      return {
        mode: String(d.mode || ''),
        global: {
          publications: Number((d.global || {}).publications ?? 0),
          published: Number((d.global || {}).published ?? 0),
          withMetrics: Number((d.global || {}).withMetrics ?? 0),
          avgEngagement: Number((d.global || {}).avgEngagement ?? 0),
        },
        auditDistribution: d.auditDistribution || {},
        platforms: (d.platforms || []).map((p: any) => ({
          platform: String(p.platform || ''),
          platformName: String(p.platformName || ''),
          published: Number(p.published ?? 0),
          totalRead: Number(p.totalRead ?? 0),
          totalLike: Number(p.totalLike ?? 0),
          totalComment: Number(p.totalComment ?? 0),
          totalShare: Number(p.totalShare ?? 0),
          avgEngagement: Number(p.avgEngagement ?? 0),
        })),
      };
    } catch (_) {
      return null;
    }
  },

  /** 素材源列表 */
  async sources(limit = 20): Promise<SourceVO[]> {
    const res = await request<any>({
      url: `/api/nexus74/sources?limit=${limit}`,
      headers: { 'X-Role': 'admin' },
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((s: any) => ({
      sourceId: Number(s.sourceId ?? 0),
      title: String(s.title || ''),
      body: String(s.body || ''),
      intent: String(s.intent || ''),
      intentLabel: String(s.intentLabel || ''),
      keywords: s.keywords || [],
      hasImage: Boolean(s.hasImage),
      hasVideo: Boolean(s.hasVideo),
      createdAt: String(s.createdAt || ''),
    }));
  },

  /** 素材登记 */
  async createSource(params: {
    title: string; body: string; intent: string;
    keywords?: string[];
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/nexus74/sources',
      method: 'POST',
      headers: { 'X-Role': 'admin' },
      data: {
        title: params.title, body: params.body,
        intent: params.intent, keywords: params.keywords || [],
      },
    });
    return (res && (res.data || res)) || {};
  },

  /** 合规检查(确定性判定链——LLM 禁入) */
  async complianceCheck(text: string, platform = 'all_platforms'): Promise<ComplianceVO> {
    const res = await request<any>({
      url: '/api/nexus74/compliance/check',
      method: 'POST',
      headers: { 'X-Role': 'admin' },
      data: { text, platform, hasWarning: false },
    });
    const d = (res && (res.data || res)) || {};
    return {
      state: String(d.state || ''),
      stateLabel: String(d.stateLabel || ''),
      platform: String(d.platform || ''),
      hits: d.hits || [],
      boundaryMatched: d.boundaryMatched || [],
      safeHarborApplied: Boolean(d.safeHarborApplied),
      isLiquorContent: Boolean(d.isLiquorContent),
      warningPresent: Boolean(d.warningPresent),
      fixable: Boolean(d.fixable),
      fixAction: String(d.fixAction || ''),
      note: String(d.note || ''),
    };
  },

  /** 警示语注入(合规修复) */
  async warningInject(text: string): Promise<any> {
    const res = await request<any>({
      url: '/api/nexus74/warning/inject',
      method: 'POST',
      headers: { 'X-Role': 'admin' },
      data: { text },
    });
    return (res && (res.data || res)) || {};
  },

  /** 平台适配(单平台) */
  async adapt(sourceId: number, platform: string): Promise<AdaptationVO | null> {
    try {
      const res = await request<any>({
        url: '/api/nexus74/adapt',
        method: 'POST',
        headers: { 'X-Role': 'admin' },
        data: { sourceId, platform },
      });
      const d = (res && (res.data || res)) || {};
      return {
        adaptationId: Number(d.adaptationId ?? 0),
        sourceId: Number(d.sourceId ?? sourceId),
        platform: String(d.platform || ''),
        platformName: String(d.platformName || ''),
        personaState: String(d.personaState || ''),
        personaStateLabel: String(d.personaStateLabel || ''),
        title: String(d.title || ''),
        summary: String(d.summary || ''),
        complianceState: String(d.complianceState || ''),
      };
    } catch (_) {
      return null;
    }
  },

  /** 适配版本列表 */
  async adaptations(limit = 20): Promise<AdaptationVO[]> {
    const res = await request<any>({
      url: `/api/nexus74/adaptations?limit=${limit}`,
      headers: { 'X-Role': 'admin' },
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((a: any) => ({
      adaptationId: Number(a.adaptationId ?? 0),
      sourceId: Number(a.sourceId ?? 0),
      platform: String(a.platform || ''),
      platformName: String(a.platformName || ''),
      personaState: String(a.personaState || ''),
      personaStateLabel: String(a.personaStateLabel || ''),
      title: String(a.title || ''),
      summary: String(a.summary || ''),
      complianceState: String(a.complianceState || ''),
    }));
  },

  /** 发布(B 档人工轨; auto 仅 full 档 A 档) */
  async publish(sourceId: number, platform: string, adaptationId = 0): Promise<any> {
    const res = await request<any>({
      url: '/api/nexus74/publish',
      method: 'POST',
      headers: { 'X-Role': 'admin' },
      data: { sourceId, platform, adaptationId, auto: false },
    });
    return (res && (res.data || res)) || {};
  },

  /** 发布记录列表 */
  async publications(limit = 20): Promise<PublicationVO[]> {
    const res = await request<any>({
      url: `/api/nexus74/publications?limit=${limit}`,
      headers: { 'X-Role': 'admin' },
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((p: any) => ({
      publicationId: Number(p.publicationId ?? 0),
      sourceId: Number(p.sourceId ?? 0),
      adaptationId: Number(p.adaptationId ?? 0),
      platform: String(p.platform || ''),
      platformName: String(p.platformName || ''),
      intentLabel: String(p.intentLabel || ''),
      adapterTier: String(p.adapterTier || ''),
      mode: String(p.mode || ''),
      autoPublished: Boolean(p.autoPublished),
      retryCount: Number(p.retryCount ?? 0),
      needsReview: Boolean(p.needsReview),
      complianceState: String(p.complianceState || ''),
      externalId: String(p.externalId || ''),
      createdAt: String(p.createdAt || p.at || ''),
      package: {
        title: String((p.package || {}).title || ''),
        summary: String((p.package || {}).summary || ''),
      },
    }));
  },

  /** 自愈重试(A 档 failed——决策面) */
  async publicationRetry(publicationId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/nexus74/publications/${publicationId}/retry`,
      method: 'POST',
      headers: { 'X-Role': 'admin' },
    });
    return (res && (res.data || res)) || {};
  },

  /** B 档人工回执登记(数据诚实——不受 MODE) */
  async publicationReceipt(publicationId: number, result: string, message = ''): Promise<any> {
    const res = await request<any>({
      url: `/api/nexus74/publications/${publicationId}/receipt`,
      method: 'POST',
      headers: { 'X-Role': 'admin' },
      data: { result, message },
    });
    return (res && (res.data || res)) || {};
  },

  /** 复盘记录列表 */
  async retrospects(limit = 10): Promise<RetroVO[]> {
    const res = await request<any>({
      url: `/api/nexus74/retrospects?limit=${limit}`,
      headers: { 'X-Role': 'admin' },
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((r: any) => ({
      retroId: Number(r.retroId ?? 0),
      scope: String(r.scope || ''),
      platformName: String(r.platformName || ''),
      intentLabel: String(r.intentLabel || ''),
      verdictLabel: String(r.verdictLabel || ''),
      advices: (r.advices || []).map((a: any) => ({
        code: String(a.code || ''),
        text: String(a.text || ''),
      })),
      engagementRate: Number(r.engagementRate ?? 0),
    }));
  },
};
