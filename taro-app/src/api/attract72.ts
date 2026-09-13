/**
 * 72号·AI智能自动引流大模型 前端 API 客户端
 * 后端: /api/attract72/*(X-Role: admin, 引流决策敏感域)
 * 口径: 观测面(画像/信号/洞察/定律/预算状态)不受 ATTRACT72_MODE
 *       影响; 决策面(结晶/发布/预分配生成/系数)off 态 409;
 *       偏差重博弈为快环域内自动(不受 MODE 影响)
 * 铁律: 配额/偏差/系数数字 100% 来自后端确定性公式(前端只渲染)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典(对齐后端 attract72_registry 常量)
// ============================================================

/** 主体类型字典 */
export const SUBJECT_TYPE_NAME: Record<string, string> = {
  member: '会员', influencer: '博主',
};

/** 人格类型字典 */
export const PERSONA_TYPE_NAME: Record<string, string> = {
  connoisseur: '专业品鉴型', sharer: '生活分享型',
  bargain_hunter: '优惠敏感型', newcomer: '新晋型',
};

/** 洞察效果类型字典 */
export const EFFECT_TYPE_NAME: Record<string, string> = {
  driver: '驱动因子', loss: '流失因子', neutral: '中性',
};

/** 洞察/定律状态字典 */
export const STATUS_NAME: Record<string, string> = {
  observed: '已观测', verified: '已验证', draft: '草案',
  submitted: '已提交46号', active: '已生效', expired: '已过期',
};

export const subjectTypeName = (t: string): string =>
  SUBJECT_TYPE_NAME[t] || t;
export const personaTypeName = (t: string): string =>
  PERSONA_TYPE_NAME[t] || t;
export const effectTypeName = (t: string): string =>
  EFFECT_TYPE_NAME[t] || t;
export const statusName = (s: string): string =>
  STATUS_NAME[s] || s;

// ============================================================
// 管理端请求头(X-Role: admin + 登录令牌)
// ============================================================

const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

export const Attract72API = {
  /** 画像列表(渠道人格——观测面) */
  async personas(filters?: {
    subjectType?: string; personaType?: string;
  }): Promise<any> {
    const q: string[] = [];
    if (filters?.subjectType) q.push(`subjectType=${filters.subjectType}`);
    if (filters?.personaType) q.push(`personaType=${filters.personaType}`);
    const res = await request<any>({
      url: `/api/attract72/personas${q.length ? '?' + q.join('&') : ''}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 感知面同步(画像生成+信号摄取——观测面动作) */
  async syncPersonas(today = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/attract72/personas/sync',
      method: 'POST',
      data: today ? { today } : {},
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 画像详情(stats/history) */
  async persona(personaId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/attract72/personas/${personaId}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 信号流列表(节日/雷达事件——观测面) */
  async signals(type = '', limit = 50): Promise<any> {
    const q = `${type ? `type=${type}&` : ''}limit=${limit}`;
    const res = await request<any>({
      url: `/api/attract72/signals?${q}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 反事实对照推理(观测/快环——确定性公式全留痕) */
  async runCausal(): Promise<any> {
    const res = await request<any>({
      url: '/api/attract72/causal/run',
      method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 因果洞察列表(驱动/流失/中性——观测面) */
  async insights(filters?: {
    dimension?: string; effectType?: string; status?: string;
  }): Promise<any> {
    const q: string[] = [];
    if (filters?.dimension) q.push(`dimension=${filters.dimension}`);
    if (filters?.effectType) q.push(`effectType=${filters.effectType}`);
    if (filters?.status) q.push(`status=${filters.status}`);
    const res = await request<any>({
      url: `/api/attract72/causal/insights${q.length ? '?' + q.join('&') : ''}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 定律台账(law/anti——观测面) */
  async laws(kind = '', status = '', limit = 50): Promise<any> {
    const q = `${kind ? `kind=${kind}&` : ''}${status ? `status=${status}&` : ''}limit=${limit}`;
    const res = await request<any>({
      url: `/api/attract72/knowledge/laws?${q}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 洞察 → 定律结晶(决策面——off 态 409) */
  async crystallize(insightId: number): Promise<any> {
    const res = await request<any>({
      url: '/api/attract72/knowledge/crystallize',
      method: 'POST',
      data: { insightId },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 定律发布(决策面——46号 approved 前置) */
  async publishLaw(lawId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/attract72/knowledge/laws/${lawId}/publish`,
      method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 自然语言查询(确定性关键词路由) */
  async query(question: string): Promise<any> {
    const res = await request<any>({
      url: '/api/attract72/knowledge/query',
      method: 'POST',
      data: { question },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 当前预算方案+偏差+历史(观测面) */
  async forecast(now = ''): Promise<any> {
    const res = await request<any>({
      url: `/api/attract72/budget/forecast${now ? `?now=${now}` : ''}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 偏差重博弈(>15% 域内自动——快环, 不受 MODE 影响) */
  async rebalance(now = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/attract72/budget/rebalance/auto',
      method: 'POST',
      data: now ? { now } : {},
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 探索基金状态(观测面) */
  async exploration(): Promise<any> {
    const res = await request<any>({
      url: '/api/attract72/budget/exploration',
      headers: adminHeaders(),
    });
    return res.data || res;
  },
};
