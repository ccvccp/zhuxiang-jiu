/**
 * 积分商城 API · 对接后端 /api/credit/exchange/*
 * 兑换目录(商品/权益) → 积分兑换(现金/商品/权益/组合) → 兑换记录 + AI 方案推荐
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 兑换类型中文名 */
export const EXCHANGE_TYPE_NAME: Record<string, string> = {
  cash: '现金',
  goods: '商品',
  benefit: '权益',
  combo: '组合',
};

/** 目录条目分类中文名 */
export const ITEM_CATEGORY_NAME: Record<string, string> = {
  goods: '好酒好物',
  benefit: '会员权益',
};

export const exchangeTypeName = (t: string): string => EXCHANGE_TYPE_NAME[t] || t;

/** 目录条目(商品/权益) */
export interface CatalogItemVO {
  itemId: string;
  category: string;         // goods / benefit
  name: string;
  points: number;
  value: number;            // 价值(元)
  roles: string[];          // 空 = 全员可兑
}

/** 兑换目录(含费率/上限) */
export interface CatalogVO {
  items: CatalogItemVO[];
  rates: Record<string, number>;
  quarterCashCap: number;
  cashTaxFreeAmount: number;
  cashTaxRate: number;
}

/** 兑换记录 */
export interface ExchangeRecordVO {
  exchangeId: number;
  exchangeType: string;
  points: number;
  itemId: string;
  itemName: string;
  value: number;
  tax: number;
  netValue: number;
  createdAt?: string;
}

/** AI 推荐方案 */
export interface ExchangePlanVO {
  planNo: number;
  type: string;
  itemId: string | null;
  itemName: string;
  points: number;
  value: number;
  tax: number;
  netValue: number;
  supplementCash: number;
  reason: string;
}

function toItem(raw: any): CatalogItemVO {
  return {
    itemId: raw.itemId || '',
    category: raw.category || 'goods',
    name: raw.name || '',
    points: Number(raw.points ?? 0),
    value: Number(raw.value ?? 0),
    roles: raw.roles || [],
  };
}

function toRecord(raw: any): ExchangeRecordVO {
  return {
    exchangeId: Number(raw.exchangeId ?? raw.id ?? 0),
    exchangeType: raw.exchangeType || '',
    points: Number(raw.points ?? 0),
    itemId: raw.itemId || '',
    itemName: raw.itemName || '',
    value: Number(raw.value ?? 0),
    tax: Number(raw.tax ?? 0),
    netValue: Number(raw.netValue ?? 0),
    createdAt: raw.createdAt || '',
  };
}

function toPlan(raw: any): ExchangePlanVO {
  return {
    planNo: Number(raw.planNo ?? 0),
    type: raw.type || '',
    itemId: raw.itemId || null,
    itemName: raw.itemName || '',
    points: Number(raw.points ?? 0),
    value: Number(raw.value ?? 0),
    tax: Number(raw.tax ?? 0),
    netValue: Number(raw.netValue ?? 0),
    supplementCash: Number(raw.supplementCash ?? 0),
    reason: raw.reason || '',
  };
}

export const PointsmallAPI = {
  /** 兑换目录(商品/权益/费率/上限, 公开) */
  async catalog(): Promise<CatalogVO> {
    const res = await request<any>({ url: '/api/credit/exchange/catalog' });
    const d = res.data || {};
    return {
      items: (d.items || []).map(toItem),
      rates: d.rates || {},
      quarterCashCap: Number(d.quarterCashCap ?? 5000),
      cashTaxFreeAmount: Number(d.cashTaxFreeAmount ?? 800),
      cashTaxRate: Number(d.cashTaxRate ?? 0.2),
    };
  },

  /** 当前信用积分余额(creditPoints) */
  async creditPoints(): Promise<number> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({ url: `/api/credit/score/${uid}` });
    const d = res.data || res;
    return Number(d.creditPoints ?? d.credit_points ?? 0);
  },

  /** 积分兑换(现金/商品/权益/组合, 含个税与季度上限校验) */
  async exchange(params: {
    exchangeType: 'cash' | 'goods' | 'benefit' | 'combo';
    points: number;
    itemId?: string;
  }): Promise<ExchangeRecordVO> {
    const res = await request<any>({
      url: '/api/credit/exchange',
      method: 'POST',
      data: {
        userId: Number(getMemberId() || 0),
        exchangeType: params.exchangeType,
        points: params.points,
        itemId: params.itemId || null,
      },
    });
    return toRecord(res.data || res);
  },

  /** 我的兑换记录 */
  async records(exchangeType?: string, limit = 50): Promise<ExchangeRecordVO[]> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const qs = exchangeType
      ? `?exchange_type=${exchangeType}&limit=${limit}`
      : `?limit=${limit}`;
    const res = await request<any>({ url: `/api/credit/exchanges/${uid}${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toRecord);
  },

  /** AI 兑换方案推荐(Top3 + 推荐理由) */
  async recommend(): Promise<{
    creditPoints: number;
    isBRole: boolean;
    plans: ExchangePlanVO[];
    recommendedPlanNo: number;
  }> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({
      url: `/api/credit/exchange/recommend/${uid}`,
    });
    const d = res.data || res;
    return {
      creditPoints: Number(d.creditPoints ?? 0),
      isBRole: Boolean(d.isBRole),
      plans: (d.plans || []).map(toPlan),
      recommendedPlanNo: Number(d.recommendedPlanNo ?? 1),
    };
  },
};
