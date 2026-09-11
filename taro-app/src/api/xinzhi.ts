/**
 * 信值·臻选购物平台 API · 对接后端 /api/xinzhi/*(68号)
 * 五维雷达(47/67/44 只读聚合) · 臻选货架(L1-L4) · 透明定价(杀熟审计)
 * 小竹导购(SOP五步法) · 邻里求购(67号范式) · 商家体系 · 灰度三态
 * 宪法口径: 观测面永不关停; 决策面 off=409(灰度放量 shadow→assist)
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 类型字典 */
export const XINZHI_GRADE_NAME: Record<string, string> = {
  S: 'S 臻选', A: 'A 优选', B: 'B 普通', C: 'C 观察', D: 'D 风险',
};
export const XINZHI_PRODUCT_GRADE_NAME: Record<string, string> = {
  L1: 'L1 臻选位', L2: 'L2 优选', L3: 'L3 普通', L4: 'L4 风险',
};
export const XINZHI_DIM_NAME: Record<string, string> = {
  integrity: '诚信度', mutual: '互助值', expert: '专业度',
  activity: '活跃度', growth: '成长力',
};
export const XINZHI_GB_STATUS_NAME: Record<string, string> = {
  published: '待响应', responded: '已有人响应',
  closed: '已解决', cancelled: '已取消',
};
export const XINZHI_FB_LEVEL_NAME: Record<string, string> = {
  L1: '已自动回复', L2: '工单处理中(24h)', L3: '紧急处理(15分钟)',
};

export const xinzhiGradeName = (g: string): string =>
  XINZHI_GRADE_NAME[g] || g;
export const xinzhiDimName = (d: string): string =>
  XINZHI_DIM_NAME[d] || d;
export const xinzhiGbStatusName = (s: string): string =>
  XINZHI_GB_STATUS_NAME[s] || s;

/** VO: 雷达维度 */
export interface RadarDimVO {
  key: string;
  label: string;
  score: number;
  weight: number | null;
  factors: string[];
}

/** VO: 五维雷达 */
export interface XinzhiRadarVO {
  memberId: number;
  dimensions: RadarDimVO[];
  totalScore: number;
  grade: string;
  explanation: string;
  circuitBroken: boolean;
  coldStart: boolean;
  tier: string;
  computedAt: string;
}

/** VO: 臻选货架商品 */
export interface PrimeItemVO {
  scoreSeq: number;
  productId: string;
  productName: string;
  series: string;
  fit: number;
  fitModules: string[];
  safety: number;
  safetyReasons: string[];
  conversion: number;
  valueScore: number;
  grade: string;
  finalRank: number;
  radarTotal: number;
  hardBlocked: boolean;
  explanation: string;
}

/** VO: 价格构成拆解(杀熟审计源) */
export interface PriceDetailVO {
  detailSeq: number;
  productId: string;
  productName: string;
  memberId: number;
  grade: string;
  tier: string;
  basePrice: number;
  afterThreeFactor: number;
  xinzhiAlpha: number;
  xinzhiCredit: number;
  finalPrice: number;
  breakdownLine: string;
  floored: boolean;
  auditFlag: string;
  pricedAt: string;
}

/** VO: 导购 SOP 五步 */
export interface GuideReplyVO {
  persona: string;
  memberId: number;
  productId: string;
  productName: string;
  intent: string;
  steps: Record<string, {
    say: string;
    [k: string]: any;
  }>;
  reply: string;
  redLines: string[];
  xinzhiMode?: string;
}

/** VO: 导购人格卡片 */
export interface GuidePersonaVO {
  persona: string;
  sopSteps: { key: string; label: string }[];
  intents: { key: string; label: string }[];
  redLines: string[];
  llmBoundary: string;
}

/** VO: 邻里臻选品类聚合 */
export interface NeighborShelfVO {
  city: string;
  categories: { series: string; buyerCount: number; orderCount: number }[];
  anonymityK: number;
  scope: string;
}

