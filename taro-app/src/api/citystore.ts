/**
 * 市级网店 API · 对接后端 /api/citystore/*
 * SVIP 开店(城市独占) → 审核 → 运营(月度考核/折扣调整) → 订单关联统计
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 网店状态: 0待审核 1运营中 2预警 3暂停 4已取消 */
export const STORE_STATUS_NAME: Record<number, string> = {
  0: '待审核',
  1: '运营中',
  2: '预警',
  3: '暂停',
  4: '已取消',
};

/** 状态 → 徽标样式 key */
export const STORE_STATUS_CLS: Record<number, string> = {
  0: 'pending',
  1: 'operating',
  2: 'warning',
  3: 'suspended',
  4: 'cancelled',
};

/** 考核资格状态: 1正常 2预警 3黄牌 4取消 */
export const QUAL_STATUS_NAME: Record<number, string> = {
  1: '正常',
  2: '预警',
  3: '黄牌',
  4: '资格取消',
};

/** 销售渠道: 1直播 2小程序 3社群 4H5 5抖音 */
export const SALES_CHANNEL_NAME: Record<number, string> = {
  1: '直播',
  2: '小程序',
  3: '社群',
  4: 'H5',
  5: '抖音',
};

export const storeStatusName = (s: number): string => STORE_STATUS_NAME[s] || `状态${s}`;
export const qualStatusName = (s: number): string => QUAL_STATUS_NAME[s] || `状态${s}`;

export interface CityStoreVO {
  storeCode: string;
  storeName: string;
  memberId: number;
  cityCode: string;
  cityName: string;
  provinceCode: string;
  provinceName: string;
  businessLicense: string;
  foodLicense: string;
  taxRegNo: string;
  status: number;
  statusName?: string;
  openDate: string | null;
  closeDate: string | null;
  currentDiscount: number;
  consecutiveBelowPurchase: number;
  consecutiveBelowSales: number;
  createdAt: string;
  updatedAt: string;
}

export interface CityVO {
  cityCode: string;
  cityName: string;
  provinceCode: string;
  provinceName: string;
}

export interface StoreAssessmentVO {
  storeCode: string;
  assessmentMonth: string;
  monthlyPurchaseAmount: number;
  purchaseTarget: number;
  purchaseQualified: number;
  monthlySalesAmount: number;
  salesTarget: number;
  salesQualified: number;
  currentMonthDiscount: number;
  nextMonthDiscount: number;
  consecutiveBelowPurchase: number;
  consecutiveBelowSales: number;
  qualificationStatus: number;
  assessedAt: string;
}

export interface StoreOrderVO {
  orderNo: string;
  productId: string;
  productName: string;
  quantity: number;
  retailPrice: number;
  totalAmount: number;
  salesChannel: number;
  createdAt: string;
}

function toStore(s: any): CityStoreVO {
  return {
    storeCode: s.storeCode || '',
    storeName: s.storeName || '',
    memberId: Number(s.memberId ?? 0),
    cityCode: s.cityCode || '',
    cityName: s.cityName || '',
    provinceCode: s.provinceCode || '',
    provinceName: s.provinceName || '',
    businessLicense: s.businessLicense || '',
    foodLicense: s.foodLicense || '',
    taxRegNo: s.taxRegNo || '',
    status: Number(s.status ?? 0),
    statusName: s.statusName || storeStatusName(Number(s.status ?? 0)),
    openDate: s.openDate ?? null,
    closeDate: s.closeDate ?? null,
    currentDiscount: Number(s.currentDiscount ?? 1),
    consecutiveBelowPurchase: Number(s.consecutiveBelowPurchase ?? 0),
    consecutiveBelowSales: Number(s.consecutiveBelowSales ?? 0),
    createdAt: s.createdAt || '',
    updatedAt: s.updatedAt || '',
  };
}

function toAssessment(a: any): StoreAssessmentVO {
  return {
    storeCode: a.storeCode || '',
    assessmentMonth: a.assessmentMonth || '',
    monthlyPurchaseAmount: Number(a.monthlyPurchaseAmount ?? 0),
    purchaseTarget: Number(a.purchaseTarget ?? 0),
    purchaseQualified: Number(a.purchaseQualified ?? 0),
    monthlySalesAmount: Number(a.monthlySalesAmount ?? 0),
    salesTarget: Number(a.salesTarget ?? 0),
    salesQualified: Number(a.salesQualified ?? 0),
    currentMonthDiscount: Number(a.currentMonthDiscount ?? 1),
    nextMonthDiscount: Number(a.nextMonthDiscount ?? 1),
    consecutiveBelowPurchase: Number(a.consecutiveBelowPurchase ?? 0),
    consecutiveBelowSales: Number(a.consecutiveBelowSales ?? 0),
    qualificationStatus: Number(a.qualificationStatus ?? 1),
    assessedAt: a.assessedAt || '',
  };
}

