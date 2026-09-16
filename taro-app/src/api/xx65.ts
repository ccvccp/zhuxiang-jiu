/**
 * 65号·网店及商品AI智能管理 API · 前端工作台消费面
 * 后端: /api/xx65/*(33 端点, 双角色 member+admin)
 * 口径: 观测面(registry/shops详情/products/campaigns/health/
 *       coach/model-status)常开; 决策面(意图/开店/认领/激活/
 *       草稿/发布/活动/撤销)受 XX65_MODE 门控(off 409);
 *       宪法豁免面(关店/人工兜底 S6/巡检/回流)不受开关影响。
 * 标签: categoryLabel/strategy label 均由后端提供——前端零字典。
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

/** 素材意图(确定性关键词路由) */
export interface IntentVO {
  intentId: number;
  category: string;
  categoryLabel: string;
  minLevel: string;
  fallback: boolean;
  complianceQuestions: string[];
}

/** 店铺(六态状态机) */
export interface ShopVO {
  shopId: number;
  ownerId: number;
  status: string;
  category: string;
  categoryLabel?: string;
  shopName?: string;
  createdAt?: string;
}

/** 内容草稿(S1 合规三道防线) */
export interface DraftVO {
  draftId: number;
  shopId: number;
  productName: string;
  description: string;
  price: number;
  status: string;
  llmTrack: string;
  replacements: { from: string; to: string }[];
  watermark?: string;
  complianceScore?: number;
}

/** 商品(S4 双轨价格仅展示) */
export interface ProductVO {
  productId: number;
  shopId: number;
  productName: string;
  price: number;
  status: string;
  trustPortion?: number;
}

/** 活动策略推荐(三因子+ROI 双算) */
export interface RecommendationVO {
  strategy: string;
  label: string;
  score: number;
  roiCashLift: number;
  trustPortion: number;
  channels: string[];
  note?: string;
}

/** 营销活动(S5 五分钟撤销窗口) */
export interface CampaignVO {
  campaignId: number;
  shopId: number;
  strategy: string;
  status: string;
  exclusive: boolean;
  createdAt?: string;
}

/** 合规健康度(三组件加权) */
export interface HealthVO {
  healthScore: number;
  components?: Record<string, number>;
  level?: string;
}

/** 经营教练贴士(按配额档分发) */
export interface CoachTipVO {
  tier: string;
  kind: string;
  title: string;
  body: string;
}

/** 店铺六态(后端状态机——前端展示字典) */
export const SHOP_STATES: Record<string, string> = {
  applying: '申请中',
  prechecked: '预检通过',
  claimed: '已认领',
  active: '经营中',
  suspended: '违规冻结',
  closed: '已关闭',
};

/** 草稿四态(S1 终审状态机) */
export const DRAFT_STATES: Record<string, string> = {
  draft: '待发布',
  pending_review: '人工审核中',
  published: '已发布',
  rejected: '已驳回',
};

/** 角色头(65号双角色——登录态注入) */
function roleHeaders(): Record<string, string> {
  const role = getSession()?.role || 'member';
  return { 'X-Role': role };
}

/** 当前会员 ID(开店流程 ownerId) */
function memberId(): number {
  return Number(getSession()?.memberId || 0);
}

