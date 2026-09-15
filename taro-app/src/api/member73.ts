/**
 * 73号·AI智能会员体验大模型 API · 用户权利面
 * 信任面板("AI 为我做了什么") + 画像遗忘权
 *
 * 后端: /api/member73/trust/*(X-Member-Id 本人鉴权, 73号 P4)
 * 口径: 观测面不受 MEMBER73_MODE 影响(用户权利永不关停);
 *       遗忘为五表硬删除+留痕, member 账户本体保留, 不可重复。
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 动作流条目(可解释) */
export interface TrustActionVO {
  kind: 'hint' | 'reveal' | 'delegate';
  refId: number;
  summary: string;
  responded: boolean | null;
  responseType: string;
  at: string;
}

/** 可撤回授权条目 */
export interface RevocableVO {
  action: string;
  grantedAt: string;
}

/** 信任面板(四可: 可解释/可撤回/可验证/可遗忘) */
export interface TrustPanelVO {
  memberId: number;
  nickname: string;
  mode: string;
  actions: TrustActionVO[];
  actionTotal: number;
  revocable: RevocableVO[];
  fourPrinciples: Record<string, string>;
}

/** 遗忘留痕(五表硬删除回执) */
export interface ForgetLedgerVO {
  forgetSeq: number;
  memberId: number;
  deletedTables: Record<string, number>;
  note: string;
  at: string;
}

/** 地平线·保级风险窗 */
export interface KeepRiskVO {
  periodConsume: number;
  requirement: number;
  remainingAmount: number;
  progressPercent: number;
  expireAt: string;
  daysRemaining: number;
  atRisk: boolean;
}

/** 会员地平线(等级/缺口/保级窗) */
export interface HorizonVO {
  memberId: number;
  level: number;
  levelName: string;
  growthValue: number;
  next: { level: number; requirement: number; gap: number } | null;
  estimatedArrival: string | null;
  keepRisk: KeepRiskVO | null;
  coldStart: { inShadow: boolean; daysRemaining?: number };
}

/** 导师时刻(决策留痕) */
export interface MomentVO {
  momentId: number;
  momentType: string;
  entry: string;
  triggerScore: number;
  decision: string;
  rendered: boolean;
  hintPayload: { text?: string };
  responded: boolean;
  responseType: string;
  at: string;
}

/** 权益对比卡 */
export interface BenefitsPreviewVO {
  fromLevel: number;
  toLevel: number;
  rows?: Array<{ name: string; from: string; to: string; delta?: string }>;
  items?: any[];
  note?: string;
}

/** 代办预判 */
export interface PredictVO {
  topAction: string;
  topReason: string;
  candidates: Array<{ action: string; signal: number }>;
  granted: boolean;
  riskTier: string;
  execMode: string;
  engine: string;
}

/** 代办授权条目 */
export interface GrantVO {
  memberId: number;
  action: string;
  granted: boolean;
  grantedAt: string;
  source: string;
}

/** 代办执行留痕 */
export interface DelegateLogVO {
  logId: number;
  action: string;
  executeResult: string;
  note: string;
  at: string;
}

/** 代办五动作(白名单; 资金类永不授权) */
export const DELEGATE_ACTION_NAME: Record<string, string> = {
  profile_completion: '资料补全',
  benefit_claim: '权益领取',
  renewal_prefill: '续费预填',
  review_order: '订单评价',
  address_confirm: '收货地址确认',
};

/** 动作风险档语义 */
export const DELEGATE_RISK_NAME: Record<string, string> = {
  authorized_execute: '授权后代办',
  prefill_only: '仅预填不执行',
  confirm_only: '单步确认引导',
};

/** 时刻类型 */
export const MOMENT_TYPE_NAME: Record<string, string> = {
  order_done: '订单完成',
  achieved: '等级达成',
  points_changed: '积分变动',
  profile_gap: '资料缺口',
};

