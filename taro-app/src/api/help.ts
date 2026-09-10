/**
 * AI智能叫帮 API · 对接后端 /api/help/*(67号)
 * 信值互助网络: 公益 100% / 有偿 10% 双轨 · LBS 大厅 · 履约流转 · 双向评价
 * P1 智能调度: 三维匹配 · 偏好画像 · 故事卡 · 安全护航 · 信值捐赠 · 荣誉体系
 * P2 生态深化: 信值传承(数字功德碑) · 企业CSR信值包 · 互助接力
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

export interface RelayLegVO {
  legNo: number;
  address: string;
  status: string;             // pending / matched / in_progress / completed
  helperId: number | null;
}

export interface RelayMetaVO {
  totalLegs: number;
  completedLegs: number;
  currentLegNo: number;
  currentLegAddress: string;
}

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
  fitScore?: number;          // P1 个性化命中(1=常接类型)
  fitReason?: string;         // P1 命中理由(可解释)
  donatedTrust?: number;      // P1 捐赠池
  isRelay?: boolean;          // P2 互助接力
  relayLegs?: RelayLegVO[];    // P2 接力段
  relayMeta?: RelayMetaVO;    // P2 大厅接力进度
}

export interface ParseResultVO {
  category: string;
  suggestedMode: string;
  publicFirst: boolean;
  hint: string;
  banned: string | null;
}

export interface HonorVO {
  name: string;
  icon: string;
  currentTrust: number;
  nextAt: number | null;
  progress: number;
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
  donateGate?: number;        // P1 捐赠资格门槛
  donatedOut?: number;        // P1 累计捐赠
  honor?: HonorVO;            // P1 荣誉等级
  carbonGrams?: number;        // P3 碳减排(克, 只读观测)
}

export interface MatchCandidateVO {
  memberId: number;
  matchScore: number;
  sameCategoryDone: number;
  ratingAvg: number | null;
  distanceKm: number;
  reason: string;
}

export interface PreferencesVO {
  memberId: number;
  helpedCount: number;
  completedCount: number;
  topCategories: { category: string; name: string; count: number }[];
  totalDurationMinutes: number;
}

export interface StoryCardVO {
  orderId: number;
  title: string;
  categoryName: string;
  theme: string;
  mode: string;
  trustValueReward: number;
  donatedTrust: number;
  date: string;
  publisherReview: string;
  helperReview: string;
  shareText: string;
}

export interface GuardVO {
  orderId: number;
  status: string;
  statusName: string;
  icebreaker: string;
  elapsedMinutes: number | null;
  expectedMinutes: number | null;
  overtimeRatio: number | null;
  reminder: string;
}

export interface HeritageVO {
  heritageId: number;
  ownerId: number;
  heirId: number;
  declaredAmount: number;
  status: string;             // pending / done / cancelled
  transferredAmount: number;
  createdAt: string;
  confirmedAt: string;
}

export interface CsrPackageVO {
  packageId: number;
  memberId: number;
  name: string;
  amount: number;
  remaining: number;
  note: string;
  status: string;             // active / exhausted
  donations: { orderId: number; orderTitle: string; amount: number; createdAt: string }[];
  createdAt: string;
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
    fitScore: o.fitScore != null ? Number(o.fitScore) : undefined,
    fitReason: o.fitReason || undefined,
    donatedTrust: o.donatedTrust != null ? Number(o.donatedTrust) : undefined,
    isRelay: o.isRelay ? true : undefined,
    relayLegs: o.relayLegs
      ? o.relayLegs.map((l: any) => ({
        legNo: Number(l.legNo ?? 0),
        address: l.address || '',
        status: l.status || 'pending',
        helperId: l.helperId ?? null,
      }))
      : undefined,
    relayMeta: o.relayMeta
      ? {
        totalLegs: Number(o.relayMeta.totalLegs ?? 0),
        completedLegs: Number(o.relayMeta.completedLegs ?? 0),
        currentLegNo: Number(o.relayMeta.currentLegNo ?? 1),
        currentLegAddress: o.relayMeta.currentLegAddress || '',
      }
      : undefined,
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

  /** 发布求助(P2 支持 relay 接力多段) */
  async publish(params: {
    mode: string; category: string; title: string; description: string;
    longitude: number; latitude: number; address: string;
    durationMinutes: number; price: number; urgency: string;
    relay?: boolean; legs?: number;
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

  /** 我的信值档案(公益/有偿双轨 + 评分 + P1 荣誉/捐赠) */
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
        donateGate: d.donateGate != null ? Number(d.donateGate) : undefined,
        donatedOut: d.donatedOut != null ? Number(d.donatedOut) : undefined,
        honor: d.honor || undefined,
        carbonGrams: d.carbonGrams != null ? Number(d.carbonGrams) : undefined,
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

  // ================= P1 智能调度层 =================

  /** 三维匹配推荐(Top3 候选 + 可解释理由; 公开观测) */
  async match(orderId: number): Promise<{ orderId: number; topCandidates: MatchCandidateVO[]; candidatePool: number }> {
    const res = await request<any>({ url: `/api/help/orders/${orderId}/match` });
    const d = res.data || res;
    return {
      orderId: Number(d.orderId ?? 0),
      topCandidates: (d.topCandidates || []).map((c: any) => ({
        memberId: Number(c.memberId ?? 0),
        matchScore: Number(c.matchScore ?? 0),
        sameCategoryDone: Number(c.sameCategoryDone ?? 0),
        ratingAvg: c.ratingAvg ?? null,
        distanceKm: Number(c.distanceKm ?? 0),
        reason: c.reason || '',
      })),
      candidatePool: Number(d.candidatePool ?? 0),
    };
  },

  /** 我的互助偏好画像(常接类型/累计统计; 确定性聚合) */
  async preferences(): Promise<PreferencesVO | null> {
    try {
      const res = await request<any>({ url: '/api/help/preferences' });
      const d = res.data || res;
      return {
        memberId: Number(d.memberId ?? 0),
        helpedCount: Number(d.helpedCount ?? 0),
        completedCount: Number(d.completedCount ?? 0),
        topCategories: (d.topCategories || []).map((c: any) => ({
          category: c.category || '', name: c.name || '', count: Number(c.count ?? 0),
        })),
        totalDurationMinutes: Number(d.totalDurationMinutes ?? 0),
      };
    } catch (_) {
      return null;
    }
  },

  /** 互助故事卡(仅已完成单; 确定性模板) */
  async storyCard(orderId: number): Promise<StoryCardVO> {
    const res = await request<any>({ url: `/api/help/orders/${orderId}/story` });
    const d = res.data || res;
    return {
      orderId: Number(d.orderId ?? 0),
      title: d.title || '',
      categoryName: d.categoryName || '',
      theme: d.theme || '',
      mode: d.mode || 'public',
      trustValueReward: Number(d.trustValueReward ?? 0),
      donatedTrust: Number(d.donatedTrust ?? 0),
      date: d.date || '',
      publisherReview: d.publisherReview || '',
      helperReview: d.helperReview || '',
      shareText: d.shareText || '',
    };
  },

  /** 安全护航(破冰提示 + 超时温和提醒; 观测不干预) */
  async guard(orderId: number): Promise<GuardVO> {
    const res = await request<any>({ url: `/api/help/orders/${orderId}/guard` });
    const d = res.data || res;
    return {
      orderId: Number(d.orderId ?? 0),
      status: d.status || '',
      statusName: d.statusName || '',
      icebreaker: d.icebreaker || '',
      elapsedMinutes: d.elapsedMinutes != null ? Number(d.elapsedMinutes) : null,
      expectedMinutes: d.expectedMinutes != null ? Number(d.expectedMinutes) : null,
      overtimeRatio: d.overtimeRatio != null ? Number(d.overtimeRatio) : null,
      reminder: d.reminder || '',
    };
  },

  /** 信值捐赠(≥50 信值用户反哺公益单; 完成时奖励帮助者) */
  async donate(orderId: number, amount: number): Promise<{ orderDonatedTrust: number; donorTrustLeft: number }> {
    const res = await request<any>({
      url: `/api/help/orders/${orderId}/donate`,
      method: 'POST',
      data: { amount },
    });
    const d = res.data || res;
    return {
      orderDonatedTrust: Number(d.orderDonatedTrust ?? 0),
      donorTrustLeft: Number(d.donorTrustLeft ?? 0),
    };
  },

  // ================= P2 生态深化层 =================

  /** 发起信值传承(数字功德碑: 仅公益信值; 受让人确认后划转) */
  async heritageApply(heirMemberId: number, amount?: number): Promise<HeritageVO> {
    const res = await request<any>({
      url: '/api/help/heritage/apply',
      method: 'POST',
      data: { heirMemberId, amount: amount ?? null },
    });
    return toHeritage(res.data || res);
  },

  /** 受让人确认传承(划转即时生效) */
  async heritageAccept(heritageId: number): Promise<{ transferred: number }> {
    const res = await request<any>({
      url: `/api/help/heritage/${heritageId}/accept`,
      method: 'POST',
    });
    const d = res.data || res;
    return { transferred: Number(d.transferred ?? 0) };
  },

  /** 发起人撤回待确认传承(v2: 即时生效, 留痕不删除) */
  async heritageCancel(heritageId: number): Promise<void> {
    await request<any>({
      url: `/api/help/heritage/${heritageId}/cancel`,
      method: 'POST',
    });
  },

  /** 我的传承记录(发起 + 受让两向) */
  async heritageMy(): Promise<{ outgoing: HeritageVO[]; incoming: HeritageVO[] }> {
    const res = await request<any>({ url: '/api/help/heritage/my' });
    const d = res.data || res;
    return {
      outgoing: (d.outgoing || []).map(toHeritage),
      incoming: (d.incoming || []).map(toHeritage),
    };
  },

  /** 创建企业信值包(CSR 认捐留痕) */
  async csrCreatePackage(name: string, amount: number, note = ''): Promise<CsrPackageVO> {
    const res = await request<any>({
      url: '/api/help/csr/packages',
      method: 'POST',
      data: { name, amount, note },
    });
    return toCsrPackage(res.data || res);
  },

  /** 我的企业信值包列表 */
  async csrMyPackages(): Promise<CsrPackageVO[]> {
    const res = await request<any>({ url: '/api/help/csr/my' });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toCsrPackage);
  },

  /** 企业包定向捐助公益单(入单捐赠池) */
  async csrDonate(packageId: number, orderId: number, amount: number): Promise<{ orderDonatedTrust: number; remaining: number }> {
    const res = await request<any>({
      url: `/api/help/csr/packages/${packageId}/donate`,
      method: 'POST',
      data: { orderId, amount },
    });
    const d = res.data || res;
    return {
      orderDonatedTrust: Number(d.orderDonatedTrust ?? 0),
      remaining: Number(d.package?.remaining ?? 0),
    };
  },
};

function toHeritage(h: any): HeritageVO {
  return {
    heritageId: Number(h.heritageId ?? 0),
    ownerId: Number(h.ownerId ?? 0),
    heirId: Number(h.heirId ?? 0),
    declaredAmount: Number(h.declaredAmount ?? 0),
    status: h.status || 'pending',
    transferredAmount: Number(h.transferredAmount ?? 0),
    createdAt: h.createdAt || '',
    confirmedAt: h.confirmedAt || '',
  };
}

function toCsrPackage(p: any): CsrPackageVO {
  return {
    packageId: Number(p.packageId ?? 0),
    memberId: Number(p.memberId ?? 0),
    name: p.name || '',
    amount: Number(p.amount ?? 0),
    remaining: Number(p.remaining ?? 0),
    note: p.note || '',
    status: p.status || 'active',
    donations: (p.donations || []).map((d: any) => ({
      orderId: Number(d.orderId ?? 0),
      orderTitle: d.orderTitle || '',
      amount: Number(d.amount ?? 0),
      createdAt: d.createdAt || '',
    })),
    createdAt: p.createdAt || '',
  };
}
