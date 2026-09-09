/**
 * AI智能叫帮 API · 对接后端 /api/help/*(67号)
 * 信值互助网络: 公益 100% / 有偿 10% 双轨 · LBS 大厅 · 履约流转 · 双向评价
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 类型字典 */
export const HELP_CATEGORY_NAME: Record<string, string> = {
  repair: '维修互助', escort: '陪护出行', carry: '搬运帮手',
  care: '照看陪伴', teach: '技能传授', other: '其他求助',
};

/** 状态名 */
export const HELP_STATUS_NAME: Record<string, string> = {
  published: '待接单', matched: '已接单', in_progress: '服务中',
  completed: '已完成', cancelled: '已取消',
};

export const helpCategoryName = (c: string): string => HELP_CATEGORY_NAME[c] || c;
export const helpStatusName = (s: string): string => HELP_STATUS_NAME[s] || s;

export interface HelpOrderVO {
  orderId: number;
  publisherId: number;
  mode: string;               // public / paid
  category: string;
  title: string;
  description: string;
  address: string;
  longitude: number;
  latitude: number;
  durationMinutes: number;
  price: number;
  urgency: string;            // normal / urgent
  trustValueReward: number;
  status: string;
  helperId: number | null;
  createdAt: string;
  distanceKm?: number;
}

export interface ParseResultVO {
  category: string;
  suggestedMode: string;
  publicFirst: boolean;
  hint: string;
  banned: string | null;
}

export interface TrustProfileVO {
  memberId: number;
  publicTrust: number;
  paidTrust: number;
  totalTrust: number;
  ratingAvg: number | null;
  ratingCount: number;
  gates: { public: number; paid: number };
  ledger: { ledgerId: number; delta: number; track: string; reason: string; createdAt: string }[];
}

function toOrder(o: any): HelpOrderVO {
  return {
    orderId: Number(o.orderId ?? 0),
    publisherId: Number(o.publisherId ?? 0),
    mode: o.mode || 'public',
    category: o.category || 'other',
    title: o.title || '',
    description: o.description || '',
    address: o.address || '',
    longitude: Number(o.longitude ?? 0),
    latitude: Number(o.latitude ?? 0),
    durationMinutes: Number(o.durationMinutes ?? 60),
    price: Number(o.price ?? 0),
    urgency: o.urgency || 'normal',
    trustValueReward: Number(o.trustValueReward ?? 0),
    status: o.status || 'published',
    helperId: o.helperId ?? null,
    createdAt: o.createdAt || '',
    distanceKm: o.distanceKm != null ? Number(o.distanceKm) : undefined,
  };
}

export const HelpAPI = {
  /** 需求智能解析(类型识别+模式推荐+违禁预检; 发布表单实时调用) */
  async parse(title: string, description: string): Promise<ParseResultVO> {
    const res = await request<any>({
      url: '/api/help/parse',
      method: 'POST',
      data: { title, description },
    });
    const d = res.data || res;
    return {
      category: d.category || 'other',
      suggestedMode: d.suggestedMode || 'public',
      publicFirst: Boolean(d.publicFirst),
      hint: d.hint || '',
      banned: d.banned || null,
    };
  },

  /** 发布求助 */
  async publish(params: {
    mode: string; category: string; title: string; description: string;
    longitude: number; latitude: number; address: string;
    durationMinutes: number; price: number; urgency: string;
  }): Promise<HelpOrderVO> {
    const res = await request<any>({
      url: '/api/help/orders',
      method: 'POST',
      data: params,
    });
    return toOrder(res.data || res);
  },

  /** 互助大厅(LBS: 紧急→距离→新单排序) */
  async hall(params: {
    longitude: number; latitude: number;
    mode?: string; category?: string; radiusKm?: number;
  }): Promise<HelpOrderVO[]> {
    const p: string[] = [
      `longitude=${params.longitude}`,
      `latitude=${params.latitude}`,
      `radius_km=${params.radiusKm ?? 20}`,
    ];
    if (params.mode) p.push(`mode=${params.mode}`);
    if (params.category) p.push(`category=${params.category}`);
    const res = await request<any>({ url: `/api/help/orders?${p.join('&')}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 单详情(公开) */
  async detail(orderId: number): Promise<HelpOrderVO> {
    const res = await request<any>({ url: `/api/help/orders/${orderId}` });
    return toOrder(res.data || res);
  },

  /** 接单(公益零门槛; 有偿需信值≥20) */
  async accept(orderId: number): Promise<HelpOrderVO> {
    const res = await request<any>({
      url: `/api/help/orders/${orderId}/accept`, method: 'POST',
    });
    return toOrder(res.data || res);
  },

  /** 开始服务(帮助者) */
  async start(orderId: number): Promise<HelpOrderVO> {
    const res = await request<any>({
      url: `/api/help/orders/${orderId}/start`, method: 'POST',
    });
    return toOrder(res.data || res);
  },

  /** 确认完成(双方; 触发信值结算) */
  async complete(orderId: number): Promise<any> {
    return await request<any>({
      url: `/api/help/orders/${orderId}/complete`, method: 'POST',
    });
  },

  /** 取消(接单后帮助者取消扣 2 信值) */
  async cancel(orderId: number, reason = ''): Promise<any> {
    return await request<any>({
      url: `/api/help/orders/${orderId}/cancel`,
      method: 'POST',
      data: { reason },
    });
  },

  /** 双向评价(1-5 星; 发布者差评扣帮助者信值) */
  async review(orderId: number, score: number, content: string): Promise<any> {
    return await request<any>({
      url: `/api/help/orders/${orderId}/review`,
      method: 'POST',
      data: { score, content },
    });
  },

  /** 我发布的求助 */
  async myPublished(): Promise<HelpOrderVO[]> {
    const res = await request<any>({ url: '/api/help/my/published' });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 我接的互助 */
  async myHelped(): Promise<HelpOrderVO[]> {
    const res = await request<any>({ url: '/api/help/my/helped' });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 我的信值档案(公益/有偿双轨 + 评分) */
  async trustProfile(memberId?: number): Promise<TrustProfileVO | null> {
    const mid = memberId ?? Number(getMemberId() || 0);
    if (!mid) return null;
    try {
      const res = await request<any>({ url: `/api/help/trust/${mid}` });
      const d = res.data || res;
      return {
        memberId: Number(d.memberId ?? 0),
        publicTrust: Number(d.publicTrust ?? 0),
        paidTrust: Number(d.paidTrust ?? 0),
        totalTrust: Number(d.totalTrust ?? 0),
        ratingAvg: d.ratingAvg ?? null,
        ratingCount: Number(d.ratingCount ?? 0),
        gates: d.gates || { public: 0, paid: 20 },
        ledger: d.ledger || [],
      };
    } catch (_) {
      return null;
    }
  },

  /** 类型字典(公开) */
  async categories(): Promise<{ code: string; name: string; trustBase: number }[]> {
    const res = await request<any>({ url: '/api/help/categories' });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((c: any) => ({
      code: c.code || '', name: c.name || '', trustBase: Number(c.trustBase ?? 0),
    }));
  },
};
