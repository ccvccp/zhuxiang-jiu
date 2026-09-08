/**
 * 站内消息 API · 对接后端 /api/message/*
 * 消息列表 → 详情/已读 → 批量已读 → 统计(未读数)
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 消息分类显示名 */
export const CATEGORY_NAME: Record<string, string> = {
  system: '系统通知',
  order: '订单消息',
  logistics: '物流消息',
  activity: '活动消息',
  coupon: '优惠券',
  member: '会员消息',
  old_wine: '老酒回收',
  content: '内容消息',
  security: '安全通知',
  service: '服务消息',
};

/** 分类图标 */
export const CATEGORY_ICON: Record<string, string> = {
  system: '📢',
  order: '📦',
  logistics: '🚚',
  activity: '🎁',
  coupon: '🎫',
  member: '👤',
  old_wine: '🍶',
  content: '📰',
  security: '🔒',
  service: '🎧',
};

export const categoryName = (c: string): string => CATEGORY_NAME[c] || '通知';

export interface MessageVO {
  id: number;
  userId: number;
  channel: string;
  title: string;
  content: string;
  category: string;
  jumpUrl: string;
  priority: string;
  status: string;   // unread / read / deleted
  createdAt: string;
  readAt: string | null;
}

function toMessage(m: any): MessageVO {
  return {
    id: m.id ?? 0,
    userId: Number(m.userId ?? m.user_id ?? 0),
    channel: m.channel || 'inmail',
    title: m.title || '',
    content: m.content || '',
    category: m.category || 'system',
    jumpUrl: m.jumpUrl || m.jump_url || '',
    priority: m.priority || 'P2',
    status: m.status || 'unread',
    createdAt: m.createdAt || m.created_at || '',
    readAt: m.readAt || m.read_at || null,
  };
}

export const MessageAPI = {
  /** 我的消息列表(会员仅自己) */
  async list(params?: {
    category?: string;
    status?: string;
    limit?: number;
  }): Promise<MessageVO[]> {
    const qs: string[] = [`user_id=${encodeURIComponent(getMemberId() || '0')}`];
    if (params?.category) qs.push(`category=${params.category}`);
    if (params?.status) qs.push(`status=${params.status}`);
    qs.push(`limit=${params?.limit ?? 50}`);
    const res = await request<any>({ url: `/api/message/list?${qs.join('&')}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toMessage);
  },

  /** 消息详情 */
  async detail(messageId: number): Promise<MessageVO> {
    const res = await request<any>({ url: `/api/message/${messageId}` });
    return toMessage(res.data || res);
  },

  /** 标记单条已读 */
  async markRead(messageId: number): Promise<any> {
    return await request<any>({
      url: `/api/message/mark-read/${messageId}`,
      method: 'POST',
      data: {},
    });
  },

  /** 批量标记已读 */
  async markAllRead(): Promise<any> {
    return await request<any>({
      url: '/api/message/mark-all-read',
      method: 'POST',
      data: { userId: Number(getMemberId()) },
    });
  },

  /** 消息统计(未读数) */
  async stats(): Promise<{ total: number; unread: number; read: number }> {
    const res = await request<any>({ url: '/api/message/stats' });
    const d = res.data || res;
    return {
      total: d.totalMessages ?? d.total ?? 0,
      unread: d.unreadCount ?? d.unread ?? 0,
      read: d.readCount ?? d.read ?? 0,
    };
  },
};