export const Member73API = {
  /** 信任面板(动作流+可撤回授权清单; 本人/admin) */
  async trustPanel(memberId?: number): Promise<TrustPanelVO> {
    const mid = memberId ?? (Number(getMemberId()) || 0);
    const res = await request<any>({
      url: `/api/member73/trust/panel/${mid}`,
    });
    const d = (res && (res.data || res)) || {};
    return {
      memberId: Number(d.memberId ?? mid),
      nickname: String(d.nickname || ''),
      mode: String(d.mode || ''),
      actions: (d.actions || []).map((a: any) => ({
        kind: a.kind,
        refId: Number(a.refId ?? 0),
        summary: String(a.summary || ''),
        responded: a.responded ?? null,
        responseType: String(a.responseType || ''),
        at: String(a.at || ''),
      })),
      actionTotal: Number(d.actionTotal ?? 0),
      revocable: (d.revocable || []).map((g: any) => ({
        action: String(g.action || ''),
        grantedAt: String(g.grantedAt || ''),
      })),
      fourPrinciples: d.fourPrinciples || {},
    };
  },

  /** 画像遗忘(五表硬删除+seq 留痕; 账户本体保留; 重复遗忘 409) */
  async trustForget(): Promise<ForgetLedgerVO> {
    const res = await request<any>({
      url: '/api/member73/trust/forget',
      method: 'POST',
    });
    const d = (res && (res.data || res)) || {};
    return {
      forgetSeq: Number(d.forgetSeq ?? 0),
      memberId: Number(d.memberId ?? 0),
      deletedTables: Object.fromEntries(
        Object.entries(d.deletedTables || {})
          .map(([k, v]) => [k, Number(v)])),
      note: String(d.note || ''),
      at: String(d.at || ''),
    };
  },

  // ============================================================
  // 地平线(观测面, 本人)
  // ============================================================

  /** 个体升级视野(缺口/外推/保级窗/影子位) */
  async horizon(memberId?: number): Promise<HorizonVO> {
    const mid = memberId ?? (Number(getMemberId()) || 0);
    const res = await request<any>({
      url: `/api/member73/horizon/${mid}`,
    });
    const d = (res && (res.data || res)) || {};
    const k = d.keepRisk || {};
    return {
      memberId: Number(d.memberId ?? mid),
      level: Number(d.level ?? 0),
      levelName: String(d.levelName || ''),
      growthValue: Number(d.growthValue ?? 0),
      next: d.next ? {
        level: Number(d.next.level ?? 0),
        requirement: Number(d.next.requirement ?? 0),
        gap: Number(d.next.gap ?? 0),
      } : null,
      estimatedArrival: d.estimatedArrival || null,
      keepRisk: d.keepRisk ? {
        periodConsume: Number(k.periodConsume ?? 0),
        requirement: Number(k.requirement ?? 0),
        remainingAmount: Number(k.remainingAmount ?? 0),
        progressPercent: Number(k.progressPercent ?? 0),
        expireAt: String(k.expireAt || ''),
        daysRemaining: Number(k.daysRemaining ?? 0),
        atRisk: Boolean(k.atRisk),
      } : null,
      coldStart: {
        inShadow: Boolean((d.coldStart || {}).inShadow),
        daysRemaining: Number((d.coldStart || {}).daysRemaining ?? 0),
      },
    };
  },

  // ============================================================
  // 导师时刻(快环+观测; 本人可查列表/响应)
  // ============================================================

  /** 我的时机留痕(最近 limit 条) */
  async mentorMoments(limit = 30): Promise<MomentVO[]> {
    const mid = Number(getMemberId()) || 0;
    const res = await request<any>({
      url: `/api/member73/mentor/moments?memberId=${mid}&limit=${limit}`,
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((m: any) => ({
      momentId: Number(m.momentId ?? 0),
      momentType: String(m.momentType || ''),
      entry: String(m.entry || ''),
      triggerScore: Number(m.triggerScore ?? 0),
      decision: String(m.decision || ''),
      rendered: Boolean(m.rendered),
      hintPayload: { text: (m.hintPayload || {}).text || '' },
      responded: Boolean(m.responded),
      responseType: String(m.responseType || ''),
      at: String(m.at || ''),
    }));
  },

  /** 时刻响应回流(形式效果学习源; click/upgrade/ignore) */
  async momentRespond(momentId: number, responseType: string): Promise<any> {
    const res = await request<any>({
      url: `/api/member73/mentor/${momentId}/respond`,
      method: 'POST',
      data: { responseType },
    });
    return (res && (res.data || res)) || {};
  },

  // ============================================================
  // 权益揭示(观测面, 本人)
  // ============================================================

  /** 升级前后权益对比卡(L5 最高档 409——调用方兜底) */
  async benefitsPreview(memberId?: number): Promise<BenefitsPreviewVO | null> {
    const mid = memberId ?? (Number(getMemberId()) || 0);
    try {
      const res = await request<any>({
        url: `/api/member73/benefits/preview/${mid}`,
      });
      const d = (res && (res.data || res)) || {};
      return {
        fromLevel: Number(d.fromLevel ?? 0),
        toLevel: Number(d.toLevel ?? 0),
        rows: d.rows || [],
        items: d.items || [],
        note: String(d.note || ''),
      };
    } catch (_) {
      return null;
    }
  },

  /** 我的权益告知留痕 */
  async benefitReveals(limit = 20): Promise<any[]> {
    const mid = Number(getMemberId()) || 0;
    const res = await request<any>({
      url: `/api/member73/benefits/reveals?memberId=${mid}&limit=${limit}`,
    });
    const rows = (res && (res.data || res)) || [];
    return Array.isArray(rows) ? rows : [];
  },

  // ============================================================
  // 代办授权管理(用户显式授权/撤回/执行; 本人)
  // ============================================================

  /** 下一步操作预判(行为序列频率确定性排序) */
  async delegatePredict(): Promise<PredictVO> {
    const res = await request<any>({
      url: '/api/member73/delegate/predict',
      method: 'POST',
    });
    const d = (res && (res.data || res)) || {};
    return {
      topAction: String(d.topAction || ''),
      topReason: String(d.topReason || ''),
      candidates: (d.candidates || []).map((c: any) => ({
        action: String(c.action || ''),
        signal: Number(c.signal ?? 0),
      })),
      granted: Boolean(d.granted),
      riskTier: String(d.riskTier || ''),
      execMode: String(d.execMode || ''),
      engine: String(d.engine || ''),
    };
  },

  /** 我的授权台账(五动作位图) */
  async delegateGrants(memberId?: number): Promise<GrantVO[]> {
    const mid = memberId ?? (Number(getMemberId()) || 0);
    const res = await request<any>({
      url: `/api/member73/delegate/grants/${mid}`,
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((g: any) => ({
      memberId: Number(g.memberId ?? 0),
      action: String(g.action || ''),
      granted: Boolean(g.granted),
      grantedAt: String(g.grantedAt || ''),
      source: String(g.source || ''),
    }));
  },

  /** 授权(用户显式 per-action; 资金类永不授权) */
  async delegateGrant(action: string): Promise<any> {
    const res = await request<any>({
      url: '/api/member73/delegate/grant',
      method: 'POST',
      data: { memberId: 0, action },
    });
    return (res && (res.data || res)) || {};
  },

  /** 撤回授权(用户即否决权, 即时生效留痕) */
  async delegateRevoke(action: string): Promise<any> {
    const res = await request<any>({
      url: '/api/member73/delegate/revoke',
      method: 'POST',
      data: { memberId: 0, action },
    });
    return (res && (res.data || res)) || {};
  },

  /** 代办执行(白名单+授权双重校验; 未授权 rejected) */
  async delegateExecute(action: string): Promise<any> {
    const res = await request<any>({
      url: '/api/member73/delegate/execute',
      method: 'POST',
      data: { memberId: 0, action },
    });
    return (res && (res.data || res)) || {};
  },

  /** 我的代办执行留痕 */
  async delegateLogs(limit = 20): Promise<DelegateLogVO[]> {
    const mid = Number(getMemberId()) || 0;
    const res = await request<any>({
      url: `/api/member73/delegate/logs?memberId=${mid}&limit=${limit}`,
    });
    const rows = (res && (res.data || res)) || [];
    return (Array.isArray(rows) ? rows : []).map((l: any) => ({
      logId: Number(l.logId ?? 0),
      action: String(l.action || ''),
      executeResult: String(l.executeResult || ''),
      note: String(l.note || ''),
      at: String(l.at || ''),
    }));
  },
};
