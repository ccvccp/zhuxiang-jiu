/**
 * 同盟商城 API · 对接后端 /api/alliance/*
 * 酒水不分家(水茶酒菜肉鱼器境八类目): 浏览 → 下单 → 我的订单 → 评价
 * 入盟: 超级会员申请 → AI 预审 → 人工终审
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 类目码 → 名称 */
export const CATEGORY_NAME: Record<string, string> = {
  water: '好水',
  tea: '好茶',
  wine: '好酒',
  dish: '好菜',
  meat: '肉类',
  fish: '鱼类',
  vessel: '酒具',
  venue: '好境',
};

/** 商户状态名 */
export const MERCHANT_STATUS_NAME: Record<string, string> = {
  pending: '待处理',
  ai_reviewing: 'AI 预审中',
  manual_reviewing: '人工审核中',
  signed: '已签约',
  probation: '试用期',
  active: '营业中',
  suspended: '已暂停',
  terminated: '已终止',
};

/** 同盟订单状态名 */
export const ORDER_STATUS_NAME: Record<string, string> = {
  paid: '已支付',
  completed: '已完成',
  cancelled: '已取消',
};

/** 场景单状态名 */
export const SCENE_STATUS_NAME: Record<string, string> = {
  created: '待核销',
  redeemed: '已核销',
};

/** 定制需求状态名 */
export const DEMAND_STATUS_NAME: Record<string, string> = {
  demand: '待报价',
  quoted: '已报价',
  confirmed: '已确认',
  producing: '制作中',
  delivered: '已交付',
  cancelled: '已取消',
};

/** 定制类型名 */
export const DEMAND_TYPE_NAME: Record<string, string> = {
  engraving: '酒具刻字',
  private_feast: '私宴定制',
  sealing: '封坛定制',
};

export const categoryName = (c: string): string => CATEGORY_NAME[c] || c;
export const merchantStatusName = (s: string): string => MERCHANT_STATUS_NAME[s] || s;
export const orderStatusName = (s: string): string => ORDER_STATUS_NAME[s] || s;
export const sceneStatusName = (s: string): string => SCENE_STATUS_NAME[s] || s;
export const demandStatusName = (s: string): string => DEMAND_STATUS_NAME[s] || s;
export const demandTypeName = (t: string): string => DEMAND_TYPE_NAME[t] || t;

export interface AllianceCategoryVO {
  code: string;
  name: string;
  traceLevel: string;
  requiredCredentials: string[];
  gridCap: number;
}

export interface AllianceProductVO {
  productId: number;
  sku: string;
  merchantId: number;
  category: string;
  name: string;
  description: string;
  price: number;
  stock: number;
  status: string;
  trace: {
    level: string;
    batchNo: string;
    credentials: string[];
    traceVerified: boolean;
  };
  createdAt: string;
}

export interface AllianceMerchantVO {
  merchantId: number;
  memberId: number;
  category: string;
  shopName: string;
  status: string;
  grade: string;
  creditScore: number;
  ratingAvg: number;
  ratingCount: number;
}

export interface AllianceOrderVO {
  orderId: string;
  productId: number;
  merchantId: number;
  buyerId: number;
  quantity: number;
  amount: number;
  status: string;
  settled: boolean;
  createdAt: string;
}

export interface AllianceReviewVO {
  reviewId: number;
  merchantId: number;
  orderId: string;
  reviewerId: number;
  score: number;
  content: string;
  folded: boolean;
  createdAt: string;
}

/** 酒友小聚场景单(一单三子单 + 核销码) */
export interface SceneVO {
  sceneId: number;
  type: string;
  userId: number;
  partySize: number;
  gatheringTime: string;
  items: { type: string; orderId: string; productId: number; amount: number }[];
  totalAmount: number;
  status: string;
  redeemCode: string;
  redeemedAt: string;
  createdAt: string;
}

/** 定制需求单 */
export interface CustomDemandVO {
  demandId: number;
  userId: number;
  merchantId: number;
  demandType: string;
  description: string;
  budget: number;
  quotedPrice: number;
  status: string;
  createdAt: string;
  updatedAt: string;
}

