/**
 * 智能知识库训练模型 前端 API 客户端
 * 后端: /api/knowledge/*(admin 域)
 * 铁律: 观测面(dual/stats/mode/samples)永不关停;
 *       黄金标准/负例样本仅为建议数据, 流转必经人工 review/publish
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// 管理端请求头
const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

/** 条目状态名 */
export const ENTRY_STATUS_NAME: Record<string, string> = {
  pending: '待审核',
  approved: '审核通过',
  published: '已发布',
  rejected: '已拒绝',
  retired: '已退役',
};

/** 缺口状态名 */
export const GAP_STATUS_NAME: Record<string, string> = {
  open: '待处理',
  resolved: '已解决',
  ignored: '已忽略',
};

/** 双师样本类型名 */
export const SAMPLE_KIND_NAME: Record<string, string> = {
  golden: '黄金标准',
  negative: '负例',
};

export const entryStatusName = (s: string): string =>
  ENTRY_STATUS_NAME[s] || s;
export const gapStatusName = (s: string): string =>
  GAP_STATUS_NAME[s] || s;
export const sampleKindName = (k: string): string =>
  SAMPLE_KIND_NAME[k] || k;

export const KbModelAPI = {
  /** 双师灰度态(观测面) */
  async dualMode(): Promise<any> {
    const res = await request<any>({
      url: '/api/knowledge/dual/mode', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 双师统计(否决率/黄金数/直方图) */
  async dualStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/knowledge/dual/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 双师样本列表(kind: golden/negative) */
  async dualSamples(kind?: string, limit = 50): Promise<any[]> {
    const q = kind ? `?kind=${kind}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/knowledge/dual/samples${q}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 知识条目列表(status 筛选) */
  async entries(status?: string, limit = 100): Promise<any[]> {
    const q = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/knowledge/entries${q}`, headers: adminHeaders(),
    });
    const d = res.data;
    const list = Array.isArray(d) ? d : (d?.entries || []);
    return Array.isArray(list) ? list : [];
  },

  /** 条目审核(approve) */
  async reviewEntry(entryId: number, approve: boolean, reason = ''): Promise<any> {
    const res = await request<any>({
      url: `/api/knowledge/entries/${entryId}/review`,
      method: 'POST', headers: adminHeaders(),
      data: { approve, reason },
    });
    return res.data || res;
  },

  /** 条目发布(approved → published) */
  async publishEntry(entryId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/knowledge/entries/${entryId}/publish`,
      method: 'POST', headers: adminHeaders(),
      data: {},
    });
    return res.data || res;
  },

  /** 条目退役(published → retired) */
  async retireEntry(entryId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/knowledge/entries/${entryId}/retire`,
      method: 'POST', headers: adminHeaders(),
      data: {},
    });
    return res.data || res;
  },

  /** 知识缺口队列 */
  async gaps(limit = 100): Promise<any[]> {
    const res = await request<any>({
      url: `/api/knowledge/gaps?limit=${limit}`, headers: adminHeaders(),
    });
    const d = res.data;
    const list = Array.isArray(d) ? d : (d?.gaps || []);
    return Array.isArray(list) ? list : [];
  },

  /** 知识库统计(命中率/缺口) */
  async stats(): Promise<any> {
    const res = await request<any>({
      url: '/api/knowledge/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 双师问答(公开 ask, provider=dual) */
  async askDual(question: string): Promise<any> {
    const res = await request<any>({
      url: '/api/knowledge/ask',
      method: 'POST',
      data: { question, provider: 'dual' },
    });
    return res.data || res;
  },

  /** 创建知识条目(候选池 pending) */
  async createEntry(params: {
    question: string; answer: string;
    category?: string; keywords?: string;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/knowledge/entries',
      method: 'POST', headers: adminHeaders(),
      data: {
        question: params.question, answer: params.answer,
        category: params.category || 'faq',
        keywords: params.keywords || '',
        source: 'manual',
      },
    });
    return res.data || res;
  },

  /** 缺口处置(resolve 关联条目 / ignore) */
  async resolveGap(gapId: number, action: 'resolve' | 'ignore', entryId = 0): Promise<any> {
    const res = await request<any>({
      url: `/api/knowledge/gaps/${gapId}/resolve`,
      method: 'POST', headers: adminHeaders(),
      data: { action, entryId },
    });
    return res.data || res;
  },
};
