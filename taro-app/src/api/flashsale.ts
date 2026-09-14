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

// ============================================================
// 管理端(admin 面向——场次配置/发布/风控参数/统计)
// ============================================================

/** 秒杀风控参数 */
export interface FlashSettingsVO {
  enabled: boolean;
  minRegisterHours: number;
  minMemberLevel: number;
  orderExpireMinutes: number;
  maxQuantityPerOrder: number;
  updatedAt?: string;
}

/** 全局销售统计(按场次聚合) */
export interface FlashStatsVO {
  sessionCount: number;
  orderCount: number;
  paidAmount: number;
  sessions?: any[];
  [k: string]: any;
}

function adminHeaders(): Record<string, string> {
  return { 'X-Role': 'admin' };
}

function mapSettings(s: any): FlashSettingsVO {
  return {
    enabled: s.enabled !== false,
    minRegisterHours: s.minRegisterHours ?? 0,
    minMemberLevel: s.minMemberLevel ?? 0,
    orderExpireMinutes: s.orderExpireMinutes ?? 15,
    maxQuantityPerOrder: s.maxQuantityPerOrder ?? 1,
    updatedAt: s.updatedAt || s.updated_at || '',
  };
}

export const FlashAdminAPI = {
  /** 管理场次列表(含草稿/已取消——区别于公开列表) */
  async listSessions(): Promise<FlashSessionVO[]> {
    const res = await request<any>({
      url: '/api/flash/admin/sessions',
      headers: adminHeaders(),
    });
    return (res.sessions || []).map(mapSession);
  },

  /** 创建秒杀场次(草稿) */
  async createSession(name: string, startTime: string,
                      endTime: string): Promise<FlashSessionVO> {
    const res = await request<any>({
      url: '/api/flash/admin/sessions',
      method: 'POST',
      headers: adminHeaders(),
      data: { name, startTime, endTime },
    });
    return mapSession(res.session || res);
  },

  /** 编辑场次(仅草稿态; 局部更新——未传字段沿用现值) */
  async updateSession(sessionId: string,
                      patch: { name?: string; startTime?: string;
                               endTime?: string }): Promise<FlashSessionVO> {
    const res = await request<any>({
      url: `/api/flash/admin/sessions/${sessionId}`,
      method: 'PUT',
      headers: adminHeaders(),
      data: patch,
    });
    return mapSession(res.session || res);
  },

  /** 添加秒杀商品(仅草稿场次; 秒杀价须低于原价) */
  async addItem(sessionId: string, productId: string,
                 flashPrice: number, flashStock: number,
                 limitPerMember: number): Promise<FlashItemVO> {
    const res = await request<any>({
      url: `/api/flash/admin/sessions/${sessionId}/items`,
      method: 'POST',
      headers: adminHeaders(),
      data: { productId, flashPrice, flashStock, limitPerMember },
    });
    return mapItem(res.item || res);
  },

  /** 发布场次(用户侧可见) */
  async publishSession(sessionId: string): Promise<FlashSessionVO> {
    const res = await request<any>({
      url: `/api/flash/admin/sessions/${sessionId}/publish`,
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
    return mapSession(res.session || res);
  },

  /** 取消场次(联动取消待支付订单并回补库存) */
  async cancelSession(sessionId: string): Promise<FlashSessionVO> {
    const res = await request<any>({
      url: `/api/flash/admin/sessions/${sessionId}/cancel`,
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
    return mapSession(res.session || res);
  },

  /** 查询秒杀风控参数 */
  async getSettings(): Promise<FlashSettingsVO> {
    const res = await request<any>({
      url: '/api/flash/admin/settings',
      headers: adminHeaders(),
    });
    return mapSettings(res.settings || res);
  },

  /** 修改秒杀风控参数(白名单字段, 即时生效) */
  async updateSettings(patch: Partial<FlashSettingsVO>): Promise<FlashSettingsVO> {
    const res = await request<any>({
      url: '/api/flash/admin/settings',
      method: 'POST',
      headers: adminHeaders(),
      data: patch,
    });
    return mapSettings(res.settings || res);
  },

  /** 全局销售统计 */
  async stats(): Promise<FlashStatsVO> {
    const res = await request<any>({
      url: '/api/flash/admin/stats',
      headers: adminHeaders(),
    });
    return res.stats || res;
  },

  /** 批量取消超时未支付订单(回补库存) */
  async expireCancel(): Promise<{ cancelled: number }> {
    const res = await request<any>({
      url: '/api/flash/admin/orders/expire-cancel',
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
    return { cancelled: Number(res.cancelled ?? 0) };
  },
};
