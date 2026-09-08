/**
 * 老酒回收 API · 对接后端 /api/recycle/*
 * 老酒估价 → 回收申请(兑换/折现) → 兑换新酒/折现回收 → 记录查询
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 申请状态 */
export const APP_STATUS_NAME: Record<string, string> = {
  pending: '待审核',
  valuing: '估价中',
  valued: '已估价',
  reviewing: '审核中',
  approved: '审核通过',
  rejected: '已拒绝',
  recycling: '回收中',
  exchanging: '兑换中',
  completed: '已完成',
  cancelled: '已取消',
};

/** 品质分级 */
export const CONDITION_GRADES: Array<{ key: string; label: string; desc: string }> = [
  { key: 'A', label: 'A 级', desc: '全新 · 100%' },
  { key: 'B', label: 'B 级', desc: '良好 · 95%' },
  { key: 'C', label: 'C 级', desc: '一般 · 90%' },
  { key: 'D', label: 'D 级', desc: '较差 · 85%' },
];

export const appStatusName = (s: string): string => APP_STATUS_NAME[s] || s;

export interface ValuationVO {
  id: number;
  productId: string;
  purchasePrice: number;
  purchaseDate: string;
  wineAge: number;
  conditionGrade: string;
  appreciationRate: number;
  oldValue: number;
  cashValue: number;
  forExchange: number;
  lifeCode: string;
  createdAt: string;
}

export interface ApplicationVO {
  id: number;
  type: string;   // exchange / recycle
  valuationIds: number[];
  oldWineCount: number;
  oldWineTotalValue: number;
  cashValue: number;
  newProductId: string | null;
  newProductPrice: number | null;
  priceDiff?: number | null;
  payoutMethod?: string | null;
  status: string;
  createdAt: string;
}

export interface ExchangeVO {
  id: number;
  applicationId: number;
  type: string;
  oldWineTotalValue: number;
  newProductId: string;
  newProductPrice: number;
  priceDiff: number;
  pointsConverted: number;
  cashAmount?: number;
  taxAmount?: number;
  actualPayout?: number;
  status: string;
  createdAt: string;
}

function toValuation(v: any): ValuationVO {
  return {
    id: v.id ?? 0,
    productId: v.productId || v.product_id || '',
    purchasePrice: v.purchasePrice ?? v.purchase_price ?? 0,
    purchaseDate: v.purchaseDate || v.purchase_date || '',
    wineAge: v.wineAge ?? v.wine_age ?? 0,
    conditionGrade: v.conditionGrade || v.condition_grade || 'A',
    appreciationRate: v.appreciationRate ?? v.appreciation_rate ?? 0,
    oldValue: v.oldValue ?? v.old_value ?? 0,
    cashValue: v.cashValue ?? v.cash_value ?? 0,
    forExchange: v.forExchange ?? 1,
    lifeCode: v.lifeCode || v.life_code || '',
    createdAt: v.createdAt || v.created_at || '',
  };
}

function toApplication(a: any): ApplicationVO {
  return {
    id: a.id ?? 0,
    type: a.type || '',
    valuationIds: a.valuationIds || a.valuation_ids || [],
    oldWineCount: a.oldWineCount ?? a.old_wine_count ?? 0,
    oldWineTotalValue: a.oldWineTotalValue ?? a.old_wine_total_value ?? 0,
    cashValue: a.cashValue ?? a.cash_value ?? 0,
    newProductId: a.newProductId ?? null,
    newProductPrice: a.newProductPrice ?? null,
    priceDiff: a.priceDiff ?? null,
    payoutMethod: a.payoutMethod ?? null,
    status: a.status || '',
    createdAt: a.createdAt || a.created_at || '',
  };
}

