/**
 * 订单 API · 对接后端 /api/order/*
 * 状态机: PENDING待付款 → PAID待发货 → SHIPPED待收货 → RECEIVED待评价 → COMPLETED已完成
 *         PENDING → CANCELLED已取消
 */
import { request } from './request';

export interface OrderItemInput {
  productId: string;
  productName: string;
  quantity: number;
  unitPrice: number;
}

// 后端订单条目
export interface OrderItem {
  productId: string;
  productName: string;
  quantity: number;
  unitPrice: number;
}

// 价格明细
export interface OrderPriceDetail {
  goodsTotal: number;       // 商品总价
  memberDiscount: number;   // 会员折扣(负数)
  couponDiscount: number;   // 优惠券(负数)
  pointsDiscount: number;   // 积分抵扣(负数)
  shippingFee: number;      // 运费
  actualAmount: number;     // 实付金额
}

// 订单时间线节点
export interface OrderTimelineNode {
  status: string;
  time: string;
  action?: string;
}

// 订单(列表/详情通用)
export interface OrderVO {
  orderId: string;
  status: string;           // PENDING/PAID/SHIPPED/RECEIVED/COMPLETED/CANCELLED/RETURNING
  statusName: string;       // 待付款/待发货/...
  items: OrderItem[];
  priceDetail: OrderPriceDetail;
  address?: { name?: string; phone?: string; province?: string; city?: string; district?: string; detail?: string };
  timeline: OrderTimelineNode[];
  createdAt: string;
  remark?: string;
}

// 状态码 → 中文(与后端 STATUS_CN 对齐)
export const ORDER_STATUS_NAME: Record<string, string> = {
  PENDING: '待付款',
  PAID: '待发货',
  SHIPPED: '待收货',
  RECEIVED: '待评价',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
  RETURNING: '退货中',
};

// 后端条目 → VO 映射
const mapOrder = (o: any): OrderVO => ({
  orderId: String(o.orderId || o.order_id || ''),
  status: o.status || '',
  statusName: o.statusName || ORDER_STATUS_NAME[o.status] || o.status || '',
  items: (o.items || []).map((i: any): OrderItem => ({
    productId: String(i.productId || i.product_id || ''),
    productName: i.productName || i.product_name || i.name || '商品',
    quantity: i.quantity || 1,
    unitPrice: i.unitPrice || i.unit_price || i.price || 0,
  })),
  priceDetail: {
    goodsTotal: o.priceDetail?.goodsTotal ?? 0,
    memberDiscount: o.priceDetail?.memberDiscount ?? 0,
    couponDiscount: o.priceDetail?.couponDiscount ?? 0,
    pointsDiscount: o.priceDetail?.pointsDiscount ?? 0,
    shippingFee: o.priceDetail?.shippingFee ?? 0,
    actualAmount: o.priceDetail?.actualAmount ?? 0,
  },
  address: o.address || undefined,
  timeline: (o.timeline || []).map((t: any): OrderTimelineNode => ({
    status: t.status || '',
    time: t.time || '',
    action: t.action,
  })),
  createdAt: o.createdAt || o.created_at || '',
  remark: o.remark || '',
});

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

  /** 我的订单(可按状态筛选, 返回类型化列表) */
  async myList(status?: string): Promise<OrderVO[]> {
    const qs = status ? `?status=${status}` : '';
    const res = await request<any>({ url: `/api/order/my${qs}` });
    return (res.orders || []).map(mapOrder);
  },

  /** 我的订单(原始响应, 兼容旧调用) */
  async myOrders(status?: string): Promise<any> {
    const qs = status ? `?status=${status}` : '';
    return await request<any>({ url: `/api/order/my${qs}` });
  },

  /** 订单详情(类型化) */
  async detailVO(orderId: string): Promise<OrderVO> {
    const res = await request<any>({ url: `/api/order/${orderId}` });
    return mapOrder(res.order || {});
  },

  /** 订单详情(原始响应, 兼容旧调用) */
  async detail(orderId: string): Promise<any> {
    return await request<any>({ url: `/api/order/${orderId}` });
  },

  /** 支付订单 PENDING → PAID */
  async pay(orderId: string, method = 'wechat'): Promise<any> {
    return await request<any>({
      url: `/api/order/${orderId}/pay`,
      method: 'POST',
      data: { method },
    });
  },

  /** 取消订单 PENDING → CANCELLED */
  async cancel(orderId: string, reason = '用户取消'): Promise<any> {
    return await request<any>({
      url: `/api/order/${orderId}/cancel`,
      method: 'POST',
      data: { reason },
    });
  },

  /** 确认收货 SHIPPED → RECEIVED */
  async confirm(orderId: string): Promise<any> {
    return await request<any>({
      url: `/api/order/${orderId}/confirm`,
      method: 'POST',
      data: {},
    });
  },

  /** 评价订单 RECEIVED → COMPLETED */
  async review(orderId: string, rating: number, content = ''): Promise<any> {
    return await request<any>({
      url: `/api/order/${orderId}/review`,
      method: 'POST',
      data: { rating, content },
    });
  },

  /** 状态列表 */
  async statuses(): Promise<any> {
    return await request<any>({ url: '/api/order/statuses' });
  },
};