function toProduct(p: any): AllianceProductVO {
  const t = p.trace || {};
  return {
    productId: Number(p.productId ?? 0),
    sku: p.sku || '',
    merchantId: Number(p.merchantId ?? 0),
    category: p.category || '',
    name: p.name || '',
    description: p.description || '',
    price: Number(p.price ?? 0),
    stock: Number(p.stock ?? 0),
    status: p.status || 'active',
    trace: {
      level: t.level || '',
      batchNo: t.batchNo || '',
      credentials: t.credentials || [],
      traceVerified: Boolean(t.traceVerified),
    },
    createdAt: p.createdAt || '',
  };
}

function toMerchant(m: any): AllianceMerchantVO {
  return {
    merchantId: Number(m.merchantId ?? 0),
    memberId: Number(m.memberId ?? 0),
    category: m.category || '',
    shopName: m.shopName || '',
    status: m.status || '',
    grade: m.grade || 'C',
    creditScore: Number(m.creditScore ?? 0),
    ratingAvg: Number(m.ratingAvg ?? 0),
    ratingCount: Number(m.ratingCount ?? 0),
  };
}

function toOrder(o: any): AllianceOrderVO {
  return {
    orderId: o.orderId || '',
    productId: Number(o.productId ?? 0),
    merchantId: Number(o.merchantId ?? 0),
    buyerId: Number(o.buyerId ?? 0),
    quantity: Number(o.quantity ?? 1),
    amount: Number(o.amount ?? 0),
    status: o.status || 'paid',
    settled: Boolean(o.settled),
    createdAt: o.createdAt || '',
  };
}

function toReview(r: any): AllianceReviewVO {
  return {
    reviewId: Number(r.reviewId ?? 0),
    merchantId: Number(r.merchantId ?? 0),
    orderId: r.orderId || '',
    reviewerId: Number(r.reviewerId ?? 0),
    score: Number(r.score ?? 5),
    content: r.content || '',
    folded: Boolean(r.folded),
    createdAt: r.createdAt || '',
  };
}

function toScene(s: any): SceneVO {
  return {
    sceneId: Number(s.sceneId ?? 0),
    type: s.type || 'gathering',
    userId: Number(s.userId ?? 0),
    partySize: Number(s.partySize ?? 0),
    gatheringTime: s.gatheringTime || '',
    items: (s.items || []).map((i: any) => ({
      type: i.type || '',
      orderId: i.orderId || '',
      productId: Number(i.productId ?? 0),
      amount: Number(i.amount ?? 0),
    })),
    totalAmount: Number(s.totalAmount ?? 0),
    status: s.status || 'created',
    redeemCode: s.redeemCode || '',
    redeemedAt: s.redeemedAt || '',
    createdAt: s.createdAt || '',
  };
}

function toDemand(d: any): CustomDemandVO {
  return {
    demandId: Number(d.demandId ?? 0),
    userId: Number(d.userId ?? 0),
    merchantId: Number(d.merchantId ?? 0),
    demandType: d.demandType || '',
    description: d.description || '',
    budget: Number(d.budget ?? 0),
    quotedPrice: Number(d.quotedPrice ?? 0),
    status: d.status || 'demand',
    createdAt: d.createdAt || '',
    updatedAt: d.updatedAt || '',
  };
}