export const Xx65API = {
  /** 模型状态(第39档案——观测面) */
  async modelStatus(): Promise<any> {
    const res = await request<any>({
      url: '/api/xx65/model/status',
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 刚性规则宪法自描述(观测面——S1-S8) */
  async registry(): Promise<any> {
    const res = await request<any>({
      url: '/api/xx65/registry',
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 意图解析(决策面——确定性关键词路由) */
  async parseIntent(text: string): Promise<IntentVO> {
    const res = await request<any>({
      url: '/api/xx65/intents/parse',
      method: 'POST',
      data: { ownerId: memberId(), text },
      headers: roleHeaders(),
    });
    const d = (res && (res.data || res)) || {};
    return {
      intentId: Number(d.intentId ?? 0),
      category: String(d.category || ''),
      categoryLabel: String(d.categoryLabel || ''),
      minLevel: String(d.minLevel || ''),
      fallback: Boolean(d.fallback),
      complianceQuestions: d.complianceQuestions || [],
    };
  },

  /** 开店申请(决策面——S2 信值准入预检) */
  async applyShop(intentId: number): Promise<any> {
    const res = await request<any>({
      url: '/api/xx65/shops/apply',
      method: 'POST',
      data: { ownerId: memberId(), intentId },
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 一键认领(决策面——合规问卷作答) */
  async claimShop(shopId: number,
                  answers: Record<string, string>): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/shops/${shopId}/claim`,
      method: 'POST',
      data: { answers },
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 店铺激活(决策面) */
  async activateShop(shopId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/shops/${shopId}/activate`,
      method: 'POST',
      data: {},
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 自主关店(宪法豁免面——经营者退出权利, 不受开关影响) */
  async closeShop(shopId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/shops/${shopId}/close`,
      method: 'POST',
      data: { closedBy: getSession()?.role || 'member' },
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 我的店铺列表(member+本人 owner_id 可查——用户权利面板口径;
   *  admin 全量观测) */
  async myShops(): Promise<ShopVO[]> {
    const res = await request<any>({
      url: `/api/xx65/shops?owner_id=${memberId()}`,
      headers: roleHeaders(),
    });
    const rows = (res && (res.shops || res.data)) || [];
    return (Array.isArray(rows) ? rows : []).map((s: any) => ({
      shopId: Number(s.shopId ?? 0),
      ownerId: Number(s.ownerId ?? 0),
      status: String(s.status || ''),
      category: String(s.category || ''),
      categoryLabel: String(s.categoryLabel || s.category || ''),
      shopName: String(s.shopName || s.name || ''),
      createdAt: String(s.createdAt || ''),
    }));
  },

  /** 内容草稿生成(决策面·防御①——禁词替换留痕) */
  async createDraft(params: {
    shopId: number; productName: string;
    description: string; price: number;
  }): Promise<DraftVO> {
    const res = await request<any>({
      url: '/api/xx65/products/draft',
      method: 'POST',
      data: params,
      headers: roleHeaders(),
    });
    const d = (res && (res.data || res)) || {};
    return {
      draftId: Number(d.draftId ?? 0),
      shopId: Number(d.shopId ?? params.shopId),
      productName: String(d.productName || params.productName),
      description: String(d.description || ''),
      price: Number(d.price ?? params.price),
      status: String(d.status || 'draft'),
      llmTrack: String(d.llmTrack || 'rule'),
      replacements: (d.replacements || []).map((r: any) => ({
        from: String(r.from || r[0] || ''),
        to: String(r.to || r[1] || ''),
      })),
      watermark: String(d.watermark || ''),
      complianceScore: d.complianceScore,
    };
  },

  /** 草稿发布(决策面·防御②+S1 确认) */
  async publishDraft(draftId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/drafts/${draftId}/publish`,
      method: 'POST',
      data: { confirmed: true },
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 转人工审核(宪法豁免面——S6, 不受开关影响) */
  async humanReview(draftId: number, note: string): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/drafts/${draftId}/human-review`,
      method: 'POST',
      data: { note },
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 商品列表(观测面) */
  async products(shopId: number): Promise<ProductVO[]> {
    const res = await request<any>({
      url: `/api/xx65/products?shop_id=${shopId}`,
      headers: roleHeaders(),
    });
    const rows = (res && (res.products || res.data)) || [];
    return (Array.isArray(rows) ? rows : []).map((p: any) => ({
      productId: Number(p.productId ?? 0),
      shopId: Number(p.shopId ?? shopId),
      productName: String(p.productName || p.name || ''),
      price: Number(p.price ?? 0),
      status: String(p.status || ''),
      trustPortion: p.trustPortion,
    }));
  },

  /** 活动策略推荐(观测面——三因子+ROI 双算) */
  async recommendCampaigns(shopId: number,
                           productId: number): Promise<RecommendationVO[]> {
    const res = await request<any>({
      url: '/api/xx65/campaigns/recommend',
      method: 'POST',
      data: { shopId, productId },
      headers: roleHeaders(),
    });
    const d = (res && (res.data || res)) || {};
    const rows = d.recommendations || [];
    return (Array.isArray(rows) ? rows : []).map((r: any) => ({
      strategy: String(r.strategy || ''),
      label: String(r.label || r.strategy || ''),
      score: Number(r.score ?? 0),
      roiCashLift: Number(r.roiCashLift ?? 0),
      trustPortion: Number(r.trustPortion ?? 0),
      channels: r.channels || [],
      note: String(r.note || ''),
    }));
  },

  /** 创建活动(决策面——S7+R2+S1+S5) */
  async createCampaign(params: {
    shopId: number; productId: number;
    strategy: string; name?: string;
  }): Promise<CampaignVO> {
    const res = await request<any>({
      url: '/api/xx65/campaigns',
      method: 'POST',
      data: params,
      headers: roleHeaders(),
    });
    const d = (res && (res.data || res)) || {};
    return {
      campaignId: Number(d.campaignId ?? 0),
      shopId: Number(d.shopId ?? params.shopId),
      strategy: String(d.strategy || params.strategy),
      status: String(d.status || 'active'),
      exclusive: Boolean(d.exclusive),
      createdAt: String(d.createdAt || ''),
    };
  },

  /** 活动列表(观测面) */
  async campaigns(shopId: number): Promise<CampaignVO[]> {
    const res = await request<any>({
      url: `/api/xx65/campaigns?shop_id=${shopId}`,
      headers: roleHeaders(),
    });
    const rows = (res && (res.campaigns || res.data)) || [];
    return (Array.isArray(rows) ? rows : []).map((c: any) => ({
      campaignId: Number(c.campaignId ?? 0),
      shopId: Number(c.shopId ?? shopId),
      strategy: String(c.strategy || ''),
      status: String(c.status || ''),
      exclusive: Boolean(c.exclusive),
      createdAt: String(c.createdAt || ''),
    }));
  },

  /** 撤销活动(决策面——S5 五分钟窗口) */
  async revokeCampaign(campaignId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/campaigns/${campaignId}/revoke`,
      method: 'POST',
      data: { operator: getSession()?.role || 'member' },
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** 合规健康度看板(观测面) */
  async shopHealth(shopId: number): Promise<HealthVO> {
    const res = await request<any>({
      url: `/api/xx65/shops/${shopId}/health`,
      headers: roleHeaders(),
    });
    const d = (res && (res.data || res)) || {};
    return {
      healthScore: Number(d.healthScore ?? 0),
      components: d.components || {},
      level: String(d.level || ''),
    };
  },

  /** 经营教练贴士(观测面——按配额档分发) */
  async coachTips(shopId: number,
                  kind?: string): Promise<CoachTipVO[]> {
    const q = kind ? `?kind=${kind}` : '';
    const res = await request<any>({
      url: `/api/xx65/shops/${shopId}/coach${q}`,
      headers: roleHeaders(),
    });
    const d = (res && (res.data || res)) || {};
    const rows = d.tips || d.coachTips || [];
    return (Array.isArray(rows) ? rows : []).map((t: any) => ({
      tier: String(t.tier || ''),
      kind: String(t.kind || ''),
      title: String(t.title || ''),
      body: String(t.body || ''),
    }));
  },

  /** 下单窗口(观测面——S4 双轨定价展示, 扣减以 64号为准) */
  async orderWindow(productId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/products/${productId}/order-window?trust_id=${memberId()}`,
      headers: roleHeaders(),
    });
    return res && (res.data || res);
  },

  /** S7 配额升降档(admin 决策面——经 46号审批轨) */
  async quotaAdjust(shopId: number,
                    direction: 'uplift' | 'downgrade'): Promise<any> {
    const res = await request<any>({
      url: `/api/xx65/shops/${shopId}/quota-adjust`,
      method: 'POST',
      data: { direction },
      headers: { 'X-Role': 'admin' },
    });
    return res && (res.data || res);
  },

  /** 红队七向量(admin 决策面——RT-01~07 攻击仿真) */
  async redteam(): Promise<any> {
    const res = await request<any>({
      url: '/api/xx65/redteam',
      method: 'POST',
      data: {},
      headers: { 'X-Role': 'admin' },
    });
    return res && (res.data || res);
  },
};
