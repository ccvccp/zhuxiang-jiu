/**
 * AI 智能客服 API · 对接后端 /api/chat/*
 * 会话列表 → 创建会话(AI优先接待) → 消息收发 → 转人工/关闭/满意度
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 会话状态 */
export const SESSION_STATUS = {
  waiting: '排队等待',
  ai_chatting: 'AI 对话中',
  human_chatting: '人工服务中',
  transferring: '转接中',
  ended: '已结束',
  archived: '已归档',
} as const;

/** 会话类型 */
export const SESSION_TYPES: Array<{ key: string; label: string; desc: string }> = [
  { key: 'presale', label: '售前咨询', desc: '产品/价格/活动' },
  { key: 'aftersale', label: '售后服务', desc: '订单/物流/退换' },
  { key: 'old_wine', label: '老酒回收', desc: '鉴估/报价/交易' },
  { key: 'custom', label: '定制需求', desc: '封坛/礼盒/企业' },
];

/** 会话状态显示名 */
export function sessionStatusName(status: string): string {
  return (SESSION_STATUS as Record<string, string>)[status] || status;
}

export interface ChatSessionVO {
  sessionId: string;
  userId: number;
  sessionType: string;
  status: string;
  aiConfidence: number;
  satisfaction: number;
  unresolvedCount: number;
  createdAt: string;
  endedAt: string | null;
}

export interface ChatMessageVO {
  id: number;
  sessionId: string;
  senderType: 'user' | 'ai' | 'customer_service' | 'system';
  senderId: number;
  messageType: string;
  content: string;
  aiConfidence: number | null;
  createdAt: string;
}

/** 发送消息返回(AI 自动回复) */
export interface SendResultVO {
  userMessageId: number;
  sessionId: string;
  aiReply: ChatMessageVO | null;
  transferred: boolean;
  transferTrigger?: { trigger: string; reason: string };
}

function toSession(s: any): ChatSessionVO {
  return {
    sessionId: s.sessionId || s.session_id || '',
    userId: Number(s.userId ?? s.user_id ?? 0),
    sessionType: s.sessionType || s.session_type || '',
    status: s.status || '',
    aiConfidence: s.aiConfidence ?? s.ai_confidence ?? 0,
    satisfaction: s.satisfaction ?? 0,
    unresolvedCount: s.unresolvedCount ?? s.unresolved_count ?? 0,
    createdAt: s.createdAt || s.created_at || '',
    endedAt: s.endedAt || s.ended_at || null,
  };
}

function toMessage(m: any): ChatMessageVO {
  return {
    id: m.id ?? m.messageId ?? 0,
    sessionId: m.sessionId || m.session_id || '',
    senderType: m.senderType || m.sender_type || 'user',
    senderId: Number(m.senderId ?? m.sender_id ?? 0),
    messageType: m.messageType || m.message_type || 'text',
    content: m.content || '',
    aiConfidence: m.aiConfidence ?? m.ai_confidence ?? null,
    createdAt: m.createdAt || m.created_at || '',
  };
}

export const ChatAPI = {
  /** 我的会话列表(按创建时间倒序) */
  async mySessions(limit = 50): Promise<ChatSessionVO[]> {
    const res = await request<any>({ url: `/api/chat/my-sessions?limit=${limit}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toSession);
  },

  /** 创建会话(AI 优先接待, 返回含系统欢迎消息) */
  async createSession(params: {
    sessionType: string;
    ageConfirmed?: boolean;
  }): Promise<ChatSessionVO> {
    const res = await request<any>({
      url: '/api/chat/sessions',
      method: 'POST',
      data: {
        userId: Number(getMemberId()),
        sessionType: params.sessionType,
        ageConfirmed: params.ageConfirmed ?? true,
      },
    });
    return toSession(res.data || res);
  },

  /** 查询会话详情 */
  async sessionDetail(sessionId: string): Promise<ChatSessionVO> {
    const res = await request<any>({ url: `/api/chat/sessions/${sessionId}` });
    return toSession(res.data || res);
  },

  /** 查询会话消息(按时间正序) */
  async messages(sessionId: string, limit = 100): Promise<ChatMessageVO[]> {
    const res = await request<any>({ url: `/api/chat/sessions/${sessionId}/messages?limit=${limit}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toMessage);
  },

  /** 发送消息(用户消息触发 AI 自动回复) */
  async send(sessionId: string, content: string): Promise<SendResultVO> {
    const res = await request<any>({
      url: `/api/chat/sessions/${sessionId}/messages`,
      method: 'POST',
      data: {
        senderType: 'user',
        senderId: Number(getMemberId()),
        messageType: 'text',
        content,
      },
    });
    const d = res.data || res;
    return {
      userMessageId: d.userMessageId ?? d.user_message_id ?? 0,
      sessionId: d.sessionId || sessionId,
      aiReply: d.aiReply || d.ai_reply ? toMessage(d.aiReply || d.ai_reply) : null,
      transferred: Boolean(d.transferred),
      transferTrigger: d.transferTrigger || d.transfer_trigger || undefined,
    };
  },

  /** 转人工客服 */
  async transfer(sessionId: string, reason = ''): Promise<any> {
    return await request<any>({
      url: `/api/chat/sessions/${sessionId}/transfer`,
      method: 'POST',
      data: { reason },
    });
  },

  /** 关闭会话 */
  async close(sessionId: string): Promise<any> {
    return await request<any>({
      url: `/api/chat/sessions/${sessionId}/close`,
      method: 'POST',
      data: {},
    });
  },

  /** 满意度评价(1-5, 仅已关闭会话可评) */
  async rate(sessionId: string, satisfaction: number): Promise<any> {
    return await request<any>({
      url: `/api/chat/sessions/${sessionId}/satisfaction`,
      method: 'POST',
      data: { satisfaction },
    });
  },
};
