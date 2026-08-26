/**
 * 订单 API · 对接后端 /api/order/*
 */
import { request } from './request';

export interface OrderItemInput {
  productId: string;
  productName: string;
  quantity: number;
  unitPrice: number;
}

export const OrderAPI = {
  /** 创建订单 */
  async create(params: {
    items: OrderItemInput[];
    address?: any;
    usePoints?: number;
    remark?: string;
  }): Promise<any> {
    return await request<any>({
      url: '/api/order/create',
      method: 'POST',
      data: {
        items: params.items,
        address: params.address || {},
        usePoints: params.usePoints || 0,
        remark: params.remark || '',
      },
    });
  },

  /** 我的订单(可按状态筛选) */
  async myOrders(status?: string): Promise<any> {
    const qs = status ? `?status=${status}` : '';
    return await request<any>(`/api/order/my${qs}`);
  },

  /** 订单详情 */
  async detail(orderId: string): Promise<any> {
    return await request<any>(`/api/order/${orderId}`);
  },

  /** 支付订单 */
  async pay(orderId: string, method = 'wechat'): Promise<any> {
    return await request<any>({
      url: `/api/order/${orderId}/pay`,
      method: 'POST',
      data: { method },
    });
  },

  /** 取消订单 */
  async cancel(orderId: string, reason = '用户取消'): Promise<any> {
    return await request<any>({
      url: `/api/order/${orderId}/cancel`,
      method: 'POST',
      data: { reason },
    });
  },

  /** 状态列表 */
  async statuses(): Promise<any> {
    return await request<any>({ url: '/api/order/statuses' });
  },
};
