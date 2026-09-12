/**
 * 信值·臻选购物平台 API · 对接后端 /api/xinzhi/*(68号)
 * 五维雷达(47/67/44 只读聚合) · 臻选货架(L1-L4) · 透明定价(杀熟审计)
 * 小竹导购(SOP五步法) · 邻里求购(67号范式) · 商家体系 · 灰度三态
 * 宪法口径: 观测面永不关停; 决策面 off=409(灰度放量 shadow→assist)
 */
import { request } from './request';
import { getMemberId, getSession } from '@/services/auth-service';

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
/** 店铺状态(九态状态机, P6 平台化) */
export const XINZHI_SHOP_STATUS: Record<string, string> = {
  pending: '已申请(待AI预审)', ai_reviewing: 'AI预审中',
  manual_reviewing: '人工审核中', signed: '已签约',
  probation: '观察期(30天)', active: '正式营业',
  suspended: '暂停整改', terminated: '已关店', rejected: '审核拒绝',
};
/** 店铺等级(S/A/B/C 四档, 复用 P4 商家评级) */
export const XINZHI_SHOP_GRADE: Record<string, string> = {
  S: 'S 旗舰店', A: 'A 优选店', B: 'B 标准店', C: 'C 新锐店',
};
/** 铺货四门禁(P7) */
export const XINZHI_GATES: Record<string, string> = {
  qualification: '商家资质门禁', trace: '溯源门禁',
  compliance: '合规门禁', primeScore: '信值分门禁',
};
/** 铺货状态(五态) */
export const XINZHI_LISTING_STATUS: Record<string, string> = {
  draft: '草稿(门禁未全过)', reviewing: '待审核(门禁已过)',
  listed: '已上架', delisted: '商家下架', removed: '已移除',
};
/** 臻选订单状态(九态, P8) */
export const XINZHI_ORDER_STATUS: Record<string, string> = {
  PENDING: '待付款', PAID: '待发货', SHIPPED: '待收货',
  RECEIVED: '待评价', COMPLETED: '已完成', CANCELLED: '已取消',
  CLOSED: '已关闭', RETURNING: '退货中', REFUNDED: '已退款',
};
/** 支付三通道(P9, 1TV=1元) */
export const XINZHI_PAY_METHOD: Record<string, string> = {
  wallet: '余额支付', trust_value: '信值TV支付', mixed: '组合支付(TV+余额)',
};
/** 结算单状态(三态, P9) */
export const XINZHI_SETTLE_STATUS: Record<string, string> = {
  pending: '待结算', settled: '已结算', reversed: '已冲正',
};

export const xinzhiGradeName = (g: string): string =>
  XINZHI_GRADE_NAME[g] || g;
export const xinzhiDimName = (d: string): string =>
  XINZHI_DIM_NAME[d] || d;
export const xinzhiGbStatusName = (s: string): string =>
  XINZHI_GB_STATUS_NAME[s] || s;
export const xinzhiShopStatusName = (s: string): string =>
  XINZHI_SHOP_STATUS[s] || s;
export const xinzhiShopGradeName = (g: string): string =>
  XINZHI_SHOP_GRADE[g] || g;
export const xinzhiGateName = (k: string): string =>
  XINZHI_GATES[k] || k;
export const xinzhiListingStatusName = (s: string): string =>
  XINZHI_LISTING_STATUS[s] || s;
export const xinzhiOrderStatusName = (s: string): string =>
  XINZHI_ORDER_STATUS[s] || s;
export const xinzhiPayMethodName = (m: string): string =>
  XINZHI_PAY_METHOD[m] || m;
export const xinzhiSettleStatusName = (s: string): string =>
  XINZHI_SETTLE_STATUS[s] || s;

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

/** VO: 角色店铺(P6·九态状态机) */
export interface ShopVO {
  shopId: number;
  memberId?: number;
  shopName: string;
  category: string;
  categoryLabel: string;
  intro: string;
  status: string;
  statusLabel: string;
  shopLevel: string;
  merchantGrade: string;
  radarTotal: number;
  createdAt: string;
  disposition?: Record<string, any> | null;
}

/** VO: 店铺雷达快照(预警建议书——永不自动执行) */
export interface ShopRadarVO {
  shopId: number;
  status: string;
  current: { score: number; grade: string };
  snapshots: { score: number; grade: string; at: string }[];
  warning: Record<string, any> | null;
  warnLine: number;
}