function toOrder(o: any): StoreOrderVO {
  return {
    orderNo: o.orderNo || '',
    productId: String(o.productId ?? ''),
    productName: o.productName || '',
    quantity: Number(o.quantity ?? 1),
    retailPrice: Number(o.retailPrice ?? 0),
    totalAmount: Number(o.totalAmount ?? 0),
    salesChannel: Number(o.salesChannel ?? 2),
    createdAt: o.createdAt || '',
  };
}

export const CityStoreAPI = {
  /** 我的网店列表 */
  async myStores(status?: number): Promise<CityStoreVO[]> {
    const qs = status != null ? `?status=${status}` : '';
    const res = await request<any>({ url: `/api/citystore/list${qs}` });
    const list = (res.data || {}).stores || [];
    return (Array.isArray(list) ? list : []).map(toStore);
  },

  /** 可用城市列表(未被独占, 开店选城市用) */
  async availableCities(): Promise<{ cities: CityVO[]; count: number; occupiedCount: number }> {
    const res = await request<any>({ url: '/api/citystore/cities/available' });
    const d = res.data || {};
    return {
      cities: (d.cities || []).map((c: any) => ({
        cityCode: c.cityCode || '',
        cityName: c.cityName || '',
        provinceCode: c.provinceCode || '',
        provinceName: c.provinceName || '',
      })),
      count: Number(d.count ?? 0),
      occupiedCount: Number(d.occupiedCount ?? 0),
    };
  },

  /** 申请开店(SVIP 专属 + 城市独占) */
  async apply(params: {
    memberLevel: number;
    storeName: string;
    city: CityVO;
    businessLicense: string;
    foodLicense: string;
    taxRegNo?: string;
  }): Promise<CityStoreVO> {
    const res = await request<any>({
      url: '/api/citystore/apply',
      method: 'POST',
      data: {
        memberId: Number(getMemberId() || 0),
        memberLevel: params.memberLevel,
        storeName: params.storeName,
        cityCode: params.city.cityCode,
        cityName: params.city.cityName,
        provinceCode: params.city.provinceCode,
        provinceName: params.city.provinceName,
        businessLicense: params.businessLicense,
        foodLicense: params.foodLicense,
        taxRegNo: params.taxRegNo || '',
      },
    });
    return toStore(res.data || res);
  },

  /** 网店详情 */
  async storeDetail(storeCode: string): Promise<CityStoreVO> {
    const res = await request<any>({ url: `/api/citystore/${storeCode}` });
    return toStore(res.data || res);
  },

  /** 网店考核记录列表 */
  async assessments(storeCode: string): Promise<StoreAssessmentVO[]> {
    const res = await request<any>({ url: `/api/citystore/${storeCode}/assessments` });
    const d = res.data || {};
    const list = d.assessments || [];
    return (Array.isArray(list) ? list : []).map(toAssessment);
  },

  /** 网店关联订单列表(月度筛选可选) */
  async orders(storeCode: string, month?: string): Promise<StoreOrderVO[]> {
    const qs = month ? `?month=${encodeURIComponent(month)}` : '';
    const res = await request<any>({ url: `/api/citystore/${storeCode}/orders${qs}` });
    const d = res.data || {};
    const list = d.orders || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 关联订单到网店(销售额统计) */
  async addOrder(storeCode: string, params: {
    orderNo: string;
    productId: string;
    productName?: string;
    quantity: number;
    retailPrice: number;
    totalAmount: number;
    salesChannel?: number;
  }): Promise<any> {
    return await request<any>({
      url: `/api/citystore/${storeCode}/orders`,
      method: 'POST',
      data: {
        orderNo: params.orderNo,
        productId: params.productId,
        productName: params.productName || '',
        quantity: params.quantity,
        retailPrice: params.retailPrice,
        totalAmount: params.totalAmount,
        salesChannel: params.salesChannel ?? 4,
      },
    });
  },

  /** 下单入口决策(市级网店优先: 所在城市有营业市店 → 市店入口) */
  async decideOrderEntry(params: {
    cityCode?: string;
    cityName?: string;
    provinceName?: string;
    longitude?: number;
    latitude?: number;
  }): Promise<{
    entry: 'citystore' | 'site';
    reason: string;
    store: CityStoreVO | null;
  }> {
    const res = await request<any>({
      url: '/api/citystore/order-entry/decide',
      method: 'POST',
      data: {
        cityCode: params.cityCode || null,
        cityName: params.cityName || null,
        provinceName: params.provinceName || null,
        longitude: params.longitude ?? null,
        latitude: params.latitude ?? null,
      },
    });
    return {
      entry: res.entry || 'site',
      reason: res.reason || '',
      store: res.store ? toStore(res.store) : null,
    };
  },
};
