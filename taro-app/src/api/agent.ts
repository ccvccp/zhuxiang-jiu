/**
 * 代理商服务 API · 对接后端 /api/agent/*
 * 等级体系(S/A/B/C/D) / 返利档位(T0-T3 超额累进) / 申请入驻 / 代理商列表
 */
import { request } from './request';
import Taro from '@tarojs/taro';

/** 代理商等级 */
export const AGENT_LEVEL_NAME: Record<string, string> = {
  S: '战略代理', A: '金牌代理', B: '银牌代理', C: '铜牌代理', D: '区域分销',
};

export const agentLevelName = (l: string): string => AGENT_LEVEL_NAME[l] || l;

export interface AgentLevelVO {
  level: string;
  name: string;
  discountRate: number;   // 进货折扣率
  rights: string;
}

export interface RebateTierVO {
  tier: string;
  minAmount: number;
  maxAmount: number | null;
  rate: number;
}

export interface AgentVO {
  agentId: number;
  companyName: string;
  region: string;
  level: string;
  status: string;
  contactName: string;
  contactPhone: string;
  totalPurchaseAmount: number;
}

// 本地存储: 我提交的申请单号(后端 apply 记录无 memberId 关联, 详情查询按申请号)
const MY_APPLY_KEY = 'agent_my_apply_id';

export const AgentAPI = {
  /** 等级体系说明(S/A/B/C/D 权益与进货折扣, 公开) */
  async levels(): Promise<AgentLevelVO[]> {
    const res = await request<any>({ url: '/api/agent/levels' });
    const list = (res || {}).levels || [];
    return (Array.isArray(list) ? list : []).map((l: any) => ({
      level: l.level || '',
      name: l.name || '',
      discountRate: Number(l.discountRate ?? 0),
      rights: l.rights || '',
    }));
  },

  /** 返利档位说明(T0-T3 超额累进制, 公开) */
  async rebateTiers(): Promise<RebateTierVO[]> {
    const res = await request<any>({ url: '/api/agent/rebate/tiers' });
    const d = res?.data || res || {};
    const list = d.tiers || [];
    return (Array.isArray(list) ? list : []).map((t: any) => ({
      tier: t.tier || t.name || '',
      minAmount: Number(t.min ?? t.minAmount ?? 0),
      maxAmount: t.max != null ? Number(t.max) : null,
      rate: Number(t.rate ?? t.rebateRate ?? 0),
    }));
  },

  /** 申请入驻(公开: 公司名/联系人/电话/区域/等级) */
  async apply(params: {
    companyName: string;
    contactName: string;
    contactPhone: string;
    region: string;
    applyLevel: string;
  }): Promise<{ applyId: number }> {
    const res = await request<any>({
      url: '/api/agent/apply',
      method: 'POST',
      data: params,
    });
    const d = res || {};
    const applyId = Number(d.applyId ?? 0);
    if (applyId > 0) {
      Taro.setStorageSync(MY_APPLY_KEY, applyId);
    }
    return { applyId };
  },

  /** 我的申请单号(本地记录; 未申请返回 null) */
  myApplyId(): number | null {
    try {
      const v = Number(Taro.getStorageSync(MY_APPLY_KEY));
      return v > 0 ? v : null;
    } catch (_) {
      return null;
    }
  },

  /** 代理商列表(公开, 分页) */
  async list(params?: {
    level?: string; status?: string; page?: number; pageSize?: number;
  }): Promise<{ agents: AgentVO[]; total: number }> {
    const p: string[] = [];
    if (params?.level) p.push(`level=${params.level}`);
    if (params?.status) p.push(`status=${params.status}`);
    p.push(`page=${params?.page ?? 1}`);
    p.push(`page_size=${params?.pageSize ?? 20}`);
    const res = await request<any>({ url: `/api/agent/list?${p.join('&')}` });
    const d = res?.data || res || {};
    const list = Array.isArray(d) ? d : (d.agents || d.list || []);
    return {
      agents: (Array.isArray(list) ? list : []).map((a: any) => ({
        agentId: Number(a.agentId ?? a.agent_id ?? a.id ?? 0),
        companyName: a.companyName || a.company_name || a.name || '',
        region: a.region || '',
        level: a.level || a.apply_level || '',
        status: a.status || 'active',
        contactName: a.contactName || a.contact_name || '',
        contactPhone: a.contactPhone || a.contact_phone || '',
        totalPurchaseAmount: Number(a.totalPurchaseAmount ?? a.total_purchase ?? 0),
      })),
      total: Number(d.total ?? (res || {}).total ?? 0),
    };
  },
};