/** VO: 铺货商品(P7·四门禁) */
export interface ListingVO {
  listingId: number;
  shopId: number;
  shopName: string;
  productId: string;
  productName: string;
  category: string;
  sourcePrice: number;
  status: string;
  statusLabel: string;
  gatesPassed: boolean;
  xinzhiPrice: {
    finalPrice: number;
    xinzhiCredit: number;
    xinzhiAlpha: number;
    breakdownLine: string;
  };
  disposition?: Record<string, any> | null;
}

/** VO: 公开货架条目(信值价快照展平) */
export interface ShelfItemVO {
  listingId: number;
  productId: string;
  productName: string;
  category: string;
  shopId: number;
  shopName: string;
  sourcePrice: number;
  xinzhiFinalPrice: number;
  xinzhiCredit: number;
  breakdownLine: string;
}

/** VO: 公开货架(品类聚合) */
export interface ShelfVO {
  total: number;
  categories: { category: string; count: number; items: ShelfItemVO[] }[];
}

/** VO: 购物车(P8·快照=当时试算) */
export interface CartVO {
  memberId: number;
  items: {
    listingId: number;
    quantity: number;
    snapshot: { finalPrice: number; xinzhiCredit: number; breakdownLine: string };
  }[];
}

/** VO: 结算预览(实时重算) */
export interface PreviewVO {
  memberId: number;
  totals: {
    baseTotal: number;
    afterThreeTotal: number;
    xinzhiCredit: number;
    goodsTotal: number;
    shippingFee: number;
    actualAmount: number;
  };
  alphaCap: { capRate: number; creditTotal: number; capAmount: number; ok: boolean };
  shippingRule: { freeThreshold: number; fee: number };
  shopCount: number;
  crossShop: boolean;
  note: string;
}

/** VO: 臻选订单(P8·九态) */
export interface OrderVO {
  orderId: string;
  memberId: number;
  shopId: number;
  shopMemberId: number;
  shopName: string;
  items: {
    listingId: number;
    productName: string;
    quantity: number;
    unitPrice: number;
    subtotal: number;
    xinzhiCredit: number;
  }[];
  priceDetail: {
    baseTotal: number;
    xinzhiCredit: number;
    goodsTotal: number;
    shippingFee: number;
    actualAmount: number;
  };
  status: string;
  statusName: string;
  address: Record<string, any>;
  remark: string;
  payment: { method: string; paidAt: string; funding: { source: string; amount: number }[] };
  logistics: { carrier: string; waybillNo: string };
  createdAt: string;
}

/** VO: 下单结果 */
export interface CreateOrderVO {
  orderId: string;
  status: string;
  statusName: string;
  priceDetail: OrderVO['priceDetail'];
}

/** VO: 支付结果(资金源留痕 funding) */
export interface PayResultVO {
  orderId: string;
  status: string;
  statusName: string;
  payment: { method: string; paidAt: string; funding: { source: string; amount: number; txRef: string }[] };
  settlement: SettlementVO;
}

/** VO: 结算单(P9·分账明细) */
export interface SettlementVO {
  settleId: number;
  orderId: string;
  memberId?: number;
  shopId: number;
  shopMemberId: number;
  shopName: string;
  orderAmount: number;
  merchantProceeds: number;
  platformFee: number;
  proceedsRate: number;
  feeRate: number;
  status: string;
  statusLabel: string;
  walletTxNo: string;
  reversalDebt: number;
  settledAt: string;
  reversedAt: string;
}

