/**
 * 62号·AI智能无形资产估值模型 前端 API 客户端
 * 后端: /api/av62/*(X-Role: admin, 资产估值敏感域)
 * 铁律: 观测面不受 AV62_MODE 影响; 决策面(登记/估值/
 *       压力/校准)经管理端 API 调用(off 态 409);
 *       估值数字 100% 来自后端确定性引擎(前端只渲染)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典(对齐后端 av62_registry 常量)
// ============================================================

/** 三角色字典 */
export const ROLE_NAME: Record<string, string> = {
  enterprise: '企业', organization: '组织', personal: '个人',
};

/** 置信度三档字典 */
export const TIER_NAME: Record<string, string> = {
  high: '高置信', medium: '中置信', low: '低置信',
};

/** 估值目标字典 */
export const OBJECTIVE_NAME: Record<string, string> = {
  stability: '稳健', growth: '成长', fair: '公允',
};

export const roleName = (r: string): string => ROLE_NAME[r] || r;
export const tierName = (t: string): string => TIER_NAME[t] || t;
export const objectiveName = (o: string): string =>
  OBJECTIVE_NAME[o] || o;

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

export const Av62API = {
  /** 模型状态(第37档案 champion/challenger/八因子) */
  async status(): Promise<any> {
    const res = await request<any>({
      url: '/api/av62/model/status', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 四区看板(度量/资产/评估/防御——观测面不受开关影响) */
  async dashboard(): Promise<any> {
    const res = await request<any>({
      url: '/api/av62/dashboard', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 资产列表(主体/角色/域/状态过滤) */
  async assets(filters?: {
    subjectId?: number; role?: string; domain?: string;
  }): Promise<any> {
    const q: string[] = [];
    if (filters?.subjectId) q.push(`subjectId=${filters.subjectId}`);
    if (filters?.role) q.push(`role=${filters.role}`);
    if (filters?.domain) q.push(`domain=${filters.domain}`);
    const res = await request<any>({
      url: `/api/av62/assets${q.length ? '?' + q.join('&') : ''}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 资产详情(证据快照+要素定义) */
  async asset(assetId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/av62/assets/${assetId}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 评估列表(重估版本链 assessId 倒序) */
  async assessments(limit = 50): Promise<any> {
    const res = await request<any>({
      url: `/api/av62/assessments?limit=${limit}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 信任要素注册表自描述(三角色×九域+负资产域) */
  async registry(): Promise<any> {
    const res = await request<any>({
      url: '/api/av62/registry', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 公平性报告(最新) */
  async fairness(): Promise<any> {
    const res = await request<any>({
      url: '/api/av62/fairness/report', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 回流状态(P4 观测面——验证统计+幂等标记) */
  async learnStatus(): Promise<any> {
    const res = await request<any>({
      url: '/api/av62/learn/status', headers: adminHeaders(),
    });
    return res.data || res;
  },
};