/** VO: 邻里求购单 */
export interface GroupbuyVO {
  groupbuyId: number;
  publisherMasked: string;
  title: string;
  productId: string;
  series: string;
  quantity: number;
  urgency: string;
  longitude: number;
  latitude: number;
  address: string;
  status: string;
  responderCount: number;
  closed: boolean;
  carbonGrams?: number;
  distanceKm?: number;
  createdAt: string;
  xinzhiMode?: string;
}

/** VO: 碳档案 */
export interface CarbonVO {
  memberId: number;
  carbonGrams: number;
  carbonKg: number;
  helpOrders: number;
  helpCarbonGrams: number;
  groupbuys: number;
  groupbuyCarbonGrams: number;
  methodology: string;
}

/** VO: 反馈工单 */
export interface FeedbackVO {
  feedbackId: number;
  scene: string;
  tags: string[];
  content: string;
  level: string;
  status: string;
  routedTo: string;
  autoReply: string;
  sla: string;
  createdAt: string;
}

/** VO: 灰度态 */
export interface ModeVO {
  mode: string;
  source: string;
  paused: boolean;
  override: string;
  envMode: string;
  pausedReason: string;
  observablesNeverOff?: string;
  decisionSurfaces?: string;
  guard?: {
    metrics: { key: string; label: string }[];
    threshold: number;
    pausedAt: string;
    pausedReason: string;
    checkCount: number;
    breachCount: number;
  };
}

/** VO: 白皮书 */
export interface WhitepaperVO {
  year: number;
  generatedAt: string;
  sections: Record<string, any>;
  piiScanned: boolean;
  piiHits: number;
  publishNote: string;
}

/** VO: 商家档案 */
export interface MerchantVO {
  merchantId: number;
  shopName: string;
  certified: boolean;
  status: string;
  grade: string;
  merchantScore: number;
  certScore: number;
  fulfillmentScore: number;
  missingChecks?: string[];
  punishmentPolicy?: string;
  gradeHistory?: { from: string; to: string; reason: string; auto: boolean; at: string }[];
}

function toPrimeItem(x: any): PrimeItemVO {
  return {
    scoreSeq: Number(x.scoreSeq ?? 0),
    productId: x.productId || '',
    productName: x.productName || '',
    series: x.series || '',
    fit: Number(x.fit ?? 0),
    fitModules: x.fitModules || [],
    safety: Number(x.safety ?? 0),
    safetyReasons: x.safetyReasons || [],
    conversion: Number(x.conversion ?? 0),
    valueScore: Number(x.valueScore ?? 0),
    grade: x.grade || 'L3',
    finalRank: Number(x.finalRank ?? 0),
    radarTotal: Number(x.radarTotal ?? 0),
    hardBlocked: Boolean(x.hardBlocked),
    explanation: x.explanation || '',
  };
}

function toGroupbuy(x: any): GroupbuyVO {
  return {
    groupbuyId: Number(x.groupbuyId ?? 0),
    publisherMasked: x.publisherMasked || '',
    title: x.title || '',
    productId: x.productId || '',
    series: x.series || '',
    quantity: Number(x.quantity ?? 1),
    urgency: x.urgency || 'normal',
    longitude: Number(x.longitude ?? 0),
    latitude: Number(x.latitude ?? 0),
    address: x.address || '',
    status: x.status || 'published',
    responderCount: Number(x.responderCount ?? 0),
    closed: Boolean(x.closed),
    carbonGrams: x.carbonGrams != null ? Number(x.carbonGrams) : undefined,
    distanceKm: x.distanceKm != null ? Number(x.distanceKm) : undefined,
    createdAt: x.createdAt || '',
    xinzhiMode: x.xinzhiMode || undefined,
  };
}