function toExchange(e: any): ExchangeVO {
  return {
    id: e.id ?? 0,
    applicationId: e.applicationId ?? e.application_id ?? 0,
    type: e.type || '',
    oldWineTotalValue: e.oldWineTotalValue ?? e.old_wine_total_value ?? 0,
    newProductId: e.newProductId || e.new_product_id || '',
    newProductPrice: e.newProductPrice ?? e.new_product_price ?? 0,
    priceDiff: e.priceDiff ?? e.price_diff ?? 0,
    pointsConverted: e.pointsConverted ?? e.points_converted ?? 0,
    cashAmount: e.cashAmount ?? e.cash_amount,
    taxAmount: e.taxAmount ?? e.tax_amount,
    actualPayout: e.actualPayout ?? e.actual_payout,
    status: e.status || '',
    createdAt: e.createdAt || e.created_at || '',
  };
}

export const RecycleAPI = {
  /** 提交老酒估价(酒龄≥3年; 返回增值率/老酒价值/折现值) */
  async submitValuation(params: {
    productId: string;
    purchasePrice: number;
    purchaseDate: string;
    conditionGrade?: string;
    memberLevel?: number;
    forExchange?: boolean;
    lifeCode?: string;
  }): Promise<ValuationVO> {
    const res = await request<any>({
      url: '/api/recycle/valuation/submit',
      method: 'POST',
      data: {
        userId: Number(getMemberId()),
        productId: params.productId,
        purchasePrice: params.purchasePrice,
        purchaseDate: params.purchaseDate,
        conditionGrade: params.conditionGrade || 'A',
        memberLevel: params.memberLevel ?? 1,
        forExchange: params.forExchange ?? true,
        lifeCode: params.lifeCode || null,
      },
    });
    return toValuation(res.data || res);
  },

  /** 我的估价记录 */
  async myValuations(limit = 100): Promise<ValuationVO[]> {
    const res = await request<any>({ url: `/api/recycle/my-valuations?limit=${limit}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toValuation);
  },

  /** 提交回收申请(exchange: 单次≤5瓶; recycle: 单次≤3瓶) */
  async submitApplication(params: {
    type: 'exchange' | 'recycle';
    valuationIds: number[];
    newProductId?: string;
    newProductPrice?: number;
    payoutMethod?: string;
    payoutAccount?: string;
  }): Promise<ApplicationVO> {
    const res = await request<any>({
      url: '/api/recycle/application/submit',
      method: 'POST',
      data: {
        userId: Number(getMemberId()),
        type: params.type,
        valuationIds: params.valuationIds,
        newProductId: params.newProductId || null,
        newProductPrice: params.newProductPrice || null,
        payoutMethod: params.payoutMethod || null,
        payoutAccount: params.payoutAccount || null,
      },
    });
    return toApplication(res.data || res);
  },

  /** 我的回收申请列表 */
  async myApplications(limit = 50): Promise<ApplicationVO[]> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({ url: `/api/recycle/applications?user_id=${uid}&limit=${limit}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toApplication);
  },

  /** 兑换新酒(审核通过后执行; 老酒价值抵扣+差价/转积分) */
  async exchangeNewWine(appId: number, newProductId: string, newProductPrice: number): Promise<ExchangeVO> {
    const res = await request<any>({
      url: `/api/recycle/application/${appId}/exchange`,
      method: 'POST',
      data: {
        newProductId,
        newProductPrice,
        diffPaymentMethod: 'wechat',
      },
    });
    return toExchange(res.data || res);
  },

  /** 折现回收(审核通过后执行; ×80%+个税扣除) */
  async recycleForCash(appId: number, payoutMethod: string, payoutAccount: string): Promise<ExchangeVO> {
    const res = await request<any>({
      url: `/api/recycle/application/${appId}/recycle`,
      method: 'POST',
      data: { payoutMethod, payoutAccount },
    });
    return toExchange(res.data || res);
  },

  /** 我的兑换/回收记录 */
  async myExchanges(limit = 50): Promise<ExchangeVO[]> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({ url: `/api/recycle/exchanges?user_id=${uid}&limit=${limit}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toExchange);
  },
};
