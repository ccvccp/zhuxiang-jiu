/**
 * 场馆合作联盟 API · 对接后端 /api/venue/*
 * 酒店/酒吧/会所合作商: 浏览合作场馆 → 申请入驻 → 场地管理 → 铺货记录
 */
import { request } from './request';
import Taro from '@tarojs/taro';

/** 合作商类型 */
export const PARTNER_TYPE_NAME: Record<string, string> = {
  hotel: '酒店',
  bar: '酒吧',
  club: '会所',
};

/** 合作商状态 */
export const PARTNER_STATUS_NAME: Record<string, string> = {
  pending: '申请中',
  reviewing: '审核中',
  signed: '已签约',
  active: '合作中',
  suspended: '已暂停',
  terminated: '已终止',
  rejected: '已驳回',
};

/** 供货模式 */
export const SUPPLY_MODE_NAME: Record<string, string> = {
  agent: '代理商供货',
  direct: '本站直供',
  neighbor: '邻区调货',
};

export const partnerTypeName = (t: string): string => PARTNER_TYPE_NAME[t] || t;
export const partnerStatusName = (s: string): string => PARTNER_STATUS_NAME[s] || s;
export const supplyModeName = (s: string): string => SUPPLY_MODE_NAME[s] || s;

export interface VenuePartnerVO {
  id: number;
  partnerType: string;
  partnerName: string;
  creditCode: string;
  starLevel: number;
  partnerLevel: string;      // D/C/B/A/S
  status: string;
  supplyMode: string;
  tastingRate: number;
  contactAddress: string;
  contractStart: string;
  contractEnd: string;
}

export interface VenueVO {
  id: number;
  partnerId: number;
  venueName: string;
  venueType: string;
  address: string;
  capacity: number;
  managerName: string;
  managerPhone: string;
  businessHours: string;
  status: string;
}

// 本地存储: 我申请的合作商 ID(后端 partner 档案无 memberId 关联)
const MY_PARTNER_KEY = 'venue_my_partner_id';

function toPartner(p: any): VenuePartnerVO {
  return {
    id: Number(p.id ?? p.partnerId ?? 0),
    partnerType: p.partnerType || '',
    partnerName: p.partnerName || '',
    creditCode: p.creditCode || '',
    starLevel: Number(p.starLevel ?? 0),
    partnerLevel: p.partnerLevel || 'D',
    status: p.status || '',
    supplyMode: p.supplyMode || 'direct',
    tastingRate: Number(p.tastingRate ?? 0),
    contactAddress: p.contactAddress || '',
    contractStart: p.contractStart || '',
    contractEnd: p.contractEnd || '',
  };
}

function toVenue(v: any): VenueVO {
  return {
    id: Number(v.id ?? v.venueId ?? 0),
    partnerId: Number(v.partnerId ?? 0),
    venueName: v.venueName || '',
    venueType: v.venueType || '',
    address: v.address || '',
    capacity: Number(v.capacity ?? 0),
    managerName: v.managerName || '',
    managerPhone: v.managerPhone || '',
    businessHours: v.businessHours || '',
    status: v.status || '',
  };
}

export const VenueAPI = {
  /** 合作商列表(公开, 按类型筛选) */
  async partners(partnerType?: string, status = 'active'): Promise<VenuePartnerVO[]> {
    const params: string[] = [];
    if (partnerType) params.push(`partner_type=${partnerType}`);
    if (status) params.push(`status=${status}`);
    const qs = params.length ? `?${params.join('&')}` : '';
    const res = await request<any>({ url: `/api/venue/partners${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toPartner);
  },

  /** 合作商详情(公开) */
  async partnerDetail(partnerId: number): Promise<VenuePartnerVO> {
    const res = await request<any>({ url: `/api/venue/partners/${partnerId}` });
    return toPartner(res.data || res);
  },

  /** 场地列表(公开, 按合作商/类型筛选) */
  async venues(partnerId?: number, venueType?: string): Promise<VenueVO[]> {
    const params: string[] = [];
    if (partnerId) params.push(`partner_id=${partnerId}`);
    if (venueType) params.push(`venue_type=${encodeURIComponent(venueType)}`);
    const qs = params.length ? `?${params.join('&')}` : '';
    const res = await request<any>({ url: `/api/venue/venues${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toVenue);
  },

  /** 申请合作入驻(hotel/bar/club) */
  async applyPartner(params: {
    partnerType: string;
    partnerName: string;
    creditCode: string;
    legalPerson?: string;
    contactPhone?: string;
    contactAddress?: string;
    starLevel?: number;
  }): Promise<VenuePartnerVO> {
    const res = await request<any>({
      url: '/api/venue/partners',
      method: 'POST',
      data: {
        partnerType: params.partnerType,
        partnerName: params.partnerName,
        creditCode: params.creditCode,
        legalPerson: params.legalPerson || '',
        contactPhone: params.contactPhone || '',
        contactAddress: params.contactAddress || '',
        longitude: 0,
        latitude: 0,
        starLevel: params.starLevel ?? 0,
      },
    });
    const partner = toPartner(res.data || res);
    // 本地记住我申请的合作商 ID(后端档案无 memberId 关联)
    if (partner.id > 0) {
      Taro.setStorageSync(MY_PARTNER_KEY, partner.id);
    }
    return partner;
  },

  /** 我申请的合作商(本地记住 ID; 未申请返回 null) */
  async myPartner(): Promise<VenuePartnerVO | null> {
    let pid = 0;
    try {
      pid = Number(Taro.getStorageSync(MY_PARTNER_KEY)) || 0;
    } catch (_) { /* 忽略 */ }
    if (!pid) return null;
    try {
      return await this.partnerDetail(pid);
    } catch (_) {
      return null;
    }
  },

  /** 创建场地(合作商侧) */
  async createVenue(partnerId: number, params: {
    venueName: string;
    venueType: string;
    address?: string;
    capacity?: number;
    managerName?: string;
    managerPhone?: string;
  }): Promise<VenueVO> {
    const res = await request<any>({
      url: '/api/venue/venues',
      method: 'POST',
      data: {
        partnerId,
        venueName: params.venueName,
        venueType: params.venueType,
        address: params.address || '',
        capacity: params.capacity ?? 0,
        managerName: params.managerName || '',
        managerPhone: params.managerPhone || '',
        businessHours: '',
      },
    });
    return toVenue(res.data || res);
  },
};
