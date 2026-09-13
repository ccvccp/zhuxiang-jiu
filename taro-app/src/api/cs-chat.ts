/**
 * 客服工作台 API · 对接后端 /api/chat/cs/* 与 /api/chat/*(admin 域)
 * 人工客服侧: 排队列表 → 接入会话 → 收发消息(3s 增量轮询) → 关闭/统计
 * 铁律: admin 头鉴权(X-Role); AI 智能层灰度(CHAT_LLM_MODE)不影响人工回复
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';
import type { ChatSessionVO, ChatMessageVO } from './chat';
import { sessionStatusName } from './chat';

export { sessionStatusName };
export type { ChatSessionVO, ChatMessageVO };

/** 排队状态筛选(客服视角) */
export const QUEUE_STATUS_NAME: Record<string, string> = {
  waiting: '排队等待',
  transferring: '转接中',
  human_chatting: '人工服务中',
};

export const queueStatusName = (s: string): string =>
  QUEUE_STATUS_NAME[s] || sessionStatusName(s);

// 管理端请求头(X-Role: admin + 登录令牌)
const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

/** 当前客服 ID(会话 member_id, 后端工号口径) */
export const currentCsId = (): number => {
  const id = Number(getSession()?.memberId || 1);
  return Number.isFinite(id) && id > 0 ? id : 1;
};

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

export const CsChatAPI = {
  /** 排队列表(按状态: human_chatting/transferring/waiting) */
  async queue(status = 'human_chatting', limit = 100): Promise<ChatSessionVO[]> {
    const res = await request<any>({
      url: `/api/chat/cs/queue?status=${status}&limit=${limit}`,
      headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toSession);
  },

  /** 接入会话(未分配的 human_chatting; 已分配幂等返回) */
  async accept(sessionId: string): Promise<any> {
    const res = await request<any>({
      url: `/api/chat/cs/sessions/${sessionId}/accept`,
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
    return res.data || res;
  },

  /** 客服回复(走敏感词过滤, 不触发 AI) */
  async reply(sessionId: string, content: string): Promise<any> {
    const res = await request<any>({
      url: `/api/chat/cs/sessions/${sessionId}/reply`,
      method: 'POST',
      headers: adminHeaders(),
      data: {
        customerServiceId: currentCsId(),
        content,
        messageType: 'text',
      },
    });
    return res.data || res;
  },

  /** 会话消息(admin 可查任意会话; since 增量轮询) */
  async messages(sessionId: string, sinceMessageId = 0, limit = 100): Promise<ChatMessageVO[]> {
    const res = await request<any>({
      url: `/api/chat/sessions/${sessionId}/messages?limit=${limit}`
        + (sinceMessageId > 0 ? `&since_message_id=${sinceMessageId}` : ''),
      headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toMessage);
  },

  /** 关闭会话(admin 复用既有端点) */
  async close(sessionId: string): Promise<any> {
    return await request<any>({
      url: `/api/chat/sessions/${sessionId}/close`,
      method: 'POST',
      headers: adminHeaders(),
      data: {},
    });
  },

  /** 会话统计(观测面: 总数/分布/AI解决率/满意度) */
  async stats(): Promise<any> {
    const res = await request<any>({
      url: '/api/chat/stats',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** AI 智能层灰度态(观测面) */
  async mode(): Promise<any> {
    const res = await request<any>({
      url: '/api/chat/mode',
      headers: adminHeaders(),
    });
    return res.data || res;
  },
};
