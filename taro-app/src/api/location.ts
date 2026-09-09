/**
 * 位置地图 API · 对接后端 /api/location/*
 * 附近门店(距离排序) / 代理商网点(就近或区域筛选)
 */
import { request } from './request';

export interface NearbyStoreVO {
  id: number;
  storeName: string;
  storeType: string;
  province: string;
  city: string;
  district: string;
  address: string;
  phone: string;
  openHours: string;
  services: string;
  status: string;
  distance: number;    // km
}

export interface AgentLocationVO {
  id: number;
  agentId: number;
  agentName: string;
  agentLevel: string;
  province: string;
  city: string;
  address: string;
  contactName: string;
  contactPhone: string;
  distance?: number;
}

function toStore(s: any): NearbyStoreVO {
  return {
    id: Number(s.id ?? 0),
    storeName: s.storeName || '',
    storeType: s.storeType || '',
    province: s.province || '',
    city: s.city || '',
    district: s.district || '',
    address: s.address || '',
    phone: s.phone || '',
    openHours: s.openHours || '',
    services: s.services || '',
    status: s.status || '',
    distance: Number(s.distance ?? 0),
  };
}

function toAgentLoc(a: any): AgentLocationVO {
  return {
    id: Number(a.id ?? 0),
    agentId: Number(a.agentId ?? 0),
    agentName: a.agentName || '',
    agentLevel: a.agentLevel || '',
    province: a.province || '',
    city: a.city || '',
    address: a.address || '',
    contactName: a.contactName || '',
    contactPhone: a.contactPhone || '',
    distance: a.distance != null ? Number(a.distance) : undefined,
  };
}

export const LocationAPI = {
  /** 附近门店(按距离排序, 公开) */
  async nearbyStores(longitude: number, latitude: number,
                     radiusKm = 10, limit = 20): Promise<NearbyStoreVO[]> {
    const res = await request<any>({
      url: `/api/location/stores/nearby?longitude=${longitude}&latitude=${latitude}&radius_km=${radiusKm}&limit=${limit}`,
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toStore);
  },

  /** 代理商网点(就近或区域筛选, 公开) */
  async agents(params?: {
    province?: string;
    city?: string;
    longitude?: number;
    latitude?: number;
    radiusKm?: number;
    limit?: number;
  }): Promise<AgentLocationVO[]> {
    const p: string[] = [];
    if (params?.province) p.push(`province=${encodeURIComponent(params.province)}`);
    if (params?.city) p.push(`city=${encodeURIComponent(params.city)}`);
    if (params?.longitude != null) p.push(`longitude=${params.longitude}`);
    if (params?.latitude != null) p.push(`latitude=${params.latitude}`);
    if (params?.radiusKm) p.push(`radius_km=${params.radiusKm}`);
    p.push(`limit=${params?.limit ?? 20}`);
    const res = await request<any>({ url: `/api/location/agents?${p.join('&')}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toAgentLoc);
  },
};