/** VO: T+1 分账执行结果(幂等) */
export interface SettleRunVO {
  operator: string;
  settledCount: number;
  settled: number[];
  skipped: number;
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

function toShop(x: any): ShopVO {
  const status = x.status || 'pending';
  return {
    shopId: Number(x.shopId ?? 0),
    memberId: x.memberId != null ? Number(x.memberId) : undefined,
    shopName: x.shopName || '',
    category: x.category || '',
    categoryLabel: x.categoryLabel || '',
    intro: x.intro || '',
    status,
    statusLabel: x.statusLabel || xinzhiShopStatusName(status),
    shopLevel: x.shopLevel || '',
    merchantGrade: x.merchantGrade || '',
    radarTotal: Number(x.radarTotal ?? 0),
    createdAt: x.createdAt || '',
    disposition: x.disposition || null,
  };
}

function toShopRadar(x: any): ShopRadarVO {
  return {
    shopId: Number(x.shopId ?? 0),
    status: x.status || '',
    current: {
      score: Number(x.current?.score ?? 0),
      grade: x.current?.grade || '',
    },
    snapshots: (x.snapshots || []).map((s: any) => ({
      score: Number(s.score ?? 0),
      grade: s.grade || '',
      at: s.at || '',
    })),
    warning: x.warning || null,
    warnLine: Number(x.warnLine ?? 0),
  };
}

function toListing(x: any): ListingVO {
  const p = x.xinzhiPrice || {};
  const status = x.status || 'draft';
  return {
    listingId: Number(x.listingId ?? 0),
    shopId: Number(x.shopId ?? 0),
    shopName: x.shopName || '',
    productId: x.productId || '',
    productName: x.productName || '',
    category: x.category || '',
    sourcePrice: Number(x.sourcePrice ?? 0),
    status,
    statusLabel: xinzhiListingStatusName(status),
    gatesPassed: Boolean(x.gatesPassed),
    xinzhiPrice: {
      finalPrice: Number(p.finalPrice ?? 0),
      xinzhiCredit: Number(p.xinzhiCredit ?? 0),
      xinzhiAlpha: Number(p.xinzhiAlpha ?? 0),
      breakdownLine: p.breakdownLine || '',
    },
    disposition: x.disposition || null,
  };
}

function toShelfItem(x: any): ShelfItemVO {
  return {
    listingId: Number(x.listingId ?? 0),
    productId: x.productId || '',
    productName: x.productName || '',
    category: x.category || '',
    shopId: Number(x.shopId ?? 0),
    shopName: x.shopName || '',
    sourcePrice: Number(x.sourcePrice ?? 0),
    xinzhiFinalPrice: Number(x.xinzhiFinalPrice ?? 0),
    xinzhiCredit: Number(x.xinzhiCredit ?? 0),
    breakdownLine: x.breakdownLine || '',
  };
}

function toShelf(x: any): ShelfVO {
  return {
    total: Number(x.total ?? 0),
    categories: (x.categories || []).map((c: any) => ({
      category: c.category || '',
      count: Number(c.count ?? 0),
      items: (c.items || []).map(toShelfItem),
    })),
  };
}

function toCart(x: any): CartVO {
  return {
    memberId: Number(x.memberId ?? 0),
    items: (x.items || []).map((i: any) => {
      const s = i.priceSnapshot || {};
      return {
        listingId: Number(i.listingId ?? 0),
        quantity: Number(i.quantity ?? 1),
        snapshot: {
          finalPrice: Number(s.finalPrice ?? 0),
          xinzhiCredit: Number(s.xinzhiCredit ?? 0),
          breakdownLine: s.breakdownLine || '',
        },
      };
    }),
  };
}

function toPreview(x: any): PreviewVO {
  const t = x.totals || {};
  return {
    memberId: Number(x.memberId ?? 0),
    totals: {
      baseTotal: Number(t.baseTotal ?? 0),
      afterThreeTotal: Number(t.afterThreeTotal ?? 0),
      xinzhiCredit: Number(t.xinzhiCredit ?? 0),
      goodsTotal: Number(t.goodsTotal ?? 0),
      shippingFee: Number(t.shippingFee ?? 0),
      actualAmount: Number(t.actualAmount ?? 0),
    },
    alphaCap: {
      capRate: Number(x.alphaCap?.capRate ?? 0),
      creditTotal: Number(x.alphaCap?.creditTotal ?? 0),
      capAmount: Number(x.alphaCap?.capAmount ?? 0),
      ok: Boolean(x.alphaCap?.ok),
    },
    shippingRule: {
      freeThreshold: Number(x.shippingRule?.freeThreshold ?? 0),
      fee: Number(x.shippingRule?.fee ?? 0),
    },
    shopCount: Number(x.shopCount ?? 0),
    crossShop: Boolean(x.crossShop),
    note: x.note || '',
  };
}

function toOrder(x: any): OrderVO {
  const p = x.priceDetail || {};
  const status = x.status || 'PENDING';
  return {
    orderId: x.orderId || '',
    memberId: Number(x.memberId ?? 0),
    shopId: Number(x.shopId ?? 0),
    shopMemberId: Number(x.shopMemberId ?? 0),
    shopName: x.shopName || '',
    items: (x.items || []).map((i: any) => ({
      listingId: Number(i.listingId ?? 0),
      productName: i.productName || '',
      quantity: Number(i.quantity ?? 1),
      unitPrice: Number(i.unitPrice ?? 0),
      subtotal: Number(i.subtotal ?? 0),
      xinzhiCredit: Number(i.xinzhiCredit ?? 0),
    })),
    priceDetail: {
      baseTotal: Number(p.baseTotal ?? 0),
      xinzhiCredit: Number(p.xinzhiCredit ?? 0),
      goodsTotal: Number(p.goodsTotal ?? 0),
      shippingFee: Number(p.shippingFee ?? 0),
      actualAmount: Number(p.actualAmount ?? 0),
    },
    status,
    statusName: xinzhiOrderStatusName(status),
    address: x.address || {},
    remark: x.remark || '',
    payment: {
      method: x.payment?.method || '',
      paidAt: x.payment?.paidAt || '',
      funding: (x.payment?.funding || []).map((f: any) => ({
        source: f.source || '',
        amount: Number(f.amount ?? 0),
      })),
    },
    logistics: {
      carrier: x.logistics?.carrier || '',
      waybillNo: x.logistics?.waybillNo || '',
    },
    createdAt: x.createdAt || '',
  };
}

function toSettlement(x: any): SettlementVO {
  const status = x.status || 'pending';
  return {
    settleId: Number(x.settleId ?? 0),
    orderId: x.orderId || '',
    memberId: x.memberId != null ? Number(x.memberId) : undefined,
    shopId: Number(x.shopId ?? 0),
    shopMemberId: Number(x.shopMemberId ?? 0),
    shopName: x.shopName || '',
    orderAmount: Number(x.orderAmount ?? 0),
    merchantProceeds: Number(x.merchantProceeds ?? 0),
    platformFee: Number(x.platformFee ?? 0),
    proceedsRate: Number(x.proceedsRate ?? 0),
    feeRate: Number(x.feeRate ?? 0),
    status,
    statusLabel: xinzhiSettleStatusName(status),
    walletTxNo: x.walletTxNo || '',
    reversalDebt: Number(x.reversalDebt ?? 0),
    settledAt: x.settledAt || '',
    reversedAt: x.reversedAt || '',
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

/** 会员头(商家/消费者侧鉴权锚——68号惯例) */
const memberHeaders = (): Record<string, string> => ({
  'X-Member-Id': String(Number(getMemberId() || 0)),
});

/** 管理头(X-Role: admin——zt.ts 同款范式) */
const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

/** 查看者头(买家/店铺归属商家/admin 三方可见的详情端点) */
const viewerHeaders = (): Record<string, string> => {
  const headers = memberHeaders();
  if (getSession()?.role === 'admin') {
    headers['X-Role'] = 'admin';
  }
  return headers;
};

/** P6-P7 店铺与铺货(平台化·39 端点之一) */
export const XinzhiShopAPI = {
  // ================= P6 角色店铺(10) =================

  /** 店铺状态字典(九态+转移表+门槛公示——公开) */
  async shopStatuses(): Promise<any> {
    const res = await request<any>({ url: '/api/xinzhi/shop/statuses' });
    return res.data || res;
  },

  /** 开店申请(雷达≥60+L3+一人一铺; 409 拦截信息) */
  async applyShop(shopName: string, category: string, intro: string): Promise<ShopVO> {
    const res = await request<any>({
      url: '/api/xinzhi/shop/apply',
      method: 'POST',
      data: { shopName, category, intro },
      headers: memberHeaders(),
    });
    return toShop(res.data || res);
  },

  /** 我的店铺(未开店返回 null) */
  async myShop(): Promise<ShopVO | null> {
    const res = await request<any>({
      url: '/api/xinzhi/shop/mine',
      headers: memberHeaders(),
    });
    return res.data == null ? null : toShop(res.data);
  },

  /** 店铺列表(admin, 按 status 过滤) */
  async shops(status?: string): Promise<ShopVO[]> {
    const res = await request<any>({
      url: `/api/xinzhi/shops${status ? `?status=${encodeURIComponent(status)}` : ''}`,
      headers: adminHeaders(),
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toShop);
  },

  /** 店铺主页公开信息(零个体数据) */
  async shopPage(shopId: number): Promise<ShopVO> {
    const res = await request<any>({ url: `/api/xinzhi/shop/${shopId}` });
    return toShop(res.data || res);
  },

  /** 店铺人工审核(admin, 建议书) */
  async reviewShop(shopId: number, approved: boolean, note = ''): Promise<ShopVO> {
    const res = await request<any>({
      url: `/api/xinzhi/shop/${shopId}/review`,
      method: 'POST',
      data: { approved, note },
      headers: adminHeaders(),
    });
    return toShop(res.data || res);
  },

  /** 暂停整改(admin, 处罚类须确认) */
  async suspendShop(shopId: number, reason = ''): Promise<ShopVO> {
    const res = await request<any>({
      url: `/api/xinzhi/shop/${shopId}/suspend`,
      method: 'POST',
      data: { reason },
      headers: adminHeaders(),
    });
    return toShop(res.data || res);
  },

  /** 激活/恢复(admin; signed→probation→active 三路径) */
  async activateShop(shopId: number): Promise<ShopVO> {
    const res = await request<any>({
      url: `/api/xinzhi/shop/${shopId}/activate`,
      method: 'POST',
      headers: adminHeaders(),
    });
    return toShop(res.data || res);
  },

  /** 商家自关店(仅 active→terminated) */
  async closeShop(shopId: number, reason = ''): Promise<ShopVO> {
    const res = await request<any>({
      url: `/api/xinzhi/shop/${shopId}/close`,
      method: 'POST',
      data: { reason },
      headers: memberHeaders(),
    });
    return toShop(res.data || res);
  },

  /** 店铺雷达快照+跌破预警建议书(永不自动执行) */
  async radarSnapshot(shopId: number): Promise<ShopRadarVO> {
    const res = await request<any>({ url: `/api/xinzhi/shop/${shopId}/radar-snapshot` });
    return toShopRadar(res.data || res);
  },

  // ================= P7 店铺铺货(11) =================

  /** 铺货门禁字典(四门禁规则公示——公开) */
  async gatesDict(): Promise<any> {
    const res = await request<any>({ url: '/api/xinzhi/listing/gates' });
    return res.data || res;
  },

  /** 铺货提交(主站商品池只读选品→四门禁) */
  async submitListing(productId: string): Promise<ListingVO> {
    const res = await request<any>({
      url: '/api/xinzhi/listing/submit',
      method: 'POST',
      data: { productId },
      headers: memberHeaders(),
    });
    return toListing(res.data || res);
  },

  /** 我的铺货(商家维度, 最新优先) */
  async myListings(limit = 200): Promise<ListingVO[]> {
    const res = await request<any>({
      url: `/api/xinzhi/listing/mine?limit=${limit}`,
      headers: memberHeaders(),
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toListing);
  },

  /** 铺货详情(归属商家/admin, 含门禁明细) */
  async listingDetail(listingId: number): Promise<ListingVO> {
    const res = await request<any>({
      url: `/api/xinzhi/listing/${listingId}`,
      headers: viewerHeaders(),
    });
    return toListing(res.data || res);
  },

  /** 铺货列表(admin, 按 status 过滤) */
  async adminListings(status?: string): Promise<ListingVO[]> {
    const res = await request<any>({
      url: `/api/xinzhi/listings${status ? `?status=${encodeURIComponent(status)}` : ''}`,
      headers: adminHeaders(),
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toListing);
  },

  /** 铺货审核(admin, 四门禁复核+建议书) */
  async reviewListing(listingId: number, approved: boolean, note = ''): Promise<ListingVO> {
    const res = await request<any>({
      url: `/api/xinzhi/listing/${listingId}/review`,
      method: 'POST',
      data: { approved, note },
      headers: adminHeaders(),
    });
    return toListing(res.data || res);
  },

  /** 商家下架(listed→delisted) */
  async delistListing(listingId: number): Promise<ListingVO> {
    const res = await request<any>({
      url: `/api/xinzhi/listing/${listingId}/delist`,
      method: 'POST',
      headers: memberHeaders(),
    });
    return toListing(res.data || res);
  },

  /** 商家重新上架(delisted→listed) */
  async relistListing(listingId: number): Promise<ListingVO> {
    const res = await request<any>({
      url: `/api/xinzhi/listing/${listingId}/list`,
      method: 'POST',
      headers: memberHeaders(),
    });
    return toListing(res.data || res);
  },

  /** 平台移除(违规, admin 建议书) */
  async removeListing(listingId: number, reason = ''): Promise<ListingVO> {
    const res = await request<any>({
      url: `/api/xinzhi/listing/${listingId}/remove`,
      method: 'POST',
      data: { reason },
      headers: adminHeaders(),
    });
    return toListing(res.data || res);
  },

  /** 公开货架(listed 品类聚合+信值价快照) */
  async shelf(category?: string, limit = 20): Promise<ShelfVO> {
    const q: string[] = [];
    if (category) q.push(`category=${encodeURIComponent(category)}`);
    q.push(`limit=${limit}`);
    const res = await request<any>({ url: `/api/xinzhi/shelf?${q.join('&')}` });
    return toShelf(res.data || res);
  },

  /** 店铺商品(公开——listed/delisted 透明可见) */
  async shopListings(shopId: number, limit = 200): Promise<{ shopId: number; shopName: string; shopLevel: string; count: number; items: ShelfItemVO[] }> {
    const res = await request<any>({
      url: `/api/xinzhi/shop/${shopId}/listings?limit=${limit}`,
    });
    const d = res.data || res;
    return {
      shopId: Number(d.shopId ?? shopId),
      shopName: d.shopName || '',
      shopLevel: d.shopLevel || '',
      count: Number(d.count ?? 0),
      items: (d.items || []).map(toShelfItem),
    };
  },
};

/** P8-P9 购物与结算(平台化·39 端点之二) */
export const XinzhiTradeAPI = {
  // ================= P8 购物车(5) =================

  /** 加购(校验 listed; 价格快照=当时试算) */
  async cartAdd(listingId: number, quantity: number): Promise<CartVO> {
    const res = await request<any>({
      url: '/api/xinzhi/cart/add',
      method: 'POST',
      data: { listingId, quantity },
      headers: memberHeaders(),
    });
    return toCart(res.data || res);
  },

  /** 改量(条目须已在购物车) */
  async cartUpdate(listingId: number, quantity: number): Promise<CartVO> {
    const res = await request<any>({
      url: '/api/xinzhi/cart/update',
      method: 'POST',
      data: { listingId, quantity },
      headers: memberHeaders(),
    });
    return toCart(res.data || res);
  },

  /** 移除条目 */
  async cartRemove(listingId: number): Promise<CartVO> {
    const res = await request<any>({
      url: '/api/xinzhi/cart/remove',
      method: 'POST',
      data: { listingId },
      headers: memberHeaders(),
    });
    return toCart(res.data || res);
  },

  /** 我的购物车(空车诚实零值) */
  async cartMine(): Promise<CartVO> {
    const res = await request<any>({
      url: '/api/xinzhi/cart/mine',
      headers: memberHeaders(),
    });
    return toCart(res.data || res);
  },

  /** 结算预览(实时重算——α抵扣明细+运费满99免) */
  async checkoutPreview(): Promise<PreviewVO> {
    const res = await request<any>({
      url: '/api/xinzhi/cart/checkout-preview',
      method: 'POST',
      headers: memberHeaders(),
    });
    return toPreview(res.data || res);
  },

  // ================= P8 下单与流转(8) =================

  /** 下单(年龄门+库存预扣; XZ 前缀订单号; items 空=全量) */
  async createOrder(params: {
    items?: { listingId: number; quantity: number }[];
    address: Record<string, any>;
    remark?: string;
    ageConfirmed?: boolean;
  }): Promise<CreateOrderVO> {
    const res = await request<any>({
      url: '/api/xinzhi/order/create',
      method: 'POST',
      data: {
        items: params.items || null,
        address: params.address,
        remark: params.remark || '',
        ageConfirmed: Boolean(params.ageConfirmed),
      },
      headers: memberHeaders(),
    });
    const d = res.data || res;
    return {
      orderId: d.orderId || '',
      status: d.status || 'PENDING',
      statusName: d.statusName || xinzhiOrderStatusName(d.status || 'PENDING'),
      priceDetail: toOrder(d).priceDetail,
    };
  },

  /** 取消订单(PENDING→CANCELLED, 库存回补) */
  async cancelOrder(orderId: string, reason = '用户取消'): Promise<OrderVO> {
    const res = await request<any>({
      url: `/api/xinzhi/order/${orderId}/cancel`,
      method: 'POST',
      data: { reason },
      headers: memberHeaders(),
    });
    return toOrder(res.data || res);
  },

  /** 我的臻选订单(最新优先) */
  async myOrders(status?: string): Promise<OrderVO[]> {
    const res = await request<any>({
      url: `/api/xinzhi/order/mine${status ? `?status=${encodeURIComponent(status)}` : ''}`,
      headers: memberHeaders(),
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 订单详情(买家/店铺归属商家/admin 可见) */
  async orderDetail(orderId: string): Promise<OrderVO> {
    const res = await request<any>({
      url: `/api/xinzhi/order/${orderId}`,
      headers: viewerHeaders(),
    });
    return toOrder(res.data || res);
  },

  /** 支付三通道(wallet/trust_value[1TV=1元]/mixed) */
  async payOrder(
    orderId: string,
    method: string,
    useTrustValue?: number,
    trustId?: number,
  ): Promise<PayResultVO> {
    const res = await request<any>({
      url: `/api/xinzhi/order/${orderId}/pay`,
      method: 'POST',
      data: {
        method,
        useTrustValue: useTrustValue ?? null,
        trustId: trustId ?? null,
      },
      headers: memberHeaders(),
    });
    const d = res.data || res;
    return {
      orderId: d.orderId || orderId,
      status: d.status || 'PAID',
      statusName: d.statusName || xinzhiOrderStatusName(d.status || 'PAID'),
      payment: {
        method: d.payment?.method || method,
        paidAt: d.payment?.paidAt || '',
        funding: (d.payment?.funding || []).map((f: any) => ({
          source: f.source || '',
          amount: Number(f.amount ?? 0),
          txRef: f.txRef || '',
        })),
      },
      settlement: toSettlement(d.settlement || {}),
    };
  },

  /** 发货(商家侧 PAID→SHIPPED, 店铺归属校验) */
  async shipOrder(orderId: string, carrier: string, waybillNo: string): Promise<OrderVO> {
    const res = await request<any>({
      url: `/api/xinzhi/order/${orderId}/ship`,
      method: 'POST',
      data: { carrier, waybillNo },
      headers: memberHeaders(),
    });
    return toOrder(res.data || res);
  },

  /** 确认收货(SHIPPED→RECEIVED) */
  async confirmOrder(orderId: string): Promise<OrderVO> {
    const res = await request<any>({
      url: `/api/xinzhi/order/${orderId}/confirm`,
      method: 'POST',
      headers: memberHeaders(),
    });
    return toOrder(res.data || res);
  },

  /** 评价(RECEIVED→COMPLETED, 信值回流留痕) */
  async reviewOrder(orderId: string, rating: number, content = ''): Promise<OrderVO> {
    const res = await request<any>({
      url: `/api/xinzhi/order/${orderId}/review`,
      method: 'POST',
      data: { rating, content },
      headers: memberHeaders(),
    });
    return toOrder(res.data || res);
  },

  // ================= P9 结算(5) =================

  /** T+1 分账执行(admin/调度, 幂等: 已 settled 跳过) */
  async runSettlements(): Promise<SettleRunVO> {
    const res = await request<any>({
      url: '/api/xinzhi/settlement/run',
      method: 'POST',
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      operator: d.operator || '',
      settledCount: Number(d.settledCount ?? 0),
      settled: d.settled || [],
      skipped: Number(d.skipped ?? 0),
    };
  },

  /** 商家结算单列表(店铺归属) */
  async mySettlements(): Promise<SettlementVO[]> {
    const res = await request<any>({
      url: '/api/xinzhi/settlement/mine',
      headers: memberHeaders(),
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toSettlement);
  },

  /** 结算单详情(买家/归属商家/admin 可见) */
  async settlementDetail(settleId: number): Promise<SettlementVO> {
    const res = await request<any>({
      url: `/api/xinzhi/settlement/${settleId}`,
      headers: viewerHeaders(),
    });
    return toSettlement(res.data || res);
  },

  /** 结算冲正(admin; wallet 不足记负债, 诚实标注) */
  async reverseSettlement(settleId: number, reason = ''): Promise<SettlementVO> {
    const res = await request<any>({
      url: `/api/xinzhi/settlement/${settleId}/reverse`,
      method: 'POST',
      data: { reason },
      headers: adminHeaders(),
    });
    return toSettlement(res.data || res);
  },

  /** 结算单总览(admin, 可按状态筛) */
  async settlements(status?: string): Promise<SettlementVO[]> {
    const res = await request<any>({
      url: `/api/xinzhi/settlements${status ? `?status=${encodeURIComponent(status)}` : ''}`,
      headers: adminHeaders(),
    });
    const list = res.data || res || [];
    return (Array.isArray(list) ? list : []).map(toSettlement);
  },
};
