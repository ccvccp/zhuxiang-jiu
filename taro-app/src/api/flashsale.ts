/**
 * 限时秒杀 API · 对接后端 /api/flash/*
 * 场次列表/详情(剩余库存+进度) → 抢购 → 我的秒杀订单(支付/取消)
 */
import { request } from './request';

export interface FlashItemVO {
  itemId: string;
  productId: string;
  productName: string;
  originalPrice: number;
  flashPrice: number;
  flashStock: number;
  limitPerMember: number;
  soldCount: number;
  remainingStock: number;
  progressPercent: number;
}

export interface FlashSessionVO {
  sessionId: string;
  name: string;
  startTime: string;
  endTime: string;
  status: string;
  runtimeStatus: string;        // UPCOMING | IN_PROGRESS | ENDED
  runtimeStatusName: string;
  itemCount: number;
  items?: FlashItemVO[];
}

export interface FlashOrderVO {
  orderNo: string;
  sessionId: string;
  itemId: string;
  productName: string;
  quantity: number;
  totalAmount: number;
  status: string;               // PENDING | PAID | CANCELLED | EXPIRED
  createdAt: string;
}

function mapItem(i: any): FlashItemVO {
  return {
    itemId: i.itemId || i.item_id || '',
    productId: i.productId || i.product_id || '',
    productName: i.productName || i.product_name || '',
    originalPrice: i.originalPrice ?? i.original_price ?? 0,
    flashPrice: i.flashPrice ?? i.flash_price ?? 0,
    flashStock: i.flashStock ?? i.flash_stock ?? 0,
    limitPerMember: i.limitPerMember ?? i.limit_per_member ?? 1,
    soldCount: i.soldCount ?? i.sold_count ?? 0,
    remainingStock: i.remainingStock ?? i.remaining_stock ?? 0,
    progressPercent: i.progressPercent ?? i.progress_percent ?? 0,
  };
}

function mapSession(s: any): FlashSessionVO {
  return {
    sessionId: s.sessionId || s.session_id || '',
    name: s.name || '',
    startTime: s.startTime || s.start_time || '',
    endTime: s.endTime || s.end_time || '',
    status: s.status || '',
    runtimeStatus: s.runtimeStatus || s.runtime_status || '',
    runtimeStatusName: s.runtimeStatusName || s.runtime_status_name || '',
    itemCount: s.itemCount ?? s.item_count ?? 0,
    items: (s.items || []).map(mapItem),
  };
}

function mapOrder(o: any): FlashOrderVO {
  return {
    orderNo: o.orderNo || o.order_no || '',
    sessionId: o.sessionId || o.session_id || '',
    itemId: o.itemId || o.item_id || '',
    productName: o.productName || o.product_name || '',
    quantity: o.quantity ?? 1,
    totalAmount: o.totalAmount ?? o.total_amount ?? 0,
    status: o.status || 'PENDING',
    createdAt: o.createdAt || o.created_at || '',
  };
}

export const FlashAPI = {
  /** 场次列表(仅已发布, 附运行时状态) */
  async sessions(): Promise<FlashSessionVO[]> {
    const res = await request<any>({ url: '/api/flash/sessions' });
    return (res.sessions || []).map(mapSession);
  },

  /** 场次详情 + 秒杀商品(剩余库存/抢购进度) */
  async sessionDetail(sessionId: string): Promise<FlashSessionVO> {
    const res = await request<any>({ url: `/api/flash/sessions/${sessionId}` });
    return mapSession(res.session || res);
  },

  /** 抢购下单(幂等/限购/库存原子判定在后端锁内) */
  async purchase(sessionId: string, itemId: string, quantity = 1): Promise<FlashOrderVO> {
    const res = await request<any>({
      url: '/api/flash/order',
      method: 'POST',
      data: { sessionId, itemId, quantity },
    });
    return mapOrder(res.order || res);
  },

  /** 我的秒杀订单(倒序) */
  async myOrders(): Promise<FlashOrderVO[]> {
    const res = await request<any>({ url: '/api/flash/my/orders' });
    return (res.orders || []).map(mapOrder);
  },

  /** 秒杀订单支付(模拟记账) */
  async pay(orderNo: string): Promise<any> {
    return await request<any>({
      url: `/api/flash/orders/${orderNo}/pay`,
      method: 'POST',
      data: {},
    });
  },

  /** 取消秒杀订单(库存回补) */
  async cancel(orderNo: string): Promise<any> {
    return await request<any>({
      url: `/api/flash/orders/${orderNo}/cancel`,
      method: 'POST',
      data: {},
    });
  },
};