export const AllianceAPI = {
  /** 类目字典(水茶酒菜肉鱼器境) */
  async categories(): Promise<AllianceCategoryVO[]> {
    const res = await request<any>({ url: '/api/alliance/categories' });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((c: any) => ({
      code: c.code || '',
      name: c.name || '',
      traceLevel: c.traceLevel || '',
      requiredCredentials: c.requiredCredentials || [],
      gridCap: Number(c.gridCap ?? 0),
    }));
  },

  /** 同盟商品列表(按类目/商户筛选, 公开浏览) */
  async products(category?: string, merchantId?: number): Promise<AllianceProductVO[]> {
    const params: string[] = [];
    if (category) params.push(`category=${encodeURIComponent(category)}`);
    if (merchantId) params.push(`merchantId=${merchantId}`);
    const qs = params.length ? `?${params.join('&')}` : '';
    const res = await request<any>({ url: `/api/alliance/products${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toProduct);
  },

  /** 商品详情 */
  async productDetail(productId: number): Promise<AllianceProductVO> {
    const res = await request<any>({ url: `/api/alliance/products/${productId}` });
    return toProduct(res.data || res);
  },

  /** 同盟商品下单(原子扣库存, 下单即付口径) */
  async placeOrder(productId: number, quantity: number): Promise<AllianceOrderVO> {
    const res = await request<any>({
      url: '/api/alliance/order',
      method: 'POST',
      data: { productId, quantity },
    });
    return toOrder(res.data || res);
  },

  /** 我的同盟订单列表(仅本人购买记录) */
  async myOrders(status?: string): Promise<AllianceOrderVO[]> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await request<any>({ url: `/api/alliance/my-orders${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 我的同盟商铺档案(未入盟返回 null → 前端引导入盟) */
  async myMerchant(): Promise<AllianceMerchantVO | null> {
    const res = await request<any>({ url: '/api/alliance/my-merchant' });
    const m = res.data;
    return m ? toMerchant(m) : null;
  },

  /** 入盟申请(AI 预审: ≥80 快车道 / 60-79 人工审 / <60 拒) */
  async apply(params: {
    category: string;
    shopName: string;
    credentials: string[];
  }): Promise<any> {
    return await request<any>({
      url: '/api/alliance/apply',
      method: 'POST',
      data: {
        memberId: Number(getMemberId() || 0),
        category: params.category,
        shopName: params.shopName,
        credentials: params.credentials,
      },
    });
  },

  /** 商户星级概览 */
  async merchantRating(merchantId: number): Promise<{
    ratingAvg: number;
    ratingCount: number;
  }> {
    const res = await request<any>({ url: `/api/alliance/merchants/${merchantId}/rating` });
    const d = res.data || {};
    return {
      ratingAvg: Number(d.ratingAvg ?? d.rating_avg ?? 0),
      ratingCount: Number(d.ratingCount ?? d.rating_count ?? 0),
    };
  },

  /** 评价列表(公开) */
  async reviews(merchantId?: number): Promise<AllianceReviewVO[]> {
    const qs = merchantId ? `?merchantId=${merchantId}` : '';
    const res = await request<any>({ url: `/api/alliance/reviews${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toReview);
  },

  /** 提交评价(结算后一单一评, 1-5 星) */
  async submitReview(params: {
    orderId: string;
    score: number;
    content: string;
  }): Promise<any> {
    return await request<any>({
      url: '/api/alliance/review',
      method: 'POST',
      data: {
        orderId: params.orderId,
        score: params.score,
        content: params.content,
      },
    });
  },

  // ============================================================
  // P2 场景服务: 酒友小聚 / 线下核销 / 定制需求
  // ============================================================

  /** 酒友小聚编排出单(选酒+配菜+订境 → 一单三子单 + 核销码) */
  async createGathering(params: {
    partySize: number;
    wineProductId: number;
    dishMerchantId: number;
    venueMerchantId: number;
    gatheringTime?: string;
  }): Promise<SceneVO> {
    const res = await request<any>({
      url: '/api/alliance/scenes/gathering',
      method: 'POST',
      data: {
        partySize: params.partySize,
        wineProductId: params.wineProductId,
        dishMerchantId: params.dishMerchantId,
        venueMerchantId: params.venueMerchantId,
        gatheringTime: params.gatheringTime || '',
      },
    });
    return toScene(res.data || res);
  },

  /** 我的场景订单列表(一单三子单+核销码状态) */
  async myScenes(status?: string): Promise<SceneVO[]> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await request<any>({ url: `/api/alliance/scenes${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toScene);
  },

  /** 线下核销(到店出示核销码; 三子单立即结算分润, 72h 有效) */
  async redeem(code: string): Promise<any> {
    return await request<any>({
      url: '/api/alliance/redeem',
      method: 'POST',
      data: { code },
    });
  },

  /** 提交定制需求(酒具刻字/私宴定制/封坛定制) */
  async createDemand(params: {
    merchantId: number;
    demandType: string;
    description: string;
    budget?: number;
  }): Promise<CustomDemandVO> {
    const res = await request<any>({
      url: '/api/alliance/custom-demands',
      method: 'POST',
      data: {
        merchantId: params.merchantId,
        demandType: params.demandType,
        description: params.description,
        budget: params.budget ?? 0,
      },
    });
    return toDemand(res.data || res);
  },

  /** 我的定制需求列表(含报价状态) */
  async myDemands(status?: string): Promise<CustomDemandVO[]> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await request<any>({ url: `/api/alliance/my-demands${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toDemand);
  },

  /** 确认定制报价(quoted→confirmed; 须本人) */
  async confirmDemand(demandId: number): Promise<CustomDemandVO> {
    const res = await request<any>({
      url: `/api/alliance/custom-demands/${demandId}/confirm`,
      method: 'POST',
    });
    return toDemand(res.data || res);
  },
};
