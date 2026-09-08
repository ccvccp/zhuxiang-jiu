/**
 * 客服工单 API · 对接后端 /api/ticket/*
 * 创建工单 → 我的工单列表/详情 → 用户补充 → 确认解决+满意度
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 工单状态 */
export const TICKET_STATUS_NAME: Record<string, string> = {
  pending: '待分配',
  processing: '处理中',
  wait_confirm: '待确认',
  resolved: '已解决',
  closed: '已关闭',
};

/** 工单类型(与后端 TICKET_TYPES 对齐) */
export const TICKET_TYPES: Array<{ key: string; label: string; desc: string }> = [
  { key: 'presale', label: '售前咨询', desc: '产品/价格/活动' },
  { key: 'aftersale', label: '售后服务', desc: '订单/物流/退换' },
  { key: 'complaint', label: '投诉', desc: '自动升级紧急处理' },
  { key: 'suggestion', label: '建议反馈', desc: '产品与服务建议' },
  { key: 'oldwine', label: '老酒回收', desc: '鉴估/报价/交易' },
];

export const ticketStatusName = (s: string): string => TICKET_STATUS_NAME[s] || s;

export interface TicketVO {
  ticketNo: string;
  type: string;
  priority: string;
  status: string;
  description: string;
  orderId: string;
  handlerName: string;
  satisfaction: number | null;
  createdAt: string;
  updatedAt: string;
  resolution?: string;
  overdue?: boolean;
  slaDeadline?: string;
  escalated?: boolean;
}

export interface TicketReplyVO {
  id: number;
  ticketNo: string;
  replierId: string | number;
  replierRole: string;   // staff / user
  content: string;
  createdAt: string;
}

function toTicket(t: any): TicketVO {
  return {
    ticketNo: t.ticketNo || t.ticket_no || '',
    type: t.type || '',
    priority: t.priority || '',
    status: t.status || '',
    description: t.description || '',
    orderId: t.orderId || t.order_id || '',
    handlerName: t.handlerName || t.handler_name || '',
    satisfaction: t.satisfaction ?? null,
    createdAt: t.createdAt || t.created_at || '',
    updatedAt: t.updatedAt || t.updated_at || '',
    resolution: t.resolution || '',
    overdue: Boolean(t.overdue),
    slaDeadline: t.slaDeadline || t.sla_deadline || '',
    escalated: Boolean(t.escalated),
  };
}

function toReply(r: any): TicketReplyVO {
  return {
    id: r.id ?? 0,
    ticketNo: r.ticketNo || r.ticket_no || '',
    replierId: r.replierId ?? r.replier_id ?? '',
    replierRole: r.replierRole || r.replier_role || 'staff',
    content: r.content || '',
    createdAt: r.createdAt || r.created_at || '',
  };
}

export const TicketAPI = {
  /** 创建工单(投诉/VIP 自动升紧急) */
  async create(params: {
    type: string;
    description: string;
    orderId?: string;
    priority?: string;
  }): Promise<TicketVO> {
    const res = await request<any>({
      url: '/api/ticket/create',
      method: 'POST',
      data: {
        type: params.type,
        priority: params.priority || 'medium',
        description: params.description,
        source: 'user',
        orderId: params.orderId || '',
        userLevel: 1,
        memberVip: false,
      },
    });
    return toTicket(res.data || res);
  },

  /** 我的工单列表 */
  async myList(status?: string, limit = 50): Promise<TicketVO[]> {
    const qs = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({ url: `/api/ticket/my-list${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toTicket);
  },

  /** 我的工单详情(含处理记录) */
  async myDetail(ticketNo: string): Promise<TicketVO & { replies: TicketReplyVO[] }> {
    const res = await request<any>({ url: `/api/ticket/my/${ticketNo}` });
    const d = res.data || res;
    return {
      ...toTicket(d),
      replies: (d.replies || []).map(toReply),
    };
  },

  /** 用户补充工单信息 */
  async myReply(ticketNo: string, content: string): Promise<TicketReplyVO> {
    const res = await request<any>({
      url: `/api/ticket/my/${ticketNo}/reply`,
      method: 'POST',
      data: { content },
    });
    return toReply(res.data || res);
  },

  /** 确认解决+满意度评价(1-5 星) */
  async confirm(ticketNo: string, satisfaction: number): Promise<any> {
    return await request<any>({
      url: `/api/ticket/${ticketNo}/confirm`,
      method: 'POST',
      data: { satisfaction },
    });
  },
};