export const XinzhiAPI = {
  // ================= 观测面(永不关停) =================

  /** 五维雷达(最新快照/即时计算) */
  async radar(memberId?: number): Promise<XinzhiRadarVO> {
    const mid = memberId ?? Number(getMemberId() || 0);
    const res = await request<any>({ url: `/api/xinzhi/radar`, headers: { 'X-Member-Id': String(mid) } });
    const d = res.data || res;
    return {
      memberId: Number(d.memberId ?? mid),
      dimensions: (d.dimensions || []).map((x: any) => ({
        key: x.key || '',
        label: x.label || '',
        score: Number(x.score ?? 0),
        weight: x.weight != null ? Number(x.weight) : null,
        factors: x.factors || [],
      })),
      totalScore: Number(d.totalScore ?? 0),
      grade: d.grade || 'D',
      explanation: d.explanation || '',
      circuitBroken: Boolean(d.circuitBroken),
      coldStart: Boolean(d.coldStart),
      tier: d.tier || '',
      computedAt: d.computedAt || '',
    };
  },

  /** 臻选货架 L1(信值加权排序) */
  async prime(limit = 20): Promise<PrimeItemVO[]> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/prime?limit=${limit}`,
      headers: { 'X-Member-Id': String(mid) },
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toPrimeItem);
  },

  /** 商品评分明细(可解释) */
  async productScore(productId: string): Promise<PrimeItemVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/products/${productId}/score`,
      headers: { 'X-Member-Id': String(mid) },
    });
    return toPrimeItem(res.data || res);
  },

  /** 价格构成拆解(原价-信值抵扣-折扣=实付) */
  async price(productId: string, promo = 1.0): Promise<PriceDetailVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/price/${productId}?promo=${promo}`,
      headers: { 'X-Member-Id': String(mid) },
    });
    const d = res.data || res;
    return {
      detailSeq: Number(d.detailSeq ?? 0),
      productId: d.productId || productId,
      productName: d.productName || '',
      memberId: Number(d.memberId ?? mid),
      grade: d.grade || 'D',
      tier: d.tier || 'standard',
      basePrice: Number(d.basePrice ?? 0),
      afterThreeFactor: Number(d.afterThreeFactor ?? 0),
      xinzhiAlpha: Number(d.xinzhiAlpha ?? 0),
      xinzhiCredit: Number(d.xinzhiCredit ?? 0),
      finalPrice: Number(d.finalPrice ?? 0),
      breakdownLine: d.breakdownLine || '',
      floored: Boolean(d.floored),
      auditFlag: d.auditFlag || '',
      pricedAt: d.pricedAt || '',
    };
  },

  /** 邻里臻选频道(品类聚合, 零个体数据) */
  async neighbor(city?: string): Promise<NeighborShelfVO> {
    const res = await request<any>({
      url: `/api/xinzhi/neighbor${city ? `?city=${encodeURIComponent(city)}` : ''}`,
    });
    const d = res.data || res;
    return {
      city: d.city || '全站',
      categories: d.categories || [],
      anonymityK: Number(d.anonymityK ?? 5),
      scope: d.scope || '',
    };
  },

  /** 求购大厅(LBS 紧急→距离→新单) */
  async groupbuyHall(lng: number, lat: number, limit = 50): Promise<GroupbuyVO[]> {
    const res = await request<any>({
      url: `/api/xinzhi/groupbuy?longitude=${lng}&latitude=${lat}&limit=${limit}`,
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toGroupbuy);
  },

  /** 碳档案(67号互助碳+68号求购碳, 不可交易) */
  async carbon(memberId?: number): Promise<CarbonVO> {
    const mid = memberId ?? Number(getMemberId() || 0);
    const res = await request<any>({ url: `/api/xinzhi/carbon/${mid}` });
    const d = res.data || res;
    return {
      memberId: Number(d.memberId ?? mid),
      carbonGrams: Number(d.carbonGrams ?? 0),
      carbonKg: Number(d.carbonKg ?? 0),
      helpOrders: Number(d.helpOrders ?? 0),
      helpCarbonGrams: Number(d.helpCarbonGrams ?? 0),
      groupbuys: Number(d.groupbuys ?? 0),
      groupbuyCarbonGrams: Number(d.groupbuyCarbonGrams ?? 0),
      methodology: d.methodology || '',
    };
  },

  /** 导购人格卡片(公开) */
  async guidePersona(): Promise<GuidePersonaVO> {
    const res = await request<any>({ url: `/api/xinzhi/guide/personas` });
    const d = res.data || res;
    return {
      persona: d.persona || '',
      sopSteps: d.sopSteps || [],
      intents: d.intents || [],
      redLines: d.redLines || [],
      llmBoundary: d.llmBoundary || '',
    };
  },

  /** 灰度总览 */
  async mode(): Promise<ModeVO> {
    const res = await request<any>({ url: `/api/xinzhi/mode` });
    const d = res.data || res;
    return {
      mode: d.mode || 'off',
      source: d.source || 'env',
      paused: Boolean(d.paused),
      override: d.override || '',
      envMode: d.envMode || 'off',
      pausedReason: d.pausedReason || '',
      observablesNeverOff: d.observablesNeverOff,
      decisionSurfaces: d.decisionSurfaces,
      guard: d.guard,
    };
  },

  /** 年度信值白皮书(四章节, 零个体数据) */
  async whitepaper(year?: number): Promise<WhitepaperVO> {
    const res = await request<any>({
      url: `/api/xinzhi/whitepaper${year ? `?year=${year}` : ''}`,
    });
    const d = res.data || res;
    return {
      year: Number(d.year ?? 0),
      generatedAt: d.generatedAt || '',
      sections: d.sections || {},
      piiScanned: Boolean(d.piiScanned),
      piiHits: Number(d.piiHits ?? 0),
      publishNote: d.publishNote || '',
    };
  },

  // ================= 决策面(off=409 灰度) =================

  /** 导购应答(SOP五步; 决策面) */
  async guide(productId: string, query: string): Promise<GuideReplyVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/guide`,
      method: 'POST',
      data: { productId, query },
      headers: { 'X-Member-Id': String(mid) },
    });
    const d = res.data || res;
    return {
      persona: d.persona || '',
      memberId: Number(d.memberId ?? mid),
      productId: d.productId || productId,
      productName: d.productName || '',
      intent: d.intent || '',
      steps: d.steps || {},
      reply: d.reply || '',
      redLines: d.redLines || [],
      xinzhiMode: d.xinzhiMode || undefined,
    };
  },

  /** 发布邻里求购(决策面; 三单上限+违禁词) */
  async publishGroupbuy(params: {
    title: string; productId?: string; quantity?: number;
    urgency?: string; longitude: number; latitude: number; address?: string;
  }): Promise<GroupbuyVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/groupbuy`,
      method: 'POST',
      data: {
        title: params.title,
        productId: params.productId || '',
        quantity: params.quantity ?? 1,
        urgency: params.urgency || 'normal',
        longitude: params.longitude,
        latitude: params.latitude,
        address: params.address || '',
      },
      headers: { 'X-Member-Id': String(mid) },
    });
    return toGroupbuy(res.data || res);
  },

  /** 响应求购(决策面; 仅计数脱敏) */
  async respondGroupbuy(groupbuyId: number): Promise<GroupbuyVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/groupbuy/${groupbuyId}/respond`,
      method: 'POST',
      headers: { 'X-Member-Id': String(mid) },
    });
    return toGroupbuy(res.data || res);
  },

  /** 关闭求购(决策面; 发起人+碳折算) */
  async closeGroupbuy(groupbuyId: number): Promise<GroupbuyVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/groupbuy/${groupbuyId}/close`,
      method: 'POST',
      headers: { 'X-Member-Id': String(mid) },
    });
    return toGroupbuy(res.data || res);
  },

  /** 反馈提交(观测与纠错永不关停) */
  async submitFeedback(scene: string, tags: string[], content: string): Promise<FeedbackVO> {
    const mid = Number(getMemberId() || 0);
    const res = await request<any>({
      url: `/api/xinzhi/feedback`,
      method: 'POST',
      data: { scene, tags, content },
      headers: { 'X-Member-Id': String(mid) },
    });
    const d = res.data || res;
    return {
      feedbackId: Number(d.feedbackId ?? 0),
      scene: d.scene || scene,
      tags: d.tags || [],
      content: d.content || '',
      level: d.level || 'L1',
      status: d.status || '',
      routedTo: d.routedTo || '',
      autoReply: d.autoReply || '',
      sla: d.sla || '',
      createdAt: d.createdAt || '',
    };
  },
};
