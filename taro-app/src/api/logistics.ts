/**
 * 物流 API · 对接后端 /api/logistics/*
 * 订单详情物流卡: 按订单号查物流单 → 运单轨迹时间线
 */
import { request } from './request';

export interface WaybillVO {
  waybillNo: string;
  carrier: string;
  status: string;
  receiverName?: string;
  receiverAddress?: string;
}

export interface TrackVO {
  id: number;
  waybillNo: string;
  status: string;
  location?: string;
  detail?: string;
  happenedAt: string;
}

export const LogisticsAPI = {
  /** 按订单号查物流单(可能不存在, 返回 null) */
  async orderByOrder(orderId: string): Promise<WaybillVO | null> {
    const res = await request<any>({
      url: `/api/logistics/order-by-order/${orderId}`,
    });
    const w = res.data;
    if (!w) return null;
    return {
      waybillNo: w.waybillNo || w.waybill_no || '',
      carrier: w.carrier || '',
      status: w.status || '',
      receiverName: w.receiverName || w.receiver_name,
      receiverAddress: w.receiverAddress || w.receiver_address,
    };
  },

  /** 运单轨迹列表(时间倒序) */
  async tracks(waybillNo: string, limit = 50): Promise<TrackVO[]> {
    const res = await request<any>({
      url: `/api/logistics/order/${waybillNo}/tracks?limit=${limit}`,
    });
    return (res.data || []).map((t: any) => ({
      id: t.id,
      waybillNo: t.waybillNo || t.waybill_no || '',
      status: t.status || '',
      location: t.location || '',
      detail: t.detail || t.desc || '',
      happenedAt: t.happenedAt || t.happened_at || t.createdAt || '',
    }));
  },
};
